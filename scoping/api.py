from __future__ import annotations

from datetime import datetime
import hmac
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from scoping.jobs import (
    InvalidScopingJobTransition,
    JobStatus,
    ScopingJob,
    ScopingJobNotFound,
)
from scoping.models import ProjectMode
from scoping.service import ScopingService

router = APIRouter(prefix="/api/scoping", tags=["scoping"])


class CreateScopingJobRequest(BaseModel):
    recording_id: int = Field(ge=1)
    template_id: str
    mode: ProjectMode
    start_extraction: bool = True


class GenerateScopingDocumentRequest(BaseModel):
    include_inferred: bool = False


class TemplateModeResponse(BaseModel):
    id: ProjectMode
    label: str


class ScopingTemplateResponse(BaseModel):
    id: str
    name: str
    product: str
    version: str
    modes: list[TemplateModeResponse]
    answer_count_by_mode: dict[ProjectMode, int]


class ScopingAnswerResponse(BaseModel):
    id: str
    label: str
    type: str
    choices: list[str]
    applies_to: list[ProjectMode]
    guidance: str | None


class ScopingTemplateDetailResponse(ScopingTemplateResponse):
    answers: list[ScopingAnswerResponse]


class ScopingJobSummaryResponse(BaseModel):
    job_id: str
    recording_id: int
    recording_title: str | None
    template_id: str
    template_version: str
    mode: ProjectMode
    revision: int
    status: JobStatus
    found_count: int
    inferred_count: int
    unknown_count: int
    warning_count: int
    document_available: bool
    error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, job: ScopingJob) -> "ScopingJobSummaryResponse":
        answers = job.extraction.answers if job.extraction else []
        return cls(
            job_id=job.job_id,
            recording_id=job.recording_id,
            recording_title=job.recording_title,
            template_id=job.template_id,
            template_version=job.template_version,
            mode=job.mode,
            revision=job.revision,
            status=job.status,
            found_count=sum(answer.status == "found" for answer in answers),
            inferred_count=sum(answer.status == "inferred" for answer in answers),
            unknown_count=sum(answer.status == "unknown" for answer in answers),
            warning_count=len(job.extraction.warnings) if job.extraction else 0,
            document_available=job.status == "completed" and bool(job.output_path),
            error=job.error,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


@router.get("/templates", response_model=list[ScopingTemplateResponse])
async def list_scoping_templates(request: Request) -> list[ScopingTemplateResponse]:
    service = _service(request)
    return [
        ScopingTemplateResponse(
            id=template.id,
            name=template.name,
            product=template.product,
            version=template.version,
            modes=[TemplateModeResponse(id=mode.id, label=mode.label) for mode in template.project_modes],
            answer_count_by_mode={
                mode.id: len(template.extractable_answers(mode.id)) for mode in template.project_modes
            },
        )
        for template in service.catalog.list_templates()
    ]


@router.get("/templates/{template_id}", response_model=ScopingTemplateDetailResponse)
async def get_scoping_template(template_id: str, request: Request) -> ScopingTemplateDetailResponse:
    try:
        template = _service(request).catalog.get(template_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scoping template not found") from exc
    return ScopingTemplateDetailResponse(
        id=template.id,
        name=template.name,
        product=template.product,
        version=template.version,
        modes=[TemplateModeResponse(id=mode.id, label=mode.label) for mode in template.project_modes],
        answer_count_by_mode={
            mode.id: len(template.extractable_answers(mode.id)) for mode in template.project_modes
        },
        answers=[
            ScopingAnswerResponse(
                id=answer.id,
                label=answer.label,
                type=answer.type,
                choices=answer.choices,
                applies_to=answer.applies_to,
                guidance=answer.guidance,
            )
            for answer in template.answers
            if answer.extract
        ],
    )


@router.post("/jobs", response_model=ScopingJob, status_code=status.HTTP_201_CREATED)
async def create_scoping_job(
    payload: CreateScopingJobRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> ScopingJob:
    service = _service(request)
    try:
        job = service.create_job(
            recording_id=payload.recording_id,
            template_id=payload.template_id,
            mode=payload.mode,
        )
        if payload.start_extraction:
            job = service.claim_extraction(job.job_id)
            background_tasks.add_task(service.run_extraction, job.job_id)
        return job
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/jobs", response_model=list[ScopingJobSummaryResponse])
async def list_scoping_jobs(
    request: Request,
    recording_id: int | None = Query(default=None, ge=1),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ScopingJobSummaryResponse]:
    jobs = _service(request).store.list_jobs(
        recording_id=recording_id,
        status=job_status,
        limit=limit,
    )
    return [ScopingJobSummaryResponse.from_job(job) for job in jobs]


@router.get("/jobs/{job_id}", response_model=ScopingJob)
async def get_scoping_job(job_id: str, request: Request) -> ScopingJob:
    try:
        return _service(request).store.get_job(job_id)
    except ScopingJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scoping job not found") from exc


@router.post("/jobs/{job_id}/extract", response_model=ScopingJob, status_code=status.HTTP_202_ACCEPTED)
async def retry_scoping_extraction(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> ScopingJob:
    service = _service(request)
    try:
        job = service.claim_extraction(job_id)
    except ScopingJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scoping job not found") from exc
    except InvalidScopingJobTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background_tasks.add_task(service.run_extraction, job.job_id)
    return job


@router.post("/jobs/{job_id}/generate", response_model=ScopingJob, status_code=status.HTTP_202_ACCEPTED)
async def generate_scoping_document(
    job_id: str,
    payload: GenerateScopingDocumentRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> ScopingJob:
    service = _service(request)
    try:
        job = service.claim_generation(job_id, include_inferred=payload.include_inferred)
    except ScopingJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scoping job not found") from exc
    except InvalidScopingJobTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background_tasks.add_task(service.run_generation, job.job_id)
    return job


@router.get("/jobs/{job_id}/document", response_class=FileResponse)
async def download_scoping_document(job_id: str, request: Request) -> FileResponse:
    try:
        path = _service(request).completed_document(job_id)
    except ScopingJobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scoping job not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _service(request: Request) -> ScopingService:
    services: Any = getattr(request.app.state, "services", None)
    service = getattr(services, "scoping", None)
    if not isinstance(service, ScopingService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scoping service is unavailable",
        )
    if service.api_token:
        authorization = request.headers.get("Authorization", "")
        scheme, _, provided_token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            provided_token.encode("utf-8"),
            service.api_token.encode("utf-8"),
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid scoping API token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        client_host = request.client.host if request.client else ""
        if client_host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Scoping API is local-only until scoping.api_token is configured",
            )
    return service
