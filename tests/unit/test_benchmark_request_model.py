from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.models.benchmark import BenchmarkJobRequest


def test_benchmark_request_accepts_segment_start_seconds():
    req = BenchmarkJobRequest(
        video_ids=["vid001"],
        model_ids=["whisper_cpp_small"],
        setting_ids=["balanced"],
        evaluation_mode="streaming",
        sample_seconds=120,
        segment_start_seconds=35,
    )
    assert req.segment_start_seconds == 35


def test_benchmark_request_rejects_negative_segment_start_seconds():
    with pytest.raises(ValidationError):
        BenchmarkJobRequest(
            video_ids=["vid001"],
            model_ids=["whisper_cpp_small"],
            setting_ids=["balanced"],
            evaluation_mode="streaming",
            sample_seconds=120,
            segment_start_seconds=-1,
        )
