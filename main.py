from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Request, status
from fastapi.responses import JSONResponse

from config import AppConfig, load_config
from notifier import Notifier
from ollama_client import OllamaClient
from onenote_writer import OneNoteWriter
from page_builder import build_page
from speakr_client import SpeakrClient
from webhook import validate_speakr_request

LOGGER = logging.getLogger(__name__)


class AppServices:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.speakr = SpeakrClient(config.speakr)
        self.ollama = OllamaClient(config.ollama)
        self.onenote = OneNoteWriter(config.onenote)
        self.notifier = Notifier(config.notifications.enabled)


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
    background_tasks.add_task(process_delivery, services, validated.payload.recording.id)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"accepted": True})


async def process_delivery(services: AppServices, recording_id: int) -> None:
    try:
        bundle = await services.speakr.fetch_recording_bundle(recording_id)
        structured_summary = await services.ollama.summarize(bundle.transcript)
        page = build_page(bundle, structured_summary, services.config)
        page_id = services.onenote.write_page(page)
        LOGGER.info("OneNote page created", extra={"page_id": page_id, "recording_id": recording_id})
        services.notifier.notify_success(bundle.metadata.title, services.config.onenote.section)
    except Exception as exc:
        LOGGER.exception("Failed to process Speakr delivery", extra={"recording_id": recording_id})
        try:
            if "page" in locals():
                services.onenote.write_fallback_file(page)
        except Exception:
            LOGGER.exception("Failed to write fallback output")
        services.notifier.notify_failure(str(exc))


if __name__ == "__main__":
    app_config = load_config()
    uvicorn.run(
        "main:app",
        host=app_config.listener.host,
        port=app_config.listener.port,
        reload=False,
    )

