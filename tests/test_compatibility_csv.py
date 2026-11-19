"""Compatibility CSV uses the web presentation and the same filtered GPU groups."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.deps import get_db, require_login
from app.domain import compatibility_table
from app.main import create_app
from app.services import lookup_service


@pytest.fixture()
def rows():
    base = {"app_id": "pyg", "app_name": "PyG", "owners": ["owner"], "dependencies": ["torch 2.8 + py312"],
            "app_version": "1.0", "hpc_version": "3.10.1", "manifest_source": "sealed", "os": "ubuntu24.04",
            "arch": "amd64", "image_target": "dbg", "python_label": "py312", "pytorch_label": "torch2.8",
            "delivery_reference": "image:tag@sha256:digest", "app_info_commit_id": "a" * 40,
            "path": "unit_test", "command": "docker run image sh -c 'test'", "note": 'QA log: pass\n备注含逗号,和"引号"'}
    return [{**base, "GPU": chip, "chips": [chip]} for chip in ("C500", "C600", "X301")] + [
        {**base, "GPU": "C600", "chips": ["C600"], "note": "fail: C600 specific"},
        {**base, "GPU": "C500", "chips": ["C500"], "arch": "arm64"},
        {**base, "app_id": "other", "app_name": "Other", "GPU": "", "chips": [], "dependencies": [], "command": ""},
    ]


def parse(content):
    return list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))


def test_export_combines_only_gpu_differences_and_preserves_csv_text(rows):
    result = compatibility_table.export_csv(rows)
    assert result.startswith("\ufeffApp,app_id,Owner")
    exported = parse(result)
    assert len(exported) == 4
    assert exported[0]["GPU"] == "C500, C600, X301"
    assert exported[0]["镜像配置"] == "dbg · py312 · torch2.8"
    assert exported[0]["CPU 架构"] == "x86"
    assert exported[0]["发布记录来源"] == "发布时封存"
    assert exported[0]["说明"] == rows[0]["note"]
    assert exported[1]["说明"] == "fail: C600 specific"
    assert exported[2]["CPU 架构"] == "ARM"
    assert exported[3]["GPU"] == ""
    assert exported[0]["镜像 app_info commit id"] == "a" * 40
    assert rows[0]["GPU"] == "C500"  # Export never rewrites the raw inventory.


@pytest.mark.parametrize("filters,expected", [
    ({"component": "driver-sdk"}, 4), ({"component": "sdk"}, 4),
    ({"component": "torch 2.8 + py312"}, 3), ({"component": "unknown"}, 0),
    ({"arch": "amd64", "os": "ubuntu24.04", "chip": "X301", "search": "PyG"}, 1),
    ({"search": "C600 specific"}, 1), ({"search": "no match"}, 0),
])
def test_filters_apply_before_grouping(rows, filters, expected):
    exported = parse(compatibility_table.export_csv(rows, **filters))
    assert len(exported) == expected
    if filters.get("chip"):
        assert {row["GPU"] for row in exported} == {"X301"}


@pytest.mark.parametrize("role,status", [("RM", 200), ("QA", 200), ("Owner", 200), ("Guest", 403), ("SPD", 403), ("Admin", 403)])
def test_export_endpoint_permissions_filters_and_download_headers(temp_db, monkeypatch, rows, role, status):
    monkeypatch.setattr(lookup_service, "latest_running_releases", lambda conn: {"rows": rows})
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "tester", "role": role}
    with TestClient(app) as client:
        response = client.get("/api/lookup/latest-running/export", params={
            "component": "driver-sdk", "arch": "amd64", "os": "ubuntu24.04", "chip": "X301", "search": "PyG",
        })
    assert response.status_code == status
    if status == 200:
        assert response.content.startswith(b"\xef\xbb\xbf")
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"] == 'attachment; filename="compatibility.csv"'
        assert response.headers["cache-control"] == "no-store"
        exported = parse(response.text)
        assert len(exported) == 1 and exported[0]["GPU"] == "X301"


def test_export_propagates_upstream_failure_instead_of_downloading_empty_csv(temp_db, monkeypatch):
    def unavailable(conn):
        raise ApiError(502, "Harbor unavailable")
    monkeypatch.setattr(lookup_service, "latest_running_releases", unavailable)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "qa", "role": "QA"}
    with TestClient(app) as client:
        response = client.get("/api/lookup/latest-running/export")
    assert response.status_code == 502
    assert "Harbor unavailable" in response.text
    assert "content-disposition" not in response.headers
