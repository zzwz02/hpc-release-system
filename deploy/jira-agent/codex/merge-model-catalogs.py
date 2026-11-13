#!/usr/bin/env python3
"""Combine Codex's default model cache with explicit third-party catalogs.

Run on server B, then point the app-server's startup model_catalog_json here.
Custom models remain addressable by slug but are hidden from the default
provider's picker; the website reads their original catalogs separately.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile


def merge_catalogs(base, extras):
    models = []
    seen = set()
    for index, catalog in enumerate([base, *extras]):
        entries = catalog.get("models")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Each catalog must contain a nonempty models array")
        for entry in entries:
            slug = entry.get("slug")
            if not isinstance(slug, str) or not slug or slug in seen:
                raise ValueError("Missing or duplicate model slug: " + str(slug))
            seen.add(slug)
            models.append(dict(entry, visibility="hide") if index else dict(entry))
    return {"models": models}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--extra", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = [args.base, *args.extra]
    if args.output.resolve() in [path.resolve() for path in sources]:
        parser.error("Output must not overwrite a source catalog")
    result = merge_catalogs(json.loads(args.base.read_text()),
                            [json.loads(path.read_text()) for path in args.extra])
    with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent,
                                     prefix=".catalog-", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(result, handle, ensure_ascii=False, indent=2)
    try:
        os.replace(temporary, args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print("Wrote %d models to %s" % (len(result["models"]), args.output))


if __name__ == "__main__":
    main()
