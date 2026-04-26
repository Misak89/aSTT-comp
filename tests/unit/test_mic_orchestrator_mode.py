import pytest

from backend.app.services import mic_service


def _clear_mic_service_state() -> None:
    with mic_service._sessions_lock:
        mic_service._sessions.clear()
    with mic_service._v7_sequence_lock:
        mic_service._v7_sequence_timing.clear()


@pytest.fixture(autouse=True)
def _reset_mic_service_state() -> None:
    _clear_mic_service_state()
    yield
    _clear_mic_service_state()


def test_create_session_defaults_to_legacy_mode() -> None:
    session_id = mic_service.create_session("whisper_cpp_small", {})
    state = mic_service.get_session(session_id)

    assert state is not None
    assert state.orchestrator_mode == mic_service.ORCHESTRATOR_MODE_LEGACY
    assert state.run_id is None
    assert state.sequence_id is None

    payload = mic_service.get_orchestrator_payload(state)
    assert payload["orchestrator_mode"] == mic_service.ORCHESTRATOR_MODE_LEGACY
    assert payload.get("event_contract_schema") == "astt.mic.v7.event"
    assert payload.get("event_contract_version") == "1.0.0"


def test_create_session_merges_registry_mic_defaults() -> None:
    vosk_id = mic_service.create_session("vosk_small_cs_0_4", {})
    vosk = mic_service.get_session(vosk_id)
    assert vosk is not None
    assert vosk.model_params["chunk_seconds"] == 0.4
    assert vosk.model_params["input_gain_db"] == 1.0
    assert vosk.model_params["backpressure_high_s"] == 1.4
    assert vosk.model_params["backpressure_low_s"] == 0.5

    whisper_id = mic_service.create_session("whisper_cpp_base", {"threads": 6})
    whisper = mic_service.get_session(whisper_id)
    assert whisper is not None
    assert whisper.model_params["threads"] == 6
    assert whisper.model_params["beam_size"] == 1
    assert whisper.model_params["best_of"] == 1
    assert whisper.model_params["no_fallback"] is False
    assert whisper.model_params["initial_prompt"] == "Aspergerův syndrom"
    assert whisper.model_params["analysis_interval_ms"] == 2000
    assert whisper.model_params["backpressure_high_s"] == 2.0
    assert whisper.model_params["backpressure_low_s"] == 0.8


def test_create_session_v7_mode_sets_ids_from_sequence_metadata() -> None:
    session_id = mic_service.create_session(
        "whisper_cpp_small",
        {
            "mic_orchestrator_mode": "v7_cs_online",
            "auto_model_sequence_token": "seq_abc",
            "auto_model_sequence_index": 2,
            "auto_model_sequence_total": 5,
        },
    )
    state = mic_service.get_session(session_id)

    assert state is not None
    assert state.orchestrator_mode == mic_service.ORCHESTRATOR_MODE_V7
    assert state.run_id == f"run_{session_id}"
    assert state.sequence_id == "seq_abc"
    assert state.sequence_index == 2
    assert state.sequence_total == 5


def test_v7_mode_keeps_global_timeline_anchor_across_sessions() -> None:
    first_id = mic_service.create_session(
        "whisper_cpp_small",
        {
            "mic_orchestrator_mode": "v7_cs_online",
            "auto_model_sequence_token": "shared_seq",
            "auto_model_sequence_index": 1,
            "auto_model_sequence_total": 2,
        },
    )
    first = mic_service.get_session(first_id)
    assert first is not None
    with first._lock:
        first.started_at = "2026-01-01T00:00:00+00:00"
    first_payload = mic_service._update_v7_timing_on_start(first)
    assert first_payload["global_timeline_ms"] == 0.0

    second_id = mic_service.create_session(
        "whisper_cpp_small",
        {
            "mic_orchestrator_mode": "v7_preview",
            "auto_model_sequence_token": "shared_seq",
            "auto_model_sequence_index": 2,
            "auto_model_sequence_total": 2,
        },
    )
    second = mic_service.get_session(second_id)
    assert second is not None
    with second._lock:
        second.started_at = "2026-01-01T00:00:10+00:00"
    second_payload = mic_service._update_v7_timing_on_start(second)

    assert second_payload["sequence_id"] == "shared_seq"
    assert second_payload["global_timeline_ms"] == 10000.0
