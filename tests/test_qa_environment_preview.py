"""Environment previews read the referenced workbook without changing its bytes or cells."""
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.api.errors import ApiError
from app.deps import get_db, require_login
from app.main import create_app
from app.repositories import qa_results_repo
from app.services import qa_results_service


def save_report(conn, *, os="ubuntu22.04", large=False):
    book = Workbook()
    book.active.title = "测试结果"
    env = book.create_sheet("C500_server_1")
    env.append(["OS", os])
    env.append(["CPU", "Xeon\n128 cores"])
    env.append(["Number", 0])
    env.append(["Formula", "=1+2"])
    env.append(["HTML", "<script>alert(1)</script>"])
    env.merge_cells("A6:C7")
    env["A6"] = "环境文档的合并单元格"
    if large:
        env["A51"] = "beyond rows"
        env["Q1"] = "beyond columns"
        env["B8"] = "x" * 3000
    buffer = io.BytesIO()
    book.save(buffer)
    content = buffer.getvalue()
    report = qa_results_repo.save(conn, filename="QA.xlsx", content=content, environments=[env.title],
                                  uploaded_at="2026-11-16 10:00:00", uploaded_by="qa", rows=[])
    conn.commit()
    return report, content


def test_raw_environment_values_preserve_zero_formula_newlines_and_original_file(temp_db):
    report, content = save_report(temp_db)
    before = temp_db.total_changes
    preview = qa_results_service.environment_preview(temp_db, report, "C500_server_1")
    assert preview["environment"] == "C500_server_1"
    assert preview["rows"][0] == ["OS", "ubuntu22.04"]
    assert preview["rows"][1] == ["CPU", "Xeon\n128 cores"]
    assert preview["rows"][2][1] == "0"
    assert preview["rows"][3][1] == "=1+2"
    assert preview["rows"][4][1] == "<script>alert(1)</script>"
    assert preview["rows"][5][0] == "环境文档的合并单元格"
    assert not preview["truncated"]
    assert qa_results_service.download(temp_db, report) == (content, "QA.xlsx")
    assert temp_db.total_changes == before


def test_same_environment_name_is_scoped_to_the_original_report(temp_db):
    first, _ = save_report(temp_db, os="ubuntu22.04")
    second, _ = save_report(temp_db, os="ubuntu24.04")
    assert qa_results_service.environment_preview(temp_db, first, "C500_server_1")["rows"][0][1] == "ubuntu22.04"
    assert qa_results_service.environment_preview(temp_db, second, "C500_server_1")["rows"][0][1] == "ubuntu24.04"


@pytest.mark.parametrize("name", ["missing", "测试结果", "c500_server_1"])
def test_missing_or_result_sheets_are_not_treated_as_environment_pages(temp_db, name):
    report, _ = save_report(temp_db)
    with pytest.raises(ApiError, match="不存在对应的环境页"):
        qa_results_service.environment_preview(temp_db, report, name)
    with pytest.raises(ApiError, match="测试报告不存在"):
        qa_results_service.environment_preview(temp_db, report + 99, "C500_server_1")


def test_large_environment_preview_is_bounded_and_explicitly_truncated(temp_db):
    report, _ = save_report(temp_db, large=True)
    preview = qa_results_service.environment_preview(temp_db, report, "C500_server_1")
    assert preview["truncated"]
    assert "beyond rows" not in str(preview["rows"]) and "beyond columns" not in str(preview["rows"])
    assert len(preview["rows"][7][1]) == 2001 and preview["rows"][7][1].endswith("…")


@pytest.mark.parametrize("role,status", [("QA", 200), ("RM", 200), ("Owner", 200), ("Guest", 200), ("SPD", 403), ("Admin", 403)])
def test_preview_api_uses_the_same_read_permissions_as_report_download(temp_db, role, status):
    report, _ = save_report(temp_db)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "reader", "role": role}
    with TestClient(app) as client:
        response = client.get(f"/api/qa/test-results/{report}/environment", params={"name": "C500_server_1"})
        assert response.status_code == status
        if status == 200:
            assert response.headers["Cache-Control"] == "no-store"
            assert response.json()["rows"][0] == ["OS", "ubuntu22.04"]
