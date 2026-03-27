from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


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

ROUND_PATTERN = re.compile(r"(一面|二面|三面|四面|五面|六面|七面|八面|九面|十面|hr面|hrbp面|终面|主管面|交叉面)")
ROLE_PATTERN = re.compile(
    r"((?:java|python|golang|go|cpp|c\+\+|前端|后端|客户端|测试|算法|数据|运维|产品|运营|大模型|ai|nlp|cv)"
    r"(?:工程师|开发|开发工程师|实习|实习生|岗位)?)",
    re.IGNORECASE,
)
COMPANY_LINE_PATTERNS = (
    re.compile(r"(?:面试公司|公司)[：:]\s*([^\n#]+)"),
    re.compile(r"(?:投递公司|面试单位)[：:]\s*([^\n#]+)"),
)
ROLE_LINE_PATTERNS = (
    re.compile(r"(?:面试岗位|岗位|投递岗位)[：:]\s*([^\n#]+)"),
    re.compile(r"(?:面试方向)[：:]\s*([^\n#]+)"),
)
SECTION_HEADER_PATTERN = re.compile(r"^[A-Za-z0-9_+\-/#\u4e00-\u9fff]{1,20}$")
NOISE_PREFIXES = ("#")
SUMMARY_HINTS = ("面试感想", "总结", "许愿", "攒人品", "希望能过", "已oc", "求捞")
TITLE_SPLIT_PATTERN = re.compile(r"[-_|/｜·•（）()\[\]【】\s]+")
COMPANY_STRIP_PREFIX_PATTERN = re.compile(
    r"^(?:\d{2,4}届|春招|秋招|校招|社招|暑期|日常实习|提前批|内推|面经|应届)[-_/| ]*",
    re.IGNORECASE,
)
COMPANY_STRIP_SUFFIX_PATTERN = re.compile(
    r"[-_/| ]*(?:od|外包|实习|面经|春招|秋招|校招|社招|暑期|提前批|内推|一面|二面|三面|四面|五面|终面|主管面|hr面|笔试|机试)$",
    re.IGNORECASE,
)
COMPANY_NOISE_PATTERN = re.compile(
    r"^(?:\d{2,4}届|春招|秋招|校招|社招|暑期|日常实习|提前批|内推|面经|应届|发面经攒人品|求捞|许愿|offer|oc)$",
    re.IGNORECASE,
)
COMPANY_ROLE_NOISE_PATTERN = re.compile(
    r"(?:java|python|golang|go|cpp|c\+\+|前端|后端|客户端|测试|算法|开发|工程师|岗位|实习|面经)$",
    re.IGNORECASE,
)
COMPANY_TITLE_ROLE_SPLIT_PATTERN = re.compile(
    r"(java|python|golang|go|cpp|c\+\+|前端|后端|客户端|测试|算法|开发|工程师|岗位|实习)",
    re.IGNORECASE,
)
COMPANY_NOISE_SUBSTRINGS = (
    "今日已更新",
    "速览",
    "汇总",
    "合集",
    "入口",
    "分析",
    "分享",
    "攒人品",
    "问八股",
    "哪家",
    "第一面",
    "第二面",
    "第三面",
    "暑实",
    "八股",
    "项目多",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean Nowcoder long_content interview posts.")
    parser.add_argument(
        "--input",
        default="data/nowcoder/normalized/interview_posts.jsonl",
        help="Normalized interview_posts.jsonl path.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/nowcoder/clean",
        help="Output directory for cleaned long_content data.",
    )
    return parser


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[📍🕐💻❓🙌🔥✅⭐✨👉👈🎯🚀📌📎]", "", text)
    return text.strip()


def strip_hashtag_lines(value: str) -> str:
    kept_lines: list[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if not stripped:
            kept_lines.append("")
            continue
        if stripped.startswith("#") and stripped.endswith("#"):
            continue
        kept_lines.append(stripped)
    text = "\n".join(kept_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_candidates(patterns: tuple[re.Pattern[str], ...], text: str) -> list[str]:
    values: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            candidate = cleanup_candidate(match.group(1))
            if candidate:
                values.append(candidate)
    return dedupe(values)


def cleanup_candidate(value: str) -> str:
    text = value.strip("：:;；,.，。!！?？ ")
    text = re.sub(r"\s+", " ", text)
    text = text[:80].strip()
    return text


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        lowered = value.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(value)
    return result


def normalize_company_candidate(value: str) -> str:
    text = cleanup_candidate(value).strip("#")
    previous = None
    while text and text != previous:
        previous = text
        text = COMPANY_STRIP_PREFIX_PATTERN.sub("", text).strip(" -_/|")
        text = COMPANY_STRIP_SUFFIX_PATTERN.sub("", text).strip(" -_/|")
        text = cleanup_candidate(text)
    match = COMPANY_TITLE_ROLE_SPLIT_PATTERN.search(text)
    if match and match.start() >= 2:
        prefix = cleanup_candidate(text[: match.start()])
        if prefix:
            text = prefix
    return text


def is_valid_company_candidate(raw_value: str, normalized_value: str) -> bool:
    raw_text = cleanup_candidate(raw_value)
    normalized_text = cleanup_candidate(normalized_value)
    if not raw_text or not normalized_text:
        return False
    if COMPANY_NOISE_PATTERN.fullmatch(raw_text) or COMPANY_NOISE_PATTERN.fullmatch(normalized_text):
        return False
    if len(normalized_text) < 2 or len(normalized_text) > 24:
        return False
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", normalized_text):
        return False
    if "面经" in normalized_text.casefold():
        return False
    if any(token in normalized_text for token in COMPANY_NOISE_SUBSTRINGS):
        return False
    if ROLE_PATTERN.fullmatch(normalized_text):
        return False
    if COMPANY_ROLE_NOISE_PATTERN.fullmatch(normalized_text):
        return False
    return True


def score_company_segment(segment: str) -> tuple[int, str]:
    normalized = normalize_company_candidate(segment)
    if not is_valid_company_candidate(segment, normalized):
        return -10_000, ""

    score = 0
    if normalized != cleanup_candidate(segment):
        score += 1
    if re.search(r"[\u4e00-\u9fff]{2,}", normalized):
        score += 3
    if 2 <= len(normalized) <= 8:
        score += 2
    elif len(normalized) <= 16:
        score += 1
    return score, normalized


def choose_best_company(values: list[str]) -> list[str]:
    best_score = -10_000
    best_value = ""
    for value in values:
        score, normalized = score_company_segment(value)
        if score <= -10_000 or score <= best_score:
            continue
        best_score = score
        best_value = normalized

    if not best_value:
        return []
    return [best_value]


def infer_company_candidates_from_title(title: str) -> list[str]:
    segments = [cleanup_candidate(part) for part in TITLE_SPLIT_PATTERN.split(title) if cleanup_candidate(part)]
    return choose_best_company(segments)


def infer_company_candidates_from_tags(tags: list[str]) -> list[str]:
    return choose_best_company(tags)


def infer_company_candidates(title: str, body_text: str, tags: list[str]) -> list[str]:
    body_candidates = choose_best_company(extract_candidates(COMPANY_LINE_PATTERNS, body_text))
    if body_candidates:
        return body_candidates

    title_candidates = infer_company_candidates_from_title(title)
    if title_candidates:
        return title_candidates

    return infer_company_candidates_from_tags(tags)


def infer_role_candidates(title: str, body_text: str) -> list[str]:
    candidates = extract_candidates(ROLE_LINE_PATTERNS, body_text)
    for match in ROLE_PATTERN.finditer(title):
        candidates.append(cleanup_candidate(match.group(1)))
    return dedupe([value for value in candidates if value])


def infer_round_candidates(title: str, body_text: str) -> list[str]:
    candidates: list[str] = []
    for source in (title, body_text):
        for match in ROUND_PATTERN.finditer(source.lower()):
            candidates.append(match.group(1))
    return dedupe(candidates)


def is_section_header(line: str) -> bool:
    if not SECTION_HEADER_PATTERN.match(line):
        return False
    if any(token in line for token in ("？", "?", "。", "，", ",", "：", ":")):
        return False
    if line.startswith(QUESTION_STARTERS):
        return False
    return True


def is_probable_question(line: str) -> bool:
    if len(line) < 4 or len(line) > 180:
        return False
    if any(line.startswith(prefix) for prefix in NOISE_PREFIXES):
        return False
    if any(hint in line for hint in SUMMARY_HINTS):
        return False
    if "？" in line or "?" in line:
        return True
    if line.startswith(QUESTION_STARTERS):
        return True
    tail = line
    if "：" in line:
        tail = line.split("：", 1)[1].strip()
    elif ":" in line:
        tail = line.split(":", 1)[1].strip()
    return tail.startswith(QUESTION_STARTERS)


def classify_question(line: str, section: str | None) -> str:
    text = f"{section or ''} {line}".lower()
    if any(keyword in text for keyword in ("手撕", "链表", "数组", "树", "动态规划", "算法", "编码")):
        return "coding"
    if any(keyword in text for keyword in ("mysql", "redis", "jvm", "java", "数据库", "索引", "事务", "缓存", "网络", "操作系统")):
        return "backend_foundation"
    if any(keyword in text for keyword in ("项目", "架构", "设计", "系统", "场景")):
        return "project_or_system_design"
    if any(keyword in text for keyword in ("自我介绍", "为什么", "职业", "实习意向", "优缺点")):
        return "behavioral"
    return "general"


def extract_question_blocks(body_text: str) -> list[dict[str, Any]]:
    lines = [line.strip(" -\t") for line in body_text.splitlines()]
    question_blocks: list[dict[str, Any]] = []
    current_section: str | None = None

    for line in lines:
        if not line:
            continue
        if is_section_header(line):
            current_section = line
            continue
        if not is_probable_question(line):
            continue
        question_blocks.append(
            {
                "section": current_section,
                "question_text": line,
                "question_type": classify_question(line, current_section),
            }
        )
    return question_blocks


def build_clean_record(record: dict[str, Any]) -> dict[str, Any]:
    title = normalize_text(record.get("title"))
    body_text = strip_hashtag_lines(normalize_text(record.get("body_text")))
    tags = record.get("tags") or []
    company_candidates = infer_company_candidates(title=title, body_text=body_text, tags=tags)
    role_candidates = infer_role_candidates(title=title, body_text=body_text)
    question_blocks = extract_question_blocks(body_text=body_text)

    metrics = record.get("metrics") or {}
    return {
        "source_platform": record.get("source_platform"),
        "content_id": record.get("content_id"),
        "detail_kind": record.get("detail_kind"),
        "title": title,
        "body_text": body_text,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "tags": tags,
        "metrics": {
            "view_count": metrics.get("view_count"),
            "comment_count": metrics.get("comment_count"),
            "like_count": metrics.get("like_count"),
        },
        "company_candidates": company_candidates,
        "role_candidates": role_candidates,
        "question_blocks": question_blocks,
        "question_count": len(question_blocks),
        "keep_for_rag": len(question_blocks) > 0,
    }


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
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

    clean_records: list[dict[str, Any]] = []
    question_records: list[dict[str, Any]] = []

    for raw_line in input_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        record = json.loads(raw_line)
        if record.get("detail_kind") != "long_content":
            continue

        clean_record = build_clean_record(record)
        clean_records.append(clean_record)

        for index, question in enumerate(clean_record["question_blocks"], start=1):
            question_records.append(
                {
                    "question_id": f"{clean_record['content_id']}#{index}",
                    "source_content_id": clean_record["content_id"],
                    "title": clean_record["title"],
                    "company_candidates": clean_record["company_candidates"],
                    "role_candidates": clean_record["role_candidates"],
                    "section": question.get("section"),
                    "question_text": question.get("question_text"),
                    "question_type": question.get("question_type"),
                }
            )

    clean_posts_path = output_dir / "long_content_clean_posts.jsonl"
    questions_path = output_dir / "long_content_questions.jsonl"
    manifest_path = output_dir / "long_content_manifest.json"

    write_jsonl(clean_posts_path, clean_records)
    write_jsonl(questions_path, question_records)
    manifest_path.write_text(
        json.dumps(
            {
                "input": str(input_path),
                "clean_posts": str(clean_posts_path),
                "questions": str(questions_path),
                "clean_post_count": len(clean_records),
                "question_count": len(question_records),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "clean_post_count": len(clean_records),
                "question_count": len(question_records),
                "clean_posts": str(clean_posts_path),
                "questions": str(questions_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
