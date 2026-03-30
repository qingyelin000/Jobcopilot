from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from interview.embedding_utils import default_embedding_provider_name
from interview.retriever_v1 import DEFAULT_DATASET_PATH, RetrieverV1
from interview.retriever_v2 import (
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_QDRANT_URL,
    RetrieverV2,
)


ASCII_WORD_PATTERN = re.compile(r"[A-Za-z0-9+#.\-]{2,}")
CHINESE_SEGMENT_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}")
ALNUM_CJK_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")

STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "from",
    "this",
    "that",
    "are",
    "was",
    "were",
    "have",
    "has",
    "had",
    "into",
    "about",
    "项目",
    "负责",
    "以及",
    "一个",
    "我们",
    "你们",
    "他们",
    "公司",
    "岗位",
    "面试",
    "实习",
    "开发",
    "工程师",
}


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    query: str
    resume_text: str
    jd_text: str
    top_k: int
    relevance: dict[str, float]
    target_company: str
    target_role: str
    resume_keywords: list[str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate RetrieverV1 quality with labeled cases (JSONL).",
    )
    parser.add_argument(
        "--cases",
        required=True,
        help="Evaluation cases JSONL path.",
    )
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET_PATH),
        help="Retrieval dataset path used by RetrieverV1.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Default top-k if a case does not provide top_k.",
    )
    parser.add_argument(
        "--min-relevance",
        type=float,
        default=1.0,
        help="Relevance threshold to treat a label as positive.",
    )
    parser.add_argument(
        "--freshness-half-life-days",
        type=float,
        default=180.0,
        help="Half-life days for freshness score decay. Lower means stronger recency preference.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Evaluate at most N cases. 0 means all.",
    )
    parser.add_argument(
        "--output",
        default="data/nowcoder/pipeline_runs_llm/retriever_v1_eval_report.json",
        help="Evaluation report JSON output path.",
    )
    parser.add_argument(
        "--show-cases",
        type=int,
        default=5,
        help="How many worst cases to include in report preview.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail fast on invalid case format instead of skipping.",
    )
    parser.add_argument(
        "--retriever-backend",
        default="v1",
        choices=["v1", "v2"],
        help="Retriever backend to evaluate.",
    )
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL, help="Qdrant URL for RetrieverV2.")
    parser.add_argument("--qdrant-api-key", default="", help="Optional Qdrant API key for RetrieverV2.")
    parser.add_argument(
        "--qdrant-collection",
        default=DEFAULT_QDRANT_COLLECTION,
        help="Qdrant collection name for RetrieverV2.",
    )
    parser.add_argument(
        "--embedding-provider",
        default=default_embedding_provider_name(),
        choices=["hash", "openai_compatible"],
        help="Embedding provider for RetrieverV2 query vectors.",
    )
    parser.add_argument("--embedding-model", default="", help="Embedding model for RetrieverV2.")
    parser.add_argument("--embedding-api-key", default="", help="Embedding API key for RetrieverV2.")
    parser.add_argument("--embedding-base-url", default="", help="Embedding base URL for RetrieverV2.")
    parser.add_argument(
        "--embedding-dimension",
        type=int,
        default=384,
        help="Embedding dimension for hash provider in RetrieverV2.",
    )
    parser.add_argument(
        "--vector-candidate-pool",
        type=int,
        default=64,
        help="Vector candidate pool size for RetrieverV2.",
    )
    parser.add_argument(
        "--lexical-candidate-pool",
        type=int,
        default=40,
        help="Lexical candidate pool size for RetrieverV2.",
    )
    parser.add_argument(
        "--strict-metadata-filter",
        action="store_true",
        help="Enable strict company/role metadata filter in RetrieverV2.",
    )
    return parser


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_match_text(value: str | None) -> str:
    if not value:
        return ""
    lowered = str(value).lower().strip()
    return ALNUM_CJK_PATTERN.sub("", lowered)


