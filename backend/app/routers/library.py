from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from ..models.library import (
    LibraryItem,
    LatestResult,
    DownloadSubtitlesRequest,
    UpsertLibraryItemRequest,
    UpdateLibraryVisibilityRequest,
    ScanDirectoryRequest,
    ImportLocalFileRequest,
    LocalFileEntry,
    SegmentBundle,
    SegmentBundlePreviewRequest,
)
from ..services import library_service

router = APIRouter(prefix="/api/library")


@router.get("/items", response_model=list[LibraryItem])
def list_items():
    return library_service.list_items()


@router.post("/items", response_model=LibraryItem, status_code=201)
def upsert_item(req: UpsertLibraryItemRequest):
    return library_service.upsert_item(req)


@router.post("/items/{video_id}/visibility", response_model=LibraryItem)
def set_visibility(video_id: str, req: UpdateLibraryVisibilityRequest):
    try:
        return library_service.set_item_visibility(video_id, req.visible_in_menus)
    except KeyError:
        raise HTTPException(status_code=404, detail="video_id not found")


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


@router.get("/video-info")
def get_video_info(url: str = Query(...)):
    """Vrátí název a základní metadata YouTube videa pro preview před přidáním."""
    try:
        return library_service.fetch_video_info(url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/scan-directory", response_model=list[LocalFileEntry])
def scan_directory(req: ScanDirectoryRequest):
    """Prohledá lokální adresář a vrátí seznam audio/video souborů."""
    try:
        return library_service.scan_directory(req.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/import-local-file", response_model=LibraryItem, status_code=201)
def import_local_file(req: ImportLocalFileRequest):
    """Importuje lokální soubor do knihovny (bez yt-dlp)."""
    try:
        return library_service.import_local_file(req.path, req.title, req.language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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


@router.post("/segment-bundles/preview", response_model=SegmentBundle)
def preview_segment_bundle(req: SegmentBundlePreviewRequest):
    try:
        return library_service.preview_segment_bundle(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/segment-bundles/{source_id}", response_model=SegmentBundle)
def upsert_segment_bundle(source_id: str, req: SegmentBundlePreviewRequest):
    try:
        return library_service.upsert_segment_bundle(source_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/segment-bundles/{source_id}", response_model=SegmentBundle)
def get_segment_bundle(source_id: str):
    try:
        return library_service.get_segment_bundle(source_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="segment bundle not found")
