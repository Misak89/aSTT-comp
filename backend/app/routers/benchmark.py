import subprocess
import sys

from fastapi import APIRouter, HTTPException, BackgroundTasks

from ..models.benchmark import BenchmarkJobRequest, BenchmarkJobStatus, JobListResponse, Scenario, LiveJobProgress
from ..services import benchmark_service, scenario_service
from ..config import JOBS_ROOT

router = APIRouter(prefix="/api/benchmark")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@router.post("/jobs", response_model=BenchmarkJobStatus, status_code=202)
def create_job(req: BenchmarkJobRequest, background_tasks: BackgroundTasks):
    # Pokud je zadán scenario_id, doplníme chybějící pole ze scénáře
    if req.scenario_id:
        sc = scenario_service.get_scenario(req.scenario_id)
        if sc:
            req = _merge_scenario(req, sc)
    job = benchmark_service.create_job(req)
    background_tasks.add_task(benchmark_service.run_job, job.job_id)
    return job


@router.get("/jobs", response_model=JobListResponse)
def list_jobs():
    return JobListResponse(jobs=benchmark_service.list_jobs())


@router.get("/jobs/{job_id}", response_model=BenchmarkJobStatus)
def get_job(job_id: str):
    job = benchmark_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=BenchmarkJobStatus)
def cancel_job(job_id: str):
    job = benchmark_service.cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/jobs/{job_id}/open-dir")
def open_job_dir(job_id: str):
    """Otevře adresář jobu v průzkumníku souborů (lokální nástroj)."""
    job_dir = JOBS_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job dir not found")
    _open_in_explorer(job_dir)
    return {"path": str(job_dir)}


@router.get("/jobs/{job_id}/live", response_model=LiveJobProgress)
def get_live_progress(job_id: str):
    """Live data pro running job: progress percent + HW serie pro grafy."""
    data = benchmark_service.get_live_progress(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job not found")
    return data


@router.get("/options")
def get_options():
    return benchmark_service.get_options()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

@router.get("/scenarios", response_model=list[Scenario])
def list_scenarios():
    return scenario_service.list_scenarios()


@router.post("/scenarios", response_model=Scenario, status_code=201)
def create_scenario(scenario: Scenario):
    return scenario_service.save_scenario(scenario)


@router.get("/scenarios/{scenario_id}", response_model=Scenario)
def get_scenario(scenario_id: str):
    sc = scenario_service.get_scenario(scenario_id)
    if sc is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return sc


@router.put("/scenarios/{scenario_id}", response_model=Scenario)
def update_scenario(scenario_id: str, scenario: Scenario):
    if scenario.scenario_id != scenario_id:
        raise HTTPException(status_code=400, detail="scenario_id mismatch")
    return scenario_service.save_scenario(scenario)


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str):
    if not scenario_service.delete_scenario(scenario_id):
        raise HTTPException(status_code=404, detail="scenario not found")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _merge_scenario(req: BenchmarkJobRequest, sc: Scenario) -> BenchmarkJobRequest:
    """Doplní prázdná pole requestu ze scénáře."""
    data = req.model_dump()
    if not data.get("video_ids") and sc.video_ids:
        data["video_ids"] = sc.video_ids
    if not data.get("model_ids") and sc.model_ids:
        data["model_ids"] = sc.model_ids
    if not data.get("setting_ids") and sc.setting_ids:
        data["setting_ids"] = sc.setting_ids
    if not data.get("clip_seed") and sc.clip_seed is not None:
        data["clip_seed"] = sc.clip_seed
    data["sample_seconds"] = data.get("sample_seconds") or sc.clip_seconds
    return BenchmarkJobRequest(**data)


def _open_in_explorer(path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass
