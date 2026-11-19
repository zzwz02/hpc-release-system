"""Compatibility CSV presentation, matching the web table's shared column contract."""
from __future__ import annotations

import csv
import io
import json

from app.domain.shared_metadata import mapping_section

TABLE = mapping_section("compatibility_table")
UNIVERSAL = mapping_section("universal_component_group")
UNIVERSAL_MEMBERS = mapping_section("universal_components")
SOURCE_LABELS = mapping_section("publication_manifest_sources")


def cells(row):
    result = []
    for key, _label in TABLE["columns"]:
        value = row.get(key, "")
        if key == "manifest_source":
            value = SOURCE_LABELS.get(value, {}).get("label", value)
        elif key == "arch":
            value = TABLE["architecture_labels"].get(value, value)
        elif key == "image_config":
            value = " · ".join(row[key] for key in ("image_target", "python_label", "pytorch_label") if row.get(key))
        elif isinstance(value, list):
            value = ", ".join(value)
        result.append("" if value is None else str(value))
    return result


def group_gpu_rows(rows):
    groups = {}
    ignored = {*TABLE["gpu_fields"], *TABLE["group_ignored_fields"]}
    for row in rows:
        key = json.dumps({key: value for key, value in row.items() if key not in ignored}, sort_keys=True)
        if key not in groups:
            groups[key] = ({**row}, [])
        _item, chips = groups[key]
        for chip in [row["GPU"]] if row.get("GPU") else row.get("chips") or []:
            if chip not in chips:
                chips.append(chip)
    return [{**row, "GPU": ", ".join(chips), "chips": chips} for row, chips in groups.values()]


def export_csv(rows, *, component="", arch="", os="", chip="", search=""):
    universal = component == UNIVERSAL["key"] or component.casefold() in UNIVERSAL_MEMBERS
    needle = search.strip().lower()
    selected = [row for row in rows
                if (not component or universal or component in row.get("dependencies", []))
                and (not arch or row.get("arch") == arch)
                and (not os or row.get("os") == os)
                and (not chip or chip in (row.get("chips") or []))
                and (not needle or needle in " ".join([row.get("app_id", ""), *cells(row)]).lower())]
    output = io.StringIO(newline="")
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow([label for _key, label in TABLE["columns"]])
    writer.writerows(cells(row) for row in group_gpu_rows(selected))
    return output.getvalue()
