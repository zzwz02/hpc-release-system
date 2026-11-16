"""One read model for the QA report page, including independent section errors."""
from __future__ import annotations

from app.api.errors import ApiError
from app.services import qa_results_service, qa_service, state_service


def get_report(conn, *, user, release_id=""):
    payload = state_service.get_state(conn, user=user, release_id_param=release_id)
    report = {"failure_summary": None, "delivery_images": None, "errors": {}}
    release = payload["release"]
    if release:
        for section, read in (
            ("failure_summary", lambda: qa_results_service.failure_summary(conn, release["id"])),
            ("delivery_images", lambda: qa_service.delivery_images_view(
                conn, release["id"], entries=release["entries"])),
        ):
            try:
                report[section] = read()
            except (ApiError, ValueError, RuntimeError) as exc:
                report["errors"][section] = str(exc)
    return {**payload, "qa_report": report}
