"""Test runs use saved inputs and cannot post Jira comments."""
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.errors import ApiError
from app.api.jira_execution_models import ExecutionSubmission
from app.deps import require_roles
from app.domain import jira_test_package
from app.services import jira_testing_service as service, jira_testing_batches as batches_service, jira_test_evaluation as evaluations

router = APIRouter(prefix='/api/jira-agent/testing', dependencies=[Depends(require_roles('RM'))])
reviewer = require_roles('RM')


class CaseBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    input: dict
    criteria: str = Field(default='', max_length=20000)
    reference: str = Field(default='', max_length=100000)
    provenance: dict = Field(default_factory=dict)
    package_import: bool = False
    import_update: str = ""


class RunBody(ExecutionSubmission):
    version_id: str
    agent_group: str
    machine: str = ''
    ssh_key_session_id: str = ''
    model: str = Field(default='', max_length=100)
    effort: Literal['low', 'medium', 'high', 'xhigh'] = 'xhigh'
    # Older pages may still send an empty value; choosing a version is disabled.
    skill_ref: Literal[''] = ''


class ReviewBody(BaseModel):
    decision: Literal['pass', 'partial', 'fail', 'environment', 'platform', 'cancelled', 'unknown']
    note: str = Field(default='', max_length=20000)
    confirm_ai: bool = False
    evaluation_id: str = ''
    evidence_key: str = ''


@router.get('/cases')
def listing(request: Request):
    return {**service.listing(), 'builtin_warning': getattr(request.app.state, 'jira_testing_warning', '')}


@router.get('/import/{conversation_id}')
async def import_conversation(conversation_id: str):
    return await service.import_conversation(conversation_id)


@router.post('/packages/preview')
async def preview_package(request: Request):
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > jira_test_package.MAX_BYTES:
        raise ApiError(413, '用例包超过 41MB 限制')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > jira_test_package.MAX_BYTES:
            raise ApiError(413, '用例包超过 41MB 限制')
        raw.extend(chunk)
    return await run_in_threadpool(service.preview_package, bytes(raw))


@router.get('/cases/{case_id}/versions/{version_id}/package')
def export_package(case_id: str, version_id: str):
    return Response(service.export_package(case_id, version_id), media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="test-case.zip"', 'Cache-Control': 'no-store',
    })


@router.post('/cases')
def create(body: CaseBody, user: dict = Depends(reviewer)):
    return service.save(user, body.model_dump())


@router.get('/cases/{case_id}')
def detail(case_id: str):
    return service.detail(case_id)


@router.get('/cases/{case_id}/batches')
def batches(case_id: str):
    return service.batches(case_id)


@router.post('/cases/{case_id}/versions')
def version(case_id: str, body: CaseBody, user: dict = Depends(reviewer)):
    return service.save(user, body.model_dump(), case_id)


@router.post('/cases/{case_id}/batches')
async def run(case_id: str, body: RunBody, user: dict = Depends(reviewer)):
    return await service.run(user, case_id, body.model_dump())


@router.post('/runs/{conversation_id}/review')
def review(conversation_id: str, body: ReviewBody, user: dict = Depends(reviewer)):
    return service.review(user, conversation_id, body.model_dump())


@router.post('/batches/{batch_id}/cancel')
async def cancel_batch(batch_id: str):
    return await service.cancel_batch(batch_id)


class SuiteBody(RunBody):
    version_id: str = ''
    case_ids: list[str] = Field(default_factory=list, max_length=100)
    request_id: str = Field(min_length=8, max_length=100)


@router.get('/suites')
def suites():
    return batches_service.listing()


@router.post('/suites')
def submit_suite(body: SuiteBody, user: dict = Depends(reviewer)):
    return batches_service.submit(user, body.model_dump())


@router.get('/suites/{suite_id}')
def suite(suite_id: str):
    return batches_service.detail(suite_id)


@router.post('/suites/{suite_id}/stop')
def stop_suite(suite_id: str):
    return batches_service.stop(suite_id)


@router.post('/suites/{suite_id}/retry')
def retry_suite(suite_id: str):
    return batches_service.retry(suite_id)


@router.get('/runs/{conversation_id}/evaluations')
def evaluation_history(conversation_id: str):
    return evaluations.history(conversation_id)


@router.get('/runs/{conversation_id}/evaluations/{evaluation_id}/evidence')
def evaluation_evidence(conversation_id: str, evaluation_id: str):
    return evaluations.evidence(conversation_id, evaluation_id)


@router.post('/runs/{conversation_id}/evaluate')
def retry_evaluation(conversation_id: str):
    return evaluations.retry(conversation_id)


class DraftBody(BaseModel):
    agent_group: str
    input: dict
    reference: str = Field(default='', max_length=100000)


@router.post('/criteria/draft')
async def draft_criteria(body: DraftBody):
    return await evaluations.draft(body.model_dump())
