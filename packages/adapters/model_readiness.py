from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
from typing import Callable

from packages.adapters.sherpa_onnx_runner import (
    detect_sherpa_bundle_language,
    list_sherpa_model_bundles,
    resolve_sherpa_model_bundle,
)
from packages.adapters.vosk_runner import detect_vosk_model_language, resolve_vosk_model_dir
from packages.adapters.whisper_cpp_runner import resolve_whisper_cli, resolve_whisper_model_file
from packages.adapters.faster_whisper_runner import resolve_faster_whisper_model_path

ModuleChecker = Callable[[str], bool]


@dataclass(frozen=True)
class ModelCheckResult:
    model_id: str
    label: str
    model_present: bool
    runtime_ready: bool
    adapter_implemented: bool
    ready_for_real: bool
    ready_for_synthetic: bool
    model_path: str
    runtime_hint: str | None
    issues: list[str]
    ready_for_live: bool = False
    live_streaming: bool = False
    live_block_reason: str | None = None
    live_notes: list[str] | None = None
    live_language_hint: str | None = None
    live_supported_languages: list[str] | None = None

    def to_record(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "label": self.label,
            "model_present": self.model_present,
            "runtime_ready": self.runtime_ready,
            "adapter_implemented": self.adapter_implemented,
            "ready_for_real": self.ready_for_real,
            "ready_for_synthetic": self.ready_for_synthetic,
            "model_path": self.model_path,
            "runtime_hint": self.runtime_hint,
            "issues": self.issues,
            "ready_for_live": self.ready_for_live,
            "live_streaming": self.live_streaming,
            "live_block_reason": self.live_block_reason,
            "live_notes": list(self.live_notes or []),
            "live_language_hint": self.live_language_hint,
            "live_supported_languages": list(self.live_supported_languages or []),
        }


def collect_model_readiness(
    model_store_root: str | Path = ".runtime/model_store",
    *,
    module_checker: ModuleChecker | None = None,
) -> list[dict[str, object]]:
    root = Path(model_store_root)
    checker = module_checker or _module_available
    whisper_bin = resolve_whisper_cli(root)

    records = [
        _check_whisper_base(root, whisper_bin),
        _check_whisper_small(root, whisper_bin),
        _check_whisper_large_v3(root, whisper_bin),
        _check_whisper_large_v3_turbo(root, whisper_bin),
        _check_sherpa(root, checker),
        _check_sherpa_parakeet_cs(root, checker),
        _check_vosk_small_cs(root, checker),
        _check_faster_whisper_small_cs(root, checker),
        _check_faster_whisper_medium_cs(root, checker),
        _check_qwen_0_6b(root, checker),
        _check_qwen_1_7b(root, checker),
    ]
    return [record.to_record() for record in records]


def summarize_readiness(records: list[dict[str, object]]) -> dict[str, int]:
    ready_real = sum(1 for record in records if bool(record.get("ready_for_real")))
    ready_synth = sum(1 for record in records if bool(record.get("ready_for_synthetic")))
    blocked = sum(1 for record in records if not bool(record.get("ready_for_real")))
    return {
        "model_count": len(records),
        "ready_for_real_count": ready_real,
        "ready_for_synthetic_count": ready_synth,
        "blocked_for_real_count": blocked,
    }


def _check_whisper_base(model_store_root: Path, whisper_bin: str | None) -> ModelCheckResult:
    model_dir = model_store_root / "whisper_cpp_base"
    model_file = model_dir / "ggml-base.bin"

    issues: list[str] = []
    model_present = model_file.exists()
    runtime_ready = bool(whisper_bin)
    adapter_implemented = True

    if not model_present:
        issues.append(f"Missing model file: {model_file}")
    if not runtime_ready:
        issues.append(
            "Missing whisper runtime. Set WHISPER_CPP_BIN or install whisper-cli/main executable."
        )
    return ModelCheckResult(
        model_id="whisper_cpp_base",
        label="whisper.cpp base",
        model_present=model_present,
        runtime_ready=runtime_ready,
        adapter_implemented=adapter_implemented,
        ready_for_real=model_present and runtime_ready and adapter_implemented,
        ready_for_synthetic=model_present,
        model_path=str(model_file),
        runtime_hint=whisper_bin or "Install whisper-cli (GitHub release) or set WHISPER_CPP_BIN",
        issues=issues,
        ready_for_live=model_present and runtime_ready and adapter_implemented,
        live_streaming=model_present and runtime_ready and adapter_implemented,
        live_block_reason=None if (model_present and runtime_ready and adapter_implemented) else "whisper runtime/model není připraven.",
        live_notes=["Mic mód používá whisper-server cache a periodický inference interval (pseudo-streaming)."],
    )


