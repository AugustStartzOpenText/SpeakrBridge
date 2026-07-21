from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from onenote_writer import OneNoteSection, OneNoteWriteResult
from pending_jobs import PendingJob


router = APIRouter(include_in_schema=False)
api_router = APIRouter(prefix="/api/queue", tags=["queue"])
WEB_DIRECTORY = Path(__file__).resolve().parent / "queue_assets"
LOGGER = logging.getLogger(__name__)


class PendingJobSummary(BaseModel):
    job_id: str
    recording_id: int
    delivery_id: str | None
    event: str
    meeting_title: str
    created_at: datetime
    last_error: str | None
    last_attempted_at: datetime | None

    @classmethod
    def from_job(cls, job: PendingJob) -> "PendingJobSummary":
        return cls(
            job_id=job.job_id,
            recording_id=job.recording_id,
            delivery_id=job.delivery_id,
            event=job.event,
            meeting_title=job.meeting_title,
            created_at=job.created_at,
            last_error=job.last_error,
            last_attempted_at=job.last_attempted_at,
        )


class OneNoteSectionResponse(BaseModel):
    notebook_id: str
    notebook_name: str
    section_id: str
    section_name: str
    path: str
    is_saved_default: bool

    @classmethod
    def from_section(cls, section: OneNoteSection, saved: OneNoteSection | None) -> "OneNoteSectionResponse":
        return cls(
            notebook_id=section.notebook_id,
            notebook_name=section.notebook_name,
            section_id=section.section_id,
            section_name=section.section_name,
            path=section.path,
            is_saved_default=saved is not None and section.section_id == saved.section_id,
        )


class RoutePendingJobRequest(BaseModel):
    section_id: str | None = None
    save_as_default: bool = False


class RoutePendingJobResponse(BaseModel):
    job_id: str
    page_id: str
    page_link: str
    section: OneNoteSectionResponse


@router.get("/queue", response_class=FileResponse)
async def pending_queue_page() -> FileResponse:
    return FileResponse(WEB_DIRECTORY / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/queue/app.js", response_class=FileResponse)
async def pending_queue_script() -> FileResponse:
    return FileResponse(WEB_DIRECTORY / "app.js", media_type="text/javascript")


@router.get("/queue/styles.css", response_class=FileResponse)
async def pending_queue_styles() -> FileResponse:
    return FileResponse(WEB_DIRECTORY / "styles.css", media_type="text/css")


@api_router.get("/jobs", response_model=list[PendingJobSummary])
def list_pending_queue_jobs(request: Request) -> list[PendingJobSummary]:
    services = _services(request)
    return [PendingJobSummary.from_job(job) for job in services.pending_jobs.list_jobs()]


@api_router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pending_queue_job(job_id: str, request: Request) -> Response:
    services = _services(request)
    if services.pending_jobs.get_job(job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending job not found")
    services.pending_jobs.delete_job(job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.get("/sections", response_model=list[OneNoteSectionResponse])
def list_onenote_queue_sections(request: Request) -> list[OneNoteSectionResponse]:
    services = _services(request)
    saved = services.onenote.get_saved_destination()
    return [OneNoteSectionResponse.from_section(section, saved) for section in services.onenote.list_sections()]


@api_router.post("/jobs/{job_id}/route", response_model=RoutePendingJobResponse)
def route_pending_queue_job(
    job_id: str,
    payload: RoutePendingJobRequest,
    request: Request,
) -> RoutePendingJobResponse:
    services = _services(request)
    job = services.pending_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending job not found")

    section = _find_section(services, payload.section_id) if payload.section_id else None
    try:
        if section is None:
            result = services.onenote.write_page(job.page)
        else:
            result = services.onenote.write_page_to_section(job.page, section)
            if payload.save_as_default:
                result_section = services.onenote.set_destination(section.section_id)
                result = OneNoteWriteResult(
                    page_id=result.page_id,
                    page_link=result.page_link,
                    section=result_section,
                )
        _register_scoping_inbox_item(services, job, result)
        services.pending_jobs.delete_job(job.job_id)
        services.notifier.notify_success(job.meeting_title, result.section.path)
        return RoutePendingJobResponse(
            job_id=job.job_id,
            page_id=result.page_id,
            page_link=result.page_link,
            section=OneNoteSectionResponse.from_section(result.section, result.section),
        )
    except HTTPException:
        raise
    except Exception as exc:
        services.pending_jobs.mark_failed(job, str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


def _find_section(services: Any, section_id: str | None) -> OneNoteSection:
    for section in services.onenote.list_sections():
        if section.section_id == section_id:
            return section
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OneNote section not found")


def _register_scoping_inbox_item(services: Any, job: PendingJob, result: OneNoteWriteResult) -> None:
    if getattr(services, "scoping", None) is None:
        return
    try:
        services.scoping.store.enqueue_recording(
            recording_id=job.recording_id,
            recording_title=job.meeting_title,
            onenote_page_id=result.page_id,
            onenote_link=result.page_link,
        )
    except Exception:
        LOGGER.exception(
            "Failed to register scoping inbox item",
            extra={"recording_id": job.recording_id, "onenote_page_id": result.page_id},
        )


def _services(request: Request) -> Any:
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Queue API is local-only",
        )
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Services unavailable")
    return services
