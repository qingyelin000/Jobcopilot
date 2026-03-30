from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - runtime guard
    load_dotenv = None


QUESTION_TYPES = {
    "coding",
    "backend_foundation",
    "project_or_system_design",
    "behavioral",
    "general",
}

NORMALIZE_KEY_PATTERN = re.compile(
    r"[\s\"'`\u2018\u2019\u201c\u201d\u3001\u3002\uFF0C\uFF01\uFF1F\uFF08\uFF09\uFF1A\uFF1B,\.!\?():;\-_/\\]+"
)
MULTI_SPACE_PATTERN = re.compile(r"[ \t\u3000]+")
MULTI_BLANK_LINE_PATTERN = re.compile(r"\n{3,}")
LEADING_NUMBERING_PATTERN = re.compile(r"^\s*(?:\d+|[一二三四五六七八九十]+)[\.\)）:：、]\s*")
CODE_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)

SYSTEM_PROMPT = """
你是资深面试题结构化抽取器。请从一条面经帖子中抽取“可检索的面试问题”。

硬性要求：
1. 仅输出一个 JSON 对象，不要输出 markdown，不要输出解释。
2. JSON 字段必须是：
   - company: string
   - role: string
   - publish_time: string
   - questions: array
   - missing_fields: array[string]
   - confidence: number (0~1)
3. questions 每一项必须是：
   - question_text: string
   - question_type: enum["coding","backend_foundation","project_or_system_design","behavioral","general"]
4. 只保留“面试官会问的问题”。去掉广告、链接、经验总结口号、非问题句。
5. 问题要简洁去噪，可适度改写成明确问句，但不能改变原意。
6. 如果不确定类别，用 general。
7. company/role/publish_time 尽量从输入中复用；确实缺失再留空并在 missing_fields 标注。
""".strip()


def iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract structured interview questions from pre-cleaned Nowcoder posts via DeepSeek.")
    parser.add_argument(
        "--input",
        default="data/nowcoder/preclean/interview_posts_for_llm.jsonl",
        help="Input pre-clean JSONL path.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/nowcoder/llm",
        help="Output directory for LLM extraction artifacts.",
    )
    parser.add_argument(
        "--model",
        default="mimo-v2-flash",
        help="Model name for chat completions.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("MIMO_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com",
        help="OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--api-key-env",
        default="MIMO_V2_PRO_API_KEY",
        help="Environment variable name that stores API key.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=90.0,
        help="HTTP timeout for each model call.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Max retries after first attempt for API/parse failures.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.2,
        help="Sleep between successful requests.",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=0,
        help="Process at most N posts. 0 means all.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="Sampling temperature.",
    )
    return parser


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_key(text: str) -> str:
    lowered = text.casefold().strip()
    return NORMALIZE_KEY_PATTERN.sub("", lowered)


def normalize_question_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = LEADING_NUMBERING_PATTERN.sub("", text)
    text = text.replace("\r", "\n")
    text = MULTI_SPACE_PATTERN.sub(" ", text)
    text = MULTI_BLANK_LINE_PATTERN.sub("\n\n", text)
    text = text.strip(" \t\r\n-_|")
    if len(text) > 240:
        text = text[:240].rstrip()
    return text


def normalize_question_type(value: Any) -> str:
    raw = str(value or "").strip().casefold()
    if raw in QUESTION_TYPES:
        return raw

    alias_map = {
        "algorithm": "coding",
        "code": "coding",
        "coding_question": "coding",
        "backend": "backend_foundation",
        "backend_basic": "backend_foundation",
        "foundation": "backend_foundation",
        "system_design": "project_or_system_design",
        "project": "project_or_system_design",
        "behavior": "behavioral",
        "hr": "behavioral",
    }
    return alias_map.get(raw, "general")


