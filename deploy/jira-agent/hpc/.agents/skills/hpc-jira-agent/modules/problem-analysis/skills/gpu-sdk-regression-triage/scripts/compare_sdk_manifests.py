#!/usr/bin/env python3
import argparse
import csv
import sys
from pathlib import Path


FIELDS = ["type", "bytes", "sha256", "symlink", "build_id", "soname", "needed"]


def load(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        expected = ["path", *FIELDS]
        if reader.fieldnames != expected:
            raise SystemExit(f"清单列格式不符合要求，文件 {path}：{reader.fieldnames}")

        rows: dict[str, dict[str, str]] = {}
        for row in reader:
            name = row["path"]
            if name in rows:
                raise SystemExit(f"清单中存在重复路径，文件 {path}：{name}")
            rows[name] = row
        return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="比较正常与异常 GPU SDK 的 TSV 文件清单")
    parser.add_argument("good", type=Path, help="正常版本的 TSV 清单")
    parser.add_argument("bad", type=Path, help="异常版本的 TSV 清单")
    parser.add_argument("--include-same", action="store_true", help="同时输出没有差异的文件")
    args = parser.parse_args()

    good = load(args.good)
    bad = load(args.bad)
    columns = [
        "status",
        "path",
        "changed_fields",
        *[f"good_{field}" for field in FIELDS],
        *[f"bad_{field}" for field in FIELDS],
    ]
    writer = csv.DictWriter(sys.stdout, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    counts = {"changed": 0, "good-only": 0, "bad-only": 0, "same": 0}

    for name in sorted(good.keys() | bad.keys()):
        before = good.get(name)
        after = bad.get(name)

        if before is None:
            status = "bad-only"
            changed = FIELDS
        elif after is None:
            status = "good-only"
            changed = FIELDS
        else:
            changed = [field for field in FIELDS if before[field] != after[field]]
            status = "changed" if changed else "same"

        counts[status] += 1
        if status == "same" and not args.include_same:
            continue

        row = {"status": status, "path": name, "changed_fields": ",".join(changed)}
        row.update({f"good_{field}": before[field] if before else "" for field in FIELDS})
        row.update({f"bad_{field}": after[field] if after else "" for field in FIELDS})
        writer.writerow(row)

    summary = " ".join(f"{key}={counts[key]}" for key in ["changed", "good-only", "bad-only", "same"])
    print(summary, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
