"""Direct Jenkins discovery, independent of the failure-record backend."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app import runtime_config
from app.domain.permissions import roles_for_tab
from app.integrations.jenkins import JenkinsClient, JenkinsError
from app.services.mcp_evidence import correlations
from app.services.mcp_tools import Tool, ToolContext, ToolError, ToolLog, ToolResult, register

BEIJING = ZoneInfo('Asia/Shanghai')


class JobsQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    query: str = Field(default='', max_length=200, description='Name filter; spaces/punctuation ignored, e.g. ai models matches AIModels.')
    parent_url: str | None = Field(default=None, description='Folder URL returned by this tool; omit for Jenkins root. Search each folder separately.')
    offset: int = Field(default=0, strict=True, ge=0, le=100000)
    page_size: int = Field(default=100, strict=True, ge=1, le=200)


class BuildsQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_url: str
    date_from: date | None = Field(default=None, description='Inclusive Beijing build-start date, YYYY-MM-DD.')
    date_to: date | None = Field(default=None, description='Inclusive Beijing build-start date, YYYY-MM-DD.')
    offset: int = Field(default=0, strict=True, ge=0, le=100000)
    page_size: int = Field(default=50, strict=True, ge=1, le=100)

    @model_validator(mode='after')
    def dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('date_from 不能晚于 date_to')
        return self


def _job_url(client: JenkinsClient, value: str) -> str:
    return client.build_url(value.rstrip('/') + '/1/').removesuffix('1/')


def _discover(ctx: ToolContext, args: dict, *, builds: bool) -> ToolResult:
    try:
        q = (BuildsQuery if builds else JobsQuery).model_validate(args)
        client = JenkinsClient()
        if builds:
            url = _job_url(client, q.job_url)
            field, columns = 'builds', 'number,url,result,building,timestamp,duration'
        else:
            url = _job_url(client, q.parent_url) if q.parent_url else client.base+'/'
            field, columns = 'jobs', 'name,fullName,url,_class'
        end = q.offset+q.page_size
        payload = client.json(url+'api/json', params={'tree': f'{field}[{columns}]{{{q.offset},{end+1}}}'})
        if not isinstance(payload.get(field), list):
            raise ToolError('该地址没有构建列表' if builds else '该地址不是任务目录')
        raw = payload[field][:q.page_size]
        more = len(payload[field]) > q.page_size
        if builds:
            lower = datetime.combine(q.date_from, time.min, BEIJING).timestamp()*1000 if q.date_from else None
            upper = datetime.combine(q.date_to+timedelta(days=1), time.min, BEIJING).timestamp()*1000 if q.date_to else None
            records = []
            for build in raw:
                stamp = build.get('timestamp')
                if not isinstance(stamp, (int, float)):
                    continue
                if (lower is not None and stamp < lower) or (upper is not None and stamp >= upper):
                    continue
                records.append({**build, 'started_at': datetime.fromtimestamp(stamp/1000, BEIJING).isoformat(),
                                'build_url': build.get('url')})
        else:
            normalize = lambda s: re.sub(r'[^\w]', '', s.casefold()).replace('_', '')
            records = [{**job, 'is_folder': 'Folder' in job.get('_class', '') or 'MultiBranch' in job.get('_class', '')}
                       for job in raw if normalize(q.query) in normalize(job.get('fullName') or job.get('name', ''))]
        result = {field: records, 'source_url': url, 'scanned': len(raw), 'offset': q.offset,
                  'has_more': more, 'next_offset': end if more else None,
                  'timezone': 'Asia/Shanghai',
                  'scope': 'Filters apply to this page only; continue next_offset even if no matches. Folder queries are not recursive.',
                  'hint': '构建 SUCCESS 不保证每个 case 通过；继续用 jenkins_get_build_errors 读取 case 结果。'}
        # Return folders even when a name filter matches no job, so nested jobs are discoverable.
        if not builds:
            result['folders'] = [j for j in raw if 'Folder' in j.get('_class', '') or 'MultiBranch' in j.get('_class', '')]
        result['correlations'] = correlations(records, url+'api/json')
        ctx.log = ToolLog(target_type='jenkins_job', target=url, summary=f'{len(records)}/{len(raw)}')
        return ToolResult.text(json.dumps(result, ensure_ascii=False), log=ctx.log)
    except (ValidationError, JenkinsError, runtime_config.ConfigError) as exc:
        raise ToolError(str(exc)) from exc


register(Tool(name='jenkins_list_jobs', category='jenkins', title='查找 Jenkins 任务',
              description='List/search Jenkins jobs directly without the CICD Agent backend. Start at root; '
                          'follow folders using parent_url for nested jobs. Name filter applies only to each '
                          'scanned page: continue next_offset even when jobs is empty. Returns source URLs.',
              input_schema=JobsQuery.model_json_schema(), roles=roles_for_tab('jenkins-failures'),
              handler=lambda ctx, args: _discover(ctx, args, builds=False)))
register(Tool(name='jenkins_list_builds', category='jenkins', title='查询 Jenkins 构建',
              description='List builds of a job_url from jenkins_list_jobs. Optional inclusive dates use Asia/Shanghai '
                          'build-start time. All statuses are included: SUCCESS can contain failed cases. '
                          'Continue next_offset for more scanned builds. Does not depend on failure records.',
              input_schema=BuildsQuery.model_json_schema(), roles=roles_for_tab('jenkins-failures'),
              handler=lambda ctx, args: _discover(ctx, args, builds=True)))
