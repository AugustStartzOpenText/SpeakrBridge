from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Request, status
from fastapi.responses import JSONResponse

from config import AppConfig, load_config
from notifier import Notifier
from onenote_writer import OneNoteSection
from ollama_client import OllamaClient
from onenote_writer import OneNoteWriter
from pending_jobs import PendingJob, PendingJobStore
from page_builder import build_page
from scoping.api import router as scoping_router
from scoping.catalog import ScopingTemplateCatalog
from scoping.extraction import ScopingExtractor
from scoping.jobs import ScopingJobStore
from scoping.service import ScopingService
from scoping.word_writer import WordScopingWriter
from speakr_client import SpeakrClient
from webhook import validate_speakr_request

LOGGER = logging.getLogger(__name__)


class AppServices:
    def __init__(self, config: AppConfig) -> None:
        base_dir = Path(__file__).resolve().parent
        self.config = config
        self.speakr = SpeakrClient(config.speakr)
        self.ollama = OllamaClient(config.ollama)
        self.onenote = OneNoteWriter(config.onenote)
        self.pending_jobs = PendingJobStore()
        self.notifier = Notifier(config.notifications.enabled)
        self.scoping: ScopingService | None = None
        if config.scoping.enabled:
            database_path = _resolve_config_path(base_dir, config.scoping.database_file)
            output_directory = _resolve_config_path(base_dir, config.scoping.output_directory)
            self.scoping = ScopingService(
                catalog=ScopingTemplateCatalog(base_dir=base_dir),
                store=ScopingJobStore(database_path),
                recording_source=self.speakr,
                extractor=ScopingExtractor(config.ollama),
                writer=WordScopingWriter(),
                output_directory=output_directory,
                api_token=config.scoping.api_token,
            )
            recovered = self.scoping.recover_interrupted()
            if recovered:
                LOGGER.warning("Recovered interrupted scoping jobs", extra={"count": recovered})


