from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - runtime guard
    load_dotenv = None


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "backend" / "scripts"

CRAWL_SCRIPT = SCRIPTS_DIR / "crawl_nowcoder_interviews.py"
PRECLEAN_SCRIPT = SCRIPTS_DIR / "preclean_nowcoder_for_llm.py"
LLM_EXTRACT_SCRIPT = SCRIPTS_DIR / "llm_extract_nowcoder_questions.py"
MERGE_SCRIPT = SCRIPTS_DIR / "merge_retrieval_questions.py"
QUALITY_GATE_SCRIPT = SCRIPTS_DIR / "quality_gate_questions.py"

DEFAULT_QUALITY_CONFIG = REPO_ROOT / "backend" / "config" / "question_quality_gate.json"

DEFAULT_RUNS_DIR = REPO_ROOT / "data" / "nowcoder" / "pipeline_runs_llm"
DEFAULT_CANONICAL_DIR = DEFAULT_RUNS_DIR / "canonical"
DEFAULT_EXISTING_RETRIEVAL = DEFAULT_CANONICAL_DIR / "long_content_retrieval_questions.jsonl"
DEFAULT_CANONICAL_RETRIEVAL = DEFAULT_CANONICAL_DIR / "long_content_retrieval_questions.jsonl"
DEFAULT_CANONICAL_MANIFEST = DEFAULT_CANONICAL_DIR / "long_content_retrieval_manifest.json"
DEFAULT_ARCHIVE_DIR = DEFAULT_CANONICAL_DIR / "archive"


@dataclass
class StepResult:
    name: str
    command: list[str]
    return_code: int
    started_at: str
    finished_at: str
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.return_code == 0


def iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def count_jsonl_records(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Nowcoder LLM pipeline end-to-end: "
            "crawl -> preclean -> llm_extract -> merge -> quality_gate -> promote."
        )
    )
    parser.add_argument(
        "--run-id",
        default=datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S"),
        help="Pipeline run identifier used in output directory naming.",
    )
    parser.add_argument(
        "--runs-dir",
        default=str(DEFAULT_RUNS_DIR),
        help="Root directory to store all LLM pipeline run artifacts.",
    )
    parser.add_argument(
        "--existing-retrieval",
        default=str(DEFAULT_EXISTING_RETRIEVAL),
        help="Existing retrieval JSONL used in merge step.",
    )
    parser.add_argument(
        "--quality-config",
        default=str(DEFAULT_QUALITY_CONFIG),
        help="Quality gate config JSON path.",
    )
    parser.add_argument(
        "--canonical-retrieval",
        default=str(DEFAULT_CANONICAL_RETRIEVAL),
        help="Canonical retrieval JSONL path to update after successful quality gate.",
    )
    parser.add_argument(
        "--canonical-manifest",
        default=str(DEFAULT_CANONICAL_MANIFEST),
        help="Canonical manifest JSON path to update after successful quality gate.",
    )
    parser.add_argument(
        "--archive-dir",
        default=str(DEFAULT_ARCHIVE_DIR),
        help="Archive directory for backing up previous canonical dataset.",
    )
    parser.add_argument(
        "--prefer-existing",
        action="store_true",
        help="In merge tie-breaks, prefer existing dataset records over incoming records.",
    )
    parser.add_argument(
        "--llm-only",
        action="store_true",
        help="Ignore existing canonical retrieval during merge (LLM output only).",
    )
    parser.add_argument(
        "--merge-drop-field",
        action="append",
        dest="merge_drop_fields",
        default=["source_url"],
        help="Field to drop from merged output. Repeat this argument for multiple fields.",
    )
    parser.add_argument(
        "--merge-keep-field",
        action="append",
        dest="merge_keep_fields",
        help="Field to keep in merged output. Repeat this argument for multiple fields.",
    )
    parser.add_argument(
        "--no-promote",
        action="store_true",
        help="Do not replace canonical dataset even if quality gate passes.",
    )
    parser.add_argument(
        "--manual-promote-on-fail",
        action="store_true",
        help="When quality gate fails, ask for manual confirmation to promote merged dataset.",
    )

    parser.add_argument(
        "--crawl-query",
        action="append",
        dest="crawl_queries",
        help="Crawl search query. Repeat this argument to set multiple queries.",
    )
    parser.add_argument(
        "--crawl-page-limit",
        type=int,
        default=12,
        help="How many search pages to crawl per query.",
    )
    parser.add_argument(
        "--crawl-page-size",
        type=int,
        default=20,
        help="Search page size for crawl step.",
    )
    parser.add_argument(
        "--crawl-max-items",
        type=int,
        default=1200,
        help="Stop after N unique crawled items. 0 means no limit.",
    )
    parser.add_argument(
        "--crawl-min-records",
        type=int,
        default=1000,
        help="Fail fast when crawled records are below this threshold. Set 0 to disable.",
    )
    parser.add_argument(
        "--crawl-sleep-seconds",
        type=float,
        default=0.3,
        help="Delay between detail requests in crawl step.",
    )
    parser.add_argument(
        "--crawl-force",
        action="store_true",
        help="Force refetch of raw detail files in crawl step.",
    )

    parser.add_argument(
        "--preclean-min-text-length",
        type=int,
        default=80,
        help="Drop post when interview_text non-whitespace char count is below this value.",
    )
    parser.add_argument(
        "--preclean-detail-kind",
        action="append",
        dest="preclean_detail_kinds",
        default=["long_content", "moment"],
        help="detail_kind values to keep in pre-clean step.",
    )

    parser.add_argument(
        "--llm-model",
        default="mimo-v2-flash",
        help="LLM model used in extraction step.",
    )
    parser.add_argument(
        "--llm-base-url",
        default=os.getenv("MIMO_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com",
        help="OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--llm-api-key-env",
        default="MIMO_V2_PRO_API_KEY",
        help="Environment variable name that stores API key.",
    )
    parser.add_argument(
        "--llm-timeout-seconds",
        type=float,
        default=90.0,
        help="Timeout for each LLM API call.",
    )
    parser.add_argument(
        "--llm-max-retries",
        type=int,
        default=2,
        help="Max retries after first attempt in LLM extraction.",
    )
    parser.add_argument(
        "--llm-sleep-seconds",
        type=float,
        default=0.2,
        help="Sleep between successful LLM requests.",
    )
    parser.add_argument(
        "--llm-max-posts",
        type=int,
        default=0,
        help="Process at most N posts in LLM extraction. 0 means all.",
    )
    parser.add_argument(
        "--llm-temperature",
        type=float,
        default=0.1,
        help="Sampling temperature for LLM extraction.",
    )
    return parser


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def run_step(step_name: str, command: list[str], cwd: Path) -> StepResult:
    started_at = iso_now()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    finished_at = iso_now()
    return StepResult(
        name=step_name,
        command=command,
        return_code=completed.returncode,
        started_at=started_at,
        finished_at=finished_at,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def relative_to_repo(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def prompt_manual_promote(*, run_id: str, merged_output: Path, quality_report: Path) -> bool:
    print(
        f"[manual-publish] run_id={run_id}: quality gate failed.\n"
        f"merged_output={merged_output}\n"
        f"quality_report={quality_report}"
    )
    while True:
        try:
            answer = input("Promote merged dataset anyway? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nManual promotion cancelled.")
            return False

        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        print("Please answer 'y' or 'n'.")


def fail_with_manifest(
    *,
    run_id: str,
    run_dir: Path,
    pipeline_manifest: Path,
    steps: list[StepResult],
    failed_step: str,
    message: str | None = None,
    extra_artifacts: dict[str, str] | None = None,
) -> int:
    artifacts = {
        "run_dir": str(run_dir),
        "pipeline_manifest": str(pipeline_manifest),
    }
    if extra_artifacts:
        artifacts.update(extra_artifacts)
    payload = {
        "run_id": run_id,
        "status": "failed",
        "failed_step": failed_step,
        "message": message,
        "artifacts": artifacts,
        "steps": [step.__dict__ for step in steps],
    }
    dump_json(pipeline_manifest, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 1


def promote_dataset(
    *,
    merged_output: Path,
    canonical_retrieval: Path,
    canonical_manifest: Path,
    archive_dir: Path,
    run_id: str,
    merge_manifest_path: Path,
    quality_report_path: Path,
) -> dict[str, Any]:
    archive_info: dict[str, Any] = {
        "archived": False,
        "archive_path": None,
    }
    archive_dir.mkdir(parents=True, exist_ok=True)

    if canonical_retrieval.exists():
        archived_path = archive_dir / f"{canonical_retrieval.stem}__{run_id}{canonical_retrieval.suffix}"
        shutil.copy2(canonical_retrieval, archived_path)
        archive_info = {
            "archived": True,
            "archive_path": str(archived_path),
        }

    ensure_parent(canonical_retrieval)
    shutil.copy2(merged_output, canonical_retrieval)

    merge_manifest = load_json(merge_manifest_path) or {}
    quality_report = load_json(quality_report_path) or {}

    canonical_payload = {
        "updated_at": iso_now(),
        "run_id": run_id,
        "dataset": str(canonical_retrieval),
        "source_merged_output": str(merged_output),
        "merge_manifest": str(merge_manifest_path),
        "quality_report": str(quality_report_path),
        "merge_summary": merge_manifest if isinstance(merge_manifest, dict) else {},
        "quality_summary": quality_report if isinstance(quality_report, dict) else {},
        "archive": archive_info,
    }
    dump_json(canonical_manifest, canonical_payload)
    return canonical_payload


def main() -> int:
    if load_dotenv is not None:
        load_dotenv(REPO_ROOT / ".env", override=False)

    parser = build_parser()
    args = parser.parse_args()

    run_id = args.run_id
    runs_dir = Path(args.runs_dir)
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    crawl_output_dir = run_dir / "crawl"
    crawl_normalized_path = crawl_output_dir / "normalized" / "interview_posts.jsonl"
    preclean_output_dir = run_dir / "preclean"
    preclean_posts_path = preclean_output_dir / "interview_posts_for_llm.jsonl"
    llm_output_dir = run_dir / "llm"
    structured_posts_path = llm_output_dir / "structured_posts.jsonl"
    incoming_retrieval = llm_output_dir / "retrieval_questions.jsonl"
    error_posts_path = llm_output_dir / "error_posts.jsonl"
    merged_output = run_dir / "long_content_retrieval_questions_merged.jsonl"
    merge_manifest = run_dir / "long_content_retrieval_merge_manifest.json"
    quality_report = run_dir / "question_quality_gate_report.json"
    pipeline_manifest = run_dir / "pipeline_manifest.json"

    existing_retrieval = Path(args.existing_retrieval)
    if args.llm_only:
        existing_retrieval = run_dir / "__empty_existing_retrieval__.jsonl"
    quality_config = Path(args.quality_config)
    canonical_retrieval = Path(args.canonical_retrieval)
    canonical_manifest = Path(args.canonical_manifest)
    archive_dir = Path(args.archive_dir)

    if not quality_config.exists():
        raise FileNotFoundError(f"Missing quality config: {quality_config}")

    steps: list[StepResult] = []

    crawl_cmd = [
        sys.executable,
        str(CRAWL_SCRIPT),
        "--output-dir",
        str(crawl_output_dir),
        "--page-limit",
        str(args.crawl_page_limit),
        "--page-size",
        str(args.crawl_page_size),
        "--max-items",
        str(args.crawl_max_items),
        "--sleep-seconds",
        str(args.crawl_sleep_seconds),
    ]
    if args.crawl_force:
        crawl_cmd.append("--force")
    for query in args.crawl_queries or []:
        crawl_cmd.extend(["--query", query])

    steps.append(run_step("crawl", crawl_cmd, cwd=REPO_ROOT))
    if not steps[-1].ok:
        return fail_with_manifest(
            run_id=run_id,
            run_dir=run_dir,
            pipeline_manifest=pipeline_manifest,
            steps=steps,
            failed_step="crawl",
            extra_artifacts={"crawl_output_dir": str(crawl_output_dir)},
        )
    if not crawl_normalized_path.exists():
        return fail_with_manifest(
            run_id=run_id,
            run_dir=run_dir,
            pipeline_manifest=pipeline_manifest,
            steps=steps,
            failed_step="crawl_output_check",
            message=f"Expected crawl output not found: {crawl_normalized_path}",
            extra_artifacts={"crawl_output_dir": str(crawl_output_dir)},
        )

    crawled_count = count_jsonl_records(crawl_normalized_path)
    if args.crawl_min_records > 0 and crawled_count < args.crawl_min_records:
        return fail_with_manifest(
            run_id=run_id,
            run_dir=run_dir,
            pipeline_manifest=pipeline_manifest,
            steps=steps,
            failed_step="crawl_count_check",
            message=f"Crawled records {crawled_count} < required minimum {args.crawl_min_records}",
            extra_artifacts={
                "crawl_normalized_path": str(crawl_normalized_path),
                "crawled_count": str(crawled_count),
            },
        )

    preclean_cmd = [
        sys.executable,
        str(PRECLEAN_SCRIPT),
        "--input",
        str(crawl_normalized_path),
        "--output-dir",
        str(preclean_output_dir),
        "--min-text-length",
        str(args.preclean_min_text_length),
    ]
    for detail_kind in args.preclean_detail_kinds or []:
        preclean_cmd.extend(["--detail-kind", detail_kind])

    steps.append(run_step("preclean", preclean_cmd, cwd=REPO_ROOT))
    if not steps[-1].ok:
        return fail_with_manifest(
            run_id=run_id,
            run_dir=run_dir,
            pipeline_manifest=pipeline_manifest,
            steps=steps,
            failed_step="preclean",
            extra_artifacts={"preclean_output_dir": str(preclean_output_dir)},
        )
    if not preclean_posts_path.exists():
        return fail_with_manifest(
            run_id=run_id,
            run_dir=run_dir,
            pipeline_manifest=pipeline_manifest,
            steps=steps,
            failed_step="preclean_output_check",
            message=f"Expected preclean output not found: {preclean_posts_path}",
            extra_artifacts={"preclean_output_dir": str(preclean_output_dir)},
        )

    llm_cmd = [
        sys.executable,
        str(LLM_EXTRACT_SCRIPT),
        "--input",
        str(preclean_posts_path),
        "--output-dir",
        str(llm_output_dir),
        "--model",
        str(args.llm_model),
        "--base-url",
        str(args.llm_base_url),
        "--api-key-env",
        str(args.llm_api_key_env),
        "--timeout-seconds",
        str(args.llm_timeout_seconds),
        "--max-retries",
        str(args.llm_max_retries),
        "--sleep-seconds",
        str(args.llm_sleep_seconds),
        "--max-posts",
        str(args.llm_max_posts),
        "--temperature",
        str(args.llm_temperature),
    ]
    steps.append(run_step("llm_extract", llm_cmd, cwd=REPO_ROOT))
    if not steps[-1].ok:
        return fail_with_manifest(
            run_id=run_id,
            run_dir=run_dir,
            pipeline_manifest=pipeline_manifest,
            steps=steps,
            failed_step="llm_extract",
            extra_artifacts={"llm_output_dir": str(llm_output_dir)},
        )
    if not incoming_retrieval.exists():
        return fail_with_manifest(
            run_id=run_id,
            run_dir=run_dir,
            pipeline_manifest=pipeline_manifest,
            steps=steps,
            failed_step="llm_extract_output_check",
            message=f"Expected llm output not found: {incoming_retrieval}",
            extra_artifacts={"llm_output_dir": str(llm_output_dir)},
        )

    merge_cmd = [
        sys.executable,
        str(MERGE_SCRIPT),
        "--existing",
        str(existing_retrieval),
        "--incoming",
        str(incoming_retrieval),
        "--output",
        str(merged_output),
        "--manifest",
        str(merge_manifest),
    ]
    if args.prefer_existing:
        merge_cmd.append("--prefer-existing")
    for field in args.merge_drop_fields or []:
        merge_cmd.extend(["--drop-field", str(field)])
    for field in args.merge_keep_fields or []:
        merge_cmd.extend(["--keep-field", str(field)])

    steps.append(run_step("merge", merge_cmd, cwd=REPO_ROOT))
    if not steps[-1].ok:
        return fail_with_manifest(
            run_id=run_id,
            run_dir=run_dir,
            pipeline_manifest=pipeline_manifest,
            steps=steps,
            failed_step="merge",
            extra_artifacts={"merged_output": str(merged_output)},
        )

    quality_cmd = [
        sys.executable,
        str(QUALITY_GATE_SCRIPT),
        "--input",
        str(merged_output),
        "--config",
        str(quality_config),
        "--report",
        str(quality_report),
    ]
    steps.append(run_step("quality_gate", quality_cmd, cwd=REPO_ROOT))

    passed = steps[-1].ok
    promoted = False
    promotion_payload: dict[str, Any] | None = None
    manual_promote_decision: bool | None = None

    if not passed and args.manual_promote_on_fail and not args.no_promote:
        manual_promote_decision = prompt_manual_promote(
            run_id=run_id,
            merged_output=merged_output,
            quality_report=quality_report,
        )

    should_promote = (passed and not args.no_promote) or bool(manual_promote_decision)

    if should_promote:
        promotion_payload = promote_dataset(
            merged_output=merged_output,
            canonical_retrieval=canonical_retrieval,
            canonical_manifest=canonical_manifest,
            archive_dir=archive_dir,
            run_id=run_id,
            merge_manifest_path=merge_manifest,
            quality_report_path=quality_report,
        )
        promoted = True

    if passed:
        status = "passed"
    elif promoted:
        status = "failed_quality_promoted_manually"
    else:
        status = "failed"

    summary = {
        "run_id": run_id,
        "status": status,
        "quality_gate_passed": passed,
        "promoted": promoted,
        "manual_promote_on_fail": bool(args.manual_promote_on_fail),
        "manual_promote_decision": manual_promote_decision,
        "llm_only": bool(args.llm_only),
        "counts": {
            "crawled_record_count": crawled_count,
            "preclean_post_count": count_jsonl_records(preclean_posts_path),
            "structured_post_count": count_jsonl_records(structured_posts_path),
            "incoming_retrieval_count": count_jsonl_records(incoming_retrieval),
            "merged_retrieval_count": count_jsonl_records(merged_output),
            "llm_error_post_count": count_jsonl_records(error_posts_path),
        },
        "paths": {
            "run_dir": relative_to_repo(run_dir),
            "crawl_output_dir": relative_to_repo(crawl_output_dir),
            "crawl_normalized_path": relative_to_repo(crawl_normalized_path),
            "preclean_output_dir": relative_to_repo(preclean_output_dir),
            "preclean_posts_path": relative_to_repo(preclean_posts_path),
            "llm_output_dir": relative_to_repo(llm_output_dir),
            "structured_posts_path": relative_to_repo(structured_posts_path),
            "incoming_retrieval": relative_to_repo(incoming_retrieval),
            "error_posts_path": relative_to_repo(error_posts_path),
            "merged_output": relative_to_repo(merged_output),
            "merge_manifest": relative_to_repo(merge_manifest),
            "quality_report": relative_to_repo(quality_report),
            "pipeline_manifest": relative_to_repo(pipeline_manifest),
            "canonical_retrieval": relative_to_repo(canonical_retrieval),
            "canonical_manifest": relative_to_repo(canonical_manifest),
            "archive_dir": relative_to_repo(archive_dir),
        },
        "steps": [step.__dict__ for step in steps],
        "promotion": promotion_payload,
    }
    dump_json(pipeline_manifest, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if (passed or promoted) else 1


if __name__ == "__main__":
    raise SystemExit(main())