def sanitize_text_field(value: Any, max_len: int = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = MULTI_SPACE_PATTERN.sub(" ", text)
    return text[:max_len]


def parse_json_object(text: str) -> dict[str, Any]:
    payload = text.strip()
    if not payload:
        raise ValueError("empty_response")

    fenced = CODE_FENCE_PATTERN.search(payload)
    if fenced:
        payload = fenced.group(1).strip()

    try:
        loaded = json.loads(payload)
        if isinstance(loaded, dict):
            return loaded
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for idx, char in enumerate(payload):
        if char != "{":
            continue
        try:
            loaded, _end = decoder.raw_decode(payload[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded

    raise ValueError("response_not_json_object")


def build_messages(post: dict[str, Any]) -> list[dict[str, str]]:
    user_payload = {
        "source_id": post.get("source_id"),
        "source_url": post.get("source_url"),
        "publish_time": post.get("publish_time"),
        "company_hint": post.get("company_hint"),
        "role_hint": post.get("role_hint"),
        "title": post.get("title"),
        "interview_text": post.get("interview_text"),
        "question_type_enum": sorted(QUESTION_TYPES),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def call_chat_completions(
    *,
    session: requests.Session,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout_seconds: float,
    temperature: float,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }

    response = session.post(url, headers=headers, json=payload, timeout=timeout_seconds)
    if response.status_code >= 400 and response.status_code in {400, 422}:
        # Some OpenAI-compatible providers reject response_format; retry once without it.
        if "response_format" in response.text.casefold():
            payload.pop("response_format", None)
            response = session.post(url, headers=headers, json=payload, timeout=timeout_seconds)
    response.raise_for_status()

    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError("missing_choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("missing_message_content")
    return content


def normalize_questions(raw_questions: Any) -> list[dict[str, str]]:
    if not isinstance(raw_questions, list):
        return []

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_questions:
        if not isinstance(item, dict):
            continue
        question_text = normalize_question_text(item.get("question_text"))
        if not question_text:
            continue
        normalized_key = normalize_key(question_text)
        if not normalized_key or normalized_key in seen:
            continue
        seen.add(normalized_key)
        normalized.append(
            {
                "question_text": question_text,
                "question_type": normalize_question_type(item.get("question_type")),
            }
        )
    return normalized


def build_structured_record(post: dict[str, Any], llm_payload: dict[str, Any]) -> dict[str, Any]:
    company = sanitize_text_field(llm_payload.get("company")) or sanitize_text_field(post.get("company_hint"))
    role = sanitize_text_field(llm_payload.get("role")) or sanitize_text_field(post.get("role_hint"))
    publish_time = sanitize_text_field(llm_payload.get("publish_time"), max_len=48) or sanitize_text_field(
        post.get("publish_time"),
        max_len=48,
    )

    questions = normalize_questions(llm_payload.get("questions"))
    missing_fields = [
        str(field).strip()
        for field in (llm_payload.get("missing_fields") or [])
        if str(field).strip()
    ]
    for required_field, value in (("company", company), ("role", role), ("publish_time", publish_time)):
        if not value and required_field not in missing_fields:
            missing_fields.append(required_field)

    try:
        confidence = float(llm_payload.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    return {
        "source_id": str(post.get("source_id") or "").strip(),
        "source_url": str(post.get("source_url") or "").strip(),
        "company": company,
        "role": role,
        "publish_time": publish_time,
        "questions": questions,
        "missing_fields": missing_fields,
        "confidence": round(confidence, 6),
    }


def extract_source_content_id(source_id: str) -> str:
    source = source_id.strip()
    if not source:
        return ""
    if ":" not in source:
        return source
    return source.rsplit(":", 1)[-1]


def question_id_from_source_and_key(source_id: str, normalized_key: str) -> str:
    suffix = hashlib.sha1(normalized_key.encode("utf-8")).hexdigest()[:12]
    return f"{source_id}#{suffix}"


def flatten_retrieval_records(structured_record: dict[str, Any]) -> list[dict[str, Any]]:
    source_id = str(structured_record.get("source_id") or "").strip()
    source_content_id = extract_source_content_id(source_id)

    records: list[dict[str, Any]] = []
    for question in structured_record.get("questions") or []:
        question_text = str(question.get("question_text") or "").strip()
        question_type = normalize_question_type(question.get("question_type"))
        normalized_key = normalize_key(question_text)
        if not question_text or not normalized_key:
            continue

        records.append(
            {
                "question_id": question_id_from_source_and_key(source_id, normalized_key),
                "source_content_id": source_content_id,
                "source_id": source_id,
                "source_url": str(structured_record.get("source_url") or "").strip(),
                "company": str(structured_record.get("company") or "").strip(),
                "role": str(structured_record.get("role") or "").strip(),
                "publish_time": str(structured_record.get("publish_time") or "").strip(),
                "question_type": question_type,
                "question_text": question_text,
                "normalized_key": normalized_key,
            }
        )
    return records


def main() -> int:
    if load_dotenv is not None:
        repo_root = Path(__file__).resolve().parents[2]
        load_dotenv(repo_root / ".env", override=False)

    parser = build_parser()
    args = parser.parse_args()

    api_key = os.getenv(args.api_key_env, "").strip()
    resolved_api_key_env = args.api_key_env
    if not api_key:
        for candidate in ("MIMO_V2_PRO_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
            value = os.getenv(candidate, "").strip()
            if value:
                api_key = value
                resolved_api_key_env = candidate
                break
    if not api_key:
        raise EnvironmentError(
            f"Missing API key env: {args.api_key_env}. "
            "Also checked MIMO_V2_PRO_API_KEY/DEEPSEEK_API_KEY/OPENAI_API_KEY."
        )

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    structured_path = output_dir / "structured_posts.jsonl"
    retrieval_path = output_dir / "retrieval_questions.jsonl"
    error_path = output_dir / "error_posts.jsonl"
    manifest_path = output_dir / "llm_extract_manifest.json"

    posts = read_jsonl(input_path)
    if args.max_posts > 0:
        posts = posts[: args.max_posts]

    session = requests.Session()

    structured_records: list[dict[str, Any]] = []
    retrieval_records: list[dict[str, Any]] = []
    error_records: list[dict[str, Any]] = []

    for index, post in enumerate(posts, start=1):
        source_id = str(post.get("source_id") or "").strip()
        source_url = str(post.get("source_url") or "").strip()

        messages = build_messages(post)
        parsed_payload: dict[str, Any] | None = None
        last_error = ""

        for attempt in range(args.max_retries + 1):
            try:
                raw_content = call_chat_completions(
                    session=session,
                    base_url=args.base_url,
                    api_key=api_key,
                    model=args.model,
                    messages=messages,
                    timeout_seconds=args.timeout_seconds,
                    temperature=args.temperature,
                )
                parsed_payload = parse_json_object(raw_content)
                break
            except Exception as exc:  # noqa: BLE001 - collect errors for retry and manual review
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= args.max_retries:
                    break
                time.sleep(min(2.0, 0.5 * (attempt + 1)))

        if parsed_payload is None:
            error_records.append(
                {
                    "source_id": source_id,
                    "source_url": source_url,
                    "reason": "llm_call_or_parse_failed",
                    "detail": last_error,
                }
            )
            continue

        structured = build_structured_record(post, parsed_payload)
        structured_records.append(structured)

        if not structured["questions"]:
            error_records.append(
                {
                    "source_id": source_id,
                    "source_url": source_url,
                    "reason": "empty_questions",
                    "detail": "no valid question extracted",
                }
            )
        elif all(item.get("question_type") == "general" for item in structured["questions"]):
            error_records.append(
                {
                    "source_id": source_id,
                    "source_url": source_url,
                    "reason": "all_general_questions",
                    "detail": "all extracted question_type are general",
                }
            )

        retrieval_records.extend(flatten_retrieval_records(structured))

        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

        if index % 20 == 0:
            print(
                json.dumps(
                    {
                        "processed_posts": index,
                        "structured_count": len(structured_records),
                        "retrieval_count": len(retrieval_records),
                        "error_count": len(error_records),
                    },
                    ensure_ascii=False,
                )
            )

    write_jsonl(structured_path, structured_records)
    write_jsonl(retrieval_path, retrieval_records)
    write_jsonl(error_path, error_records)

    manifest = {
        "generated_at": iso_now(),
        "input": str(input_path),
        "output_dir": str(output_dir),
        "model": args.model,
        "base_url": args.base_url,
        "api_key_env": args.api_key_env,
        "resolved_api_key_env": resolved_api_key_env,
        "max_posts": args.max_posts,
        "max_retries": args.max_retries,
        "temperature": args.temperature,
        "input_post_count": len(posts),
        "structured_post_count": len(structured_records),
        "retrieval_question_count": len(retrieval_records),
        "error_post_count": len(error_records),
        "structured_posts": str(structured_path),
        "retrieval_questions": str(retrieval_path),
        "error_posts": str(error_path),
    }
    dump_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "structured_post_count": len(structured_records),
                "retrieval_question_count": len(retrieval_records),
                "error_post_count": len(error_records),
                "structured_posts": str(structured_path),
                "retrieval_questions": str(retrieval_path),
                "error_posts": str(error_path),
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
