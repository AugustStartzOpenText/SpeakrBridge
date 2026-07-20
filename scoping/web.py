from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter(include_in_schema=False)
WEB_DIRECTORY = Path(__file__).resolve().parent / "web_assets"


@router.get("/scoping", response_class=FileResponse)
async def scoping_workflow_page() -> FileResponse:
    return FileResponse(WEB_DIRECTORY / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/scoping/app.js", response_class=FileResponse)
async def scoping_workflow_script() -> FileResponse:
    return FileResponse(WEB_DIRECTORY / "app.js", media_type="text/javascript")


@router.get("/scoping/styles.css", response_class=FileResponse)
async def scoping_workflow_styles() -> FileResponse:
    return FileResponse(WEB_DIRECTORY / "styles.css", media_type="text/css")
