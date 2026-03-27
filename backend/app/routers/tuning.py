"""Tuning router — hledání nejlepší kombinace parametrů modelu."""
import subprocess
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from ..models.tuning import TuningJobRequest, TuningJobStatus
from ..services import tuning_service
from ..config import TUNING_ROOT

router = APIRouter(prefix="/api/tuning")


@router.post("/jobs", response_model=TuningJobStatus, status_code=202)
def create_tuning_job(req: TuningJobRequest):
    return tuning_service.create_job(req)


@router.get("/jobs", response_model=list[TuningJobStatus])
def list_tuning_jobs():
    return tuning_service.list_jobs()


@router.get("/jobs/{job_id}", response_model=TuningJobStatus)
def get_tuning_job(job_id: str):
    job = tuning_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="tuning job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_tuning_job(job_id: str):
    job = tuning_service.cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="tuning job not found")
    return job


@router.post("/cleanup-trial-files")
def cleanup_trial_files(min_age_days: int = 10, min_newer_jobs: int = 3):
    """Smaže trial_NNN/ adresáře a clip WAVy ze starých dokončených jobů.
    Podmínky: job starší než min_age_days dní A existuje min_newer_jobs novějších completed jobů.
    """
    deleted = tuning_service.cleanup_old_trial_files(min_age_days, min_newer_jobs)
    return {"deleted_items": deleted}


@router.post("/jobs/{job_id}/open-dir")
def open_tuning_job_dir(job_id: str):
    job_dir = TUNING_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job dir not found")
    subprocess.Popen(["explorer", str(job_dir)])
    return JSONResponse({"path": str(job_dir)})
