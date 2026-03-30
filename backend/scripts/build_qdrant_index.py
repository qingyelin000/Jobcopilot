from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from interview.embedding_utils import (
    build_embedding_provider,
    compose_question_embedding_text,
    default_embedding_provider_name,
)
from interview.retriever_v1 import DEFAULT_DATASET_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or refresh Qdrant index from canonical retrieval questions JSONL.",
    )
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET_PATH),
        help="Input retrieval question JSONL path.",
    )
    parser.add_argument(
        "--qdrant-url",
        default=(os.getenv("QDRANT_URL", "").strip() or "http://localhost:6333"),
        help="Qdrant URL, e.g. http://localhost:6333",
    )
    parser.add_argument(
        "--qdrant-api-key",
        default=os.getenv("QDRANT_API_KEY", "").strip(),
        help="Optional Qdrant API key.",
    )
    parser.add_argument(
        "--collection",
        default=(os.getenv("QDRANT_COLLECTION", "").strip() or "nowcoder_interview_questions_v1"),
        help="Qdrant collection name.",
    )
    parser.add_argument(
        "--embedding-provider",
        default=default_embedding_provider_name(),
        choices=["hash", "openai_compatible"],
        help="Embedding provider.",
    )
    parser.add_argument(
        "--embedding-model",
        default="",
        help="Embedding model name for openai_compatible provider.",
    )
    parser.add_argument(
        "--embedding-api-key",
        default="",
        help="Embedding API key for openai_compatible provider.",
    )
    parser.add_argument(
        "--embedding-base-url",
        default="",
        help="Embedding base URL for openai_compatible provider.",
    )
    parser.add_argument(
        "--embedding-dimension",
        type=int,
        default=384,
        help="Embedding dimension for hash provider.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=128,
        help="Batch size for embedding and upsert.",
    )
    parser.add_argument(
        "--distance",
        default="cosine",
        choices=["cosine", "dot", "euclid"],
        help="Vector distance metric.",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and recreate collection before indexing.",
    )
    parser.add_argument(
        "--output-manifest",
        default="data/nowcoder/pipeline_runs_llm/qdrant/index_manifest.json",
        help="Output manifest JSON path.",
    )
    return parser


def distance_from_name(value: str) -> models.Distance:
    normalized = (value or "").strip().lower()
    if normalized == "dot":
        return models.Distance.DOT
    if normalized == "euclid":
        return models.Distance.EUCLID
    return models.Distance.COSINE


def stable_point_id(question_id: str) -> int:
    digest = hashlib.sha256(question_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def load_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    skipped = 0
    seen_question_ids: set[str] = set()

    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        question_id = str(payload.get("question_id") or "").strip()
        question_text = str(payload.get("question_text") or "").strip()
        if not question_id or not question_text:
            skipped += 1
            continue
        if question_id in seen_question_ids:
            continue
        seen_question_ids.add(question_id)
        records.append(payload)

    return records, skipped


def build_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": str(record.get("question_id") or ""),
        "source_content_id": str(record.get("source_content_id") or ""),
        "company": record.get("company"),
        "role": record.get("role"),
        "publish_time": record.get("publish_time"),
        "question_type": record.get("question_type"),
        "question_text": record.get("question_text"),
        "normalized_key": record.get("normalized_key"),
    }


def ensure_collection(
    client: QdrantClient,
    *,
    collection_name: str,
    vector_size: int,
    distance: models.Distance,
    recreate: bool,
) -> None:
    existing = {collection.name for collection in client.get_collections().collections}
    if collection_name in existing and recreate:
        client.delete_collection(collection_name=collection_name)
        existing.remove(collection_name)

    if collection_name not in existing:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=vector_size, distance=distance),
        )
        return

    details = client.get_collection(collection_name=collection_name)
    vectors_config = details.config.params.vectors
    if not isinstance(vectors_config, models.VectorParams):
        raise RuntimeError("Collection vectors config is not a single unnamed vector.")
    if int(vectors_config.size) != int(vector_size):
        raise RuntimeError(
            f"Collection vector size mismatch: existing={vectors_config.size}, requested={vector_size}. "
            "Use --recreate to rebuild collection."
        )


def upsert_batch(
    client: QdrantClient,
    *,
    collection_name: str,
    records: list[dict[str, Any]],
    vectors: list[list[float]],
) -> None:
    points: list[models.PointStruct] = []
    for record, vector in zip(records, vectors, strict=True):
        question_id = str(record["question_id"])
        points.append(
            models.PointStruct(
                id=stable_point_id(question_id),
                vector=vector,
                payload=build_payload(record),
            )
        )
    client.upsert(collection_name=collection_name, points=points, wait=True)


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    output_manifest_path = Path(args.output_manifest)
    qdrant_api_key = (args.qdrant_api_key or "").strip()

    records, skipped_records = load_records(dataset_path)
    if not records:
        raise ValueError("No valid records loaded from dataset.")

    embedding_provider = build_embedding_provider(
        provider_name=args.embedding_provider,
        embedding_dimension=max(args.embedding_dimension, 1),
        embedding_model=args.embedding_model or None,
        embedding_api_key=args.embedding_api_key or None,
        embedding_base_url=args.embedding_base_url or None,
        batch_size=max(args.batch_size, 1),
    )

    client = QdrantClient(
        url=args.qdrant_url,
        api_key=qdrant_api_key or None,
    )

    batch_size = max(args.batch_size, 1)
    sample_batch = records[:batch_size]
    sample_vectors = embedding_provider.embed_texts(
        [compose_question_embedding_text(record) for record in sample_batch]
    )
    if len(sample_vectors) != len(sample_batch):
        raise RuntimeError("Embedding output size does not match input size.")
    vector_size = len(sample_vectors[0])

    ensure_collection(
        client,
        collection_name=args.collection,
        vector_size=vector_size,
        distance=distance_from_name(args.distance),
        recreate=bool(args.recreate),
    )

    upsert_batch(
        client,
        collection_name=args.collection,
        records=sample_batch,
        vectors=sample_vectors,
    )
    indexed_count = len(sample_batch)

    for offset in range(batch_size, len(records), batch_size):
        batch_records = records[offset : offset + batch_size]
        batch_vectors = embedding_provider.embed_texts(
            [compose_question_embedding_text(record) for record in batch_records]
        )
        if len(batch_vectors) != len(batch_records):
            raise RuntimeError("Embedding output size does not match input size.")
        upsert_batch(
            client,
            collection_name=args.collection,
            records=batch_records,
            vectors=batch_vectors,
        )
        indexed_count += len(batch_records)

    manifest = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "qdrant_url": args.qdrant_url,
        "collection": args.collection,
        "recreate": bool(args.recreate),
        "distance": args.distance,
        "embedding_provider": args.embedding_provider,
        "embedding_model": args.embedding_model or None,
        "embedding_dimension": vector_size,
        "batch_size": batch_size,
        "input_record_count": len(records) + skipped_records,
        "indexed_count": indexed_count,
        "skipped_record_count": skipped_records,
    }
    output_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    output_manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
