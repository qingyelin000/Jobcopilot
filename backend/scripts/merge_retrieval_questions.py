from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NORMALIZE_KEY_PATTERN = re.compile(
    r"[\s\"'`\u2018\u2019\u201c\u201d\u3001\u3002\uFF0C\uFF01\uFF1F\uFF08\uFF09\uFF1A\uFF1B,\.!\?():;\-_/\\]+"
)


@dataclass(frozen=True)
class CandidateRecord:
    record: dict[str, Any]
    origin: str
    order: int
    question_id: str
    normalized_key: str
    quality_score: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge retrieval question JSONL incrementally with de-duplication.")
    parser.add_argument(
        "--existing",
        default="data/nowcoder/pipeline_runs_llm/canonical/long_content_retrieval_questions.jsonl",
        help="Existing retrieval dataset path.",
    )
    parser.add_argument(
        "--incoming",
        required=True,
        help="Incoming retrieval dataset path to merge.",
    )
    parser.add_argument(
        "--output",
        default="data/nowcoder/pipeline_runs_llm/canonical/long_content_retrieval_questions_merged.jsonl",
        help="Merged output dataset path.",
    )
    parser.add_argument(
        "--manifest",
        default="data/nowcoder/pipeline_runs_llm/canonical/long_content_retrieval_merge_manifest.json",
        help="Merge manifest path.",
    )
    parser.add_argument(
        "--drop-field",
        action="append",
        dest="drop_fields",
        help="Field to drop from merged output records. Repeat this argument to drop multiple fields.",
    )
    parser.add_argument(
        "--keep-field",
        action="append",
        dest="keep_fields",
        help="Only keep these fields in merged output records. Repeat this argument to keep multiple fields.",
    )

    preference_group = parser.add_mutually_exclusive_group()
    preference_group.add_argument(
        "--prefer-incoming",
        action="store_true",
        dest="prefer_incoming",
        help="Prefer incoming records when quality scores are tied (default).",
    )
    preference_group.add_argument(
        "--prefer-existing",
        action="store_false",
        dest="prefer_incoming",
        help="Prefer existing records when quality scores are tied.",
    )
    parser.set_defaults(prefer_incoming=True)
    return parser


def normalize_key(text: str) -> str:
    lowered = text.casefold().strip()
    return NORMALIZE_KEY_PATTERN.sub("", lowered)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")


def score_record(record: dict[str, Any]) -> float:
    score = 0.0
    question_text = str(record.get("question_text") or "").strip()
    question_type = str(record.get("question_type") or "").strip().lower()
    company = str(record.get("company") or "").strip()
    role = str(record.get("role") or "").strip()
    section = str(record.get("section") or "").strip()

    if question_type and question_type != "general":
        score += 2.0
    if company:
        score += 1.0
    if role:
        score += 1.0
    if section:
        score += 0.5
    if len(question_text) >= 12:
        score += 1.0
    if len(question_text) >= 24:
        score += 0.5
    if "?" in question_text or "？" in question_text:
        score += 0.5
    if len(question_text) > 180:
        score -= 0.5

    return score


def to_candidate(record: dict[str, Any], origin: str, order: int) -> CandidateRecord | None:
    normalized_record = dict(record)
    question_text = str(normalized_record.get("question_text") or "").strip()
    normalized_key = str(normalized_record.get("normalized_key") or "").strip()
    if not normalized_key:
        normalized_key = normalize_key(question_text)
    if not normalized_key:
        return None

    question_id = str(normalized_record.get("question_id") or "").strip()
    if not question_id:
        source_content_id = str(normalized_record.get("source_content_id") or "unknown").strip() or "unknown"
        question_id = f"{source_content_id}#{normalized_key[:12]}"
        normalized_record["question_id"] = question_id

    normalized_record["normalized_key"] = normalized_key
    return CandidateRecord(
        record=normalized_record,
        origin=origin,
        order=order,
        question_id=question_id,
        normalized_key=normalized_key,
        quality_score=score_record(normalized_record),
    )


