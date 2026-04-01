from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Any


# 仅包含链接的一整行（纯 URL）判定
URL_ONLY_PATTERN = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)
# 仅包含 Markdown 链接的一整行判定
MARKDOWN_LINK_ONLY_PATTERN = re.compile(r"^\[[^\]]+\]\((?:https?://|www\.)\S+\)$", re.IGNORECASE)
# 连续空格/制表符（含全角空格）压缩
MULTI_SPACE_PATTERN = re.compile(r"[ \t\u3000]+")
# 超过 2 个连续空行压缩
MULTI_BLANK_LINE_PATTERN = re.compile(r"\n{3,}")
# HTML 标签匹配（用于粗清洗）
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

# 显式“公司:”标签抽取，例如：面试公司：腾讯
LABELED_COMPANY_PATTERN = re.compile(
    r"(?:面试公司|公司|投递公司|目标公司)\s*[:：]\s*([A-Za-z0-9\u4e00-\u9fff()（）·\-.]{2,32})"
)
# 标题前缀公司抽取，例如：腾讯-后端一面面经
TITLE_COMPANY_PATTERN = re.compile(
    r"^([A-Za-z0-9\u4e00-\u9fff()（）·\-.]{2,32})\s*[-|/丨]\s*.*(?:面经|面试|一面|二面|三面|hr面)",
    re.IGNORECASE,
)

# 兜底公司词典：前两种规则抽不到时做弱匹配
KNOWN_COMPANIES = (
    "字节跳动",
    "腾讯",
    "阿里巴巴",
    "阿里",
    "蚂蚁",
    "美团",
    "百度",
    "京东",
    "快手",
    "小红书",
    "拼多多",
    "滴滴",
    "华为",
    "网易",
    "携程",
    "小米",
    "OPPO",
    "vivo",
    "米哈游",
    "哔哩哔哩",
    "B站",
    "Shopee",
    "理想",
    "蔚来",
    "比亚迪",
)

# 岗位关键词 -> 标准化岗位名映射（按顺序匹配）
ROLE_HINT_PATTERNS: list[tuple[str, str]] = [
    ("java后端开发工程师", "后端开发"),
    ("java后端开发", "后端开发"),
    ("java后端", "后端开发"),
    ("后端开发工程师", "后端开发"),
    ("后端开发", "后端开发"),
    ("后端", "后端开发"),
    ("java开发工程师", "Java开发"),
    ("java开发", "Java开发"),
    ("前端开发工程师", "前端开发"),
    ("前端开发", "前端开发"),
    ("前端", "前端开发"),
    ("测试开发工程师", "测试开发"),
    ("测试开发", "测试开发"),
    ("测试工程师", "测试"),
    ("算法工程师", "算法工程师"),
    ("算法", "算法工程师"),
    ("客户端开发工程师", "客户端开发"),
    ("客户端开发", "客户端开发"),
    ("服务端开发工程师", "服务端开发"),
    ("服务端开发", "服务端开发"),
    ("数据开发工程师", "数据开发"),
    ("数据开发", "数据开发"),
    ("数据分析", "数据分析"),
    ("产品经理", "产品经理"),
    ("运维工程师", "运维"),
    ("运维", "运维"),
    ("sre", "SRE"),
    ("nlp", "NLP"),
    ("cv", "CV"),
    ("ai", "AI"),
    ("大模型", "AI"),
]

# 常见广告/导流噪声词
AD_NOISE_KEYWORDS = (
    "广告",
    "加微",
    "vx",
    "v信",
    "微信",
    "公众号",
    "进群",
    "内推",
    "私信",
    "商务合作",
    "付费咨询",
    "训练营",
    "课程",
)

# 常见互动口号噪声词
CTA_NOISE_KEYWORDS = (
    "评论",
    "分享",
    "攒人品",
    "点赞",
    "收藏",
    "转发",
    "关注",
    "一键三连",
)


def iso_now() -> str:
    """返回当前 UTC 时间（ISO-8601 字符串）。"""

    return datetime.now(tz=UTC).isoformat()


def build_parser() -> argparse.ArgumentParser:
    """定义命令行参数。"""

    parser = argparse.ArgumentParser(description="Pre-clean Nowcoder normalized posts for LLM extraction.")
    # 输入：crawl 阶段产出的标准化 JSONL
    parser.add_argument(
        "--input",
        default="data/nowcoder/pipeline_runs_llm/latest/crawl/normalized/interview_posts.jsonl",
        help="Input normalized post JSONL path.",
    )
    # 输出目录：会生成清洗后的 JSONL 和 manifest
    parser.add_argument(
        "--output-dir",
        default="data/nowcoder/pipeline_runs_llm/latest/preclean",
        help="Output directory for pre-clean artifacts.",
    )
    # 最小有效文本长度（按非空白字符计数）
    parser.add_argument(
        "--min-text-length",
        type=int,
        default=80,
        help="Drop record when interview_text non-whitespace char count is below this value.",
    )
    # 保留哪些 detail_kind；可重复传参，传 '*' 表示不过滤
    parser.add_argument(
        "--detail-kind",
        action="append",
        dest="detail_kinds",
        default=["long_content"],
        help="detail_kind values to keep. Repeat this flag to include multiple values. Use '*' to keep all.",
    )
    return parser


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """写 JSONL 文件（一行一条 JSON）。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    """写带缩进 JSON 文件（用于 manifest）。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def strip_html(value: str) -> str:
    """粗去标签并压缩空白，用于 summary/body_text 兜底清洗。"""

    text = unescape(value)
    text = HTML_TAG_PATTERN.sub("\n", text)
    text = text.replace("\r", "\n")
    text = MULTI_SPACE_PATTERN.sub(" ", text)
    text = MULTI_BLANK_LINE_PATTERN.sub("\n\n", text)
    return text.strip()


