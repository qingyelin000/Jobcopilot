from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROLE_LANGUAGE_ONLY = {
    "java",
    "python",
    "go",
    "golang",
    "c++",
    "cpp",
    "c",
}

ROLE_SCORE_KEYWORDS = (
    "后端",
    "前端",
    "客户端",
    "测试",
    "算法",
    "开发",
    "工程师",
    "软件",
)

QUESTION_STARTERS = (
    "介绍",
    "解释",
    "说下",
    "谈谈",
    "知道",
    "什么是",
    "为什么",
    "如何",
    "手撕",
    "设计",
    "实现",
    "阐述",
    "比较",
    "给定",
    "描述",
    "列举",
    "介绍一下",
    "请问",
    "说明",
    "对",
    "关于",
)

SECTION_NOISE = {
    "前言",
    "结尾总结",
    "最后一句话",
    "反问环节",
    "后续流程与注意事项",
    "面试感想",
    "总结",
}

TEXT_NOISE_SUBSTRINGS = (
    "体现了公司对",
    "说明之前都过了",
    "没什么问题",
    "我想说的是",
    "总体表现",
    "最近我整理",
    "下面是整理后的核心问题清单",
    "这类面试的关键不在于",
    "不再考",
    "问题往往有几个特点",
    "认真负责",
    "找她就对了",
    "答的不是很好",
    "我自己不是很满意",
    "感觉很慌",
    "希望能过",
)
LOW_INFORMATION_QUESTIONS = {
    "如何解决",
    "如何保证性能",
    "为什么选择它们",
    "设计模式与代码架构",
}
CONTEXT_PRONOUNS = ("它", "它们", "这个", "这些", "那样", "这种", "那个")

PREFIX_STRIP_PATTERN = re.compile(r"^\s*(?:\d+[.、]|[-*]|问题\d*[：:]|项目部分问题[：:]|八股文[：:]|场景题[：:]|编程题[：:]|追问[：:])\s*")
TRAILING_NOTE_PATTERN = re.compile(r"(?:\s*(?:（[^（）]{0,80}）|\([^()]{0,80}\)))+\s*$")
LEADING_TOPIC_PATTERN = re.compile(r"^([^：:]{1,12})[：:]\s*(.+)$")
MULTI_Q_SPLIT_PATTERN = re.compile(r"(?<=[？?])")
NORMALIZE_KEY_PATTERN = re.compile(r"[\s\"'“”‘’()（）,.，。:：;；!！?？\-_/\\]+")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refine cleaned Nowcoder questions for retrieval.")
    parser.add_argument(
        "--input",
        default="data/nowcoder/clean/long_content_questions.jsonl",
        help="Input question-level JSONL path.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/nowcoder/clean",
        help="Output directory for retrieval-ready question data.",
    )
    return parser


def cleanup_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def choose_primary_company(candidates: list[str]) -> str | None:
    for value in candidates:
        cleaned = cleanup_text(value)
        if cleaned:
            return cleaned
    return None


def normalize_role(value: str) -> str:
    text = cleanup_text(value)
    lowered = text.casefold()
    if lowered == "java":
        return "Java"
    if lowered == "python":
        return "Python"
    if lowered in {"go", "golang"}:
        return "Go"
    if lowered in {"cpp", "c++"}:
        return "C++"
    return text


def choose_primary_role(candidates: list[str]) -> str | None:
    best_score = -10_000
    best_value: str | None = None
    for raw_value in candidates:
        value = normalize_role(raw_value)
        if not value:
            continue
        score = 0
        lowered = value.casefold()
        if lowered in ROLE_LANGUAGE_ONLY:
            score -= 3
        if any(keyword in value for keyword in ROLE_SCORE_KEYWORDS):
            score += 3
        if value.endswith("实习") or value.endswith("实习生"):
            score += 1
        if len(value) >= 3:
            score += 1
        if score > best_score:
            best_score = score
            best_value = value
    return best_value


def strip_trailing_notes(text: str) -> str:
    previous = None
    current = text
    while current != previous:
        previous = current
        current = TRAILING_NOTE_PATTERN.sub("", current).strip()
    return current


def strip_prefixes(text: str) -> str:
    previous = None
    current = text
    while current != previous:
        previous = current
        current = PREFIX_STRIP_PATTERN.sub("", current).strip()
    return current


def maybe_strip_leading_topic(text: str) -> str:
    match = LEADING_TOPIC_PATTERN.match(text)
    if not match:
        return text
    topic, tail = match.groups()
    if len(topic) <= 12 and not any(token in topic for token in ("http", "tcp", "sql", "redis", "mysql")):
        return tail.strip()
    return text


def normalize_question_text(text: str) -> str:
    cleaned = cleanup_text(text)
    cleaned = strip_prefixes(cleaned)
    cleaned = strip_trailing_notes(cleaned)
    cleaned = maybe_strip_leading_topic(cleaned)
    cleaned = strip_prefixes(cleaned)
    cleaned = cleanup_text(cleaned)
    cleaned = cleaned.strip("，。；; ")
    return cleaned