def _check_whisper_small(model_store_root: Path, whisper_bin: str | None) -> ModelCheckResult:
    model_dir = model_store_root / "whisper_cpp_small"
    model_file = model_dir / "ggml-small.bin"

    issues: list[str] = []
    model_present = model_file.exists()
    runtime_ready = bool(whisper_bin)
    adapter_implemented = True

    if not model_present:
        issues.append(f"Missing model file: {model_file}")
    if not runtime_ready:
        issues.append(
            "Missing whisper runtime. Set WHISPER_CPP_BIN or install whisper-cli/main executable."
        )
    return ModelCheckResult(
        model_id="whisper_cpp_small",
        label="whisper.cpp small",
        model_present=model_present,
        runtime_ready=runtime_ready,
        adapter_implemented=adapter_implemented,
        ready_for_real=model_present and runtime_ready and adapter_implemented,
        ready_for_synthetic=model_present,
        model_path=str(model_file),
        runtime_hint=whisper_bin or "Install whisper-cli (GitHub release) or set WHISPER_CPP_BIN",
        issues=issues,
        ready_for_live=model_present and runtime_ready and adapter_implemented,
        live_streaming=model_present and runtime_ready and adapter_implemented,
        live_block_reason=None if (model_present and runtime_ready and adapter_implemented) else "whisper runtime/model není připraven.",
        live_notes=["Mic mód používá whisper-server cache a periodický inference interval (pseudo-streaming)."],
    )


def _check_whisper_large_v3(model_store_root: Path, whisper_bin: str | None) -> ModelCheckResult:
    model_dir = model_store_root / "whisper_cpp_large_v3"
    model_file = resolve_whisper_model_file(model_store_root, "whisper_cpp_large_v3")
    model_present = model_file is not None
    runtime_ready = bool(whisper_bin)
    adapter_implemented = True
    issues: list[str] = []

    if not model_present:
        issues.append(f"Missing model file under: {model_dir}")
    if not runtime_ready:
        issues.append(
            "Missing whisper runtime. Set WHISPER_CPP_BIN or install whisper-cli/main executable."
        )
    return ModelCheckResult(
        model_id="whisper_cpp_large_v3",
        label="whisper.cpp large-v3",
        model_present=model_present,
        runtime_ready=runtime_ready,
        adapter_implemented=adapter_implemented,
        ready_for_real=model_present and runtime_ready and adapter_implemented,
        ready_for_synthetic=model_present,
        model_path=str(model_file or (model_dir / "ggml-large-v3.bin")),
        runtime_hint=whisper_bin or "Install whisper-cli (GitHub release) or set WHISPER_CPP_BIN",
        issues=issues,
        ready_for_live=False,
        live_streaming=False,
        live_block_reason="whisper.cpp CLI je dávkový režim; první text přichází až po uzavření chunku.",
        live_notes=["Vhodné pro benchmark/batch (vyšší kvalita), ne pro skutečný online přepis mikrofonu."],
    )


def _check_whisper_large_v3_turbo(model_store_root: Path, whisper_bin: str | None) -> ModelCheckResult:
    model_dir = model_store_root / "whisper_cpp_large_v3_turbo"
    model_file = resolve_whisper_model_file(model_store_root, "whisper_cpp_large_v3_turbo")
    model_present = model_file is not None
    runtime_ready = bool(whisper_bin)
    issues: list[str] = []

    if not model_present:
        issues.append(f"Missing model file under: {model_dir}")
        issues.append("Download: ggml-large-v3-turbo-q5_0.bin from huggingface.co/ggerganov/whisper.cpp")
    if not runtime_ready:
        issues.append("Missing whisper runtime. Set WHISPER_CPP_BIN or install whisper-cli/main executable.")
    return ModelCheckResult(
        model_id="whisper_cpp_large_v3_turbo",
        label="whisper.cpp large-v3-turbo",
        model_present=model_present,
        runtime_ready=runtime_ready,
        adapter_implemented=True,
        ready_for_real=model_present and runtime_ready,
        ready_for_synthetic=model_present,
        model_path=str(model_file or (model_dir / "ggml-large-v3-turbo-q5_0.bin")),
        runtime_hint=whisper_bin or "Install whisper-cli (GitHub release) or set WHISPER_CPP_BIN",
        issues=issues,
        ready_for_live=model_present and runtime_ready,
        live_streaming=model_present and runtime_ready,
        live_block_reason=None if (model_present and runtime_ready) else "whisper runtime/model není připraven.",
        live_notes=["Mic mód používá whisper-server cache; kvalita vysoká, ale na slabém CPU může mít vysokou latenci."],
    )


