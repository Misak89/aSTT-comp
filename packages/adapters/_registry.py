"""
Model registry — centrální definice všech STT modelů.

Přidání nového modelu:
1. Přidej runner soubor packages/adapters/{name}_runner.py
2. Přidej ModelDescriptor do REGISTRY níže
3. Hotovo — backend, benchmark worker a frontend parametry se vygenerují automaticky
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParamSpec:
    """Specifikace jednoho parametru modelu — pro generování UI formuláře."""
    name: str
    label: str
    type: str          # "int" | "float" | "str" | "bool" | "select"
    default: object
    description: str = ""
    min: object = None
    max: object = None
    options: list[str] = field(default_factory=list)  # pro type="select"


@dataclass(frozen=True)
class ModelDescriptor:
    """Popis jednoho STT modelu — adapter, schopnosti, parametry."""
    model_id: str
    label: str
    adapter: str                    # klíč pro dispatch v runneru
    languages: list[str]            # ["cs", "en"] nebo ["en"]
    supports_streaming: bool        # yt-dlp → ffmpeg pipe → live session
    supports_microphone: bool       # sounddevice mic → live session
    params: list[ParamSpec] = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, ModelDescriptor] = {
    m.model_id: m for m in [

        ModelDescriptor(
            model_id="whisper_cpp_base",
            label="whisper.cpp base",
            adapter="whisper_cpp",
            languages=["cs", "en", "de", "fr", "es", "pl", "sk", "uk"],
            supports_streaming=False,   # whisper-cli potřebuje soubor
            supports_microphone=False,
            notes="Nejmenší model (~74 MB). Rychlý, vhodný pro rychlé testy. WER cca 15–25% na CZ.",
            params=[
                ParamSpec("language",       "Jazyk",          "str",  "cs", "Kód jazyka (cs, en, de, ...)"),
                ParamSpec("threads",        "Vlákna CPU",     "int",  4,    "Počet CPU vláken", min=1, max=16),
                ParamSpec("beam_size",      "Beam size",      "int",  5,    "Větší = přesnější, pomalejší", min=1, max=10),
                ParamSpec("best_of",        "Best of",        "int",  5,    "Počet kandidátů", min=1, max=10),
                ParamSpec("no_fallback",    "Bez fallbacku",  "bool", True, "Zakáže fallback na menší model"),
                ParamSpec("initial_prompt", "Počáteční text", "str",  "",   "Kontext pro model (např. 'Rozhovor v češtině:')"),
            ],
        ),

        ModelDescriptor(
            model_id="whisper_cpp_small",
            label="whisper.cpp small",
            adapter="whisper_cpp",
            languages=["cs", "en", "de", "fr", "es", "pl", "sk", "uk"],
            supports_streaming=False,
            supports_microphone=False,
            notes="Doporučený model pro CZ (~244 MB). Dobrý poměr přesnost/rychlost. WER cca 8–15% na CZ.",
            params=[
                ParamSpec("language",       "Jazyk",          "str",  "cs",  "Kód jazyka (cs, en, de, ...)"),
                ParamSpec("threads",        "Vlákna CPU",     "int",  4,     "Počet CPU vláken", min=1, max=16),
                ParamSpec("beam_size",      "Beam size",      "int",  5,     "Větší = přesnější, pomalejší", min=1, max=10),
                ParamSpec("best_of",        "Best of",        "int",  5,     "Počet kandidátů dekódování", min=1, max=10),
                ParamSpec("no_fallback",    "Bez fallbacku",  "bool", True,  "Zakáže fallback na menší model"),
                ParamSpec("initial_prompt", "Počáteční text", "str",  "",    "Kontext pro model (např. 'Rozhovor v češtině:')"),
            ],
        ),

        ModelDescriptor(
            model_id="whisper_cpp_large_v3",
            label="whisper.cpp large-v3",
            adapter="whisper_cpp",
            languages=["cs", "en", "de", "fr", "es", "pl", "sk", "uk"],
            supports_streaming=False,
            supports_microphone=False,
            notes="Nejlepší přesnost (~1550 MB), RTF > 1 na většině CPU — nestíhá live přepis.",
            params=[
                ParamSpec("language",       "Jazyk",          "str",  "cs",  "Kód jazyka (cs, en, de, ...)"),
                ParamSpec("threads",        "Vlákna CPU",     "int",  4,     "Počet CPU vláken", min=1, max=16),
                ParamSpec("beam_size",      "Beam size",      "int",  5,     "Větší = přesnější, pomalejší", min=1, max=10),
                ParamSpec("best_of",        "Best of",        "int",  5,     "Počet kandidátů dekódování", min=1, max=10),
                ParamSpec("no_fallback",    "Bez fallbacku",  "bool", True,  "Zakáže fallback na menší model"),
                ParamSpec("initial_prompt", "Počáteční text", "str",  "",    "Kontext pro model (např. 'Rozhovor v češtině:')"),
            ],
        ),

        ModelDescriptor(
            model_id="whisper_cpp_large_v3_turbo",
            label="whisper.cpp large-v3-turbo",
            adapter="whisper_cpp",
            languages=["cs", "en", "de", "fr", "es", "pl", "sk", "uk"],
            supports_streaming=False,
            supports_microphone=False,
            notes="Rychlá varianta large-v3 (~547 MB q5_0). ~6× rychlejší než large-v3, podobná přesnost. RTF < 1 reálné na CPU.",
            params=[
                ParamSpec("language",       "Jazyk",          "str",  "cs",  "Kód jazyka (cs, en, de, ...)"),
                ParamSpec("threads",        "Vlákna CPU",     "int",  4,     "Počet CPU vláken", min=1, max=16),
                ParamSpec("beam_size",      "Beam size",      "int",  5,     "Větší = přesnější, pomalejší", min=1, max=10),
                ParamSpec("best_of",        "Best of",        "int",  5,     "Počet kandidátů dekódování", min=1, max=10),
                ParamSpec("no_fallback",    "Bez fallbacku",  "bool", True,  "Zakáže fallback na menší model"),
                ParamSpec("initial_prompt", "Počáteční text", "str",  "",    "Kontext pro model"),
            ],
        ),

        ModelDescriptor(
            model_id="vosk_small_cs_0_4",
            label="VOSK small cs-0.4",
            adapter="vosk",
            languages=["cs"],
            supports_streaming=True,
            supports_microphone=True,
            notes="Dedikovaný CZ model, velmi nízké nároky na RAM",
            params=[
                ParamSpec("sample_rate",    "Sample rate",      "int",   16000, "Hz", min=8000, max=48000),
                ParamSpec("chunk_seconds",  "Chunk délka",      "float", 0.20,  "Délka audio chunku v sekundách", min=0.05, max=2.0),
                ParamSpec("set_words",      "Word timestamps",  "bool",  False, "Vrátit timestamps pro každé slovo"),
            ],
        ),

        ModelDescriptor(
            model_id="sherpa_onnx_small",
            label="sherpa-onnx small",
            adapter="sherpa_onnx",
            languages=["en"],
            supports_streaming=True,
            supports_microphone=True,
            notes="Streaming ONNX model, nízká latence",
            params=[
                ParamSpec("num_threads",        "Vlákna CPU",       "int",    2,              min=1, max=16),
                ParamSpec("decoding_method",    "Dekódování",       "select", "greedy_search",
                          options=["greedy_search", "modified_beam_search"]),
                ParamSpec("provider",           "Backend",          "select", "cpu",
                          options=["cpu", "cuda", "coreml"]),
                ParamSpec("sample_rate",        "Sample rate",      "int",    16000,          min=8000, max=48000),
            ],
        ),

        ModelDescriptor(
            model_id="qwen3_asr_0_6b",
            label="Qwen3-ASR 0.6B",
            adapter="qwen_asr",
            languages=["cs", "en", "de", "fr", "es", "zh", "ja"],
            supports_streaming=False,
            supports_microphone=False,
            notes="LLM-based ASR, potřebuje soubor. Na Windows: vždy float32 (ne bfloat16 — crash).",
            params=[
                ParamSpec("language",       "Jazyk",        "str",    "Czech"),
                ParamSpec("dtype",          "Datový typ",   "select", "float32",
                          description="Na Windows VŽDY float32",
                          options=["float32", "float16", "bfloat16"]),
                ParamSpec("device_map",     "Zařízení",     "select", "cpu",
                          options=["cpu", "cuda", "auto"]),
                ParamSpec("max_new_tokens", "Max tokeny",   "int",    256,  min=64, max=1024),
            ],
        ),

        ModelDescriptor(
            model_id="qwen3_asr_1_7b",
            label="Qwen3-ASR 1.7B",
            adapter="qwen_asr",
            languages=["cs", "en", "de", "fr", "es", "zh", "ja"],
            supports_streaming=False,
            supports_microphone=False,
            notes="LLM-based ASR, větší model. Na Windows: vždy float32.",
            params=[
                ParamSpec("language",       "Jazyk",        "str",    "Czech"),
                ParamSpec("dtype",          "Datový typ",   "select", "float32",
                          options=["float32", "float16", "bfloat16"]),
                ParamSpec("device_map",     "Zařízení",     "select", "cpu",
                          options=["cpu", "cuda", "auto"]),
                ParamSpec("max_new_tokens", "Max tokeny",   "int",    256,  min=64, max=1024),
            ],
        ),

        ModelDescriptor(
            model_id="moonshine_medium_en",
            label="Moonshine Medium (EN)",
            adapter="moonshine",
            languages=["en"],
            supports_streaming=True,
            supports_microphone=True,
            notes="Moderní streaming ASR, 245M params, WER 6.65% na EN. Zatím pouze angličtina.",
            params=[
                ParamSpec("model_arch",             "Architektura",         "select", "medium",
                          options=["tiny", "small", "medium"]),
                ParamSpec("analysis_interval_ms",   "Interval analýzy",     "int",    500,
                          description="Jak často se přepisuje (ms)", min=100, max=2000),
            ],
        ),
    ]
}


def get_model(model_id: str) -> ModelDescriptor | None:
    return REGISTRY.get(model_id)


def list_models() -> list[ModelDescriptor]:
    return list(REGISTRY.values())


def get_params_schema(model_id: str) -> list[dict]:
    """Vrátí seznam parametrů jako dict pro JSON serializaci (API / frontend)."""
    descriptor = REGISTRY.get(model_id)
    if not descriptor:
        return []
    return [
        {
            "name": p.name,
            "label": p.label,
            "type": p.type,
            "default": p.default,
            "description": p.description,
            "min": p.min,
            "max": p.max,
            "options": p.options,
        }
        for p in descriptor.params
    ]
