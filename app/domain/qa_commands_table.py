"""CSV display and QA-template projections of the version command worksheet."""
from __future__ import annotations

import csv
import io
import json

from app.domain.shared_metadata import mapping_section

TABLE = mapping_section("qa_test_commands_table")


def export_csv(result, *, template=False):
    columns, rows = result["columns"], result["rows"]
    if not template:
        columns = [column for column in columns if column not in TABLE["hidden_columns"]]
        groups = {}
        for row in rows:
            # Hidden labels remain part of the build identity, just as on the page.
            key = json.dumps({key: value for key, value in row.items() if key not in TABLE["gpu_fields"]}, sort_keys=True)
            if key not in groups:
                groups[key] = (row, [])
            _original, chips = groups[key]
            if row.get("GPU") and row["GPU"] not in chips:
                chips.append(row["GPU"])
        rows = [{**row, "GPU": ", ".join(chips)} for row, chips in groups.values()]
    output = io.StringIO(newline="")
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(columns)
    writer.writerows([row.get(column, "") for column in columns] for row in rows)
    return output.getvalue()