def _check_sherpa(model_store_root: Path, module_checker: ModuleChecker) -> ModelCheckResult:
    model_dir = model_store_root / "sherpa_onnx_small"
    bundles = list_sherpa_model_bundles(model_store_root)
    bundle = resolve_sherpa_model_bundle(model_store_root)
    model_present = bundle is not None
    runtime_ready = module_checker("sherpa_onnx")
    adapter_implemented = True
    supported_languages = sorted(
        {
            language
            for language in (detect_sherpa_bundle_language(item.model_dir) for item in bundles)
            if language
        }
    )
    live_language_hint = "cs" if "cs" in supported_languages else (detect_sherpa_bundle_language(bundle.model_dir) if bundle is not None else None)

    issues: list[str] = []
    if not model_present:
        issues.append(
            "No sherpa-onnx transducer bundle found (tokens + encoder/decoder/joiner .onnx)."
        )
    if not runtime_ready:
        issues.append("Python package sherpa_onnx is not installed.")
    if not adapter_implemented:
        issues.append("Benchmark real adapter for sherpa_onnx_small is not wired yet.")

    live_notes: list[str] = ["Streaming recognizer je dostupný přes sherpa_onnx OnlineRecognizer."]
    if supported_languages:
        live_notes.append(f"Dostupné jazykové bundly: {', '.join(supported_languages)}.")
    else:
        live_notes.append("Jazyk bundle nelze jednoznačně detekovat z názvu adresáře.")
    if live_language_hint == "en":
        live_notes.append("Aktuální bundle vypadá anglicky; pro češtinu může mít slabou kvalitu.")
    if "cs" in supported_languages:
        live_notes.append("CZ bundle je dostupný pro true streaming režim.")

    return ModelCheckResult(
        model_id="sherpa_onnx_small",
        label="sherpa-onnx small",
        model_present=model_present,
        runtime_ready=runtime_ready,
        adapter_implemented=adapter_implemented,
        ready_for_real=model_present and runtime_ready and adapter_implemented,
        ready_for_synthetic=model_present,
        model_path=str(Path(bundle.model_dir) if bundle is not None else model_dir),
        runtime_hint="pip install sherpa-onnx and download an offline transducer model bundle",
        issues=issues,
        ready_for_live=model_present and runtime_ready and adapter_implemented,
        live_streaming=model_present and runtime_ready and adapter_implemented,
        live_block_reason=None if (model_present and runtime_ready and adapter_implemented) else "Streaming recognizer není připraven.",
        live_notes=live_notes,
        live_language_hint=live_language_hint,
        live_supported_languages=supported_languages,
    )


def _check_sherpa_parakeet_cs(model_store_root: Path, module_checker: ModuleChecker) -> ModelCheckResult:
    bundles = list_sherpa_model_bundles(model_store_root)
    cs_bundle = resolve_sherpa_model_bundle(model_store_root, preferred_language="cs")
    model_present = cs_bundle is not None
    runtime_ready = module_checker("sherpa_onnx")
    issues: list[str] = []
    if not model_present:
        issues.append("No Czech sherpa-onnx bundle found (tokens + encoder/decoder/joiner .onnx).")
    if not runtime_ready:
        issues.append("Python package sherpa_onnx is not installed.")
    issues.append("Temporarily disabled: Windows runtime incompatibility ('window_size' metadata) in online transducer path.")
    notes = [
        "Požadován CZ bundle (např. Parakeet int8) ve runtime/model_store.",
        "Live streaming je true online přes sherpa_onnx OnlineRecognizer.",
    ]
    return ModelCheckResult(
        model_id="sherpa_onnx_parakeet_cs_int8",
        label="sherpa-onnx Parakeet 0.6B int8 (CZ)",
        model_present=model_present,
        runtime_ready=runtime_ready,
        adapter_implemented=True,
        ready_for_real=False,
        ready_for_synthetic=model_present,
        model_path=str(Path(cs_bundle.model_dir) if cs_bundle is not None else (model_store_root / "sherpa_onnx_parakeet_cs_int8")),
        runtime_hint="pip install sherpa-onnx and place CZ Parakeet int8 bundle in runtime/model_store",
        issues=issues,
        ready_for_live=False,
        live_streaming=False,
        live_block_reason="Temporarily disabled due to sherpa runtime metadata incompatibility on Windows.",
        live_notes=notes,
        live_language_hint="cs",
        live_supported_languages=["cs"],
    )