def match_soft(target: str | None, candidate: str | None) -> bool:
    normalized_target = normalize_match_text(target)
    normalized_candidate = normalize_match_text(candidate)
    if not normalized_target or not normalized_candidate:
        return False
    return normalized_target in normalized_candidate or normalized_candidate in normalized_target


def parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
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


def tokenize_text(text: str) -> list[str]:
    tokens: list[str] = []
    lowered = text.lower()

    for word in ASCII_WORD_PATTERN.findall(lowered):
        token = word.strip()
        if token:
            tokens.append(token)

    for segment in CHINESE_SEGMENT_PATTERN.findall(text):
        cleaned = segment.strip()
        if not cleaned:
            continue
        if len(cleaned) <= 6:
            tokens.append(cleaned)
        for index in range(len(cleaned) - 1):
            tokens.append(cleaned[index : index + 2])
    return tokens


def is_informative_token(token: str) -> bool:
    lowered = token.lower().strip()
    if not lowered:
        return False
    if lowered in STOPWORDS:
        return False
    if lowered.isdigit():
        return False
    if len(lowered) <= 1:
        return False
    return True


def build_resume_anchor_terms(case: EvalCase) -> set[str]:
    anchors: set[str] = set()
    if case.resume_keywords:
        for keyword in case.resume_keywords:
            for token in tokenize_text(keyword):
                if is_informative_token(token):
                    anchors.add(token.lower())
            normalized = normalize_match_text(keyword)
            if normalized and is_informative_token(normalized):
                anchors.add(normalized)

    combined = " ".join(part for part in (case.resume_text, case.jd_text, case.query) if part).strip()
    token_freq: dict[str, int] = {}
    for token in tokenize_text(combined):
        normalized = token.lower().strip()
        if not is_informative_token(normalized):
            continue
        token_freq[normalized] = token_freq.get(normalized, 0) + 1

    sorted_tokens = sorted(token_freq.items(), key=lambda item: item[1], reverse=True)
    for token, _ in sorted_tokens[:40]:
        anchors.add(token)

    for hint in (case.target_company, case.target_role):
        if not hint:
            continue
        for token in tokenize_text(hint):
            normalized = token.lower().strip()
            if is_informative_token(normalized):
                anchors.add(normalized)
        normalized_hint = normalize_match_text(hint)
        if normalized_hint and is_informative_token(normalized_hint):
            anchors.add(normalized_hint)

    return anchors


def compute_duplicate_metrics(signatures: list[str]) -> tuple[float, float]:
    if not signatures:
        return 1.0, 0.0
    unique_count = len(set(signatures))
    total_count = len(signatures)
    diversity = unique_count / total_count
    dup_rate = 1.0 - diversity
    return dup_rate, diversity


def freshness_from_publish_time(
    publish_time: str | None,
    *,
    now_utc: datetime,
    half_life_days: float,
) -> float | None:
    published_at = parse_iso_datetime(publish_time)
    if published_at is None:
        return None
    age_days = max((now_utc - published_at).total_seconds() / 86400.0, 0.0)
    if half_life_days <= 0:
        return 0.0
    return math.exp(-age_days / half_life_days)


def rank_weights(count: int) -> list[float]:
    return [1.0 / math.log2(index + 2.0) for index in range(count)]


def weighted_average(values: list[float | None], weights: list[float]) -> float | None:
    weighted_sum = 0.0
    total_weight = 0.0
    for index, value in enumerate(values):
        if value is None:
            continue
        weight = weights[index] if index < len(weights) else 1.0
        weighted_sum += float(value) * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return weighted_sum / total_weight


def round_optional(value: float | None, ndigits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), ndigits)


