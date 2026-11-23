"""Validated inputs and outputs of the AI layer."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Check = Literal["fail", "pass"]


class FailingRequest(BaseModel):
    method: str = "POST"
    path: str = "/checkout"
    body: Any = None
    # None when nobody recorded it; the pipeline then measures it in the lab.
    status: int | None = None
    response_excerpt: str = ""


class IncidentContext(BaseModel):
    incident_id: str
    broken_rev: str
    healthy_rev: str
    broken_slot: str
    failing_request: FailingRequest
    shop_repo: Path
    container_logs_hint: str | None = None


class Evidence(BaseModel):
    kind: Literal["diff", "log", "config", "request", "source"]
    ref: str | None
    excerpt: str
    url: str | None = None


class Finding(BaseModel):
    agent: str
    claim: str
    confidence: float = Field(ge=0, le=1)
    suspect_type: Literal["code", "config", "dependency", "unknown"]
    suspect_ref: str | None
    evidence: list[Evidence]


class Decision(BaseModel):
    cause_type: Literal["code", "config", "unknown"]
    suspect_ref: str | None
    remedy: Literal["code_fix", "config_restore", "escalate"]
    reasoning: str
    uses_findings: list[str]


class Candidate(BaseModel):
    candidate_id: str
    rev: str
    diff: str
    rationale: str
    by: Literal["agent", "judge"]


class Verification(BaseModel):
    candidate_id: str
    broken: Check
    healthy: Check
    candidate: Check
    unit_tests: Check
    diff_review_ok: bool
    issues: list[str]
    verdict: Literal["VERIFIED", "REJECTED"]
    reason: str


class PipelineOutcome(BaseModel):
    status: Literal["verified", "escalated"]
    candidate_id: str | None = None
    candidate_rev: str | None = None
    verification: Verification | None = None
