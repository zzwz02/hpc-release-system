"""Learning-job configuration, evidence packaging and proposal validation."""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from app import runtime_config
from app.integrations import knowledge_repository as repository

KINDS = {"skill_update", "new_skill", "do_not_publish"}


@dataclass(frozen=True)
class LearningGroup:
    name: str
    ws_url: str
    ws_token: str
    workspace_root: str
    model: str
    timeout_seconds: int
    repository_ref: str = ""


def load_groups() -> dict[str, LearningGroup]:
    groups = {}
    for name, values in runtime_config.learning_group_sections().items():
        section = runtime_config.LEARNING_GROUP_PREFIX + name
        url = runtime_config.required(section, values, "CODEX_WS_URL")
        analysis = runtime_config.agent_group_sections().get(name, {})
        if url.rstrip('/') == analysis.get('CODEX_WS_URL', '').rstrip('/'):
            raise runtime_config.ConfigError(f'release_system.conf [{section}] 必须使用独立学习 app-server')
        token = runtime_config.required(section, values, "CODEX_WS_TOKEN")
        root = runtime_config.required(section, values, "WORKSPACE_ROOT").rstrip("/")
        if not url.startswith(("ws://", "wss://")) or not root.startswith("/"):
            raise runtime_config.ConfigError(
                f"release_system.conf [{section}] 需要 ws/wss 地址和绝对路径 WORKSPACE_ROOT"
            )
        timeout = runtime_config.required_int(section, values, "TURN_TIMEOUT_SECONDS")
        if timeout < 60:
            raise runtime_config.ConfigError(f"release_system.conf [{section}] TURN_TIMEOUT_SECONDS 至少为 60")
        groups[name] = LearningGroup(
            name=name, ws_url=url, ws_token=token, workspace_root=root,
            model=values.get("MODEL", ""), timeout_seconds=timeout,
            repository_ref=runtime_config.required(section, values, "REPOSITORY_REF"),
        )
    return groups


def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}
PROPOSAL_SCHEMA = _obj({
    "proposals": {
        "type": "array",
        "items": _obj({
            "kind": {"type": "string", "enum": sorted(KINDS)},
            "title": _STR,
            "summary": _STR,
            "proposed_text": _STR,
            "files": {"type": "array", "items": _obj({"path": _STR, "updated_text": _STR})},
            "dependencies": _STR_LIST,
            "previous_id": _STR,
            "applicability": _STR,
            "evidence_ids": _STR_LIST,
            "limitations": _STR,
            "material_gaps": {"type": "array", "items": _obj({"source_id": _STR, "impact": _STR})},
            "validation": _STR,
        }),
    },
})


