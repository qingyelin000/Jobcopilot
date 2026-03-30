from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "min_total_count": 50,
    "max_duplicate_rate": 0.12,
    "max_missing_normalized_key_rate": 0.02,
    "max_general_ratio": 0.65,
    "question_type_min_ratio": {
        "coding": 0.08,
        "backend_foundation": 0.08,
        "project_or_system_design": 0.05,
        "behavioral": 0.03,
    },
}

NORMALIZE_KEY_PATTERN = re.compile(
    r"[\s\"'`\u2018\u2019\u201c\u201d\u3001\u3002\uFF0C\uFF01\uFF1F\uFF08\uFF09\uFF1A\uFF1B,\.!\?():;\-_/\\]+"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run quality gates for retrieval question dataset.")
    parser.add_argument(
        "--input",
        default="data/nowcoder/pipeline_runs_llm/canonical/long_content_retrieval_questions.jsonl",
        help="Input retrieval question JSONL path.",
    )
    parser.add_argument(
        "--config",
        default="backend/config/question_quality_gate.json",
        help="Quality gate JSON config path.",
    )
    parser.add_argument(
        "--report",
        default="data/nowcoder/pipeline_runs_llm/canonical/question_quality_gate_report.json",
        help="Output report path.",
    )
    return parser


def normalize_key(text: str) -> str:
    lowered = text.casefold().strip()
    return NORMALIZE_KEY_PATTERN.sub("", lowered)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def merge_config(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
            continue
        merged[key] = value
    return merged


def load_config(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return dict(DEFAULT_CONFIG), True

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Config must be a JSON object: {path}")
    return merge_config(DEFAULT_CONFIG, payload), False


def round_ratio(value: float) -> float:
    return round(value, 6)


def as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_check(name: str, passed: bool, actual: Any, expected: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "actual": actual,
        "expected": expected,
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input)
    config_path = Path(args.config)
    report_path = Path(args.report)

    config, used_default_config = load_config(config_path)
    records = read_jsonl(input_path)

    total_count = len(records)
    type_counter: Counter[str] = Counter()
    normalized_keys: list[str] = []
    missing_key_count = 0

    for record in records:
        question_type = str(record.get("question_type") or "unknown").strip() or "unknown"
        type_counter[question_type] += 1

        normalized_key = str(record.get("normalized_key") or "").strip()
        if not normalized_key:
            normalized_key = normalize_key(str(record.get("question_text") or ""))
        if not normalized_key:
            missing_key_count += 1
            continue
        normalized_keys.append(normalized_key)

    unique_normalized_key_count = len(set(normalized_keys))
    duplicate_count = max(len(normalized_keys) - unique_normalized_key_count, 0)
    duplicate_rate = duplicate_count / total_count if total_count else 1.0
    missing_key_rate = missing_key_count / total_count if total_count else 1.0

    type_ratios = {
        question_type: round_ratio(count / total_count) if total_count else 0.0
        for question_type, count in sorted(type_counter.items())
    }
    general_ratio = type_counter.get("general", 0) / total_count if total_count else 0.0

    min_total_count = as_int(config.get("min_total_count"), DEFAULT_CONFIG["min_total_count"])
    max_duplicate_rate = as_float(config.get("max_duplicate_rate"), DEFAULT_CONFIG["max_duplicate_rate"])
    max_missing_key_rate = as_float(
        config.get("max_missing_normalized_key_rate"),
        DEFAULT_CONFIG["max_missing_normalized_key_rate"],
    )
    max_general_ratio = as_float(config.get("max_general_ratio"), DEFAULT_CONFIG["max_general_ratio"])
    type_min_ratio = config.get("question_type_min_ratio") or {}
    if not isinstance(type_min_ratio, dict):
        type_min_ratio = {}

    checks: list[dict[str, Any]] = []
    checks.append(
        build_check(
            "min_total_count",
            total_count >= min_total_count,
            total_count,
            f">= {min_total_count}",
        )
    )
    checks.append(
        build_check(
            "max_duplicate_rate",
            duplicate_rate <= max_duplicate_rate,
            round_ratio(duplicate_rate),
            f"<= {max_duplicate_rate}",
        )
    )
    checks.append(
        build_check(
            "max_missing_normalized_key_rate",
            missing_key_rate <= max_missing_key_rate,
            round_ratio(missing_key_rate),
            f"<= {max_missing_key_rate}",
        )
    )
    checks.append(
        build_check(
            "max_general_ratio",
            general_ratio <= max_general_ratio,
            round_ratio(general_ratio),
            f"<= {max_general_ratio}",
        )
    )

    for question_type, min_ratio in sorted(type_min_ratio.items()):
        threshold = as_float(min_ratio, 0.0)
        actual_ratio = type_counter.get(question_type, 0) / total_count if total_count else 0.0
        checks.append(
            build_check(
                f"type_ratio.{question_type}",
                actual_ratio >= threshold,
                round_ratio(actual_ratio),
                f">= {threshold}",
            )
        )

    passed = all(check["passed"] for check in checks)
    report = {
        "passed": passed,
        "input": str(input_path),
        "config": str(config_path),
        "used_default_config": used_default_config,
        "thresholds": {
            "min_total_count": min_total_count,
            "max_duplicate_rate": max_duplicate_rate,
            "max_missing_normalized_key_rate": max_missing_key_rate,
            "max_general_ratio": max_general_ratio,
            "question_type_min_ratio": type_min_ratio,
        },
        "metrics": {
            "total_count": total_count,
            "unique_normalized_key_count": unique_normalized_key_count,
            "duplicate_count": duplicate_count,
            "duplicate_rate": round_ratio(duplicate_rate),
            "missing_normalized_key_count": missing_key_count,
            "missing_normalized_key_rate": round_ratio(missing_key_rate),
            "type_counts": dict(sorted(type_counter.items())),
            "type_ratios": type_ratios,
            "general_ratio": round_ratio(general_ratio),
        },
        "checks": checks,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
