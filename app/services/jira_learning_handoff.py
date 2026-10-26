"""Human-controlled proposal bundles and links to independent test results."""
import io
import json
import zipfile

from app.api.errors import ApiError
from app.db.connection import transaction
from app.domain import jira_learning as domain
from app.integrations import knowledge_repository as knowledge
from app.repositories import jira_learning_repo as repo
from app.repositories.base import new_id
from app.services import jira_learning_service as learning, jira_testing_batches as testing
from app.timeutil import beijing_timestamp


def selected(conn, job_id, ids, statuses=('approved',)):
    job = repo.get_job(conn, job_id)
    if not job:
        raise ApiError(404, '学习任务不存在')
    if not ids or len(ids) > 8 or len(ids) != len(set(ids)):
        raise ValueError('请选择 1 至 8 条不重复的提案')
    proposals = [repo.get_proposal(conn, pid) for pid in ids]
    if any(not p or p['job_id'] != job_id or p['superseded_by'] or p['status'] not in statuses
           or p['kind'] not in ('skill_update', 'new_skill') or not p['files'] for p in proposals):
        raise ApiError(409, '只能选择本任务未被替代、已通过审核的提案')
    if len({p['base_commit'] for p in proposals}) != 1 or not proposals[0]['base_commit']:
        raise ApiError(409, '所选提案的基线不一致，请统一整理后重新审核')
    files = {}
    for proposal in proposals:
        for file in proposal['files']:
            if file['path'] in files:
                raise ApiError(409, '所选提案重复修改同一文件，请先合并提案并重新审核')
            files[file['path']] = file
    for proposal in proposals:
        own = {f['path'] for f in proposal['files']}
        if set(proposal['dependencies']).intersection(files).difference(own):
            raise ApiError(409, '提案修改了另一提案依赖的文件，请一起整理并重新审核')
    config = domain.load_groups().get(job['agent_group'])
    if not config:
        raise ApiError(409, '未配置学习服务正式分支')
    return job, proposals, list(files.values()), config


def export(job_id, ids):
    with learning._db() as conn:
        job, proposals, files, config = selected(conn, job_id, ids)
    current = knowledge.snapshot(config.repository_ref)
    for proposal in proposals:
        learning._baseline(proposal, config.repository_ref, current)
    if any(current['hashes'].get(f['path']) != f['base_hash'] for f in files):
        raise ApiError(409, '目标文件已更新，请核对实际提交后登记入库或重新整理')
    base = proposals[0]['base_commit']
    manifest = {'job_id': job_id, 'title': job['title'], 'issue_keys': job['issue_keys'],
                'base_commit': base, 'repository_ref': config.repository_ref,
                'proposals': [{k: p[k] for k in ('id', 'version', 'title', 'evidence_ids', 'applicability', 'validation')} for p in proposals],
                'files': {f['path']: f['base_hash'] for f in files}}
    instructions = f'''# {job['title']}

仓库：jira_agent_skill，基线：`{base}`。

在干净的工作分支核对基线后执行：

```sh
git apply --check changes.patch
git apply changes.patch
```

结合 manifest.json 中的适用范围和验证要求检查改动。用原案例及其他场景验证，候选 skill 在独立验证环境使用，勿替换正在运行的分析服务。网站不会应用或部署这些改动。

人工提交后可统一登记提交 SHA；登记会核对正式分支和文件内容。若实际修改与提案不同，先按实际内容重新审核。
'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('changes.patch', ''.join(p['patch_text'] for p in proposals))
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('README.md', instructions)
    return buffer.getvalue()


def publish(user, job_id, ids, commit, note):
    if not learning._COMMIT.fullmatch(commit.strip()) or len(note) > 4000:
        raise ValueError('请输入有效提交哈希和简短备注')
    with learning._db() as conn:
        job, proposals, files, config = selected(conn, job_id, ids)
        resolved = knowledge.verify_commit(commit.strip(), config.repository_ref, files, proposals[0]['base_commit'])
        with transaction(conn):
            conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
            selected(conn, job_id, ids)
            for proposal in proposals:
                if not repo.record_publication(conn, proposal['id'], user['username'], resolved, note.strip()):
                    raise ApiError(409, '提案已变化，请刷新后重试')
    return {'commit_sha': resolved, 'proposal_ids': ids}


def link_validation(user, job_id, ids, suite_id, purpose, note):
    if purpose not in ('original', 'other') or len(note) > 4000:
        raise ValueError('请选择验证用途并填写简短备注')
    testing.detail(suite_id)  # Existence check; this never feeds test results to learning.
    with learning._db() as conn, transaction(conn):
        conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
        selected(conn, job_id, ids, ('approved', 'published'))
        conn.execute('INSERT INTO jira_learning_validations VALUES(?,?,?,?,?,?,?,?)',
                     (new_id('jlv'), job_id, suite_id, json.dumps(ids), purpose, note.strip(), user['username'], beijing_timestamp()))
    return {'validations': validations(job_id)}


def deploy(user, job_id, target, commit, result, note):
    if not target.strip() or len(target) > 200 or result not in ('succeeded', 'failed') or len(note) > 4000:
        raise ValueError('请填写目标服务、部署结果和简短备注')
    with learning._db() as conn, transaction(conn):
        conn.execute('UPDATE jira_learning_jobs SET id=id WHERE 0')
        job = repo.get_job(conn, job_id)
        if not job:
            raise ApiError(404, '学习任务不存在')
        published = [p for p in repo.list_proposals(conn, job_id) if p['status'] == 'published' and p['published_commit'] == commit]
        if not published:
            raise ApiError(409, '请先登记该提交的入库版本')
        repo.add_deployment(conn, job['agent_group'], target.strip(), commit, result, note.strip(), user['username'])
        return {'deployments': repo.list_deployments(conn, published[0]['id'])}


def validations(job_id):
    with learning._db() as conn:
        if not repo.get_job(conn, job_id):
            raise ApiError(404, '学习任务不存在')
        rows = [dict(row) for row in conn.execute('SELECT * FROM jira_learning_validations WHERE job_id=? ORDER BY rowid DESC', (job_id,))]
        for row in rows:
            row['proposal_ids'] = json.loads(row.pop('proposal_ids_json'))
            proposals = [repo.get_proposal(conn, pid) for pid in row['proposal_ids']]
            row['superseded'] = any(p['superseded_by'] for p in proposals)
            base = knowledge.snapshot(proposals[0]['base_commit'], complete=True)['files']
            expected = knowledge.hashes({**base, **{f['path']: f['updated_text'] for p in proposals for f in p['files']}})
            expected = {k: v for k, v in expected.items() if k.startswith(knowledge.PREFIX)}
            suite = testing.detail(row['suite_id'])
            fingerprints = []
            for entry in suite['entries']:
                if not entry['batch_id']:
                    continue
                batch = conn.execute('SELECT pack_json,config_json FROM jira_test_batches WHERE id=?', (entry['batch_id'],)).fetchone()
                pack, config = json.loads(batch['pack_json']), json.loads(batch['config_json'])
                hashes = knowledge.hashes(pack.get('files', {}))
                hashes = {k: v for k, v in hashes.items() if k.startswith(knowledge.PREFIX)}
                fingerprints.append({'case_id': entry['case_id'], 'skill_sha256': config.get('skill_sha256', ''),
                                     'server': pack.get('server', ''), 'matches': bool(hashes) and hashes == expected})
            row.update(tests=fingerprints, counts=suite['counts'], total=suite['total'], active=suite['active'],
                       matches=bool(fingerprints) and len(fingerprints) == len(suite['entries']) and all(p['matches'] for p in fingerprints))
    return rows
