from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

SEARCH_API_URL = "https://gw-c.nowcoder.com/api/sparta/pc/search"
LONG_CONTENT_DETAIL_API_URL = "https://gw-c.nowcoder.com/api/sparta/detail/content-data/detail/{content_id}"
MOMENT_DETAIL_API_URL = "https://gw-c.nowcoder.com/api/sparta/detail/moment-data/detail/{uuid}"
CONTENT_TYPE_LONG_CONTENT = 250
CONTENT_TYPE_MOMENT = 74

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nowcoder.com",
}

DEFAULT_QUERIES = [
    "Java 面经",
    "后端 面经",
    "算法 面经",
]


class NowcoderCrawlerError(RuntimeError):
    pass


class HTMLTextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "article",
        "blockquote",
        "br",
        "dd",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def get_text(self) -> str:
        text = unescape("".join(self.parts))
        text = text.replace("\r", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()


@dataclass(frozen=True)
class SearchHit:
    query: str
    page: int
    content_type: int
    detail_kind: str
    public_id: str
    detail_lookup_key: str
    title: str
    snippet: str
    public_url: str | None
    author_nickname: str | None
    author_profile: str | None
    created_at: str | None
    tags: list[str]
    search_record: dict[str, Any]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawl Nowcoder interview posts via public JSON APIs.")
    parser.add_argument(
        "--query",
        action="append",
        dest="queries",
        help="Search query. Repeat this flag to crawl multiple keyword groups.",
    )
    parser.add_argument("--page-limit", type=int, default=1, help="How many search pages to crawl per query.")
    parser.add_argument("--page-size", type=int, default=20, help="Search page size. Nowcoder currently accepts 20.")
    parser.add_argument("--max-items", type=int, default=0, help="Stop after N unique items. 0 means no limit.")
    parser.add_argument(
        "--output-dir",
        default="data/nowcoder",
        help="Output directory for raw API responses and normalized JSONL.",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.5, help="Delay between detail requests.")
    parser.add_argument("--force", action="store_true", help="Refetch and overwrite existing raw detail files.")
    return parser


def timestamp_ms_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(numeric / 1000, tz=UTC).isoformat()


def rich_text_to_plain_text(value: str | None) -> str:
    if not value:
        return ""
    extractor = HTMLTextExtractor()
    extractor.feed(value)
    extractor.close()
    return extractor.get_text()


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = unescape(value).replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def slugify_query(query: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", query.strip())
    return slug.strip("_") or "query"


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_search_filename(query: str, page: int) -> str:
    return f"search__{slugify_query(query)}__page_{page}.json"


def build_detail_filename(hit: SearchHit) -> str:
    return f"detail__{hit.detail_kind}__{hit.detail_lookup_key}.json"


class NowcoderCrawler:
    def __init__(self, output_dir: Path, sleep_seconds: float, force: bool = False) -> None:
        self.output_dir = output_dir
        self.sleep_seconds = sleep_seconds
        self.force = force
        self.raw_json_dir = self.output_dir / "json"
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def search_posts(self, query: str, page: int, page_size: int) -> tuple[list[SearchHit], dict[str, Any]]:
        payload = {
            "query": query,
            "type": "post",
            "page": page,
            "pageSize": page_size,
            "searchType": "undefined",
            "subType": 0,
        }
        encoded_query = quote(query)
        response = self.session.post(
            SEARCH_API_URL,
            json=payload,
            headers={"Referer": f"https://www.nowcoder.com/search/post?query={encoded_query}&type=post"},
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("success"):
            raise NowcoderCrawlerError(f"Search API failed for query={query!r}: {result.get('msg')}")

        records = result.get("data", {}).get("records", [])
        hits: list[SearchHit] = []
        for record in records:
            hit = self._normalize_search_hit(query=query, page=page, record=record)
            if hit is not None:
                hits.append(hit)
        return hits, result

    def _normalize_search_hit(self, query: str, page: int, record: dict[str, Any]) -> SearchHit | None:
        content_type = int(record.get("contentType") or 0)
        user_brief = record.get("userBrief") or {}

        # Nowcoder search results expose a stable contentType field.
        # We classify long posts vs feed moments here before hitting detail APIs.
        if content_type == CONTENT_TYPE_LONG_CONTENT:
            content_data = record.get("contentData") or {}
            public_id = str(content_data.get("id") or "").strip()
            if not public_id:
                return None
            snippet = clean_text(content_data.get("content")) or rich_text_to_plain_text(content_data.get("richText"))
            return SearchHit(
                query=query,
                page=page,
                content_type=content_type,
                detail_kind="long_content",
                public_id=public_id,
                detail_lookup_key=public_id,
                title=clean_text(content_data.get("title")) or clean_text(record.get("title")),
                snippet=snippet,
                public_url=f"https://www.nowcoder.com/discuss/{public_id}",
                author_nickname=user_brief.get("nickname"),
                author_profile=user_brief.get("authDisplayInfo"),
                created_at=timestamp_ms_to_iso(content_data.get("createTime")),
                tags=[item.get("content") for item in (record.get("subjectData") or []) if item.get("content")],
                search_record=record,
            )

        if content_type == CONTENT_TYPE_MOMENT:
            moment_data = record.get("momentData") or {}
            public_id = str(moment_data.get("id") or "").strip()
            detail_lookup_key = str(moment_data.get("uuid") or "").strip()
            if not public_id or not detail_lookup_key:
                return None
            return SearchHit(
                query=query,
                page=page,
                content_type=content_type,
                detail_kind="moment",
                public_id=public_id,
                detail_lookup_key=detail_lookup_key,
                title=clean_text(moment_data.get("title")) or clean_text(record.get("title")),
                snippet=rich_text_to_plain_text(moment_data.get("content")),
                public_url=None,
                author_nickname=user_brief.get("nickname"),
                author_profile=user_brief.get("authDisplayInfo"),
                created_at=timestamp_ms_to_iso(moment_data.get("createTime")),
                tags=[item.get("content") for item in (record.get("subjectData") or []) if item.get("content")],
                search_record=record,
            )

        return None

    def fetch_detail(self, hit: SearchHit) -> tuple[dict[str, Any], Path]:
        detail_path = self.raw_json_dir / build_detail_filename(hit)
        if detail_path.exists() and not self.force:
            return load_json(detail_path), detail_path

        if hit.detail_kind == "long_content":
            url = LONG_CONTENT_DETAIL_API_URL.format(content_id=hit.detail_lookup_key)
            referer = hit.public_url or "https://www.nowcoder.com/"
        elif hit.detail_kind == "moment":
            url = MOMENT_DETAIL_API_URL.format(uuid=hit.detail_lookup_key)
            referer = "https://www.nowcoder.com/"
        else:
            raise NowcoderCrawlerError(f"Unsupported detail kind: {hit.detail_kind}")

        response = self.session.get(url, headers={"Referer": referer}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise NowcoderCrawlerError(
                f"Detail API failed for {hit.detail_kind}:{hit.detail_lookup_key}: {payload.get('msg')}"
            )

        dump_json(detail_path, payload)
        if self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)
        return payload, detail_path

    def normalize_detail(
        self,
        hit: SearchHit,
        detail_payload: dict[str, Any],
        detail_path: Path,
        search_path: Path,
    ) -> dict[str, Any]:
        data = detail_payload.get("data") or {}
        user_brief = data.get("userBrief") or hit.search_record.get("userBrief") or {}

        if hit.detail_kind == "long_content":
            body_text = rich_text_to_plain_text(data.get("richText")) or clean_text(data.get("content"))
            view_count = nested_get(hit.search_record, ["frequencyData", "viewCnt"])
            comment_count = nested_get(hit.search_record, ["frequencyData", "totalCommentCnt"])
            like_count = nested_get(hit.search_record, ["frequencyData", "likeCnt"])
            tags = [item.get("content") for item in (data.get("subjectData") or []) if item.get("content")]
            return {
                "source_platform": "nowcoder",
                "source_kind": "interview_post",
                "content_type": hit.content_type,
                "detail_kind": hit.detail_kind,
                "query": hit.query,
                "search_page": hit.page,
                "content_id": hit.public_id,
                "detail_lookup_key": hit.detail_lookup_key,
                "source_url": hit.public_url,
                "title": clean_text(data.get("title")) or hit.title,
                "summary": clean_text(data.get("content")) or hit.snippet,
                "body_text": body_text,
                "rich_text": data.get("richText"),
                "author": {
                    "user_id": user_brief.get("userId"),
                    "nickname": user_brief.get("nickname"),
                    "profile": user_brief.get("authDisplayInfo"),
                    "education": user_brief.get("educationInfo"),
                    "major": user_brief.get("secondMajorName"),
                },
                "tags": tags or hit.tags,
                "metrics": {
                    "view_count": view_count,
                    "comment_count": comment_count,
                    "like_count": like_count,
                },
                "location": data.get("ip4Location"),
                "created_at": timestamp_ms_to_iso(data.get("createTime")) or hit.created_at,
                "updated_at": timestamp_ms_to_iso(data.get("editTime")),
                "raw_paths": {
                    "search": str(search_path),
                    "detail": str(detail_path),
                },
            }

        body_text = rich_text_to_plain_text(data.get("content")) or clean_text(data.get("content"))
        tags = [item.get("content") for item in (data.get("subjectData") or []) if item.get("content")]
        return {
            "source_platform": "nowcoder",
            "source_kind": "interview_post",
            "content_type": hit.content_type,
            "detail_kind": hit.detail_kind,
            "query": hit.query,
            "search_page": hit.page,
            "content_id": hit.public_id,
            "detail_lookup_key": hit.detail_lookup_key,
            "source_url": hit.public_url,
            "title": clean_text(data.get("title")) or hit.title,
            "summary": hit.snippet,
            "body_text": body_text,
            "rich_text": data.get("content"),
            "author": {
                "user_id": user_brief.get("userId"),
                "nickname": user_brief.get("nickname"),
                "profile": user_brief.get("authDisplayInfo"),
                "education": user_brief.get("educationInfo"),
                "major": user_brief.get("secondMajorName"),
            },
            "tags": tags or hit.tags,
            "metrics": {
                "view_count": data.get("viewCount"),
                "comment_count": data.get("commentCount"),
                "like_count": data.get("likeCount"),
            },
            "location": data.get("ip4Location"),
            "created_at": timestamp_ms_to_iso(data.get("createTime")) or hit.created_at,
            "updated_at": timestamp_ms_to_iso(data.get("editTime")),
            "raw_paths": {
                "search": str(search_path),
                "detail": str(detail_path),
            },
        }


def nested_get(payload: dict[str, Any], path: list[str]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    queries = args.queries or DEFAULT_QUERIES
    output_dir = Path(args.output_dir)
    crawler = NowcoderCrawler(output_dir=output_dir, sleep_seconds=args.sleep_seconds, force=args.force)

    normalized_records: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    raw_search_paths: list[str] = []

    for query in queries:
        for page in range(1, args.page_limit + 1):
            hits, raw_search = crawler.search_posts(query=query, page=page, page_size=args.page_size)
            search_path = crawler.raw_json_dir / build_search_filename(query=query, page=page)
            dump_json(search_path, raw_search)
            raw_search_paths.append(str(search_path))

            if not hits:
                continue

            for hit in hits:
                unique_key = (hit.detail_kind, hit.detail_lookup_key)
                if unique_key in seen_keys:
                    continue

                detail_payload, detail_path = crawler.fetch_detail(hit)
                normalized_records.append(
                    crawler.normalize_detail(
                        hit=hit,
                        detail_payload=detail_payload,
                        detail_path=detail_path,
                        search_path=search_path,
                    )
                )
                seen_keys.add(unique_key)

                if args.max_items and len(normalized_records) >= args.max_items:
                    break

            if args.max_items and len(normalized_records) >= args.max_items:
                break

        if args.max_items and len(normalized_records) >= args.max_items:
            break

    jsonl_path = output_dir / "normalized" / "interview_posts.jsonl"
    write_jsonl(jsonl_path, normalized_records)

    manifest = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "queries": queries,
        "page_limit": args.page_limit,
        "page_size": args.page_size,
        "record_count": len(normalized_records),
        "raw_search_files": raw_search_paths,
        "normalized_jsonl": str(jsonl_path),
    }
    dump_json(output_dir / "manifest.json", manifest)

    print(f"Saved {len(normalized_records)} normalized records to {jsonl_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
