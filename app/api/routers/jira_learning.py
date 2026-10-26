"""Staged Jira knowledge proposals; no route changes the live skill pack."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from app.deps import require_roles
from app.services import jira_learning_service as service
from app.services import jira_learning_handoff as handoff
from app.services import jira_learning_relations as relations

router = APIRouter(prefix="/api/jira-agent/learning")
require_reviewer = require_roles("RM", message="只有 RM 可以管理 Jira 知识学习")


class JobBody(BaseModel):
    title: str = ""
    request_id: str = ""
    issue_keys: list[str] = []
    agent_group: str = "HPC"
    instructions: str = ""
    repository_only: bool = False


class MessageBody(BaseModel):
    text: str


class ReviewBody(BaseModel):
    decision: str
    note: str = ""


class CombineBody(BaseModel):
    proposal_ids: list[str]
    note: str = ""
    request_id: str


class DuplicateBody(BaseModel):
    replacement_id: str


@router.get('/proposals/{proposal_id}/compare/{other_id}')
def compare(proposal_id: str, other_id: str, user: dict = Depends(require_reviewer)) -> dict:
    return relations.compare(proposal_id, other_id)


@router.post('/proposals/combine')
def combine(body: CombineBody, user: dict = Depends(require_reviewer)) -> dict:
    return relations.combine(user, body.proposal_ids, body.note, body.request_id)


@router.post('/proposals/{proposal_id}/close-duplicate')
def close_duplicate(proposal_id: str, body: DuplicateBody, user: dict = Depends(require_reviewer)) -> dict:
    return relations.close_duplicate(user, proposal_id, body.replacement_id)


class PublicationBody(BaseModel):
    commit_sha: str
    note: str = ""


@router.get("/jobs")
def list_jobs(group: str = "", issue_key: str = "", limit: int = Query(50, ge=1, le=100),
              offset: int = Query(0, ge=0), user: dict = Depends(require_reviewer)) -> dict:
    jobs = service.list_jobs(group, limit + 1, offset, issue_key)
    return {"jobs": jobs[:limit], "has_more": len(jobs) > limit}


@router.post("/jobs")
def create_job(body: JobBody, user: dict = Depends(require_reviewer)) -> dict:
    return service.create_job(
        user,
        body.agent_group,
        body.issue_keys, body.instructions, body.repository_only, body.title, body.request_id,
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: str, after: int = Query(0, ge=0), user: dict = Depends(require_reviewer)) -> dict:
    return service.get_job(job_id, after)


@router.post("/jobs/{job_id}/messages")
def send_message(
    job_id: str, body: MessageBody, user: dict = Depends(require_reviewer)
) -> dict:
    return service.send_message(user, job_id, body.text)


@router.get("/jobs/{job_id}/source")
def get_source(job_id: str, user: dict = Depends(require_reviewer)) -> dict:
    return service.source_summary(job_id)


@router.get('/jobs/{job_id}/materials')
def materials(job_id: str, issue: str = '', kind: str = '', query: str = Query('', max_length=500),
              offset: int = Query(0,ge=0), limit: int = Query(20,ge=1,le=100),
              ids: list[str] | None = Query(None, max_length=100), user: dict = Depends(require_reviewer)) -> dict:
    return service.material_index(job_id,issue,kind,query,offset,limit,ids)


@router.get('/jobs/{job_id}/material')
async def material(job_id: str, source_id: str = Query(...,max_length=2000), offset: int = Query(0,ge=0),
                   limit: int = Query(12000,ge=1,le=48000), user: dict = Depends(require_reviewer)) -> dict:
    return await service.material_read(job_id,source_id,offset,limit)


@router.post("/proposals/{proposal_id}/review")
def review(
    proposal_id: str, body: ReviewBody, user: dict = Depends(require_reviewer)
) -> dict:
    return {"proposal": service.review(user, proposal_id, body.decision, body.note)}


@router.post("/proposals/{proposal_id}/publication")
def publication(
    proposal_id: str, body: PublicationBody, user: dict = Depends(require_reviewer)
) -> dict:
    return {
        "proposal": service.record_publication(
            user, proposal_id, body.commit_sha, body.note
        )
    }


class HistoryBody(BaseModel):
    issue_keys: list[str]
    agent_group: str = "HPC"


class RetryBody(BaseModel):
    repository_only: bool = False


class DeploymentBody(BaseModel):
    target: str
    commit_sha: str
    result: str
    note: str = ""


@router.post('/jobs/{job_id}/deployments')
def deploy_job(job_id: str, body: DeploymentBody, user: dict = Depends(require_reviewer)) -> dict:
    return handoff.deploy(user, job_id, body.target, body.commit_sha, body.result, body.note)


@router.get("/issues")
async def list_issues(group: str = "HPC", user: dict = Depends(require_reviewer)) -> dict:
    return await service.list_issues(user, group)


@router.get("/issues/preview")
def preview_issues(query: str, user: dict = Depends(require_reviewer)) -> dict:
    return service.preview_issues(query)


@router.post("/issues")
def add_issues(body: HistoryBody, user: dict = Depends(require_reviewer)) -> dict:
    return service.add_issues(user, body.issue_keys, body.agent_group)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, user: dict = Depends(require_reviewer)) -> dict:
    return {"job": service.cancel(user, job_id)}


@router.post("/jobs/{job_id}/retry")
def retry(job_id: str, body: RetryBody, user: dict = Depends(require_reviewer)) -> dict:
    return service.retry(user, job_id, body.repository_only)


@router.post("/jobs/{job_id}/refresh")
def refresh(job_id: str, user: dict = Depends(require_reviewer)) -> dict:
    return service.refresh(user, job_id)


@router.post("/proposals/{proposal_id}/deployments")
def deployment(proposal_id: str, body: DeploymentBody, user: dict = Depends(require_reviewer)) -> dict:
    return service.record_deployment(user, proposal_id, body.target, body.commit_sha, body.result, body.note)


@router.post("/proposals/{proposal_id}/publication-revision")
def publication_revision(proposal_id: str, body: PublicationBody, user: dict = Depends(require_reviewer)) -> dict:
    return service.publication_revision(user, proposal_id, body.commit_sha, body.note)


class BundleBody(PublicationBody):
    proposal_ids: list[str]




@router.get("/jobs/{job_id}/patch-bundle")
def export_bundle(job_id: str, proposal_ids: str, user: dict = Depends(require_reviewer)) -> Response:
    data = handoff.export(job_id, proposal_ids.split(','))
    return Response(data, media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="skill-proposals.zip"'})


@router.post("/jobs/{job_id}/publication")
def publish_bundle(job_id: str, body: BundleBody, user: dict = Depends(require_reviewer)) -> dict:
    return handoff.publish(user, job_id, body.proposal_ids, body.commit_sha, body.note)
