from typing import Literal

from pydantic import BaseModel, Field


class ExecutionTarget(BaseModel):
    target: str = Field(max_length=300)
    purpose: str = Field(default="", max_length=500)


class ExecutionConfig(BaseModel):
    mode: Literal["auto", "custom"] = "auto"
    selection_policy: Literal["all_required", "as_needed"] = "as_needed"
    targets: list[ExecutionTarget] = Field(default_factory=list, max_length=32)
    dataset_requirement: Literal["auto", "required", "not_required"] = "auto"


class PreflightContext(BaseModel):
    kind: Literal["jira", "test", "suite"]
    issue_key: str = ""
    case_id: str = ""
    version_id: str = ""
    case_ids: list[str] = Field(default_factory=list, max_length=100)


class PreflightBody(BaseModel):
    agent_group: str
    context: PreflightContext
    execution: ExecutionConfig
    key_sessions: dict[str, str] = Field(default_factory=dict)


class ExecutionSubmission(BaseModel):
    preflight_id: str = ""
    request_id: str = Field(default="", max_length=100)
    acknowledged_issue_ids: list[str] = Field(default_factory=list, max_length=1000)