def company_role_match_score(
    *,
    target_company: str,
    target_role: str,
    question_company: str | None,
    question_role: str | None,
) -> float | None:
    has_company_target = bool(target_company.strip())
    has_role_target = bool(target_role.strip())
    if not has_company_target and not has_role_target:
        return None

    company_match = match_soft(target_company, question_company) if has_company_target else False
    role_match = match_soft(target_role, question_role) if has_role_target else False

    if has_company_target and has_role_target:
        if company_match and role_match:
            return 1.0
        if role_match:
            return 0.7
        if company_match:
            return 0.5
        return 0.0
    if has_role_target:
        return 1.0 if role_match else 0.0
    return 1.0 if company_match else 0.0


def overlap_ratio(base_terms: set[str], candidate_terms: set[str]) -> float:
    if not base_terms or not candidate_terms:
        return 0.0
    overlap = len(base_terms.intersection(candidate_terms))
    return overlap / len(base_terms)


def load_cases(path: Path, *, default_top_k: int, strict: bool) -> tuple[list[EvalCase], int]:
    cases: list[EvalCase] = []
    skipped = 0
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            if strict:
                raise ValueError(f"Invalid JSON at line {line_no}: {exc}") from exc
            skipped += 1
            continue

        case_id = str(payload.get("case_id") or f"case_{line_no}").strip()
        query = str(payload.get("query") or payload.get("extra_query") or "").strip()
        resume_text = str(payload.get("resume_text") or "").strip()
        jd_text = str(payload.get("jd_text") or "").strip()
        top_k = max(to_int(payload.get("top_k"), default_top_k), 1)
        target_company = str(
            payload.get("target_company")
            or payload.get("company")
            or payload.get("jd_company")
            or ""
        ).strip()
        target_role = str(
            payload.get("target_role")
            or payload.get("role")
            or payload.get("job_title")
            or ""
        ).strip()

        raw_resume_keywords = payload.get("resume_keywords")
        resume_keywords: list[str] = []
        if isinstance(raw_resume_keywords, list):
            for item in raw_resume_keywords:
                token = str(item).strip()
                if token:
                    resume_keywords.append(token)

        relevance: dict[str, float] = {}
        raw_relevance = payload.get("relevance")
        if isinstance(raw_relevance, dict):
            for key, value in raw_relevance.items():
                qid = str(key).strip()
                if not qid:
                    continue
                relevance[qid] = to_float(value, 0.0)

        raw_relevant_ids = payload.get("relevant_question_ids")
        if isinstance(raw_relevant_ids, list):
            for qid in raw_relevant_ids:
                normalized = str(qid).strip()
                if not normalized:
                    continue
                relevance[normalized] = max(relevance.get(normalized, 0.0), 1.0)

        raw_judgments = payload.get("judgments")
        if isinstance(raw_judgments, list):
            for item in raw_judgments:
                if not isinstance(item, dict):
                    continue
                qid = str(item.get("question_id") or "").strip()
                if not qid:
                    continue
                relevance[qid] = to_float(item.get("relevance"), relevance.get(qid, 0.0))

        if not query and not resume_text and not jd_text:
            if strict:
                raise ValueError(f"Case {case_id} has no query/resume_text/jd_text.")
            skipped += 1
            continue
        if strict and not relevance:
            raise ValueError(f"Case {case_id} has empty relevance labels in strict mode.")

        cases.append(
            EvalCase(
                case_id=case_id,
                query=query,
                resume_text=resume_text,
                jd_text=jd_text,
                top_k=top_k,
                relevance=relevance,
                target_company=target_company,
                target_role=target_role,
                resume_keywords=resume_keywords,
            )
        )
    return cases, skipped


def dcg_at_k(gains: list[float], k: int) -> float:
    score = 0.0
    for idx, gain in enumerate(gains[:k], start=1):
        if gain <= 0:
            continue
        score += (2**gain - 1) / math.log2(idx + 1)
    return score


