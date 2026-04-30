from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
import re
from pathlib import Path

import requests
from qdrant_client import QdrantClient, models

from .embedding_utils import (
    build_embedding_provider,
    compose_query_embedding_text,
    default_embedding_provider_name,
    tokenize_for_embedding,
)
from .lexical_retriever import (
    DEFAULT_DATASET_PATH,
    LexicalRetriever,
    RetrievalQuestion,
    RetrievedQuestion,
    serialize_retrieved_question,
)


NON_ALNUM_CJK_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")

DEFAULT_QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").strip() or "http://localhost:6333"
DEFAULT_QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
DEFAULT_QDRANT_COLLECTION = (
    os.getenv("QDRANT_COLLECTION", "nowcoder_interview_questions_v1").strip()
    or "nowcoder_interview_questions_v1"
)

DEFAULT_RERANK_BASE_URL = (
    os.getenv("RERANK_BASE_URL", "https://api.siliconflow.cn/v1").strip()
    or "https://api.siliconflow.cn/v1"
)
DEFAULT_RERANK_MODEL = (
    os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3").strip()
    or "BAAI/bge-reranker-v2-m3"
)


@dataclass(frozen=True)
class _Candidate:
    question: RetrievalQuestion
    dense_rank: int | None
    dense_score_raw: float
    lexical_rank: int | None
    lexical_score_raw: float


def _normalize_for_match(value: str | None) -> str:
    if not value:
        return ""
    lowered = str(value).lower().strip()
    return NON_ALNUM_CJK_PATTERN.sub("", lowered)


def _soft_match(left: str | None, right: str | None) -> bool:
    left_normalized = _normalize_for_match(left)
    right_normalized = _normalize_for_match(right)
    if not left_normalized or not right_normalized:
        return False
    return left_normalized in right_normalized or right_normalized in left_normalized