def should_drop_question(section: str | None, text: str) -> bool:
    cleaned = cleanup_text(text)
    if not cleaned:
        return True
    if section and cleanup_text(section) in SECTION_NOISE:
        return True
    if section and cleaned == cleanup_text(section):
        return True
    if any(token in cleaned for token in TEXT_NOISE_SUBSTRINGS):
        return True
    if cleaned in LOW_INFORMATION_QUESTIONS:
        return True
    if len(cleaned) <= 8 and any(token in cleaned for token in CONTEXT_PRONOUNS):
        return True
    if len(cleaned) < 4 or len(cleaned) > 220:
        return True
    if cleaned.startswith(("能走到这一步", "最近我整理", "这类面试的关键", "下面是整理后的核心问题清单")):
        return True
    return False


def split_atomic_questions(text: str) -> list[str]:
    normalized = text.replace("?", "？")
    if "？" not in normalized:
        return [normalized]

    parts = []
    for piece in MULTI_Q_SPLIT_PATTERN.split(normalized):
        piece = cleanup_text(piece)
        if not piece:
            continue
        piece = piece.rstrip("？").strip()
        if piece:
            parts.append(piece)
    return parts or [normalized]


def classify_question(section: str | None, text: str, previous_type: str | None) -> str:
    combined = f"{section or ''} {text}".lower()
    if any(keyword in combined for keyword in ("自我介绍", "为什么", "意向", "价值观", "加班", "职业", "个人规划", "实习目标")):
        return "behavioral"
    if any(keyword in combined for keyword in ("如果", "怎么做", "如何做", "如何设计", "怎么实现", "如何实现")) and any(
        keyword in combined for keyword in ("系统", "架构", "方案", "部署", "限流", "降级", "压测", "缓存", "高并发", "分布式", "对账")
    ):
        return "project_or_system_design"
    if any(keyword in combined for keyword in ("链表", "数组", "树", "动态规划", "回溯", "二分", "算法题", "sql语句", "编程题", "手撕")):
        return "coding"
    if any(
        keyword in combined
        for keyword in (
            "mysql",
            "redis",
            "jvm",
            "java",
            "tcp",
            "http",
            "索引",
            "事务",
            "缓存",
            "数据库",
            "锁",
            "gc",
            "线程",
            "并发",
            "elasticsearch",
            "es",
            "cap",
            "限流",
        )
    ):
        return "backend_foundation"
    if any(
        keyword in combined
        for keyword in ("项目", "架构", "设计", "系统", "部署", "压测", "瓶颈", "优化", "场景", "高可用", "微服务", "定位")
    ):
        return "project_or_system_design"
    return previous_type or "general"


def make_normalized_key(text: str) -> str:
    lowered = text.casefold()
    return NORMALIZE_KEY_PATTERN.sub("", lowered)


def build_retrieval_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    seen_keys: set[tuple[str | None, str | None, str]] = set()
    output: list[dict[str, object]] = []

    for record in records:
        company = choose_primary_company(record.get("company_candidates") or [])
        role = choose_primary_role(record.get("role_candidates") or [])
        section = cleanup_text(record.get("section"))
        base_text = normalize_question_text(str(record.get("question_text") or ""))

        if should_drop_question(section=section or None, text=base_text):
            continue

        for index, atomic_text in enumerate(split_atomic_questions(base_text), start=1):
            refined_text = normalize_question_text(atomic_text)
            if should_drop_question(section=section or None, text=refined_text):
                continue
            if not ("？" in atomic_text or refined_text.startswith(QUESTION_STARTERS)):
                # Drop descriptive statements that survived the first pass but are not really askable.
                continue

            normalized_key = make_normalized_key(refined_text)
            if not normalized_key:
                continue
            dedupe_key = (company, role, normalized_key)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            question_id = str(record.get("question_id") or "")
            output.append(
                {
                    "question_id": question_id if index == 1 else f"{question_id}__{index}",
                    "source_content_id": record.get("source_content_id"),
                    "company": company,
                    "role": role,
                    "section": section or None,
                    "question_text": refined_text,
                    "question_type": classify_question(
                        section=section or None,
                        text=refined_text,
                        previous_type=str(record.get("question_type") or ""),
                    ),
                }
            )
    return output


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)

    source_records = [
        json.loads(line)
        for line in input_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    retrieval_records = build_retrieval_records(source_records)

    output_path = output_dir / "long_content_retrieval_questions.jsonl"
    manifest_path = output_dir / "long_content_retrieval_manifest.json"

    write_jsonl(output_path, retrieval_records)
    manifest_path.write_text(
        json.dumps(
            {
                "input": str(input_path),
                "output": str(output_path),
                "source_question_count": len(source_records),
                "retrieval_question_count": len(retrieval_records),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "source_question_count": len(source_records),
                "retrieval_question_count": len(retrieval_records),
                "output": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