def _check_qwen_0_6b(model_store_root: Path, module_checker: ModuleChecker) -> ModelCheckResult:
    model_dir = model_store_root / "qwen3_asr_0_6b"
    required = [
        model_dir / "config.json",
        model_dir / "tokenizer_config.json",
        model_dir / "preprocessor_config.json",
    ]
    weight_present = (model_dir / "model.safetensors").exists()
    model_present = all(path.exists() for path in required) and weight_present
    runtime_ready = module_checker("qwen_asr") and module_checker("torch") and module_checker("transformers")
    adapter_implemented = True

    issues: list[str] = []
    if not model_present:
        issues.append("Missing one or more Qwen3-ASR-0.6B files (config/tokenizer/preprocessor/weights).")
    if not runtime_ready:
        issues.append("Python packages torch and transformers are required for Qwen runtime.")
    if not adapter_implemented:
        issues.append("Benchmark real adapter for qwen3_asr_0_6b is not wired yet.")

    return ModelCheckResult(
        model_id="qwen3_asr_0_6b",
        label="Qwen3-ASR 0.6B",
        model_present=model_present,
        runtime_ready=runtime_ready,
        adapter_implemented=adapter_implemented,
        ready_for_real=model_present and runtime_ready and adapter_implemented,
        ready_for_synthetic=model_present,
        model_path=str(model_dir),
        runtime_hint="pip install qwen-asr",
        issues=issues,
        ready_for_live=False,
        live_streaming=False,
        live_block_reason="Qwen ASR v repu běží dávkově; pro mikrofonní online přepis je příliš pozdní.",
        live_notes=["Vhodné pro batch/benchmark, ne pro skutečný streaming live transcript."],
    )


def _check_qwen_1_7b(model_store_root: Path, module_checker: ModuleChecker) -> ModelCheckResult:
    model_dir = model_store_root / "qwen3_asr_1_7b"
    required = [
        model_dir / "config.json",
        model_dir / "tokenizer_config.json",
        model_dir / "preprocessor_config.json",
        model_dir / "model.safetensors.index.json",
    ]
    shard_1 = model_dir / "model-00001-of-00002.safetensors"
    shard_2 = model_dir / "model-00002-of-00002.safetensors"
    model_present = all(path.exists() for path in required) and shard_1.exists() and shard_2.exists()
    runtime_ready = module_checker("qwen_asr") and module_checker("torch") and module_checker("transformers")
    adapter_implemented = True

    issues: list[str] = []
    if not model_present:
        issues.append("Missing one or more Qwen3-ASR-1.7B files (config/index/shards).")
    if not runtime_ready:
        issues.append("Python packages torch and transformers are required for Qwen runtime.")
    if not adapter_implemented:
        issues.append("Benchmark real adapter for qwen3_asr_1_7b is not wired yet.")

    return ModelCheckResult(
        model_id="qwen3_asr_1_7b",
        label="Qwen3-ASR 1.7B",
        model_present=model_present,
        runtime_ready=runtime_ready,
        adapter_implemented=adapter_implemented,
        ready_for_real=model_present and runtime_ready and adapter_implemented,
        ready_for_synthetic=model_present,
        model_path=str(model_dir),
        runtime_hint="pip install qwen-asr",
        issues=issues,
        ready_for_live=False,
        live_streaming=False,
        live_block_reason="Qwen ASR v repu běží dávkově; pro mikrofonní online přepis je příliš pozdní.",
        live_notes=["Vhodné pro batch/benchmark, ne pro skutečný streaming live transcript."],
    )


