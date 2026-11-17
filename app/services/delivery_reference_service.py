"""Read-time delivery tag completion; never rewrite persisted image references."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.api.errors import ApiError
from app.domain import image_lookup, qa_results
from app.domain.test_commands import image_tag_version, replace_command_image, select_image_tag
from app.integrations import harbor
from app.integrations.read_session import read_session

# Legacy deliveries need only Harbor tags, with bounded, request-local reuse.
TAG_LOOKUP_WORKERS = 16


def _tags(reference):
    try:
        image = image_lookup.parse_lookup_image(reference)
        if image["host"] != harbor.HARBOR_HOST:
            raise ValueError(f"仅支持 {harbor.HARBOR_HOST} 的镜像")
        artifact = harbor.get_artifact(image["project"], image["repository"], image["digest"])
        return [tag["name"] for tag in artifact.get("tags") or []
                if isinstance(tag, dict) and isinstance(tag.get("name"), str)]
    except (ValueError, RuntimeError, ApiError) as exc:
        return exc


def _pin_delivery(row, tags):
    reference = row.get("delivery_reference", "")
    if not reference or ":" in reference.split("@", 1)[0].rsplit("/", 1)[-1]:
        return row
    try:
        pattern = row.get("image_pattern", "").partition(":")[2]
        if not pattern:
            raise ValueError("缺少构建配置，无法确定实际 tag")
        actual = tags[reference]
        if isinstance(actual, Exception):
            raise actual
        # Prefer release aliases in this cycle. A commit alias is also valid
        # when the old artifact has no matching release alias; never invent one.
        versions = {version for tag in actual if (version := image_tag_version(pattern, tag))
                    and row["hpc_version"] in qa_results.hpc_families(version)}
        tag = select_image_tag(pattern, actual, versions)
        address, digest = reference.split("@", 1)
        tagged = f"{address}:{tag}@{digest}"
        result = {**row, "delivery_reference": tagged, "image": tagged}
        if row.get("command"):
            result["command"] = replace_command_image(row["command"], reference, tagged)
        return result
    except (ValueError, RuntimeError, ApiError) as exc:
        return {**row, "reference_error": str(exc)}


def resolve_references(rows):
    """Reuse artifact reads within this request and preserve explicitly recorded tags."""
    references = list(dict.fromkeys(row["delivery_reference"] for row in rows
                      if row.get("delivery_reference") and row.get("image_pattern")
                      and ":" not in row["delivery_reference"].split("@", 1)[0].rsplit("/", 1)[-1]))
    with read_session() as batch, ThreadPoolExecutor(max_workers=TAG_LOOKUP_WORKERS) as pool:
        tags = dict(zip(references, pool.map(lambda ref: batch.run(_tags, ref), references), strict=True))
    return [_pin_delivery(row, tags) for row in rows]
