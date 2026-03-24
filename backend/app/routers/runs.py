from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..models.runs import RunSummary, RunDetail
from ..services import results_service

router = APIRouter(prefix="/api/runs")

from packages.benchmarks.metrics.text_metrics import word_diff as compute_word_diff


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


@router.get("/{run_id}/results/{result_idx}/sources/{source_idx}/diff")
def get_word_diff(run_id: str, result_idx: int, source_idx: int):
    """Word-level diff (alignment) mezi referenčním textem a přepisem."""
    run = results_service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if result_idx >= len(run.results):
        raise HTTPException(status_code=404, detail="result_idx out of range")
    result = run.results[result_idx]
    if source_idx >= len(result.source_metrics):
        raise HTTPException(status_code=404, detail="source_idx out of range")
    sm = result.source_metrics[source_idx]
    if not sm.transcript or not sm.reference_text:
        raise HTTPException(status_code=422, detail="missing transcript or reference_text")
    diff = compute_word_diff(sm.reference_text, sm.transcript)
    stats = {
        "total": len(diff),
        "correct": sum(1 for d in diff if d["op"] == "="),
        "substitutions": sum(1 for d in diff if d["op"] == "S"),
        "deletions": sum(1 for d in diff if d["op"] == "D"),
        "insertions": sum(1 for d in diff if d["op"] == "I"),
    }
    return {
        "run_id": run_id,
        "model_id": result.model_id,
        "setting_id": result.setting_id,
        "diff": diff,
        "stats": stats,
    }
