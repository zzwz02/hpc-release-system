"""Read legacy reports through proven App identities without rewriting their data."""
from __future__ import annotations

from app.domain import image_lookup, qa_results
from app.repositories import app_info_repo, apps_repo, qa_results_repo


def list_records(conn, **filters):
    records = qa_results_repo.list_results(conn, **filters)
    if not any(not record["data"].get("app_id") for record in records):
        return [{**record, "data": qa_results.canonical_result(record["data"])} for record in records]
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    sources = [{**source, "branch": apps[source["app_id"]]["git_branch"],
                "repositories": image_lookup.image_repositories(source["raw"])}
               for source in app_info_repo.image_lookup_sources(conn) if source["app_id"] in apps]
    projected = []
    for record in records:
        data = qa_results.canonical_result(record["data"])
        if not data["app_id"]:
            try:
                reference = record["image_reference"] or qa_results.result_reference(data)
                repository = image_lookup.parse_lookup_image(reference)["repository"].rsplit("/", 1)[-1]
            except (ValueError, TypeError):
                repository = ""
            candidates = [source for source in sources if repository in source["repositories"]
                          and source["branch"] == str(data["git_branch"]).strip()
                          and str(source["raw"].get("app_version") or "") == str(data["app_version"]).strip()]
            commit = str(data.get("qa_digest_gerrit_commit_id") or "")
            exact = [source for source in candidates if commit and source["commit_id"]
                     and (source["commit_id"].startswith(commit) or commit.startswith(source["commit_id"]))]
            ids = {source["app_id"] for source in exact or candidates}
            if len(ids) == 1:
                data["app_id"] = ids.pop()
            else:
                data["app_id_error"] = "旧报告无法唯一确定 app_id，请重新下载测试命令并上传报告"
        projected.append({**record, "data": data})
    return projected
