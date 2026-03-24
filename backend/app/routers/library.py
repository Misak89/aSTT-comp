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


@router.post("/search")
def search_youtube(req: dict):
    return library_service.search_youtube(
        q=req.get("q", ""),
        max_results=int(req.get("max_results", 20)),
        min_duration=int(req.get("min_duration", 0)),
        max_duration=int(req.get("max_duration", 0)),
        min_views=int(req.get("min_views", 0)),
        uploaded_after=req.get("uploaded_after", ""),
        audio_langs=req.get("audio_langs") or None,
        subtitle_langs=req.get("subtitle_langs") or None,
        subtitle_type=req.get("subtitle_type", "any"),
        content_type=req.get("content_type", "any"),
        categories=req.get("categories") or None,
    )
