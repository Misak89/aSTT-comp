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
            supports_microphone=True,
            notes="Menší Whisper model pro slabší HW. Mic v4 (experimental) jako rychlejší alternativa k whisper_cpp_small.",
            params=[
                ParamSpec("language",       "Jazyk",          "str",  "cs", "Kód jazyka (cs, en, de, ...)"),
                ParamSpec("threads",        "Vlákna CPU",     "int",  4,    "Počet CPU vláken", min=1, max=16),
                ParamSpec("beam_size",      "Beam size",      "int",  5,    "Větší = přesnější, pomalejší", min=1, max=10),
                ParamSpec("best_of",        "Best of",        "int",  5,    "Počet kandidátů", min=1, max=10),
                ParamSpec("no_fallback",    "Bez fallbacku",  "bool", True, "Zakáže fallback na menší model"),
                ParamSpec("initial_prompt", "Kontext (prompt)", "str",  "",   "Tip: krátký věcný kontext zlepší přesnost — např. 'Psychiatr Jiří Horáček, rozhovor o mozku:'. Příliš dlouhý prompt může škodit."),
                ParamSpec("analysis_interval_ms", "Mic interval (ms)", "int", 1400, "Jak často se aktualizuje live přepis", min=300, max=5000),
                ParamSpec("analysis_window_seconds", "Mic okno (s)", "int", 10, "Délka analyzovaného audio okna pro live partial", min=3, max=45),
                ParamSpec("input_gain_db", "Mic gain (dB)", "float", 0.0, "Softwarové zesílení/zeslabení vstupu před STT", min=-24.0, max=24.0),
                ParamSpec("backpressure_high_s", "Backpressure high (s)", "float", 1.2, "Prahový dluh zpracování; nad touto hodnotou začne drop chunků", min=0.2, max=5.0),
                ParamSpec("backpressure_low_s", "Backpressure low (s)", "float", 0.4, "Hystereze; pod touto hodnotou se backpressure vypne", min=0.05, max=3.0),
            ],
        ),

        ModelDescriptor(
            model_id="whisper_cpp_small",
            label="whisper.cpp small",
            adapter="whisper_cpp",
            languages=["cs", "en", "de", "fr", "es", "pl", "sk", "uk"],
            supports_streaming=False,
            supports_microphone=True,
            notes="Doporučený model pro CZ (~244 MB). Dobrý poměr přesnost/rychlost. Mic v4 (experimental).",
            params=[
                ParamSpec("language",       "Jazyk",          "str",  "cs",  "Kód jazyka (cs, en, de, ...)"),
                ParamSpec("threads",        "Vlákna CPU",     "int",  4,     "Počet CPU vláken", min=1, max=16),
                ParamSpec("beam_size",      "Beam size",      "int",  5,     "Větší = přesnější, pomalejší", min=1, max=10),
                ParamSpec("best_of",        "Best of",        "int",  5,     "Počet kandidátů dekódování", min=1, max=10),
                ParamSpec("no_fallback",    "Bez fallbacku",  "bool", True,  "Zakáže fallback na menší model"),
                ParamSpec("initial_prompt", "Kontext (prompt)", "str",  "",    "Tip: krátký věcný kontext zlepší přesnost — např. 'Psychiatr Jiří Horáček, rozhovor o mozku:'. Příliš dlouhý prompt může škodit."),
                ParamSpec("analysis_interval_ms", "Mic interval (ms)", "int", 1200, "Jak často se aktualizuje live přepis", min=300, max=5000),
                ParamSpec("analysis_window_seconds", "Mic okno (s)", "int", 12, "Délka analyzovaného audio okna pro live partial", min=3, max=45),
                ParamSpec("input_gain_db", "Mic gain (dB)", "float", 0.0, "Softwarové zesílení/zeslabení vstupu před STT", min=-24.0, max=24.0),
                ParamSpec("backpressure_high_s", "Backpressure high (s)", "float", 1.2, "Prahový dluh zpracování; nad touto hodnotou začne drop chunků", min=0.2, max=5.0),
                ParamSpec("backpressure_low_s", "Backpressure low (s)", "float", 0.4, "Hystereze; pod touto hodnotou se backpressure vypne", min=0.05, max=3.0),
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
                ParamSpec("initial_prompt", "Kontext (prompt)", "str",  "",    "Tip: krátký věcný kontext zlepší přesnost — např. 'Psychiatr Jiří Horáček, rozhovor o mozku:'. Příliš dlouhý prompt může škodit."),
            ],
        ),

        ModelDescriptor(
            model_id="whisper_cpp_large_v3_turbo",
            label="whisper.cpp large-v3-turbo",
            adapter="whisper_cpp",
            languages=["cs", "en", "de", "fr", "es", "pl", "sk", "uk"],
            supports_streaming=False,
            supports_microphone=True,
            notes="Rychlá varianta large-v3 (~547 MB q5_0). Mic v4 (experimental).",
            params=[
                ParamSpec("language",       "Jazyk",          "str",  "cs",  "Kód jazyka (cs, en, de, ...)"),
                ParamSpec("threads",        "Vlákna CPU",     "int",  4,     "Počet CPU vláken", min=1, max=16),
                ParamSpec("beam_size",      "Beam size",      "int",  5,     "Větší = přesnější, pomalejší", min=1, max=10),
                ParamSpec("best_of",        "Best of",        "int",  5,     "Počet kandidátů dekódování", min=1, max=10),
                ParamSpec("no_fallback",    "Bez fallbacku",  "bool", True,  "Zakáže fallback na menší model"),
                ParamSpec("initial_prompt", "Kontext (prompt)", "str",  "",    "Tip: krátký věcný kontext zlepší přesnost — např. 'Psychiatr Jiří Horáček, rozhovor o mozku:'. Příliš dlouhý prompt může škodit."),
                ParamSpec("analysis_interval_ms", "Mic interval (ms)", "int", 1200, "Jak často se aktualizuje live přepis", min=300, max=5000),
                ParamSpec("analysis_window_seconds", "Mic okno (s)", "int", 12, "Délka analyzovaného audio okna pro live partial", min=3, max=45),
                ParamSpec("input_gain_db", "Mic gain (dB)", "float", 0.0, "Softwarové zesílení/zeslabení vstupu před STT", min=-24.0, max=24.0),
                ParamSpec("backpressure_high_s", "Backpressure high (s)", "float", 1.2, "Prahový dluh zpracování; nad touto hodnotou začne drop chunků", min=0.2, max=5.0),
                ParamSpec("backpressure_low_s", "Backpressure low (s)", "float", 0.4, "Hystereze; pod touto hodnotou se backpressure vypne", min=0.05, max=3.0),
            ],
        ),

        ModelDescriptor(
            model_id="faster_whisper_small_cs_int8",
            label="faster-whisper small (CZ int8)",
            adapter="faster_whisper",
            languages=["cs", "en", "de", "fr", "es", "pl", "sk", "uk"],
            supports_streaming=True,
            supports_microphone=True,
            notes="CTranslate2/faster-whisper v CPU int8 režimu. Vyžaduje lokální CTranslate2 model (bez auto-download v offline režimu).",
            params=[
                ParamSpec("language",       "Jazyk",          "str",  "cs",  "Kód jazyka (cs, en, de, ...)"),
                ParamSpec("threads",        "Vlákna CPU",     "int",  4,     "Počet CPU vláken", min=1, max=16),
                ParamSpec("beam_size",      "Beam size",      "int",  1,     "Větší = přesnější, pomalejší", min=1, max=10),
                ParamSpec("best_of",        "Best of",        "int",  1,     "Počet kandidátů dekódování", min=1, max=10),
                ParamSpec("compute_type",   "Compute type",   "select", "int8",
                          options=["int8", "int8_float16", "float16", "float32"]),
                ParamSpec("device",         "Zařízení",       "select", "cpu", options=["cpu", "cuda", "auto"]),
                ParamSpec("analysis_interval_ms", "Mic interval (ms)", "int", 1200, "Jak často se aktualizuje live přepis", min=300, max=5000),
                ParamSpec("analysis_window_seconds", "Mic okno (s)", "int", 12, "Délka analyzovaného audio okna pro live partial", min=3, max=45),
                ParamSpec("input_gain_db", "Mic gain (dB)", "float", 0.0, "Softwarové zesílení/zeslabení vstupu před STT", min=-24.0, max=24.0),
                ParamSpec("backpressure_high_s", "Backpressure high (s)", "float", 1.2, "Prahový dluh zpracování; nad touto hodnotou začne drop chunků", min=0.2, max=5.0),
                ParamSpec("backpressure_low_s", "Backpressure low (s)", "float", 0.4, "Hystereze; pod touto hodnotou se backpressure vypne", min=0.05, max=3.0),
            ],
        ),

        ModelDescriptor(
            model_id="faster_whisper_medium_cs_int8",
            label="faster-whisper medium (CZ int8)",
            adapter="faster_whisper",
            languages=["cs", "en", "de", "fr", "es", "pl", "sk", "uk"],
            supports_streaming=True,
            supports_microphone=True,
            notes="CTranslate2/faster-whisper medium v CPU int8 režimu. Vyšší přesnost za cenu vyššího CPU.",
            params=[
                ParamSpec("language",       "Jazyk",          "str",  "cs",  "Kód jazyka (cs, en, de, ...)"),
                ParamSpec("threads",        "Vlákna CPU",     "int",  6,     "Počet CPU vláken", min=1, max=16),
                ParamSpec("beam_size",      "Beam size",      "int",  3,     "Větší = přesnější, pomalejší", min=1, max=10),
                ParamSpec("best_of",        "Best of",        "int",  3,     "Počet kandidátů dekódování", min=1, max=10),
                ParamSpec("compute_type",   "Compute type",   "select", "int8",
                          options=["int8", "int8_float16", "float16", "float32"]),
                ParamSpec("device",         "Zařízení",       "select", "cpu", options=["cpu", "cuda", "auto"]),
                ParamSpec("analysis_interval_ms", "Mic interval (ms)", "int", 1400, "Jak často se aktualizuje live přepis", min=300, max=5000),
                ParamSpec("analysis_window_seconds", "Mic okno (s)", "int", 14, "Délka analyzovaného audio okna pro live partial", min=3, max=45),
                ParamSpec("input_gain_db", "Mic gain (dB)", "float", 0.0, "Softwarové zesílení/zeslabení vstupu před STT", min=-24.0, max=24.0),
                ParamSpec("backpressure_high_s", "Backpressure high (s)", "float", 1.2, "Prahový dluh zpracování; nad touto hodnotou začne drop chunků", min=0.2, max=5.0),
                ParamSpec("backpressure_low_s", "Backpressure low (s)", "float", 0.4, "Hystereze; pod touto hodnotou se backpressure vypne", min=0.05, max=3.0),
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
                ParamSpec("input_gain_db", "Mic gain (dB)", "float", 0.0, "Softwarové zesílení/zeslabení vstupu před STT", min=-24.0, max=24.0),
                ParamSpec("backpressure_high_s", "Backpressure high (s)", "float", 1.2, "Prahový dluh zpracování; nad touto hodnotou začne drop chunků", min=0.2, max=5.0),
                ParamSpec("backpressure_low_s", "Backpressure low (s)", "float", 0.4, "Hystereze; pod touto hodnotou se backpressure vypne", min=0.05, max=3.0),
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
                ParamSpec("input_gain_db", "Mic gain (dB)", "float", 0.0, "Softwarové zesílení/zeslabení vstupu před STT", min=-24.0, max=24.0),
                ParamSpec("backpressure_high_s", "Backpressure high (s)", "float", 1.2, "Prahový dluh zpracování; nad touto hodnotou začne drop chunků", min=0.2, max=5.0),
                ParamSpec("backpressure_low_s", "Backpressure low (s)", "float", 0.4, "Hystereze; pod touto hodnotou se backpressure vypne", min=0.05, max=3.0),
            ],
        ),

        ModelDescriptor(
            model_id="sherpa_onnx_parakeet_cs_int8",
            label="sherpa-onnx Parakeet 0.6B int8 (CZ)",
            adapter="sherpa_onnx",
            languages=["cs", "en", "de", "fr", "es", "pl", "sk", "uk"],
            supports_streaming=False,
            supports_microphone=False,
            notes="Dočasně vypnuto: aktuální sherpa runtime na Windows padá na metadata 'window_size'. Model zůstává připraven pro budoucí kompatibilní verzi.",
            params=[
                ParamSpec("language",           "Jazyk",            "str",    "cs"),
                ParamSpec("num_threads",        "Vlákna CPU",       "int",    4, min=1, max=16),
                ParamSpec("decoding_method",    "Dekódování",       "select", "greedy_search",
                          options=["greedy_search", "modified_beam_search"]),
                ParamSpec("provider",           "Backend",          "select", "cpu",
                          options=["cpu", "cuda", "coreml"]),
                ParamSpec("sample_rate",        "Sample rate",      "int",    16000, min=8000, max=48000),
                ParamSpec("input_gain_db", "Mic gain (dB)", "float", 0.0, "Softwarové zesílení/zeslabení vstupu před STT", min=-24.0, max=24.0),
                ParamSpec("backpressure_high_s", "Backpressure high (s)", "float", 1.2, "Prahový dluh zpracování; nad touto hodnotou začne drop chunků", min=0.2, max=5.0),
                ParamSpec("backpressure_low_s", "Backpressure low (s)", "float", 0.4, "Hystereze; pod touto hodnotou se backpressure vypne", min=0.05, max=3.0),
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
                ParamSpec("input_gain_db", "Mic gain (dB)", "float", 0.0, "Softwarové zesílení/zeslabení vstupu před STT", min=-24.0, max=24.0),
                ParamSpec("backpressure_high_s", "Backpressure high (s)", "float", 1.2, "Prahový dluh zpracování; nad touto hodnotou začne drop chunků", min=0.2, max=5.0),
                ParamSpec("backpressure_low_s", "Backpressure low (s)", "float", 0.4, "Hystereze; pod touto hodnotou se backpressure vypne", min=0.05, max=3.0),
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