def clean_title(value: str | None) -> str:
    """标题轻清洗：取首行、压空白、限制长度。"""

    if not value:
        return ""
    text = unescape(value).replace("\r", "\n")
    text = text.splitlines()[0] if text.splitlines() else text
    text = MULTI_SPACE_PATTERN.sub(" ", text).strip()
    return text[:120]


def line_is_pure_link(line: str) -> bool:
    """判断一行是否“只有链接”，用于丢弃无信息行。"""

    return bool(URL_ONLY_PATTERN.fullmatch(line) or MARKDOWN_LINK_ONLY_PATTERN.fullmatch(line))


def line_is_noise(line: str) -> bool:
    """根据长度+关键词识别广告/互动口号噪声行。"""

    lowered = line.casefold()
    if len(line) <= 42 and any(keyword in lowered for keyword in AD_NOISE_KEYWORDS):
        return True
    if len(line) <= 20 and any(keyword in lowered for keyword in CTA_NOISE_KEYWORDS):
        return True
    return False


def clean_interview_text(value: str) -> str:
    """面经正文清洗：去噪、去重行、规范空行。"""

    text = unescape(value).replace("\r", "\n")
    cleaned_lines: list[str] = []
    prev_non_empty = ""

    for raw_line in text.splitlines():
        line = MULTI_SPACE_PATTERN.sub(" ", raw_line).strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1]:
                cleaned_lines.append("")
            continue
        if line_is_pure_link(line) or line_is_noise(line):
            continue
        if line == prev_non_empty:
            continue

        cleaned_lines.append(line)
        prev_non_empty = line

    joined = "\n".join(cleaned_lines).strip()
    joined = MULTI_BLANK_LINE_PATTERN.sub("\n\n", joined)
    return joined


def non_whitespace_length(value: str) -> int:
    """统计非空白字符数（用于最小文本长度门槛）。"""

    return len(re.sub(r"\s+", "", value))


def sanitize_hint(value: str) -> str:
    """清洗 company/role hint 的候选片段。"""

    text = value.strip(" \t\r\n-_|/,:：;；。.!?()（）[]【】")
    text = MULTI_SPACE_PATTERN.sub(" ", text)
    return text[:32]


def infer_company_hint(title: str, interview_text: str) -> str:
    """推断公司线索：标签抽取 -> 标题抽取 -> 词典兜底。"""

    combined_head = "\n".join(interview_text.splitlines()[:12])
    probe_text = f"{title}\n{combined_head}".strip()

    labeled_match = LABELED_COMPANY_PATTERN.search(probe_text)
    if labeled_match:
        candidate = sanitize_hint(labeled_match.group(1))
        if len(candidate) >= 2:
            return candidate

    title_match = TITLE_COMPANY_PATTERN.search(title)
    if title_match:
        candidate = sanitize_hint(title_match.group(1))
        if len(candidate) >= 2:
            return candidate

    for company in KNOWN_COMPANIES:
        if company.casefold() in probe_text.casefold():
            return company

    return ""


def infer_role_hint(title: str, interview_text: str) -> str:
    """推断岗位线索：按关键词映射成标准岗位名。"""

    combined_head = "\n".join(interview_text.splitlines()[:12])
    probe_text = f"{title}\n{combined_head}".casefold()
    for keyword, normalized in ROLE_HINT_PATTERNS:
        if keyword.casefold() in probe_text:
            return normalized
    return ""


def build_source_url(record: dict[str, Any], detail_kind: str, content_id: str) -> str:
    """构建可追溯 source_url；缺失时按规则补全。"""

    source_url = str(record.get("source_url") or "").strip()
    if source_url:
        return source_url
    if detail_kind == "long_content" and content_id:
        return f"https://www.nowcoder.com/discuss/{content_id}"
    return ""


def build_source_id(
    *,
    platform: str,
    detail_kind: str,
    content_id: str,
    detail_lookup_key: str,
    source_url: str,
) -> str:
    """构建稳定 source_id，优先用 content_id，其次 detail_key，再其次 URL 哈希。"""

    if content_id:
        stable_part = content_id
    elif detail_lookup_key:
        stable_part = detail_lookup_key
    elif source_url:
        stable_part = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:16]
    else:
        return ""
    return f"{platform}:{detail_kind}:{stable_part}"