def _parse_publish_time(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    if maximum - minimum < 1e-12:
        return [1.0] * len(values)
    return [(value - minimum) / (maximum - minimum) for value in values]


def _rank_score(rank: int | None, *, k: int = 60) -> float:
    if rank is None:
        return 0.0
    return 1.0 / (k + rank)


def _token_overlap(anchor_terms: set[str], text: str) -> float:
    if not anchor_terms:
        return 0.0
    candidate_terms = {
        token.lower().strip()
        for token in tokenize_for_embedding(text)
        if len(token.strip()) > 1
    }
    if not candidate_terms:
        return 0.0
    overlap = len(anchor_terms.intersection(candidate_terms))
    return overlap / len(anchor_terms)


def _freshness_score(publish_time: str | None, *, now_utc: datetime, half_life_days: float = 180.0) -> float:
    parsed = _parse_publish_time(publish_time)
    if parsed is None:
        return 0.0
    age_days = max((now_utc - parsed).total_seconds() / 86400.0, 0.0)
    return math.exp(-age_days / max(half_life_days, 1e-6))


def _token_set(text: str) -> set[str]:
    return {
        token.lower().strip()
        for token in tokenize_for_embedding(text)
        if len(token.strip()) > 1
    }


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


class RetrieverV2:
    def __init__(
        self,
        *,
        dataset_path: Path | str = DEFAULT_DATASET_PATH,
        qdrant_url: str = DEFAULT_QDRANT_URL,
        qdrant_api_key: str | None = DEFAULT_QDRANT_API_KEY,
        collection_name: str = DEFAULT_QDRANT_COLLECTION,
        embedding_provider: str = default_embedding_provider_name(),
        embedding_model: str | None = None,
        embedding_api_key: str | None = None,
        embedding_base_url: str | None = None,
        embedding_dimension: int = 384,
        vector_candidate_pool: int = 64,
        lexical_candidate_pool: int = 40,
        strict_metadata_filter: bool = False,
        rerank_enabled: bool | None = None,
        rerank_model: str | None = None,
        rerank_api_key: str | None = None,
        rerank_base_url: str | None = None,
        rerank_timeout_seconds: float | None = None,
        rerank_candidate_pool: int = 40,
    ) -> None:
        self.dataset_path = Path(dataset_path)
        self.collection_name = collection_name
        self.vector_candidate_pool = max(int(vector_candidate_pool), 8)
        self.lexical_candidate_pool = max(int(lexical_candidate_pool), 8)
        self.strict_metadata_filter = bool(strict_metadata_filter)

        # 混合检索：词法分支负责可解释匹配，向量分支负责语义召回。
        self.lexical_retriever = LexicalRetriever(dataset_path=self.dataset_path)
        self.embedding_provider = build_embedding_provider(
            provider_name=embedding_provider,
            embedding_model=embedding_model,
            embedding_api_key=embedding_api_key,
            embedding_base_url=embedding_base_url,
            embedding_dimension=embedding_dimension,
        )
        self.client = QdrantClient(
            url=qdrant_url,
            api_key=(qdrant_api_key or "").strip() or None,
        )

        self.rerank_enabled = (
            _env_flag("RERANK_ENABLED", default=True)
            if rerank_enabled is None
            else bool(rerank_enabled)
        )
        self.rerank_model = (rerank_model or "").strip() or DEFAULT_RERANK_MODEL
        self.rerank_base_url = (rerank_base_url or "").strip() or DEFAULT_RERANK_BASE_URL
        self.rerank_api_key = (
            (rerank_api_key or "").strip()
            or os.getenv("RERANK_API_KEY", "").strip()
            or os.getenv("EMBEDDING_API_KEY", "").strip()
            or os.getenv("OPENAI_API_KEY", "").strip()
            or ""
        )
        timeout_value = (
            float(rerank_timeout_seconds)
            if rerank_timeout_seconds is not None
            else _env_float("RERANK_TIMEOUT_SECONDS", 15.0)
        )
        self.rerank_timeout_seconds = max(float(timeout_value), 1.0)
        self.rerank_candidate_pool = max(int(rerank_candidate_pool), 8)
        if not self.rerank_api_key:
            self.rerank_enabled = False

    @staticmethod
    def _question_from_payload(payload: dict) -> RetrievalQuestion:
        return RetrievalQuestion(
            question_id=str(payload.get("question_id") or ""),
            source_content_id=str(payload.get("source_content_id") or ""),
            company=payload.get("company"),
            role=payload.get("role"),
            section=payload.get("section"),
            publish_time=payload.get("publish_time"),
            normalized_key=payload.get("normalized_key"),
            question_text=str(payload.get("question_text") or ""),
            question_type=str(payload.get("question_type") or "general"),
        )

    def _build_qdrant_filter(
        self,
        *,
        target_company: str | None,
        target_role: str | None,
        strict_metadata_filter: bool,
    ) -> models.Filter | None:
        # strict 模式下在向量召回阶段直接按公司/岗位过滤，降低误召回。
        if not strict_metadata_filter:
            return None
        must_conditions: list[models.FieldCondition] = []
        if (target_company or "").strip():
            must_conditions.append(
                models.FieldCondition(
                    key="company",
                    match=models.MatchValue(value=target_company.strip()),
                )
            )
        if (target_role or "").strip():
            must_conditions.append(
                models.FieldCondition(
                    key="role",
                    match=models.MatchValue(value=target_role.strip()),
                )
            )
        if not must_conditions:
            return None
        return models.Filter(must=must_conditions)

    def _build_query_vector(
        self,
        *,
        resume_text: str,
        jd_text: str,
        extra_query: str | None,
    ) -> list[float]:
        query_text = compose_query_embedding_text(
            resume_text=resume_text,
            jd_text=jd_text,
            extra_query=extra_query,
        )
        return self.embedding_provider.embed_texts([query_text])[0]

    @staticmethod
    def _extract_points(result: object) -> list[object]:
        if result is None:
            return []
        if isinstance(result, list):
            return result
        points = getattr(result, "points", None)
        if isinstance(points, list):
            return points
        legacy = getattr(result, "result", None)
        if isinstance(legacy, list):
            return legacy
        return []

    def _search_dense_candidates(
        self,
        *,
        query_vector: list[float],
        query_filter: models.Filter | None,
    ) -> list[tuple[RetrievalQuestion, float]]:
        common_kwargs = {
            "collection_name": self.collection_name,
            "limit": self.vector_candidate_pool,
            "with_payload": True,
            "with_vectors": False,
        }

        hits: list[object] = []
        query_kwargs = dict(common_kwargs)
        query_kwargs["query_filter"] = query_filter

        # build_qdrant_index.py creates the default unnamed vector; keep named
        # fallback only for older collections that were created with "dense".
        if hasattr(self.client, "query_points"):
            try:
                response = self.client.query_points(query=query_vector, **query_kwargs)
                hits = self._extract_points(response)
            except Exception:
                try:
                    response = self.client.query_points(query=query_vector, using="dense", **query_kwargs)
                    hits = self._extract_points(response)
                except Exception:
                    hits = []

        if not hits and hasattr(self.client, "search"):
            search_kwargs = dict(common_kwargs)
            search_kwargs["query_filter"] = query_filter
            try:
                response = self.client.search(query_vector=query_vector, **search_kwargs)
                hits = self._extract_points(response)
            except Exception:
                try:
                    response = self.client.search(
                        query_vector=models.NamedVector(name="dense", vector=query_vector),  # type: ignore[arg-type]
                        **search_kwargs,
                    )
                    hits = self._extract_points(response)
                except Exception:
                    hits = []

        results: list[tuple[RetrievalQuestion, float]] = []
        for hit in hits:
            payload = getattr(hit, "payload", None) or {}
            question = self._question_from_payload(payload)
            if not question.question_id or not question.question_text:
                continue
            score = float(getattr(hit, "score", 0.0) or 0.0)
            results.append((question, score))
        return results

    def _lexical_candidates(
        self,
        *,
        resume_text: str,
        jd_text: str,
        extra_query: str | None,
    ) -> list[tuple[RetrievalQuestion, float]]:
        lexical_hits = self.lexical_retriever.search(
            resume_text=resume_text,
            jd_text=jd_text,
            top_k=self.lexical_candidate_pool,
            extra_query=extra_query,
        )
        return [(item.question, float(item.score)) for item in lexical_hits]

    @staticmethod
    def _extract_anchor_terms(
        *,
        resume_text: str,
        jd_text: str,
        extra_query: str | None,
        target_company: str | None,
        target_role: str | None,
    ) -> set[str]:
        # 锚点词用于计算题目与查询上下文（简历/JD/目标岗位）的词项重合度。
        merged = " ".join(
            part
            for part in (
                extra_query or "",
                jd_text,
                resume_text,
                target_company or "",
                target_role or "",
            )
            if part
        )
        return {
            token.lower().strip()
            for token in tokenize_for_embedding(merged)
            if len(token.strip()) > 1
        }

    @staticmethod
    def _compose_rerank_document(question: RetrievalQuestion) -> str:
        parts = [
            str(question.company or "").strip(),
            str(question.role or "").strip(),
            str(question.question_type or "").strip(),
            str(question.question_text or "").strip(),
        ]
        return " | ".join(part for part in parts if part)

    @staticmethod
    def _compose_rerank_query(
        *,
        resume_text: str,
        jd_text: str,
        extra_query: str | None,
        target_company: str | None,
        target_role: str | None,
    ) -> str:
        base = compose_query_embedding_text(
            resume_text=resume_text,
            jd_text=jd_text,
            extra_query=extra_query,
        )
        tail = " ".join(part for part in (target_company or "", target_role or "") if part).strip()
        if tail:
            return f"{base}\n{tail}".strip()
        return base

    def _call_rerank_api(self, *, query: str, documents: list[str]) -> list[float] | None:
        if not self.rerank_enabled or not self.rerank_api_key or not query.strip() or not documents:
            return None
        url = f"{self.rerank_base_url.rstrip('/')}/rerank"
        headers = {
            "Authorization": f"Bearer {self.rerank_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.rerank_model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
            "return_documents": False,
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=self.rerank_timeout_seconds)
            response.raise_for_status()
            body = response.json()
        except Exception:
            return None

        scores = [0.0] * len(documents)
        raw_results = body.get("results")
        if raw_results is None and isinstance(body.get("data"), dict):
            raw_results = body["data"].get("results")
        if not isinstance(raw_results, list):
            return None

        any_score = False
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            score_raw = item.get("relevance_score", item.get("score"))
            try:
                score = float(score_raw)
            except (TypeError, ValueError):
                continue
            index_raw = item.get("index")
            if index_raw is None:
                continue
            try:
                index = int(index_raw)
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= len(documents):
                continue
            scores[index] = score
            any_score = True
        return scores if any_score else None

    def search(
        self,
        *,
        resume_text: str,
        jd_text: str,
        top_k: int = 8,
        extra_query: str | None = None,
        target_company: str | None = None,
        target_role: str | None = None,
        strict_metadata_filter: bool | None = None,
    ) -> list[RetrievedQuestion]:
        use_strict_filter = self.strict_metadata_filter if strict_metadata_filter is None else strict_metadata_filter
        query_filter = self._build_qdrant_filter(
            target_company=target_company,
            target_role=target_role,
            strict_metadata_filter=use_strict_filter,
        )
        dense_query = self._build_query_vector(
            resume_text=resume_text,
            jd_text=jd_text,
            extra_query=extra_query,
        )
        dense_hits = self._search_dense_candidates(
            query_vector=dense_query,
            query_filter=query_filter,
        )
        lexical_hits = self._lexical_candidates(
            resume_text=resume_text,
            jd_text=jd_text,
            extra_query=extra_query,
        )

        merged: dict[str, _Candidate] = {}
        for rank, (question, score) in enumerate(dense_hits, start=1):
            merged[question.question_id] = _Candidate(
                question=question,
                dense_rank=rank,
                dense_score_raw=score,
                lexical_rank=None,
                lexical_score_raw=0.0,
            )

        for rank, (question, score) in enumerate(lexical_hits, start=1):
            existing = merged.get(question.question_id)
            if existing is None:
                merged[question.question_id] = _Candidate(
                    question=question,
                    dense_rank=None,
                    dense_score_raw=0.0,
                    lexical_rank=rank,
                    lexical_score_raw=float(score),
                )
                continue
            merged[question.question_id] = _Candidate(
                question=existing.question,
                dense_rank=existing.dense_rank,
                dense_score_raw=existing.dense_score_raw,
                lexical_rank=rank,
                lexical_score_raw=float(score),
            )

        candidates = list(merged.values())
        if not candidates:
            return []

        normalized_dense_scores = _normalize_scores([candidate.dense_score_raw for candidate in candidates])
        normalized_lexical_scores = _normalize_scores([candidate.lexical_score_raw for candidate in candidates])
        now_utc = datetime.now(timezone.utc)
        anchor_terms = self._extract_anchor_terms(
            resume_text=resume_text,
            jd_text=jd_text,
            extra_query=extra_query,
            target_company=target_company,
            target_role=target_role,
        )

        rough_scores: list[float] = []
        for index, candidate in enumerate(candidates):
            dense_score = normalized_dense_scores[index]
            lexical_score = normalized_lexical_scores[index]
            rrf_score = _rank_score(candidate.dense_rank) + _rank_score(candidate.lexical_rank)
            # 粗排仅用于筛选 rerank 候选，不直接作为最终输出分。
            rough_scores.append(0.55 * dense_score + 0.25 * lexical_score + 0.20 * rrf_score)

        rerank_raw_scores = [0.0] * len(candidates)
        if self.rerank_enabled and candidates:
            rerank_query = self._compose_rerank_query(
                resume_text=resume_text,
                jd_text=jd_text,
                extra_query=extra_query,
                target_company=target_company,
                target_role=target_role,
            )
            rerank_limit = min(self.rerank_candidate_pool, len(candidates))
            # 只对粗排 TopN 做重排，控制延迟与调用成本。
            selected_indexes = sorted(
                range(len(candidates)),
                key=lambda idx: rough_scores[idx],
                reverse=True,
            )[:rerank_limit]
            rerank_documents = [
                self._compose_rerank_document(candidates[idx].question)
                for idx in selected_indexes
            ]
            rerank_scores = self._call_rerank_api(
                query=rerank_query,
                documents=rerank_documents,
            )
            if rerank_scores is not None:
                for local_idx, score in enumerate(rerank_scores):
                    if local_idx >= len(selected_indexes):
                        break
                    rerank_raw_scores[selected_indexes[local_idx]] = max(float(score), 0.0)

        if any(score > 0.0 for score in rerank_raw_scores):
            normalized_rerank_scores = _normalize_scores(rerank_raw_scores)
        else:
            normalized_rerank_scores = [0.0] * len(candidates)

        rescored: list[RetrievedQuestion] = []
        for index, candidate in enumerate(candidates):
            question = candidate.question
            dense_score = normalized_dense_scores[index]
            lexical_score = normalized_lexical_scores[index]
            rerank_score = normalized_rerank_scores[index]
            rrf_score = _rank_score(candidate.dense_rank) + _rank_score(candidate.lexical_rank)
            role_score = 1.0 if (target_role and _soft_match(target_role, question.role)) else 0.0
            company_score = 1.0 if (target_company and _soft_match(target_company, question.company)) else 0.0

            resume_align_score = _token_overlap(
                anchor_terms,
                " ".join(
                    part
                    for part in (
                        question.company or "",
                        question.role or "",
                        question.question_text,
                    )
                    if part
                ),
            )
            freshness_score = _freshness_score(question.publish_time, now_utc=now_utc)

            # Dense 召回 + 词法召回 + RRF + 简历对齐 + 二阶段 rerank 融合。
            text_relevance_score = (
                0.36 * dense_score
                + 0.18 * lexical_score
                + 0.10 * rrf_score
                + 0.16 * resume_align_score
                + 0.20 * rerank_score
            )
            # 最终分：文本相关性为主，再叠加公司/岗位匹配与时效性。
            final_score = (
                0.70 * text_relevance_score
                + 0.16 * role_score
                + 0.09 * company_score
                + 0.05 * freshness_score
            )

            rescored.append(
                RetrievedQuestion(
                    question=question,
                    score=round(final_score, 6),
                    matched_keywords=[],
                    score_breakdown={
                        "text_relevance": round(text_relevance_score, 6),
                        "dense": round(dense_score, 6),
                        "lexical": round(lexical_score, 6),
                        # backward-compat: old clients may still read "sparse" key.
                        "sparse": round(lexical_score, 6),
                        "rrf": round(rrf_score, 6),
                        "rerank": round(rerank_score, 6),
                        "role": round(role_score, 6),
                        "company": round(company_score, 6),
                        "resume_align": round(resume_align_score, 6),
                        "freshness": round(freshness_score, 6),
                    },
                )
            )

        rescored.sort(key=lambda item: item.score, reverse=True)
        selected: list[RetrievedQuestion] = []
        selected_ids: set[str] = set()
        seen_keys: set[str] = set()
        selected_token_sets: list[set[str]] = []
        candidates_left = rescored[:]
        while candidates_left and len(selected) < max(top_k, 1):
            best_item: RetrievedQuestion | None = None
            best_adjusted_score = -1e9
            best_normalized_key = ""
            best_tokens: set[str] = set()

            for item in candidates_left:
                normalized_key = (item.question.normalized_key or "").strip() or _normalize_for_match(item.question.question_text)
                if normalized_key and normalized_key in seen_keys:
                    continue
                if item.question.question_id in selected_ids:
                    continue

                candidate_tokens = _token_set(item.question.question_text)
                max_similarity = 0.0
                for chosen_tokens in selected_token_sets:
                    similarity = _jaccard_similarity(candidate_tokens, chosen_tokens)
                    if similarity > max_similarity:
                        max_similarity = similarity

                # 对语义高度相似的问题施加惩罚，降低重复题比例。
                duplicate_penalty = 0.22 * max_similarity
                adjusted_score = item.score - duplicate_penalty
                if adjusted_score > best_adjusted_score:
                    best_adjusted_score = adjusted_score
                    best_item = item
                    best_normalized_key = normalized_key
                    best_tokens = candidate_tokens

            if best_item is None:
                break

            selected.append(best_item)
            selected_ids.add(best_item.question.question_id)
            selected_token_sets.append(best_tokens)
            if best_normalized_key:
                seen_keys.add(best_normalized_key)
            candidates_left = [item for item in candidates_left if item.question.question_id != best_item.question.question_id]

        if len(selected) < max(top_k, 1):
            for item in rescored:
                if len(selected) >= max(top_k, 1):
                    break
                if item.question.question_id in selected_ids:
                    continue
                selected.append(item)
                selected_ids.add(item.question.question_id)

        return sorted(selected, key=lambda item: item.score, reverse=True)[: max(top_k, 1)]
