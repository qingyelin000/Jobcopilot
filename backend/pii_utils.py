"""轻量 PII redaction：手机号 / 邮箱 / 中国大陆身份证 / 显式中文姓名占位。

设计目标：在把简历/JD 文本传给外部 LLM 前替换敏感字段为占位符，
LLM 回包后由调用方按需 unredact。原样保留结构，避免把整段直接外发。
"""

from __future__ import annotations

import re
from typing import Tuple

_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9}|\+?\d{1,3}[- ]?\d{3,4}[- ]?\d{4,8})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ID_RE = re.compile(r"(?<!\d)([1-9]\d{14}|[1-9]\d{16}[\dXx])(?!\d)")
_URL_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)


def redact(text: str) -> Tuple[str, dict[str, str]]:
    """返回 (redacted_text, mapping)。mapping 形如 {"<PHONE_1>": "138..."}。"""

    mapping: dict[str, str] = {}
    counters = {"PHONE": 0, "EMAIL": 0, "ID": 0, "URL": 0}

    def _replace(kind: str, m: re.Match) -> str:
        counters[kind] += 1
        token = f"<{kind}_{counters[kind]}>"
        mapping[token] = m.group(0)
        return token

    out = _EMAIL_RE.sub(lambda m: _replace("EMAIL", m), text or "")
    out = _ID_RE.sub(lambda m: _replace("ID", m), out)
    out = _PHONE_RE.sub(lambda m: _replace("PHONE", m), out)
    out = _URL_RE.sub(lambda m: _replace("URL", m), out)
    return out, mapping


def unredact(text: str, mapping: dict[str, str]) -> str:
    out = text or ""
    for token, original in mapping.items():
        out = out.replace(token, original)
    return out


__all__ = ["redact", "unredact"]
