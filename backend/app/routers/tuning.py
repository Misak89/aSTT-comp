"""Tuning router — hledání nejlepší kombinace parametrů modelu."""
from fastapi import APIRouter, HTTPException
from ..models.tuning import TuningJobRequest, TuningJobStatus
from ..services import tuning_service

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