def _resolve_config_path(base_dir: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def configure_logging(config: AppConfig) -> None:
    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(config.logging.file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    configure_logging(config)
    app.state.services = AppServices(config)
    LOGGER.info("SpeakrBridge started", extra={"port": config.listener.port})
    yield


app = FastAPI(title="SpeakrBridge", version="0.1.0", lifespan=lifespan)
app.include_router(scoping_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook/speakr")
async def speakr_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    services: AppServices = request.app.state.services
    validated = await validate_speakr_request(request, services.config.listener.hmac_secret)
    LOGGER.info(
        "Accepted webhook",
        extra={
            "delivery_id": validated.delivery_id,
            "event": validated.event,
            "recording_id": validated.payload.recording.id,
        },
    )
    background_tasks.add_task(
        process_delivery,
        services,
        validated.payload.recording.id,
        validated.delivery_id,
        validated.event,
    )
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"accepted": True})


async def process_delivery(
    services: AppServices,
    recording_id: int,
    delivery_id: str | None,
    event: str,
) -> None:
    try:
        prepared_job = await prepare_pending_job(services, recording_id, delivery_id, event)
        if services.config.onenote.manual_selection:
            duplicate = services.pending_jobs.find_duplicate(
                recording_id=recording_id,
                delivery_id=delivery_id,
                event=event,
            )
            if duplicate is not None:
                LOGGER.info(
                    "Skipped duplicate pending job",
                    extra={"recording_id": recording_id, "job_id": duplicate.job_id, "delivery_id": delivery_id},
                )
                return
            services.pending_jobs.save_job(prepared_job)
            LOGGER.info(
                "Queued pending OneNote routing job",
                extra={"recording_id": recording_id, "job_id": prepared_job.job_id, "delivery_id": delivery_id},
            )
            services.notifier.notify_success(prepared_job.meeting_title, "Queued for manual routing")
            return

        result = deliver_pending_job_to_default(services, prepared_job)
        LOGGER.info(
            "OneNote page created",
            extra={
                "page_id": result.page_id,
                "recording_id": recording_id,
                "section_id": result.section.section_id,
                "section_name": result.section.section_name,
            },
        )
        services.notifier.notify_success(prepared_job.meeting_title, result.section.path)
    except Exception as exc:
        LOGGER.exception("Failed to process Speakr delivery", extra={"recording_id": recording_id})
        try:
            if "prepared_job" in locals():
                services.onenote.write_fallback_file(prepared_job.page)
        except Exception:
            LOGGER.exception("Failed to write fallback output")
        services.notifier.notify_failure(str(exc))


async def prepare_pending_job(
    services: AppServices,
    recording_id: int,
    delivery_id: str | None,
    event: str,
) -> PendingJob:
    bundle = await services.speakr.fetch_recording_bundle(recording_id)
    structured_summary = await services.ollama.summarize(bundle.transcript)
    page = build_page(bundle, structured_summary, services.config)
    return services.pending_jobs.create_job(
        recording_id=recording_id,
        delivery_id=delivery_id,
        event=event,
        meeting_title=bundle.metadata.title,
        page=page,
    )


def deliver_pending_job_to_default(services: AppServices, job: PendingJob):
    return services.onenote.write_page(job.page)


def deliver_pending_job_to_section(services: AppServices, job: PendingJob, section: OneNoteSection):
    return services.onenote.write_page_to_section(job.page, section)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SpeakrBridge service and OneNote destination tools")
    parser.add_argument(
        "--list-onenote-sections",
        action="store_true",
        help="List available OneNote notebook/section destinations and exit.",
    )
    parser.add_argument(
        "--set-destination",
        action="store_true",
        help="Interactively select and save the default OneNote destination.",
    )
    parser.add_argument(
        "--list-pending",
        action="store_true",
        help="List queued OneNote routing jobs and exit.",
    )
    parser.add_argument(
        "--route-pending",
        action="store_true",
        help="Interactively route queued OneNote jobs.",
    )
    parser.add_argument(
        "--route-job",
        help="Route a specific queued job by job id.",
    )
    return parser


def list_onenote_sections(services: AppServices) -> int:
    sections = services.onenote.list_sections()
    if not sections:
        print("No OneNote sections found.")
        return 1

    saved = services.onenote.get_saved_destination()
    for index, section in enumerate(sections, start=1):
        marker = " (saved default)" if saved and saved.section_id == section.section_id else ""
        print(f"{index}. {services.onenote.format_section_choice(section)}{marker}")
    return 0


def prompt_for_destination(services: AppServices) -> int:
    sections = services.onenote.list_sections()
    if not sections:
        print("No OneNote sections found.")
        return 1

    saved = services.onenote.get_saved_destination()
    for index, section in enumerate(sections, start=1):
        marker = " (saved default)" if saved and saved.section_id == section.section_id else ""
        print(f"{index}. {services.onenote.format_section_choice(section)}{marker}")

    selection = input("Select destination number: ").strip()
    if not selection.isdigit():
        print("Selection must be a number.")
        return 1

    selected_index = int(selection)
    if selected_index < 1 or selected_index > len(sections):
        print("Selection out of range.")
        return 1

    chosen = services.onenote.set_destination(sections[selected_index - 1].section_id)
    print(f"Saved destination: {services.onenote.format_section_choice(chosen)}")
    return 0


def list_pending_jobs(services: AppServices) -> int:
    jobs = services.pending_jobs.list_jobs()
    if not jobs:
        print("No pending jobs.")
        return 0

    for index, job in enumerate(jobs, start=1):
        attempted = f" | last error: {job.last_error}" if job.last_error else ""
        print(
            f"{index}. {job.job_id} | recording {job.recording_id} | {job.meeting_title} | "
            f"{job.created_at.isoformat()}{attempted}"
        )
    return 0


def route_pending_jobs(services: AppServices, job_id: str | None = None) -> int:
    jobs = [services.pending_jobs.get_job(job_id)] if job_id else services.pending_jobs.list_jobs()
    jobs = [job for job in jobs if job is not None]
    if not jobs:
        print("No pending jobs.")
        return 0

    for job in jobs:
        outcome = route_single_job(services, job)
        if outcome != 0 and job_id:
            return outcome
    return 0


def route_single_job(services: AppServices, job: PendingJob) -> int:
    print()
    print(f"Job: {job.job_id}")
    print(f"Title: {job.meeting_title}")
    print(f"Recording ID: {job.recording_id}")
    print(f"Created: {job.created_at.isoformat()}")

    saved = services.onenote.get_saved_destination()
    print("Options:")
    print("1. Use saved default destination")
    print("2. Choose another section")
    print("3. Skip for now")

    selection = input("Select routing option: ").strip()
    if selection == "1":
        return _write_job_with_default(services, job)
    if selection == "2":
        return _write_job_with_selected_section(services, job, saved)
    if selection == "3":
        print("Skipped.")
        return 0

    print("Selection out of range.")
    return 1


def _write_job_with_default(services: AppServices, job: PendingJob) -> int:
    try:
        result = deliver_pending_job_to_default(services, job)
        services.pending_jobs.delete_job(job.job_id)
        print(f"Wrote page to {result.section.path}")
        services.notifier.notify_success(job.meeting_title, result.section.path)
        return 0
    except Exception as exc:
        services.pending_jobs.mark_failed(job, str(exc))
        LOGGER.exception("Failed to route pending job", extra={"job_id": job.job_id})
        print(f"Failed to route job: {exc}")
        return 1


def _write_job_with_selected_section(
    services: AppServices,
    job: PendingJob,
    saved: OneNoteSection | None,
) -> int:
    sections = services.onenote.list_sections()
    if not sections:
        print("No OneNote sections found.")
        return 1

    for index, section in enumerate(sections, start=1):
        marker = " (saved default)" if saved and saved.section_id == section.section_id else ""
        print(f"{index}. {services.onenote.format_section_choice(section)}{marker}")

    selection = input("Select destination number: ").strip()
    if not selection.isdigit():
        print("Selection must be a number.")
        return 1

    selected_index = int(selection)
    if selected_index < 1 or selected_index > len(sections):
        print("Selection out of range.")
        return 1

    section = sections[selected_index - 1]
    try:
        result = deliver_pending_job_to_section(services, job, section)
        services.pending_jobs.delete_job(job.job_id)
        update_default = input("Save this as the new default destination? [y/N]: ").strip().lower()
        if update_default == "y":
            services.onenote.set_destination(section.section_id)
        print(f"Wrote page to {result.section.path}")
        services.notifier.notify_success(job.meeting_title, result.section.path)
        return 0
    except Exception as exc:
        services.pending_jobs.mark_failed(job, str(exc))
        LOGGER.exception("Failed to route pending job", extra={"job_id": job.job_id, "section_id": section.section_id})
        print(f"Failed to route job: {exc}")
        return 1


if __name__ == "__main__":
    app_config = load_config()
    configure_logging(app_config)
    args = build_arg_parser().parse_args()

    if (
        args.list_onenote_sections
        or args.set_destination
        or args.list_pending
        or args.route_pending
        or args.route_job
    ):
        services = AppServices(app_config)
        if args.list_onenote_sections:
            raise SystemExit(list_onenote_sections(services))
        if args.set_destination:
            raise SystemExit(prompt_for_destination(services))
        if args.list_pending:
            raise SystemExit(list_pending_jobs(services))
        if args.route_pending:
            raise SystemExit(route_pending_jobs(services))
        raise SystemExit(route_pending_jobs(services, args.route_job))

    uvicorn.run("main:app", host=app_config.listener.host, port=app_config.listener.port, reload=False)
