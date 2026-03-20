from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..models.runs import RunSummary, RunDetail
from ..services import results_service

router = APIRouter(prefix="/api/runs")


@router.get("", response_model=list[RunSummary])
def list_runs():
    return results_service.list_runs()


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str):
    run = results_service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get("/{run_id}/file/{filepath:path}", response_class=PlainTextResponse)
def get_run_file(run_id: str, filepath: str):
    """Serve a run artifact file (path-traversal protected)."""
    content = results_service.read_run_file(run_id, filepath)
    if content is None:
        raise HTTPException(status_code=404)
    return content
