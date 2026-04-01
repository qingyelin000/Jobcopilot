from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from interview.embedding_utils import default_embedding_provider_name
from interview.retriever_v2 import (
    DEFAULT_DATASET_PATH,
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_QDRANT_URL,
    RetrieverV2,
    serialize_retrieved_question,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run RetrieverV2 (Qdrant + lexical fusion).")
    parser.add_argument("--resume-file", help="Resume text file path.")
    parser.add_argument("--jd-file", help="JD text file path.")
    parser.add_argument("--query", default="", help="Optional extra query text.")
    parser.add_argument("--top-k", type=int, default=8, help="How many questions to return.")
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET_PATH),
        help="Fallback lexical dataset path.",
    )
    parser.add_argument("--target-company", default="", help="Optional target company for rerank/filter.")
    parser.add_argument("--target-role", default="", help="Optional target role for rerank/filter.")
    parser.add_argument(
        "--strict-metadata-filter",
        action="store_true",
        help="Enable strict metadata filter in vector recall (company/role exact match).",
    )
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL, help="Qdrant URL.")
    parser.add_argument("--qdrant-api-key", default="", help="Optional Qdrant API key.")
    parser.add_argument(
        "--collection",
        default=DEFAULT_QDRANT_COLLECTION,
        help="Qdrant collection name.",
    )
    parser.add_argument(
        "--embedding-provider",
        default=default_embedding_provider_name(),
        choices=["hash", "openai_compatible"],
        help="Embedding provider for query vectors.",
    )
    parser.add_argument(
        "--embedding-model",
        default="",
        help="Embedding model for openai_compatible provider.",
    )
    parser.add_argument("--embedding-api-key", default="", help="Embedding API key override.")
    parser.add_argument("--embedding-base-url", default="", help="Embedding base URL override.")
    parser.add_argument(
        "--embedding-dimension",
        type=int,
        default=384,
        help="Embedding dimension for hash provider.",
    )
    parser.add_argument(
        "--vector-candidate-pool",
        type=int,
        default=64,
        help="Vector recall candidate pool size.",
    )
    parser.add_argument(
        "--lexical-candidate-pool",
        type=int,
        default=40,
        help="Lexical recall candidate pool size.",
    )
    parser.add_argument(
        "--disable-rerank",
        action="store_true",
        help="Disable rerank stage and keep first-stage retrieval scoring only.",
    )
    parser.add_argument("--rerank-model", default="", help="Optional rerank model override.")
    parser.add_argument("--rerank-api-key", default="", help="Optional rerank API key override.")
    parser.add_argument("--rerank-base-url", default="", help="Optional rerank API base URL override.")
    parser.add_argument(
        "--rerank-timeout-seconds",
        type=float,
        default=15.0,
        help="Rerank API timeout in seconds.",
    )
    parser.add_argument(
        "--rerank-candidate-pool",
        type=int,
        default=40,
        help="Candidate pool size sent to rerank API.",
    )
    return parser


def read_optional_text(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    resume_text = read_optional_text(args.resume_file)
    jd_text = read_optional_text(args.jd_file)

    retriever = RetrieverV2(
        dataset_path=args.dataset,
        qdrant_url=args.qdrant_url,
        qdrant_api_key=args.qdrant_api_key or None,
        collection_name=args.collection,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model or None,
        embedding_api_key=args.embedding_api_key or None,
        embedding_base_url=args.embedding_base_url or None,
        embedding_dimension=max(args.embedding_dimension, 1),
        vector_candidate_pool=max(args.vector_candidate_pool, args.top_k),
        lexical_candidate_pool=max(args.lexical_candidate_pool, args.top_k),
        strict_metadata_filter=bool(args.strict_metadata_filter),
        rerank_enabled=not bool(args.disable_rerank),
        rerank_model=args.rerank_model or None,
        rerank_api_key=args.rerank_api_key or None,
        rerank_base_url=args.rerank_base_url or None,
        rerank_timeout_seconds=max(float(args.rerank_timeout_seconds), 1.0),
        rerank_candidate_pool=max(int(args.rerank_candidate_pool), args.top_k),
    )
    results = retriever.search(
        resume_text=resume_text,
        jd_text=jd_text,
        top_k=args.top_k,
        extra_query=args.query or None,
        target_company=args.target_company or None,
        target_role=args.target_role or None,
        strict_metadata_filter=bool(args.strict_metadata_filter),
    )
    print(json.dumps([serialize_retrieved_question(item) for item in results], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
