"""Bounded CPU workers for material preparation and reading; no model calls here."""
from __future__ import annotations

import asyncio
import fcntl
import json
import multiprocessing
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from app.config import settings

_pool: ProcessPoolExecutor | None = None


def shutdown() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


async def execute(action: str, directory: Path, *args):
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('spawn'))
    return await asyncio.get_running_loop().run_in_executor(
        _pool, work, action, str(directory), str(settings.runtime_conf_path), settings.jira_agent_database_url, args)


def work(action: str, directory: str, config: str, database: str, args: tuple):
    from app.db.jira_agent_connection import _sqlite_path
    from app.domain import jira_learning as domain
    from app.repositories import jira_learning_repo as repo
    from app.services.jira_learning_archive import LearningArchive
    from app.services.jira_learning_materials import MaterialStore, catalog, gerrit_catalog
    settings.runtime_conf_path = Path(config)
    root = Path(directory)
    archive = LearningArchive(root)
    if action == 'local':
        key, group = args
        conn = sqlite3.connect('file:'+str(_sqlite_path(database))+'?mode=ro',uri=True)
        conn.row_factory=sqlite3.Row
        try:
            conn.execute('BEGIN')
            messages=repo.conversation_messages(conn,key,group)
            results=repo.agent_results(conn,key,group)
            files=repo.learning_files(conn,key,group)
        finally:
            conn.close()
        source=domain.build_source({'key':key},messages,set())
        source['sources']=[s for s in source['sources'] if s['id'] != 'jira:description']
        source['sources'].extend({'id':'agent:result:'+r['turn_id'],'kind':'agent_result','name':f"第 {r['turn_seq']} 轮结果",
            'text':json.dumps(r['result'],ensure_ascii=False),'independent':False,
            **{k:r[k] for k in ('conversation_id','turn_id','turn_seq','turn_status','created_at')}} for r in results)
        source['_materials']=catalog({'key':key},messages,files)+gerrit_catalog(source['sources'],key)
        archive.put_case(key,source,local=True)
        return archive.progress()
    if action == 'jira':
        key, issue, comments = args
        source=domain.build_source(issue,[],set(comments))
        source['_materials']=catalog(issue,[],[])+gerrit_catalog(source['sources'],key)
        archive.put_case(key,source)
        return archive.progress()
    if action == 'read':
        arguments,call_id,agent=args[:3]
        lock=root/'materials'/'read.lock'
        lock.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with lock.open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            store=MaterialStore(root)
            return store.request(arguments,call_id,record=agent)
    if action == 'inherit':
        aliases = {}
        for item in args[0]:
            aliases.update(archive.inherit(root.parent/item['job_id'],item.get('prefix',''),item.get('manifest',''),item.get('sha256','')))
        return aliases
    if action == 'validation':
        return archive.validation()
    raise ValueError('未知材料操作')
