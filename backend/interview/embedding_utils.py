from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol

from openai import OpenAI


ASCII_WORD_PATTERN = re.compile(r"[A-Za-z0-9+#.\-]{2,}")
CHINESE_SEGMENT_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}")


class EmbeddingProvider(Protocol):
    @property
    def dimension(self) -> int: ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


def normalize_text(value: str | None) -> str:
    return str(value or "").strip()


def tokenize_for_embedding(text: str) -> list[str]:
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


def compose_question_embedding_text(payload: dict) -> str:
    parts = [
        str(payload.get("company") or "").strip(),
        str(payload.get("role") or "").strip(),
        str(payload.get("question_type") or "").strip(),
        str(payload.get("question_text") or "").strip(),
    ]
    return " | ".join(part for part in parts if part)


def compose_query_embedding_text(
    *,
    resume_text: str,
    jd_text: str,
    extra_query: str | None = None,
) -> str:
    parts = [
        normalize_text(extra_query),
        normalize_text(jd_text),
        normalize_text(resume_text),
    ]
    return "\n".join(part for part in parts if part)


class HashEmbeddingProvider:
    def __init__(self, *, dimension: int = 384) -> None:
        if dimension <= 0:
            raise ValueError("Hash embedding dimension must be positive.")
        self._dimension = int(dimension)

    @property
    def dimension(self) -> int:
        return self._dimension

    def _embed_one(self, text: str) -> list[float]:
        tokens = tokenize_for_embedding(text)
        if not tokens:
            tokens = [normalize_text(text) or "__empty__"]

        vector = [0.0] * self._dimension
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], byteorder="big", signed=False) % self._dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            vector[0] = 1.0
            norm = 1.0
        return [value / norm for value in vector]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(normalize_text(text)) for text in texts]


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        batch_size: int = 64,
        dimension_hint: int | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("Embedding model name is required.")
        if not api_key.strip():
            raise ValueError("Embedding API key is required for openai_compatible provider.")
        self.model = model.strip()
        self._client = OpenAI(api_key=api_key.strip(), base_url=(base_url or "").strip() or None)
        self._batch_size = max(int(batch_size), 1)
        self._dimension = int(dimension_hint) if dimension_hint and int(dimension_hint) > 0 else None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError("Embedding dimension is unknown before the first embed call.")
        return int(self._dimension)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        outputs: list[list[float]] = []
        if not texts:
            return outputs

        for offset in range(0, len(texts), self._batch_size):
            batch = [normalize_text(item) for item in texts[offset : offset + self._batch_size]]
            response = self._client.embeddings.create(model=self.model, input=batch)
            for entry in sorted(response.data, key=lambda item: item.index):
                vector = [float(value) for value in entry.embedding]
                if self._dimension is None:
                    self._dimension = len(vector)
                elif len(vector) != self._dimension:
                    raise ValueError(
                        f"Inconsistent embedding dimensions: expected {self._dimension}, got {len(vector)}."
                    )
                outputs.append(vector)
        return outputs


def default_embedding_provider_name() -> str:
    return (os.getenv("EMBEDDING_PROVIDER", "openai_compatible").strip() or "openai_compatible").lower()


def supports_hybrid_embedding(provider: EmbeddingProvider) -> bool:
    """保留兼容接口：当前在线 provider 不提供 hybrid 输出。"""
    return callable(getattr(provider, "embed_hybrid_texts", None))


def build_embedding_provider(
    *,
    provider_name: str,
    embedding_dimension: int = 384,
    embedding_model: str | None = None,
    embedding_api_key: str | None = None,
    embedding_base_url: str | None = None,
    batch_size: int = 64,
) -> EmbeddingProvider:
    normalized = (provider_name or "").strip().lower()
    if normalized in {"hash", "local_hash"}:
        return HashEmbeddingProvider(dimension=max(int(embedding_dimension), 1))

    if normalized in {"local_bge", "bge_m3", "sentence_transformers", "local_st"}:
        raise ValueError(
            "local_bge/local_st providers are removed. Use openai_compatible or hash."
        )

    if normalized in {"openai", "openai_compatible", "openai-compatible"}:
        api_key = (
            (embedding_api_key or "").strip()
            or os.getenv("EMBEDDING_API_KEY", "").strip()
            or os.getenv("OPENAI_API_KEY", "").strip()
            or os.getenv("DEEPSEEK_API_KEY", "").strip()
            or os.getenv("MIMO_V2_PRO_API_KEY", "").strip()
            or os.getenv("MIMO_API_KEY", "").strip()
        )
        base_url = (
            (embedding_base_url or "").strip()
            or os.getenv("EMBEDDING_BASE_URL", "").strip()
            or os.getenv("OPENAI_BASE_URL", "").strip()
            or os.getenv("MIMO_BASE_URL", "").strip()
        )
        model = (
            (embedding_model or "").strip()
            or os.getenv("EMBEDDING_MODEL", "").strip()
            or "text-embedding-3-small"
        )
        return OpenAICompatibleEmbeddingProvider(
            model=model,
            api_key=api_key,
            base_url=base_url,
            batch_size=batch_size,
        )

    raise ValueError(
        "Unsupported embedding provider. Use one of: hash, openai_compatible."
    )
