from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate old Nowcoder raw layout into a flat json/ directory.")
    parser.add_argument(
        "--output-dir",
        default="data/nowcoder",
        help="Root directory of the Nowcoder crawl output.",
    )
    parser.add_argument(
        "--delete-empty-raw-dirs",
        action="store_true",
        help="Remove empty legacy raw/ directories after migration.",
    )
    return parser


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def move_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if source.read_bytes() != target.read_bytes():
            raise RuntimeError(f"Refusing to overwrite different file: {target}")
        source.unlink()
        return
    source.replace(target)


def format_rel(path: Path) -> str:
    return str(path)


def migrate_search_files(output_dir: Path, json_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    search_root = output_dir / "raw" / "search"
    if not search_root.exists():
        return mapping

    for source in sorted(search_root.glob("*/*.json")):
        query_slug = source.parent.name
        page_token = source.stem
        if not page_token.startswith("page_"):
            raise RuntimeError(f"Unexpected search filename: {source.name}")
        page = page_token.removeprefix("page_")
        target = json_dir / f"search__{query_slug}__page_{page}.json"
        mapping[format_rel(source)] = format_rel(target)
        move_file(source, target)
    return mapping


def migrate_detail_files(output_dir: Path, json_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    detail_root = output_dir / "raw" / "detail"
    if not detail_root.exists():
        return mapping

    for detail_kind_dir in sorted(detail_root.iterdir()):
        if not detail_kind_dir.is_dir():
            continue
        detail_kind = detail_kind_dir.name
        for source in sorted(detail_kind_dir.glob("*.json")):
            lookup_key = source.stem
            target = json_dir / f"detail__{detail_kind}__{lookup_key}.json"
            mapping[format_rel(source)] = format_rel(target)
            move_file(source, target)
    return mapping


def rewrite_paths_in_jsonl(jsonl_path: Path, mapping: dict[str, str]) -> int:
    if not jsonl_path.exists():
        return 0

    rewritten = 0
    records: list[str] = []
    for raw_line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        record = json.loads(raw_line)
        raw_paths = record.get("raw_paths") or {}
        changed = False
        for key in ("search", "detail"):
            current = raw_paths.get(key)
            if current in mapping:
                raw_paths[key] = mapping[current]
                changed = True
        if changed:
            record["raw_paths"] = raw_paths
            rewritten += 1
        records.append(json.dumps(record, ensure_ascii=False))

    jsonl_path.write_text("\n".join(records) + ("\n" if records else ""), encoding="utf-8")
    return rewritten


def rewrite_manifest(manifest_path: Path, mapping: dict[str, str]) -> bool:
    if not manifest_path.exists():
        return False

    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        return False

    changed = False
    raw_search_files = manifest.get("raw_search_files")
    if isinstance(raw_search_files, list):
        new_files = []
        for item in raw_search_files:
            if isinstance(item, str) and item in mapping:
                new_files.append(mapping[item])
                changed = True
            else:
                new_files.append(item)
        manifest["raw_search_files"] = new_files

    if changed:
        dump_json(manifest_path, manifest)
    return changed


def prune_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda item: len(item.parts), reverse=True):
        if any(directory.iterdir()):
            continue
        directory.rmdir()
    if root.exists() and not any(root.iterdir()):
        root.rmdir()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    json_dir = output_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)

    mapping: dict[str, str] = {}
    mapping.update(migrate_search_files(output_dir=output_dir, json_dir=json_dir))
    mapping.update(migrate_detail_files(output_dir=output_dir, json_dir=json_dir))

    jsonl_path = output_dir / "normalized" / "interview_posts.jsonl"
    rewritten_records = rewrite_paths_in_jsonl(jsonl_path=jsonl_path, mapping=mapping)
    manifest_changed = rewrite_manifest(manifest_path=output_dir / "manifest.json", mapping=mapping)

    if args.delete_empty_raw_dirs:
        prune_empty_dirs(output_dir / "raw")

    print(
        json.dumps(
            {
                "migrated_files": len(mapping),
                "rewritten_records": rewritten_records,
                "manifest_updated": manifest_changed,
                "json_dir": str(json_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