def build_source(
    issue: dict,
    messages: list[dict],
    agent_comment_ids: set[str],
    *,
    additional_sources: list[dict] | None = None,
    collection_gaps: list[dict] | None = None,
) -> dict:
    """Retain source identities and provenance; agent-authored comments are secondary."""
    sources = [{
        "id": "jira:description",
        "kind": "description",
        "text": issue.get("description") or "",
        "independent": bool((issue.get("description") or "").strip()),
    }]
    for key, field in sorted((issue.get("custom_fields") or {}).items()):
        value = field.get("value") if isinstance(field, dict) else field
        if value is not None and value != "" and value != []:
            sources.append({
                "id": f"jira:field:{key}",
                "kind": "custom_field",
                "name": field.get("name", key) if isinstance(field, dict) else key,
                "text": json.dumps(value, ensure_ascii=False, default=str),
                "independent": True,
            })
    for comment in issue.get("comments") or []:
        cid = str(comment.get("id") or "")
        if not cid:
            continue
        sources.append({
            "id": f"jira:comment:{cid}",
            "kind": "agent_comment" if cid in agent_comment_ids else "comment",
            "author": comment.get("author_name") or comment.get("author") or "",
            "created_at": comment.get("created") or "",
            "visibility": comment.get("visibility"),
            "text": comment.get("body_raw") or comment.get("body") or "",
            "independent": cid not in agent_comment_ids and bool((comment.get("body_raw") or comment.get("body") or "").strip()),
        })
    for message in messages:
        sources.append({
            "id": f"chat:{message['id']}",
            "kind": message.get("kind", "owner_message"),
            "author": message["author"],
            "created_at": message["created_at"],
            "conversation_id": message["conversation_id"],
            "turn_id": message["turn_id"],
            "text": message["text"],
            "files": message["files"],
            "independent": (message.get('kind', 'owner_message') == 'owner_message' or message.get('event_kind') == 'command') and bool(message["text"].strip()),
            **{k: message[k] for k in ('event_kind', 'event_seq', 'turn_seq', 'turn_status') if k in message},
        })
    sources.extend(additional_sources or [])
    ids = [item.get("id") for item in sources]
    if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("学习来源 ID 缺失或重复")
    return {
        "issue": {
            "key": issue["key"],
            "url": issue.get("url") or "",
            "summary": issue.get("summary") or "",
            "status": issue.get("status") or "",
            "components": issue.get("components") or [],
            "attachments": [
                {"id": item.get("id"), "filename": item.get("filename"), "size": item.get("size")}
                for item in issue.get("attachments") or []
            ],
        },
        "acquisition": issue.get("acquisition") or {},
        "sources": sources,
        "collection_gaps": collection_gaps or [],
    }


MODEL_SOURCE_MAX_CHARS = 600_000
MODEL_SOURCE_MAX_ITEM_CHARS = 120_000


def conversation_index(sources: list[dict]) -> list[dict]:
    conversations = {}
    for item in sources:
        if not item.get('conversation_id') or item.get('kind') not in ('owner_message', 'agent_message', 'agent_tool', 'agent_result'):
            continue
        turns = conversations.setdefault(item['conversation_id'], {})
        turn = turns.setdefault(item.get('turn_id', ''), {'id': item.get('turn_id', ''),
            'seq': item.get('turn_seq'), 'status': item.get('turn_status', ''), 'source_ids': []})
        turn['source_ids'].append(item['id'])
    return [{'id': cid, 'turns': sorted(turns.values(), key=lambda t: t['seq'] or 0)} for cid, turns in conversations.items()]