def build_preclean_record(record: dict[str, Any], min_text_length: int) -> tuple[dict[str, Any] | None, str | None]:
    """
    将 crawl 记录转换为给 LLM 的轻清洗结构。
    返回 (record, None) 表示保留；返回 (None, reason) 表示丢弃并给出原因。
    """

    platform = str(record.get("source_platform") or "nowcoder").strip().lower() or "nowcoder"
    detail_kind = str(record.get("detail_kind") or "unknown").strip().lower() or "unknown"
    content_id = str(record.get("content_id") or "").strip()
    detail_lookup_key = str(record.get("detail_lookup_key") or "").strip()
    source_url = build_source_url(record, detail_kind, content_id)
    source_id = build_source_id(
        platform=platform,
        detail_kind=detail_kind,
        content_id=content_id,
        detail_lookup_key=detail_lookup_key,
        source_url=source_url,
    )
    if not source_id:
        return None, "missing_source_id"

    # 优先使用正文；正文缺失时回退到 summary 并做 HTML 粗清洗
    raw_text = str(record.get("body_text") or "").strip()
    if not raw_text:
        raw_text = strip_html(str(record.get("summary") or ""))

    # 正文清洗与最小长度门槛
    interview_text = clean_interview_text(raw_text)
    if not interview_text:
        return None, "empty_interview_text"
    if non_whitespace_length(interview_text) < min_text_length:
        return None, "interview_text_too_short"

    title = clean_title(record.get("title"))
    # 从标题/正文前若干行提取公司与岗位提示
    company_hint = infer_company_hint(title=title, interview_text=interview_text)
    role_hint = infer_role_hint(title=title, interview_text=interview_text)
    publish_time = str(record.get("created_at") or "").strip() or str(record.get("updated_at") or "").strip()
    crawl_query = str(record.get("query") or "").strip()

    return (
        {
            "source_id": source_id,
            "source_url": source_url,
            "platform": platform,
            "publish_time": publish_time,
            "company_hint": company_hint,
            "role_hint": role_hint,
            "title": title,
            "interview_text": interview_text,
            "crawl_query": crawl_query,
        },
        None,
    )


def main() -> int:
    """读取输入 JSONL，执行过滤+清洗，输出 preclean JSONL 与 manifest。"""

    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_jsonl = output_dir / "interview_posts_for_llm.jsonl"
    output_manifest = output_dir / "interview_posts_for_llm_manifest.json"

    detail_kinds_raw = [str(item).strip().lower() for item in (args.detail_kinds or []) if str(item).strip()]
    # 传 '*' 时关闭 detail_kind 过滤；否则仅保留指定类型
    allowed_detail_kinds = None if "*" in detail_kinds_raw else set(detail_kinds_raw)

    records: list[dict[str, Any]] = []
    dropped_by_reason: dict[str, int] = {}
    invalid_json_lines = 0
    input_count = 0

    for raw_line in input_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        input_count += 1
        try:
            raw_record = json.loads(raw_line)
        except json.JSONDecodeError:
            # 非法 JSON 行计入统计并跳过
            invalid_json_lines += 1
            dropped_by_reason["invalid_json_line"] = dropped_by_reason.get("invalid_json_line", 0) + 1
            continue

        detail_kind = str(raw_record.get("detail_kind") or "").strip().lower()
        if allowed_detail_kinds is not None and detail_kind not in allowed_detail_kinds:
            # 不在保留类型中，直接过滤
            dropped_by_reason["filtered_detail_kind"] = dropped_by_reason.get("filtered_detail_kind", 0) + 1
            continue

        preclean_record, drop_reason = build_preclean_record(raw_record, min_text_length=args.min_text_length)
        if preclean_record is None:
            # 按原因累计丢弃计数，便于后续调参
            key = drop_reason or "unknown"
            dropped_by_reason[key] = dropped_by_reason.get(key, 0) + 1
            continue
        records.append(preclean_record)

    write_jsonl(output_jsonl, records)

    # 产出运行统计，供 pipeline 与质量排查使用
    dropped_count = max(input_count - len(records), 0)
    manifest = {
        "generated_at": iso_now(),
        "input": str(input_path),
        "output": str(output_jsonl),
        "detail_kinds": sorted(allowed_detail_kinds) if allowed_detail_kinds is not None else ["*"],
        "min_text_length": args.min_text_length,
        "input_count": input_count,
        "kept_count": len(records),
        "dropped_count": dropped_count,
        "invalid_json_lines": invalid_json_lines,
        "dropped_by_reason": dropped_by_reason,
    }
    dump_json(output_manifest, manifest)

    print(
        json.dumps(
            {
                "kept_count": len(records),
                "dropped_count": dropped_count,
                "output": str(output_jsonl),
                "manifest": str(output_manifest),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
