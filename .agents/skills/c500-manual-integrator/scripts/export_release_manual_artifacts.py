#!/usr/bin/env python3
"""Export and lightly normalize C500 manual artifacts from release_system.db."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path


KINDS = ("manual", "ai4sci")


@dataclass
class ParsedEntry:
    app: str
    versions: list[str]
    official_url: str
    intro: str
    selected_markdown: str
    headings: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path, help="Path to release_system.db")
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/c500_manual_artifacts"))
    parser.add_argument("--release-id", help="Release id to export; default prefers latest final artifact")
    return parser.parse_args()


def natural_key(value: str) -> list[object]:
    parts = re.split(r"(\d+)", value.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def select_artifact(conn: sqlite3.Connection, kind: str, release_id: str | None) -> dict[str, object]:
    conn.row_factory = sqlite3.Row
    if release_id:
        row = conn.execute(
            """
            SELECT a.release_id, r.name AS release_name, r.maca_version, a.kind, a.name,
                   a.content, a.final, a.generated_at
            FROM artifacts a
            LEFT JOIN releases r ON r.id = a.release_id
            WHERE a.kind = ? AND a.release_id = ?
            ORDER BY a.final DESC, a.generated_at DESC
            LIMIT 1
            """,
            (kind, release_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT a.release_id, r.name AS release_name, r.maca_version, a.kind, a.name,
                   a.content, a.final, a.generated_at
            FROM artifacts a
            LEFT JOIN releases r ON r.id = a.release_id
            WHERE a.kind = ?
            ORDER BY a.final DESC, a.generated_at DESC
            LIMIT 1
            """,
            (kind,),
        ).fetchone()
    if row is None:
        raise SystemExit(f"No artifact found for kind={kind!r}")
    return dict(row)


def split_markdown_entries(markdown: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", markdown, flags=re.MULTILINE))
    entries: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        entries.append((match.group(1).strip(), markdown[start:end].strip()))
    return entries


def extract_version(body: str) -> str:
    match = re.search(r"^版本：\s*(.+?)\s*$", body, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def extract_official_url(body: str) -> str:
    match = re.search(r"^官方网址：\s*(.+?)\s*$", body, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def strip_version_from_title(title: str, version: str) -> str:
    if not version:
        return title.strip()
    variants = {version, version.lstrip("vV")}
    for variant in sorted(variants, key=len, reverse=True):
        if variant and title.endswith(" " + variant):
            return title[: -len(variant)].strip()
    return title.strip()


def extract_intro(body: str) -> str:
    stop = re.search(
        r"^(版本：|官方网址：|\*\*镜像使用方法：\*\*|\*\*二进制包使用方法：\*\*|\*\*环境搭建：\*\*|\*\*测试方法：\*\*)",
        body,
        flags=re.MULTILINE,
    )
    intro = body[: stop.start()].strip() if stop else body.strip()
    return intro


def merge_entries(markdown: str) -> list[ParsedEntry]:
    grouped: dict[str, ParsedEntry] = {}
    for heading, body in split_markdown_entries(markdown):
        version = extract_version(body)
        app = strip_version_from_title(heading, version)
        key = re.sub(r"[\s_\-]+", "", app).lower()
        official_url = extract_official_url(body)
        if key not in grouped:
            grouped[key] = ParsedEntry(
                app=app,
                versions=[],
                official_url=official_url,
                intro=extract_intro(body),
                selected_markdown=body,
                headings=[heading],
            )
        entry = grouped[key]
        if version and version not in entry.versions:
            entry.versions.append(version)
        if official_url and not entry.official_url:
            entry.official_url = official_url
        entry.headings.append(heading) if heading not in entry.headings else None
    for entry in grouped.values():
        entry.versions.sort(key=natural_key)
    return sorted(grouped.values(), key=lambda item: item.app.lower())


def rst_heading(title: str, marker: str) -> str:
    width = sum(2 if "\u4e00" <= char <= "\u9fff" else 1 for char in title)
    return f"{title}\n{marker * max(width, len(title))}\n"


def markdown_to_rst(markdown: str) -> str:
    lines = markdown.splitlines()
    output: list[str] = []
    in_code = False
    code_lang = "bash"
    for line in lines:
        fence = re.match(r"^```(\w+)?\s*$", line)
        if fence:
            if not in_code:
                code_lang = fence.group(1) or "bash"
                output.append(f".. code-block:: {code_lang}")
                output.append("")
                in_code = True
            else:
                output.append("")
                in_code = False
            continue
        if in_code:
            output.append("   " + line)
        elif line.startswith(">"):
            output.append("   " + line.lstrip("> "))
        else:
            output.append(line)
    return "\n".join(output).strip() + "\n"


def hpc_rst_draft(entries: list[ParsedEntry]) -> str:
    chunks = [rst_heading("METAX DockerHub HPC APP", "=")]
    for entry in entries:
        chunks.append(rst_heading(entry.app, "~"))
        if entry.intro:
            chunks.append(entry.intro.strip() + "\n")
        if entry.versions:
            chunks.append(f"版本：{'、'.join(entry.versions)}\n")
        if entry.official_url:
            chunks.append(f"官方网址：{entry.official_url}\n")
    return "\n".join(chunks).strip() + "\n"


def ai4sci_rst_draft(entries: list[ParsedEntry]) -> str:
    chunks: list[str] = []
    for entry in entries:
        chunks.append(rst_heading(entry.app, "~"))
        body = entry.selected_markdown
        if entry.versions:
            body = re.sub(
                r"^版本：\s*.+?$",
                f"版本：{'、'.join(entry.versions)}",
                body,
                count=1,
                flags=re.MULTILINE,
            )
        chunks.append(markdown_to_rst(body))
    return "\n".join(chunks).strip() + "\n"


def write_outputs(out_dir: Path, kind: str, artifact: dict[str, object], entries: list[ParsedEntry]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / kind
    prefix.with_suffix(".raw.md").write_text(str(artifact["content"]), encoding="utf-8")
    prefix.with_suffix(".artifact.json").write_text(
        json.dumps({k: v for k, v in artifact.items() if k != "content"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    prefix.with_suffix(".entries.json").write_text(
        json.dumps([asdict(entry) for entry in entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if kind == "manual":
        (out_dir / "manual.hpc_chapter10_draft.rst").write_text(hpc_rst_draft(entries), encoding="utf-8")
    elif kind == "ai4sci":
        (out_dir / "ai4sci.full_draft.rst").write_text(ai4sci_rst_draft(entries), encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not args.db.is_file():
        raise SystemExit(f"DB not found: {args.db}")
    conn = sqlite3.connect(args.db)
    for kind in KINDS:
        artifact = select_artifact(conn, kind, args.release_id)
        entries = merge_entries(str(artifact["content"]))
        write_outputs(args.out_dir, kind, artifact, entries)
        final_note = "final" if artifact["final"] else "non-final"
        print(
            f"{kind}: {len(entries)} merged entries from {artifact['name']} "
            f"({artifact['release_id']}, {final_note}, {artifact['generated_at']})"
        )
    print(f"outputs: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