def model_source(source: dict) -> tuple[dict, list[dict]]:
    """Make a bounded model input while leaving the review snapshot complete."""
    result = copy.deepcopy(source)
    # Full inventory is available through the task-scoped list tool.
    if result.get('material_access'):
        result['materials_total'] = len(result.get('materials', []))
        result['materials'] = result.get('materials', [])[:100]
        result['collection_gaps'] = [g for g in result.get('collection_gaps', []) if g.get('category') == 'blocking']
        result.pop('material_reads', None)
    if result.get('combination'):
        result['combination'] = [{'id': p['id'], 'title': p['title'], 'file': f"related/{p['id']}.json"}
                                 for p in result['combination']]
    gaps = []
    items = [item for item in result.get("sources") or [] if isinstance(item.get("text"), str)]
    # Reserve readable context for the issue, corrections and analysis before file excerpts.
    primary = [item for item in items if item.get('kind') in (
        'description', 'comment', 'owner_message', 'agent_result', 'agent_comment', 'agent_analysis_path', 'agent_message'
    ) or (item.get('name') or '').lower().endswith('report.md')]
    primary_ids = {item['id'] for item in primary}
    secondary = [item for item in items if item['id'] not in primary_ids]

    def cap_for(group, budget):
        low, high = 0, min(MODEL_SOURCE_MAX_ITEM_CHARS, budget)
        while low < high:
            cap = (low + high + 1) // 2
            if sum(min(len(item['text']), cap) for item in group) <= budget:
                low = cap
            else:
                high = cap - 1
        return low

    main_cap = cap_for(primary, MODEL_SOURCE_MAX_CHARS * 3 // 4 if secondary else MODEL_SOURCE_MAX_CHARS)
    remaining = MODEL_SOURCE_MAX_CHARS - sum(min(len(item['text']), main_cap) for item in primary)
    file_cap = cap_for(secondary, remaining)
    marker = "\n\n[学习输入中间内容已省略]\n\n"
    for item in items:
        low = main_cap if item['id'] in primary_ids else file_cap
        text = item["text"]
        if len(text) <= low:
            continue
        item["truncated"] = True
        # The runner writes the full source separately for on-demand reading.
        if result.get('material_access'):
            item['full_text_source_id'] = item['id']
        else:
            item['full_text_file'] = 'sources/' + hashlib.sha256(item['id'].encode()).hexdigest() + '.json'
        if low <= len(marker):
            item["text"] = ""
            item["independent"] = False
            gaps.append({"source": item.get("id") or "", "reason": "模型输入总大小达到限制，正文未发送", "category": "indexed"})
            continue
        head = (low - len(marker)) // 2
        tail = low - len(marker) - head
        item["text"] = text[:head] + marker + text[-tail:]
        gaps.append({"source": item.get("id") or "", "reason": "正文为适配模型输入大小仅保留首尾，完整正文见审核材料", "category": "indexed"})
    return result, gaps


def skill_context(snapshot: dict[str, str]) -> str:
    """Render an exact, path-delimited knowledge snapshot for the curator."""
    files = snapshot
    parts = []
    for path in sorted(files):
        parts.append(f"## {path}\n\n{files[path]}")
    return "\n\n".join(parts)


MAX_UPDATED_FILE_BYTES = 512 * 1024
MAX_PROPOSAL_PATCH_BYTES = 512 * 1024
MAX_JOB_PATCH_BYTES = 2 * 1024 * 1024


def _proposal_patch(path: str, previous: str | None, updated: str) -> str:
    """Generate a standard review patch from exact text without touching the pack."""
    before = [] if previous is None else previous.splitlines(keepends=True)
    after = updated.splitlines(keepends=True)
    lines = difflib.unified_diff(
        before, after,
        fromfile="/dev/null" if previous is None else f"a/{path}",
        tofile=f"b/{path}",
        lineterm="\n",
    )
    body = []
    for line in lines:
        body.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    header = f"diff --git a/{path} b/{path}\n"
    if previous is None:
        header += "new file mode 100644\n"
    return header + "".join(body)


def validate_proposals(
    raw: str, source: dict, snapshot: dict[str, str],
) -> list[dict]:
    try:
        result = json.loads(raw)
    except ValueError as exc:
        raise ValueError("学习服务未返回有效 JSON") from exc
    proposals = result.get("proposals") if isinstance(result, dict) else None
    if not isinstance(proposals, list) or len(proposals) > 8:
        raise ValueError("学习服务提案数量或结构无效")
    baseline = snapshot
    canonical = any(path.startswith(".agents/") for path in baseline)
    sources = {item["id"]: item for item in source["sources"]}
    checked = []
    targets = set()
    total = 0
    for item in proposals:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise ValueError("学习服务返回了未知提案类型")
        for field in ("title", "summary", "proposed_text", "applicability", "limitations", "validation"):
            if not isinstance(item.get(field), str) or len(item[field]) > 12000:
                raise ValueError(f"学习提案字段无效：{field}")
        if not item['title'].strip():
            raise ValueError('学习提案缺少标题')
        evidence = item.get('evidence_ids')
        if not isinstance(evidence, list) or any(not isinstance(ref, str) or ref not in sources for ref in evidence):
            raise ValueError('学习提案的证据引用无效')
        missing = item.get('material_gaps', [])
        if not isinstance(missing, list) or len(missing) > 20:
            raise ValueError('材料缺失说明格式无效')
        reads = {entry['source_id']: entry for entry in source.get('material_reads', []) if entry.get('source_id')}
        materials = {entry['id']: entry for entry in source.get('materials', [])}
        material_gaps = []
        for gap in missing:
            if not isinstance(gap, dict) or not isinstance(gap.get('impact'), str) or not gap['impact'].strip() or len(gap['impact']) > 2000:
                raise ValueError('材料缺失应说明对判断的影响')
            record = reads.get(gap.get('source_id'))
            if not record or record['success']:
                raise ValueError('材料缺失必须对应实际未成功的读取请求')
            sid = record['source_id']
            material_gaps.append({'source_id': sid, 'name': materials.get(sid, {}).get('name', sid),
                                  'reason': record['error'], 'impact': gap['impact']})
        changes = item.get('files')
        if changes is None:  # Older persisted model outputs can still be recovered.
            changes = [] if item['kind'] == 'do_not_publish' else [
                {'path': item.get('target_path'), 'updated_text': item.get('updated_text')}
            ]
        if not isinstance(changes, list) or len(changes) > 12:
            raise ValueError('关联文件数量或格式无效')
        if item['kind'] == 'do_not_publish':
            if changes or item.get('updated_text'):
                raise ValueError('do_not_publish 不应包含目标文件修改')
        elif not changes or not evidence or not any(sources[ref].get('independent') for ref in evidence):
            raise ValueError('可发布提案至少需要一条独立证据和有效文件修改')
        files = []
        patches = []
        for change in changes:
            if not isinstance(change, dict):
                raise ValueError('目标文件格式无效')
            path, updated = change.get('path'), change.get('updated_text')
            if not isinstance(path, str) or not repository.allowed(path if canonical else repository.PREFIX + path, writable=True):
                raise ValueError('学习提案目标必须是 HPC skill 内的 Markdown 文件')
            if path in targets:
                raise ValueError('同一学习轮次不能重复修改同一目标文件；请合为一条关联提案')
            targets.add(path)
            previous = baseline.get(path)
            if not isinstance(updated, str) or not updated.strip() or len(updated.encode()) > MAX_UPDATED_FILE_BYTES:
                raise ValueError('学习提案的目标文件正文无效或过大')
            if previous == updated:
                raise ValueError('学习提案没有形成有效的目标文件修改')
            patch = _proposal_patch(path, previous, updated)
            patches.append(patch)
            files.append({'path': path, 'previous_text': previous, 'updated_text': updated,
                          'base_hash': repository.hashes({path: previous})[path] if previous is not None else None})
        if item['kind'] == 'skill_update' and not any(f['previous_text'] is not None for f in files):
            raise ValueError('skill_update 必须修改知识快照中已有的文件')
        if item['kind'] == 'new_skill' and not any(f['previous_text'] is None and PurePosixPath(f['path']).name == 'SKILL.md' for f in files):
            raise ValueError('new_skill 必须新增尚不存在的 SKILL.md')
        dependencies = item.get('dependencies') or []
        if not isinstance(dependencies, list) or any(not isinstance(path, str) or path not in baseline for path in dependencies):
            raise ValueError('提案依赖必须来自知识快照')
        # Navigation links do not establish semantic dependencies. Target file
        # baselines are protected separately by files[].base_hash.
        patch = ''.join(patches)
        size = len(patch.encode())
        total += size
        if size > MAX_PROPOSAL_PATCH_BYTES or total > MAX_JOB_PATCH_BYTES:
            raise ValueError('学习提案 patch 超过大小限制')
        checked.append({**{key: value for key, value in item.items() if key not in ('updated_text', 'dependencies')},
                        'target_path': files[0]['path'] if files else '', 'files': files,
                        'dependencies': repository.hashes({path: baseline[path] for path in dependencies}),
                        'patch_text': patch, 'material_gaps': material_gaps})
    return checked
