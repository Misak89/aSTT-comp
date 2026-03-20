from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..models.library import (
    LibraryItem,
    LatestResult,
    DownloadSubtitlesRequest,
    UpsertLibraryItemRequest,
)
from ..services import library_service

router = APIRouter(prefix="/api/library")


@router.get("/items", response_model=list[LibraryItem])
def list_items():
    return library_service.list_items()


@router.post("/items", response_model=LibraryItem, status_code=201)
def upsert_item(req: UpsertLibraryItemRequest):
    return library_service.upsert_item(req)


@router.post("/download-subtitles")
def download_subtitles(req: DownloadSubtitlesRequest):
    result = library_service.download_subtitles(req.video_id, req.url)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result.get("error", "failed"))
    return result


@router.get("/subtitle/{video_id}/{filename}", response_class=PlainTextResponse)
def get_subtitle_file(video_id: str, filename: str):
    content = library_service.read_subtitle_file(video_id, filename)
    if content is None:
        raise HTTPException(status_code=404)
    return content


@router.get("/latest-results/{video_id}", response_model=list[LatestResult])
def latest_results(video_id: str):
    return library_service.get_latest_results(video_id)