def _check_vosk_small_cs(model_store_root: Path, module_checker: ModuleChecker) -> ModelCheckResult:
    model_root = model_store_root / "vosk_small_cs_0_4"
    model_dir = resolve_vosk_model_dir(model_store_root)
    model_present = model_dir is not None
    runtime_ready = module_checker("vosk")
    adapter_implemented = True
    language_hint = detect_vosk_model_language(model_dir) if model_dir is not None else "cs"

    issues: list[str] = []
    if not model_present:
        issues.append("Missing VOSK model directory (expected conf/model.conf + am/final.mdl).")
    if not runtime_ready:
        issues.append("Python package vosk is not installed.")

    live_notes = [
        "VOSK KaldiRecognizer poskytuje true streaming i v low-latency CPU rezimu.",
        "Model je urceny primarne pro cestinu (cs).",
    ]
    if model_dir is not None:
        live_notes.append(f"Aktivni model adresar: {model_dir}")

    return ModelCheckResult(
        model_id="vosk_small_cs_0_4",
        label="VOSK small cs-0.4",
        model_present=model_present,
        runtime_ready=runtime_ready,
        adapter_implemented=adapter_implemented,
        ready_for_real=model_present and runtime_ready and adapter_implemented,
        ready_for_synthetic=model_present,
        model_path=str(model_dir or model_root),
        runtime_hint="pip install vosk and download vosk-model-small-cs-0.4-rhasspy",
        issues=issues,
        ready_for_live=model_present and runtime_ready and adapter_implemented,
        live_streaming=model_present and runtime_ready and adapter_implemented,
        live_block_reason=None if (model_present and runtime_ready and adapter_implemented) else "VOSK streaming runtime neni pripraven.",
        live_notes=live_notes,
        live_language_hint=language_hint or "cs",
        live_supported_languages=["cs"],
    )


def _check_faster_whisper_small_cs(model_store_root: Path, module_checker: ModuleChecker) -> ModelCheckResult:
    return _check_faster_whisper_model(
        model_store_root=model_store_root,
        module_checker=module_checker,
        model_id="faster_whisper_small_cs_int8",
        label="faster-whisper small (CZ int8)",
    )


def _check_faster_whisper_medium_cs(model_store_root: Path, module_checker: ModuleChecker) -> ModelCheckResult:
    return _check_faster_whisper_model(
        model_store_root=model_store_root,
        module_checker=module_checker,
        model_id="faster_whisper_medium_cs_int8",
        label="faster-whisper medium (CZ int8)",
    )


def _check_faster_whisper_model(
    *,
    model_store_root: Path,
    module_checker: ModuleChecker,
    model_id: str,
    label: str,
) -> ModelCheckResult:
    model_dir = resolve_faster_whisper_model_path(model_store_root, model_id)
    model_present = model_dir is not None and (Path(model_dir) / "model.bin").exists()
    runtime_ready = module_checker("faster_whisper")
    issues: list[str] = []
    if not model_present:
        issues.append(
            f"Missing faster-whisper CTranslate2 model directory (expected runtime/model_store/{model_id}/model.bin)."
        )
    if not runtime_ready:
        issues.append("Python package faster-whisper is not installed.")
    notes = [
        "Model je načítán pouze lokálně (bez auto-download) kvůli offline režimu.",
        "Mic režim používá periodický inference interval nad rolling audio oknem.",
    ]
    return ModelCheckResult(
        model_id=model_id,
        label=label,
        model_present=model_present,
        runtime_ready=runtime_ready,
        adapter_implemented=True,
        ready_for_real=model_present and runtime_ready,
        ready_for_synthetic=model_present,
        model_path=str(model_dir or (model_store_root / model_id)),
        runtime_hint=f"pip install faster-whisper and place local CTranslate2 model under runtime/model_store/{model_id}",
        issues=issues,
        ready_for_live=model_present and runtime_ready,
        live_streaming=model_present and runtime_ready,
        live_block_reason=None if (model_present and runtime_ready) else "faster-whisper runtime/model není připraven.",
        live_notes=notes,
        live_language_hint="cs",
        live_supported_languages=["cs"],
    )


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None