def compute_quality_score(
    *,
    diversity_at_k: float | None,
    freshness_at_k: float | None,
    company_role_match_at_k: float | None,
    resume_alignment_at_k: float | None,
) -> float | None:
    components = (
        (resume_alignment_at_k, 0.30),
        (company_role_match_at_k, 0.30),
        (diversity_at_k, 0.25),
        (freshness_at_k, 0.15),
    )
    weighted_sum = 0.0
    total_weight = 0.0
    for value, weight in components:
        if value is None:
            continue
        weighted_sum += value * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return max(0.0, min(1.0, weighted_sum / total_weight))


def evaluate_case(
    case: EvalCase,
    *,
    retriever: Any,
    retriever_backend: str,
    min_relevance: float,
    freshness_half_life_days: float,
    now_utc: datetime,
) -> dict[str, Any]:
    if retriever_backend == "v2":
        results = retriever.search(
            resume_text=case.resume_text,
            jd_text=case.jd_text,
            top_k=case.top_k,
            extra_query=case.query or None,
            target_company=case.target_company or None,
            target_role=case.target_role or None,
        )
    else:
        results = retriever.search(
            resume_text=case.resume_text,
            jd_text=case.jd_text,
            top_k=case.top_k,
            extra_query=case.query or None,
        )
    ranked = results[: case.top_k]
    ranked_ids = [item.question.question_id for item in ranked]
    ranked_scores = [case.relevance.get(qid, 0.0) for qid in ranked_ids]
    has_labels = bool(case.relevance)

    if has_labels:
        positive_ids = {qid for qid, score in case.relevance.items() if score >= min_relevance}
        positive_count = len(positive_ids)
        hit_flags = [1 if case.relevance.get(qid, 0.0) >= min_relevance else 0 for qid in ranked_ids]
        retrieved_positive = sum(hit_flags)

        hit_at_k = 1.0 if retrieved_positive > 0 else 0.0
        precision_at_k = retrieved_positive / max(case.top_k, 1)
        recall_at_k = retrieved_positive / max(positive_count, 1)

        mrr_at_k = 0.0
        for idx, flag in enumerate(hit_flags, start=1):
            if flag:
                mrr_at_k = 1.0 / idx
                break

        ap_sum = 0.0
        hit_running = 0
        for idx, flag in enumerate(hit_flags, start=1):
            if not flag:
                continue
            hit_running += 1
            ap_sum += hit_running / idx
        map_at_k = ap_sum / max(positive_count, 1)

        dcg = dcg_at_k(ranked_scores, case.top_k)
        ideal_gains = sorted(case.relevance.values(), reverse=True)
        idcg = dcg_at_k(ideal_gains, case.top_k)
        ndcg_at_k = dcg / idcg if idcg > 0 else 0.0
    else:
        positive_count = None
        retrieved_positive = None
        hit_at_k = None
        precision_at_k = None
        recall_at_k = None
        mrr_at_k = None
        map_at_k = None
        ndcg_at_k = None

    signatures: list[str] = []
    freshness_scores: list[float | None] = []
    company_role_scores: list[float | None] = []
    resume_alignment_scores: list[float] = []
    resume_anchor_terms = build_resume_anchor_terms(case)

    for item in ranked:
        signature = (item.question.normalized_key or "").strip()
        if not signature:
            signature = normalize_match_text(item.question.question_text)
        signatures.append(signature or item.question.question_id)

        freshness_scores.append(
            freshness_from_publish_time(
                item.question.publish_time,
                now_utc=now_utc,
                half_life_days=freshness_half_life_days,
            )
        )

        company_role_scores.append(
            company_role_match_score(
                target_company=case.target_company,
                target_role=case.target_role,
                question_company=item.question.company,
                question_role=item.question.role,
            )
        )

        question_terms = {
            token.lower()
            for token in tokenize_text(
                " ".join(
                    part
                    for part in (
                        item.question.company or "",
                        item.question.role or "",
                        item.question.question_text,
                    )
                    if part
                )
            )
            if is_informative_token(token)
        }
        resume_alignment_scores.append(overlap_ratio(resume_anchor_terms, question_terms))

    dup_rate_at_k, diversity_at_k = compute_duplicate_metrics(signatures)
    weights = rank_weights(len(ranked))
    freshness_at_k = weighted_average(freshness_scores, weights)
    company_role_match_at_k = weighted_average(company_role_scores, weights)
    resume_alignment_at_k = weighted_average(
        [float(value) for value in resume_alignment_scores],
        weights,
    ) if resume_anchor_terms else None

    has_company_and_role_target = bool(case.target_company.strip()) and bool(case.target_role.strip())
    top3_company_role_hit = None
    if has_company_and_role_target:
        top3_company_role_hit = 0.0
        for item in ranked[:3]:
            company_match = match_soft(case.target_company, item.question.company)
            role_match = match_soft(case.target_role, item.question.role)
            if company_match and role_match:
                top3_company_role_hit = 1.0
                break

    quality_score = compute_quality_score(
        diversity_at_k=diversity_at_k,
        freshness_at_k=freshness_at_k,
        company_role_match_at_k=company_role_match_at_k,
        resume_alignment_at_k=resume_alignment_at_k,
    )

    return {
        "case_id": case.case_id,
        "top_k": case.top_k,
        "retrieved_count": len(ranked),
        "has_labels": has_labels,
        "positive_count": positive_count,
        "retrieved_positive": retrieved_positive,
        "hit_at_k": round_optional(hit_at_k),
        "precision_at_k": round_optional(precision_at_k),
        "recall_at_k": round_optional(recall_at_k),
        "mrr_at_k": round_optional(mrr_at_k),
        "map_at_k": round_optional(map_at_k),
        "ndcg_at_k": round_optional(ndcg_at_k),
        "dup_rate_at_k": round_optional(dup_rate_at_k),
        "diversity_at_k": round_optional(diversity_at_k),
        "freshness_at_k": round_optional(freshness_at_k),
        "company_role_match_at_k": round_optional(company_role_match_at_k),
        "top3_company_role_hit": round_optional(top3_company_role_hit),
        "resume_alignment_at_k": round_optional(resume_alignment_at_k),
        "quality_score": round_optional(quality_score),
        "target_company": case.target_company or None,
        "target_role": case.target_role or None,
        "resume_anchor_term_count": len(resume_anchor_terms),
        "query_preview": (case.query or case.jd_text or case.resume_text)[:120],
        "top_result_ids": ranked_ids[: min(5, len(ranked_ids))],
    }


def avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.fmean(values))


def metric_values(per_case: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for item in per_case:
        value = item.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def build_metric_summary(per_case: list[dict[str, Any]], key: str) -> tuple[float | None, int]:
    values = metric_values(per_case, key)
    if not values:
        return None, 0
    return round(avg(values), 6), len(values)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    cases_path = Path(args.cases)
    dataset_path = Path(args.dataset)
    output_path = Path(args.output)

    cases, skipped_cases = load_cases(
        cases_path,
        default_top_k=max(args.top_k, 1),
        strict=bool(args.strict),
    )
    if args.limit > 0:
        cases = cases[: args.limit]
    if not cases:
        raise ValueError("No valid evaluation cases loaded.")

    if args.retriever_backend == "v2":
        retriever = RetrieverV2(
            dataset_path=dataset_path,
            qdrant_url=args.qdrant_url,
            qdrant_api_key=args.qdrant_api_key or None,
            collection_name=args.qdrant_collection,
            embedding_provider=args.embedding_provider,
            embedding_model=args.embedding_model or None,
            embedding_api_key=args.embedding_api_key or None,
            embedding_base_url=args.embedding_base_url or None,
            embedding_dimension=max(args.embedding_dimension, 1),
            vector_candidate_pool=max(args.vector_candidate_pool, 8),
            lexical_candidate_pool=max(args.lexical_candidate_pool, 8),
            strict_metadata_filter=bool(args.strict_metadata_filter),
        )
    else:
        retriever = RetrieverV1(dataset_path=dataset_path)
    now_utc = datetime.now(timezone.utc)

    per_case = [
        evaluate_case(
            case,
            retriever=retriever,
            retriever_backend=args.retriever_backend,
            min_relevance=float(args.min_relevance),
            freshness_half_life_days=float(args.freshness_half_life_days),
            now_utc=now_utc,
        )
        for case in cases
    ]

    hit_rate_at_k, hit_coverage = build_metric_summary(per_case, "hit_at_k")
    precision_at_k, precision_coverage = build_metric_summary(per_case, "precision_at_k")
    recall_at_k, recall_coverage = build_metric_summary(per_case, "recall_at_k")
    mrr_at_k, mrr_coverage = build_metric_summary(per_case, "mrr_at_k")
    map_at_k, map_coverage = build_metric_summary(per_case, "map_at_k")
    ndcg_at_k, ndcg_coverage = build_metric_summary(per_case, "ndcg_at_k")

    dup_rate_at_k, dup_coverage = build_metric_summary(per_case, "dup_rate_at_k")
    diversity_at_k, diversity_coverage = build_metric_summary(per_case, "diversity_at_k")
    freshness_at_k, freshness_coverage = build_metric_summary(per_case, "freshness_at_k")
    company_role_match_at_k, company_role_coverage = build_metric_summary(per_case, "company_role_match_at_k")
    top3_company_role_hit_rate, top3_hit_coverage = build_metric_summary(per_case, "top3_company_role_hit")
    resume_alignment_at_k, resume_align_coverage = build_metric_summary(per_case, "resume_alignment_at_k")
    quality_score, quality_score_coverage = build_metric_summary(per_case, "quality_score")

    summary = {
        "evaluated_case_count": len(per_case),
        "labeled_case_count": sum(1 for item in per_case if item.get("has_labels")),
        "skipped_case_count": skipped_cases,
        "dataset": str(dataset_path),
        "cases_file": str(cases_path),
        "retriever_backend": args.retriever_backend,
        "min_relevance": float(args.min_relevance),
        "freshness_half_life_days": float(args.freshness_half_life_days),
        "metrics": {
            "hit_rate_at_k": hit_rate_at_k,
            "precision_at_k": precision_at_k,
            "recall_at_k": recall_at_k,
            "mrr_at_k": mrr_at_k,
            "map_at_k": map_at_k,
            "ndcg_at_k": ndcg_at_k,
            "dup_rate_at_k": dup_rate_at_k,
            "diversity_at_k": diversity_at_k,
            "freshness_at_k": freshness_at_k,
            "company_role_match_at_k": company_role_match_at_k,
            "top3_company_role_hit_rate": top3_company_role_hit_rate,
            "resume_alignment_at_k": resume_alignment_at_k,
            "quality_score": quality_score,
        },
        "metric_coverage": {
            "hit_rate_at_k": hit_coverage,
            "precision_at_k": precision_coverage,
            "recall_at_k": recall_coverage,
            "mrr_at_k": mrr_coverage,
            "map_at_k": map_coverage,
            "ndcg_at_k": ndcg_coverage,
            "dup_rate_at_k": dup_coverage,
            "diversity_at_k": diversity_coverage,
            "freshness_at_k": freshness_coverage,
            "company_role_match_at_k": company_role_coverage,
            "top3_company_role_hit_rate": top3_hit_coverage,
            "resume_alignment_at_k": resume_align_coverage,
            "quality_score": quality_score_coverage,
        },
    }

    def worst_case_key(item: dict[str, Any]) -> float:
        quality = item.get("quality_score")
        if isinstance(quality, (int, float)):
            return float(quality)
        ndcg = item.get("ndcg_at_k")
        if isinstance(ndcg, (int, float)):
            return float(ndcg)
        return 1.0

    worst_cases = sorted(per_case, key=worst_case_key)[: max(args.show_cases, 0)]
    report = {
        "summary": summary,
        "worst_cases_preview": worst_cases,
        "per_case": per_case,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
