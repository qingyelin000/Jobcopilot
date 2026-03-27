from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from interview.retriever_v1 import DEFAULT_DATASET_PATH, RetrieverV1, serialize_retrieved_question


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run mock interview retriever v1 against the cleaned question set.")
    parser.add_argument("--resume-file", help="Resume text file path.")
    parser.add_argument("--jd-file", help="JD text file path.")
    parser.add_argument("--query", default="", help="Optional extra query text.")
    parser.add_argument("--top-k", type=int, default=8, help="How many questions to return.")
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET_PATH),
        help="Retrieval question dataset path.",
    )
    return parser


def read_optional_text(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    resume_text = read_optional_text(args.resume_file)
    jd_text = read_optional_text(args.jd_file)
    retriever = RetrieverV1(dataset_path=args.dataset)
    results = retriever.search(
        resume_text=resume_text,
        jd_text=jd_text,
        top_k=args.top_k,
        extra_query=args.query or None,
    )
    print(json.dumps([serialize_retrieved_question(item) for item in results], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