def choose_better(left: CandidateRecord, right: CandidateRecord, prefer_incoming: bool) -> CandidateRecord:
    if right.quality_score > left.quality_score:
        return right
    if right.quality_score < left.quality_score:
        return left

    if left.origin != right.origin:
        if prefer_incoming and right.origin == "incoming":
            return right
        if not prefer_incoming and right.origin == "existing":
            return right
        return left

    if right.order < left.order:
        return right
    return left


def dedupe_candidates(
    candidates: list[CandidateRecord],
    *,
    key_name: str,
    prefer_incoming: bool,
) -> tuple[list[CandidateRecord], int]:
    chosen: dict[str, CandidateRecord] = {}
    for candidate in candidates:
        key = candidate.question_id if key_name == "question_id" else candidate.normalized_key
        existing = chosen.get(key)
        if existing is None:
            chosen[key] = candidate
            continue
        chosen[key] = choose_better(existing, candidate, prefer_incoming=prefer_incoming)

    deduped = list(chosen.values())
    dropped_count = len(candidates) - len(deduped)
    return deduped, dropped_count


def project_record(
    record: dict[str, Any],
    *,
    keep_fields: list[str] | None,
    drop_fields: set[str],
) -> dict[str, Any]:
    if keep_fields:
        projected = {key: record.get(key) for key in keep_fields if key in record}
    else:
        projected = dict(record)

    for field in drop_fields:
        projected.pop(field, None)
    return projected


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    existing_path = Path(args.existing)
    incoming_path = Path(args.incoming)
    output_path = Path(args.output)
    manifest_path = Path(args.manifest)

    existing_records = read_jsonl(existing_path)
    incoming_records = read_jsonl(incoming_path)
    keep_fields = [item.strip() for item in (args.keep_fields or []) if item and item.strip()]
    drop_fields = {item.strip() for item in (args.drop_fields or []) if item and item.strip()}

    candidates: list[CandidateRecord] = []
    skipped_records = 0

    for index, record in enumerate(existing_records):
        candidate = to_candidate(record, origin="existing", order=index)
        if candidate is None:
            skipped_records += 1
            continue
        candidates.append(candidate)

    base_order = len(existing_records)
    for index, record in enumerate(incoming_records):
        candidate = to_candidate(record, origin="incoming", order=base_order + index)
        if candidate is None:
            skipped_records += 1
            continue
        candidates.append(candidate)

    deduped_by_id, dropped_by_question_id = dedupe_candidates(
        candidates,
        key_name="question_id",
        prefer_incoming=args.prefer_incoming,
    )
    deduped_final, dropped_by_normalized_key = dedupe_candidates(
        deduped_by_id,
        key_name="normalized_key",
        prefer_incoming=args.prefer_incoming,
    )

    deduped_final.sort(key=lambda item: item.order)
    merged_records = [
        project_record(item.record, keep_fields=keep_fields if keep_fields else None, drop_fields=drop_fields)
        for item in deduped_final
    ]
    write_jsonl(output_path, merged_records)

    existing_ids = {item.question_id for item in candidates if item.origin == "existing"}
    existing_keys = {item.normalized_key for item in candidates if item.origin == "existing"}
    incoming_kept = [item for item in deduped_final if item.origin == "incoming"]
    added_count = sum(
        1
        for item in incoming_kept
        if item.question_id not in existing_ids and item.normalized_key not in existing_keys
    )
    updated_count = len(incoming_kept) - added_count

    manifest = {
        "existing_input": str(existing_path),
        "incoming_input": str(incoming_path),
        "output": str(output_path),
        "prefer_incoming": bool(args.prefer_incoming),
        "keep_fields": keep_fields,
        "drop_fields": sorted(drop_fields),
        "existing_count": len(existing_records),
        "incoming_count": len(incoming_records),
        "candidate_count": len(candidates),
        "skipped_records": skipped_records,
        "dropped_by_question_id": dropped_by_question_id,
        "dropped_by_normalized_key": dropped_by_normalized_key,
        "kept_existing_count": sum(1 for item in deduped_final if item.origin == "existing"),
        "kept_incoming_count": len(incoming_kept),
        "added_count": added_count,
        "updated_count": updated_count,
        "merged_count": len(merged_records),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
