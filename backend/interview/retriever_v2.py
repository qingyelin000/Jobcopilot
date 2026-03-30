from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
import re
from pathlib import Path

from qdrant_client import QdrantClient, models

from .embedding_utils import (
    build_embedding_provider,
    compose_query_embedding_text,
    default_embedding_provider_name,
    tokenize_for_embedding,
)
from .retriever_v1 import (
    DEFAULT_DATASET_PATH,
    RetrievalQuestion,
    RetrievedQuestion,
    RetrieverV1,
)


NON_ALNUM_CJK_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")

DEFAULT_QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").strip() or "http://localhost:6333"
DEFAULT_QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
DEFAULT_QDRANT_COLLECTION = (
    os.getenv("QDRANT_COLLECTION", "nowcoder_interview_questions_v1").strip()
    or "nowcoder_interview_questions_v1"
)


@dataclass(frozen=True)
class _Candidate:
    question: RetrievalQuestion
    vector_rank: int | None
    vector_score_raw: float
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
    ) -> None:
        self.dataset_path = Path(dataset_path)
        self.collection_name = collection_name
        self.vector_candidate_pool = max(int(vector_candidate_pool), 8)
        self.lexical_candidate_pool = max(int(lexical_candidate_pool), 8)
        self.strict_metadata_filter = bool(strict_metadata_filter)

        self.lexical_retriever = RetrieverV1(dataset_path=self.dataset_path)
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

    def _vector_candidates(
        self,
        *,
        resume_text: str,
        jd_text: str,
        extra_query: str | None,
        target_company: str | None,
        target_role: str | None,
        strict_metadata_filter: bool,
    ) -> list[tuple[RetrievalQuestion, float]]:
        query_text = compose_query_embedding_text(
            resume_text=resume_text,
            jd_text=jd_text,
            extra_query=extra_query,
        )
        query_vector = self.embedding_provider.embed_texts([query_text])[0]
        query_filter = self._build_qdrant_filter(
            target_company=target_company,
            target_role=target_role,
            strict_metadata_filter=strict_metadata_filter,
        )

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

        common_kwargs = {
            "collection_name": self.collection_name,
            "limit": self.vector_candidate_pool,
            "with_payload": True,
            "with_vectors": False,
        }

        hits: list[object] = []
        if hasattr(self.client, "search"):
            search_kwargs = dict(common_kwargs)
            search_kwargs["query_filter"] = query_filter
            try:
                hits = _extract_points(self.client.search(query_vector=query_vector, **search_kwargs))
            except TypeError:
                # Compatibility fallback for older/newer qdrant-client signatures.
                hits = _extract_points(self.client.search(vector=query_vector, **search_kwargs))
        else:
            query_kwargs = dict(common_kwargs)
            query_kwargs["query_filter"] = query_filter
            try:
                response = self.client.query_points(query=query_vector, **query_kwargs)
            except TypeError:
                try:
                    response = self.client.query_points(query_vector=query_vector, **query_kwargs)
                except TypeError:
                    # Some versions use `filter` instead of `query_filter`.
                    query_kwargs.pop("query_filter", None)
                    query_kwargs["filter"] = query_filter
                    response = self.client.query_points(query=query_vector, **query_kwargs)
            hits = _extract_points(response)

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
    ) -> list[RetrievedQuestion]:
        return self.lexical_retriever.search(
            resume_text=resume_text,
            jd_text=jd_text,
            top_k=self.lexical_candidate_pool,
            extra_query=extra_query,
        )

    @staticmethod
    def _extract_anchor_terms(
        *,
        resume_text: str,
        jd_text: str,
        extra_query: str | None,
        target_company: str | None,
        target_role: str | None,
    ) -> set[str]:
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
        vector_hits = self._vector_candidates(
            resume_text=resume_text,
            jd_text=jd_text,
            extra_query=extra_query,
            target_company=target_company,
            target_role=target_role,
            strict_metadata_filter=use_strict_filter,
        )
        lexical_hits = self._lexical_candidates(
            resume_text=resume_text,
            jd_text=jd_text,
            extra_query=extra_query,
        )

        merged: dict[str, _Candidate] = {}
        for rank, (question, score) in enumerate(vector_hits, start=1):
            merged[question.question_id] = _Candidate(
                question=question,
                vector_rank=rank,
                vector_score_raw=score,
                lexical_rank=None,
                lexical_score_raw=0.0,
            )

        for rank, item in enumerate(lexical_hits, start=1):
            existing = merged.get(item.question.question_id)
            if existing is None:
                merged[item.question.question_id] = _Candidate(
                    question=item.question,
                    vector_rank=None,
                    vector_score_raw=0.0,
                    lexical_rank=rank,
                    lexical_score_raw=float(item.score),
                )
                continue
            merged[item.question.question_id] = _Candidate(
                question=existing.question,
                vector_rank=existing.vector_rank,
                vector_score_raw=existing.vector_score_raw,
                lexical_rank=rank,
                lexical_score_raw=float(item.score),
            )

        candidates = list(merged.values())
        if not candidates:
            return []

        normalized_vector_scores = _normalize_scores([candidate.vector_score_raw for candidate in candidates])
        normalized_lexical_scores = _normalize_scores([candidate.lexical_score_raw for candidate in candidates])
        now_utc = datetime.now(timezone.utc)
        anchor_terms = self._extract_anchor_terms(
            resume_text=resume_text,
            jd_text=jd_text,
            extra_query=extra_query,
            target_company=target_company,
            target_role=target_role,
        )

        rescored: list[RetrievedQuestion] = []
        for index, candidate in enumerate(candidates):
            question = candidate.question
            semantic_score = normalized_vector_scores[index]
            lexical_score = normalized_lexical_scores[index]
            rrf_score = _rank_score(candidate.vector_rank) + _rank_score(candidate.lexical_rank)
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

            # Weight priority: question_text relevance > role > company > freshness.
            text_relevance_score = (
                0.50 * semantic_score
                + 0.20 * lexical_score
                + 0.10 * rrf_score
                + 0.20 * resume_align_score
            )
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
                        "semantic": round(semantic_score, 6),
                        "lexical": round(lexical_score, 6),
                        "rrf": round(rrf_score, 6),
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

                # Penalize semantically similar questions to control duplicate rate.
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

        return selected[: max(top_k, 1)]
