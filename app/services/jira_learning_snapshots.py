"""Content-addressed learning manifests shared by queue and review workflows."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from app.config import settings


def knowledge_metadata(snapshot: dict) -> dict:
    return {**snapshot, **{kind: {k: v for k, v in snapshot.get(kind, {}).items() if k != 'files'}
                          for kind in ('repository', 'analysis')}}


def write(job_id: str, source: dict) -> dict:
    root = settings.jira_agent_data_dir / 'learning'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    directory = root / job_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    content = json.dumps(source, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(content).hexdigest()
    path = directory / f'source-{digest}.json'
    with tempfile.NamedTemporaryFile(dir=directory, prefix='.source-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {'source_path': str(path), 'source_sha256': digest,
            'acquisition_status': source.get('acquisition', {}).get('status', 'unknown')}


def read(job: dict) -> dict:
    root = (settings.jira_agent_data_dir / 'learning').resolve()
    path = Path(job['source_path']).resolve()
    if not path.is_relative_to(root / job['id']) or not path.is_file():
        raise ValueError('来源快照不存在')
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != job['source_sha256']:
        raise ValueError('来源快照校验失败')
    return json.loads(content)


def metadata(source: dict) -> dict:
    result = {k: v for k, v in source.items() if k not in (
        'sources', '_materials', 'materials', 'material_reads', 'conversations',
        'knowledge_files', 'knowledge_sha256')}
    if 'knowledge_snapshot' in result:
        result['knowledge_snapshot'] = knowledge_metadata(result['knowledge_snapshot'])
    return {**result, 'format': 2, 'sources': [], 'material_access': True}


def load(job: dict) -> tuple:
    """Normalize old manifests once; consumers only see the SQLite material store."""
    from app.services.jira_learning_archive import LearningArchive
    source = read(job)
    archive = LearningArchive(Path(job['source_path']).parent)
    if source.get('format') != 2:
        archive.import_legacy(source)
        if job.get('thread_id') and not source.get('material_access'):
            source['legacy_thread_without_tools'] = True
    return archive, {**metadata(source), **archive.bundle()}
