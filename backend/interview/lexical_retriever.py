from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Iterable


DEFAULT_DATASET_PATH = Path("data/nowcoder/pipeline_runs_llm/canonical/long_content_retrieval_questions.jsonl")

TECH_KEYWORDS = {
    "java": "Java",
    "python": "Python",
    "golang": "Go",
    "go": "Go",
    "c++": "C++",
    "cpp": "C++",
    "mysql": "MySQL",
    "redis": "Redis",
    "jvm": "JVM",
    "tcp": "TCP",
    "http": "HTTP",
    "sql": "SQL",
    "rag": "RAG",
    "agent": "Agent",
    "prompt": "Prompt",
    "embedding": "Embedding",
    "langchain": "LangChain",
    "langgraph": "LangGraph",
    "fastapi": "FastAPI",
    "react": "React",
    "docker": "Docker",
    "k8s": "K8s",
    "kubernetes": "Kubernetes",
    "es": "Elasticsearch",
    "elasticsearch": "Elasticsearch",
    "向量数据库": "向量数据库",
    "向量检索": "向量检索",
    "分布式": "分布式",
    "分布式锁": "分布式锁",
    "缓存": "缓存",
    "一致性": "一致性",
    "高并发": "高并发",
    "限流": "限流",
    "微服务": "微服务",
    "架构": "架构",
    "系统设计": "系统设计",
    "项目": "项目",
    "项目经历": "项目",
    "线程": "线程",
    "锁": "锁",
    "索引": "索引",
    "事务": "事务",
    "面向对象": "面向对象",
}

ROLE_KEYWORDS = (
    "后端开发",
    "Java后端",
    "Java开发",
    "前端开发",
    "客户端开发",
    "算法工程师",
    "测试开发",
    "AI应用开发",
    "大模型应用开发",
    "开发工程师",
    "后端",
    "前端",
    "客户端",
    "算法",
    "测试",
    "C++",
    "Java",
    "Python",
    "Go",
)

QUESTION_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "project_or_system_design": (
        "项目",
        "项目经历",
        "系统设计",
        "架构",
        "高并发",
        "分布式",
        "限流",
        "缓存",
        "一致性",
        "微服务",
        "部署",
        "压测",
    ),
    "backend_foundation": (
        "mysql",
        "redis",
        "jvm",
        "tcp",
        "http",
        "sql",
        "索引",
        "事务",
        "线程",
        "锁",
    ),
    "coding": (
        "算法",
        "手撕",
        "链表",
        "数组",
        "树",
        "动态规划",
        "回溯",
        "编码",
        "c++",
    ),
    "behavioral": (
        "自我介绍",
        "职业规划",
        "实习目标",
        "价值观",
        "加班",
        "沟通",
    ),
}

QUESTION_TYPE_ANCHORS: dict[str, tuple[str, ...]] = {
    "project_or_system_design": ("项目", "架构", "系统设计", "分布式", "高并发", "限流"),
    "backend_foundation": ("mysql", "redis", "jvm", "tcp", "http", "sql", "索引", "事务"),
    "coding": ("算法", "手撕", "编码", "链表", "数组"),
    "behavioral": ("自我介绍", "职业规划", "实习目标"),
}

ASCII_WORD_PATTERN = re.compile(r"[A-Za-z0-9+#.\-]+")
CHINESE_SEGMENT_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}")
SPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class RetrievalQuestion:
    question_id: str
    source_content_id: str
    company: str | None
    role: str | None
    section: str | None
    publish_time: str | None
    normalized_key: str | None
    question_text: str
    question_type: str

    @cached_property
    def searchable_text(self) -> str:
        parts = [self.company or "", self.role or "", self.section or "", self.question_text]
        return " ".join(part for part in parts if part)


@dataclass(frozen=True)
class RetrievedQuestion:
    question: RetrievalQuestion
    score: float
    matched_keywords: list[str]
    score_breakdown: dict[str, float]


@dataclass(frozen=True)
class QueryProfile:
    raw_query: str
    keywords: list[str]
    roles: list[str]
    preferred_types: list[str]


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return SPACE_PATTERN.sub(" ", value).strip()


def _extract_curated_keywords(text: str) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for raw, canonical in TECH_KEYWORDS.items():
        if raw in lowered and canonical not in found:
            found.append(canonical)
    return found


def _extract_roles(text: str) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for role in ROLE_KEYWORDS:
        if role.lower() in lowered and role not in found:
            found.append(role)
    return found


def _infer_preferred_types(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    scores: dict[str, int] = {name: 0 for name in QUESTION_TYPE_HINTS}
    for question_type, hints in QUESTION_TYPE_HINTS.items():
        for hint in hints:
            if hint.lower() in lowered:
                scores[question_type] += 1
    for keyword in keywords:
        if keyword in {"RAG", "Agent", "Prompt", "向量数据库", "LangChain", "LangGraph"}:
            scores["project_or_system_design"] += 1
        if keyword in {"MySQL", "Redis", "JVM", "TCP", "HTTP", "SQL"}:
            scores["backend_foundation"] += 1
    return [name for name, score in sorted(scores.items(), key=lambda item: item[1], reverse=True) if score > 0]


def build_query_profile(resume_text: str, jd_text: str, extra_query: str | None = None) -> QueryProfile:
    # 将查询上下文统一成 profile，供词法召回阶段复用。
    combined = "\n".join(part for part in (extra_query or "", jd_text, resume_text) if part).strip()
    keywords = _extract_curated_keywords(combined)
    return QueryProfile(
        raw_query=combined,
        keywords=keywords,
        roles=_extract_roles(combined),
        preferred_types=_infer_preferred_types(combined, keywords),
    )


def _mixed_tokenize(text: str) -> list[str]:
    # 中英文混合切词：英文词 + 中文片段 + 中文 n-gram，提高召回鲁棒性。
    lowered = text.lower()
    tokens: list[str] = []
    for word in ASCII_WORD_PATTERN.findall(lowered):
        tokens.append(word)
    for segment in CHINESE_SEGMENT_PATTERN.findall(text):
        if len(segment) <= 6:
            tokens.append(segment)
        for index in range(len(segment) - 1):
            tokens.append(segment[index : index + 2])
        for index in range(len(segment) - 2):
            tokens.append(segment[index : index + 3])
    for keyword in _extract_curated_keywords(text):
        tokens.append(keyword.lower())
    return tokens


class LexicalRetriever:
    """
    Lightweight lexical retriever used as the sparse branch in RetrieverV2.

    It keeps the original v1 lexical behavior (tokenization, BM25 and
    metadata/type boosts) without introducing extra runtime dependencies.
    """

    def __init__(self, dataset_path: Path | str = DEFAULT_DATASET_PATH) -> None:
        self.dataset_path = Path(dataset_path)
        self.questions = self._load_questions()
        self._doc_tokens = [self._build_document_tokens(item) for item in self.questions]
        self._avg_doc_len = sum(len(tokens) for tokens in self._doc_tokens) / max(len(self._doc_tokens), 1)
        self._term_doc_freq = self._build_doc_freq(self._doc_tokens)

    def _load_questions(self) -> list[RetrievalQuestion]:
        records: list[RetrievalQuestion] = []
        for raw_line in self.dataset_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            payload = json.loads(raw_line)
            records.append(
                RetrievalQuestion(
                    question_id=str(payload["question_id"]),
                    source_content_id=str(payload["source_content_id"]),
                    company=payload.get("company"),
                    role=payload.get("role"),
                    section=payload.get("section"),
                    publish_time=payload.get("publish_time"),
                    normalized_key=payload.get("normalized_key"),
                    question_text=str(payload["question_text"]),
                    question_type=str(payload["question_type"]),
                )
            )
        return records

    @staticmethod
    def _build_doc_freq(doc_tokens: list[list[str]]) -> dict[str, int]:
        frequencies: dict[str, int] = {}
        for tokens in doc_tokens:
            for token in set(tokens):
                frequencies[token] = frequencies.get(token, 0) + 1
        return frequencies

    @staticmethod
    def _build_document_tokens(question: RetrievalQuestion) -> list[str]:
        return _mixed_tokenize(question.searchable_text)

    def _idf(self, token: str) -> float:
        doc_freq = self._term_doc_freq.get(token, 0)
        total_docs = max(len(self.questions), 1)
        return math.log(1 + (total_docs - doc_freq + 0.5) / (doc_freq + 0.5))

    def _bm25_score(self, query_tokens: list[str], doc_tokens: list[str]) -> float:
        if not doc_tokens or not query_tokens:
            return 0.0
        # BM25 是词法匹配主干分，后续会再叠加业务 boost。
        k1 = 1.5
        b = 0.75
        frequencies: dict[str, int] = {}
        for token in doc_tokens:
            frequencies[token] = frequencies.get(token, 0) + 1
        score = 0.0
        doc_len = len(doc_tokens)
        for token in query_tokens:
            term_freq = frequencies.get(token, 0)
            if term_freq == 0:
                continue
            numerator = term_freq * (k1 + 1)
            denominator = term_freq + k1 * (1 - b + b * doc_len / max(self._avg_doc_len, 1))
            score += self._idf(token) * numerator / denominator
        return score

    @staticmethod
    def _metadata_overlap(values: Iterable[str], target: str | None) -> int:
        if not target:
            return 0
        lowered_target = target.lower()
        return sum(1 for value in values if value.lower() in lowered_target or lowered_target in value.lower())

    @staticmethod
    def _diversify_results(
        results: list[RetrievedQuestion],
        top_k: int,
        preferred_types: list[str],
    ) -> list[RetrievedQuestion]:
        # 多样性策略：优先覆盖偏好题型，同时限制同 section 过度集中。
        if len(results) <= top_k:
            return results

        selected: list[RetrievedQuestion] = []
        selected_ids: set[str] = set()
        section_counts: dict[str, int] = {}

        def try_select(item: RetrievedQuestion, *, ignore_section_limit: bool = False) -> bool:
            question_id = item.question.question_id
            if question_id in selected_ids:
                return False
            section = item.question.section or ""
            if not ignore_section_limit and section and section_counts.get(section, 0) >= 2:
                return False
            selected.append(item)
            selected_ids.add(question_id)
            if section:
                section_counts[section] = section_counts.get(section, 0) + 1
            return True

        for preferred_type in preferred_types[:3]:
            for item in results:
                if item.question.question_type == preferred_type and try_select(item, ignore_section_limit=True):
                    break

        for item in results:
            if len(selected) >= top_k:
                break
            if try_select(item):
                continue

        if len(selected) < top_k:
            for item in results:
                if len(selected) >= top_k:
                    break
                try_select(item, ignore_section_limit=True)

        return selected[:top_k]

    def search(
        self,
        resume_text: str,
        jd_text: str,
        top_k: int = 8,
        extra_query: str | None = None,
    ) -> list[RetrievedQuestion]:
        query_profile = build_query_profile(resume_text=resume_text, jd_text=jd_text, extra_query=extra_query)
        query_tokens: list[str] = []
        # 构造查询词：关键词、岗位词、题型锚点和额外查询共同参与检索。
        for keyword in query_profile.keywords:
            query_tokens.extend(_mixed_tokenize(keyword))
        for role in query_profile.roles:
            query_tokens.extend(_mixed_tokenize(role))
        for preferred_type in query_profile.preferred_types:
            for anchor in QUESTION_TYPE_ANCHORS.get(preferred_type, ()):
                query_tokens.extend(_mixed_tokenize(anchor))
        if extra_query:
            query_tokens.extend(_mixed_tokenize(extra_query))
        if not query_tokens:
            query_tokens = _mixed_tokenize(query_profile.raw_query)

        scored_results: list[RetrievedQuestion] = []
        for question, doc_tokens in zip(self.questions, self._doc_tokens, strict=True):
            bm25_score = self._bm25_score(query_tokens, doc_tokens)
            matched_keywords = [
                keyword
                for keyword in query_profile.keywords
                if keyword.lower() in question.searchable_text.lower()
            ]
            keyword_score = 0.9 * len(matched_keywords)

            role_score = 0.0
            if question.role and query_profile.roles:
                role_score = 1.5 * self._metadata_overlap(query_profile.roles, question.role)

            type_score = 0.0
            if query_profile.preferred_types:
                for index, question_type in enumerate(query_profile.preferred_types):
                    if question.question_type == question_type:
                        type_score = max(type_score, 1.2 - 0.2 * index)

            company_score = 0.0
            if question.company and question.company in query_profile.raw_query:
                company_score = 1.2

            # 词法总分 = BM25 + 关键词/岗位/题型/公司加权信号。
            total_score = bm25_score + keyword_score + role_score + type_score + company_score
            if total_score <= 0:
                continue

            scored_results.append(
                RetrievedQuestion(
                    question=question,
                    score=round(total_score, 4),
                    matched_keywords=matched_keywords,
                    score_breakdown={
                        "bm25": round(bm25_score, 4),
                        "keyword_boost": round(keyword_score, 4),
                        "role_boost": round(role_score, 4),
                        "type_boost": round(type_score, 4),
                        "company_boost": round(company_score, 4),
                    },
                )
            )

        scored_results.sort(key=lambda item: item.score, reverse=True)
        return self._diversify_results(
            results=scored_results,
            top_k=top_k,
            preferred_types=query_profile.preferred_types,
        )


def serialize_retrieved_question(item: RetrievedQuestion) -> dict[str, object]:
    return {
        "question_id": item.question.question_id,
        "source_content_id": item.question.source_content_id,
        "company": item.question.company,
        "role": item.question.role,
        "section": item.question.section,
        "publish_time": item.question.publish_time,
        "question_text": item.question.question_text,
        "question_type": item.question.question_type,
        "score": item.score,
        "matched_keywords": item.matched_keywords,
        "score_breakdown": item.score_breakdown,
    }
