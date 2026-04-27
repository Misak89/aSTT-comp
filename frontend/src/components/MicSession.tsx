/**
 * MicSession — live přepis z mikrofonu přes WebSocket.
 *
 * Lifecycle:
 *   1. Uživatel vybere model + parametry
 *   2. Klikne Start → POST /api/mic/sessions → session_id
 *   3. Otevře WebSocket /api/mic/sessions/{id}/stream
 *   4. Prohlížeč čte mikrofon přes MediaRecorder / AudioWorklet → posílá PCM frames
 *   5. Backend vrací partial výsledky → zobrazují se live
 *   6. Stop → WebSocket pošle {"action":"stop"} → final výsledek
 *
 * Pozn.: AudioWorklet API pro PCM float32 → Int16 → binary WS frame.
 */
import { Fragment, useState, useRef, useEffect, useCallback, useMemo } from 'react'
import type {
  ModelDescriptor,
  ParamSpec,
  AudioDevice,
  LibraryItem,
  MicMobileLoopPackageResponse,
  MicMobileLoopPackageListItem,
  MicSequenceReport,
  MicSequenceConclusionModel,
  MicSequenceTrial,
  MicTrialStatus,
} from '../types'
import { api } from '../api/client'
import { ModelParamsForm, ParamInput } from './ModelParamsForm'
import { videoLabel } from '../utils'
import { formatDateTimeMedium } from '../lib/time'

interface Props {
  /** Modely které podporují mic (supports_microphone: true) */
  availableModels: ModelDescriptor[]
  /** Volitelně knihovna videí pro referenční mic test */
  library?: LibraryItem[]
}

type Status = 'idle' | 'connecting' | 'recording' | 'stopping' | 'done' | 'error'
type MicTestMode = 'free_speech' | 'reference_video'
type MicOrchestratorMode = 'legacy_sequence' | 'v7_cs_online'
type SequenceParamProfileId = 'recommended_per_model' | 'fast_online' | 'quality_online'
type TuningSweepMode = 'focused' | 'wide'

type TuningSweepSlot = {
  slotIndex: number
  slotTotal: number
  modelId: string
  variantId: string
  variantLabel: string
  repeatIndex: number
  repeatTotal: number
  mode: TuningSweepMode
  stepSize: number
  params: Record<string, unknown>
  baselineParams: Record<string, unknown>
  changedParams: Record<string, unknown>
}

type TuningSweepRangeConfig = {
  enabled: boolean
  from: number
  to: number
  step: number
}

type TuningSweepRangeConfigMap = Record<string, TuningSweepRangeConfig>

type TuningParamEffectHint = {
  minus: string
  plus: string
}

type MicMetrics = {
  latency_ms?: number
  first_word_audio_ms?: number
  first_word_wall_ms?: number
  rtf?: number
  elapsed_s?: number
  processing_ms_p95?: number
  segment_finalize_ms_p50?: number
  segment_finalize_ms_p95?: number
  capture_jitter_ms_p95?: number
  capture_lag_ms_p95?: number
  queue_depth_peak_ms?: number
  backpressure_events?: number
  drop_rate?: number
  worker_rss_peak_mb?: number
  reason_code?: string | null
}

type MicInputProof = {
  device_label: string
  sample_rate: number
  chunk_count: number
  audio_payload_bytes: number
  ws_payload_bytes: number
  rms_dbfs: number | null
  peak_dbfs: number | null
  clipping_pct: number
  vad_speech: boolean
  silence_ms: number
  last_chunk_age_ms: number | null
}

type ReferenceTextId = 'prepared_1' | 'prepared_2' | 'custom_1' | 'custom_2'
type ClipDurationAnchor = 'from' | 'to'

type SavedWebMicResult = {
  record_id: string
  saved_at: string
  model_id: string
  reference_label: string
  mic_test_mode: 'free_speech' | 'reference_video' | 'unknown'
  transcript: string
  transcript_source?: string | null
  note: string
  quality_assessment: string
  source: string
  rtf?: number
  drop_rate?: number
  reason_code?: string | null
  first_word_wall_ms?: number | null
  p50_fin_ms?: number | null
  p95_fin_ms?: number | null
  q_peak_s?: number | null
  rss_peak_mb?: number | null
  trial_status?: 'ok' | 'borderline' | 'too_slow_for_slot' | 'fail' | null
  mobile_loop_package_id?: string | null
  sequence_token?: string | null
  sequence_index?: number | null
  orchestrator_mode?: MicOrchestratorMode | null
  model_params_used?: Record<string, unknown> | null
  sequence_common_params_enabled?: boolean | null
  sequence_common_params_used?: Record<string, unknown> | null
  sequence_param_profile?: string | null
  tuning_series_id?: string | null
  tuning_variant_id?: string | null
  tuning_variant_label?: string | null
  tuning_step_size?: number | null
  tuning_repeat_index?: number | null
  tuning_repeat_total?: number | null
  tuning_changed_params?: Record<string, unknown> | null
}

type AutoModelSequenceMeta = {
  sequence_token: string
  sequence_index: number
  sequence_total: number
  queue_model_ids?: string[]
  selected_model_ids?: string[]
  tuning_series_id?: string | null
  tuning_slot?: TuningSweepSlot | null
}

type ActiveSessionLoopConfig = {
  enabled: boolean
  speechS: number | null
  captureSpeechS: number | null
  earlyStopS: number | null
  pauseS: number | null
  audioStartDelayS: number | null
  syncFirstRound: boolean | null
  measuredRounds: number | null
  autoStop: boolean | null
  packageId: string | null
}

type HistoryModeFilter = 'all' | 'free_speech' | 'reference_video'
type HistorySortKey = 'saved_at' | 'model_id' | 'mic_test_mode' | 'reference_label' | 'rtf' | 'drop_rate'
type LibrarySortKey =
  | 'title'
  | 'language'
  | 'duration'
  | 'genre'
  | 'view_count'
  | 'subtitle_languages'
  | 'subtitles'
  | 'audio'
  | 'visible_in_menus'
  | 'added_at'
  | 'wer'

const LIB_SETTINGS_KEY = 'astt_library_settings_v1'
const LIB_SORT_DEFAULT_DIR: Record<LibrarySortKey, 'asc' | 'desc'> = {
  title: 'asc',
  language: 'asc',
  duration: 'asc',
  genre: 'asc',
  view_count: 'desc',
  subtitle_languages: 'asc',
  subtitles: 'desc',
  audio: 'desc',
  visible_in_menus: 'desc',
  added_at: 'desc',
  wer: 'asc',
}

function isLibrarySortKey(value: unknown): value is LibrarySortKey {
  return (
    value === 'title' ||
    value === 'language' ||
    value === 'duration' ||
    value === 'genre' ||
    value === 'view_count' ||
    value === 'subtitle_languages' ||
    value === 'subtitles' ||
    value === 'audio' ||
    value === 'visible_in_menus' ||
    value === 'added_at' ||
    value === 'wer'
  )
}

function loadLibrarySortSettings(): {
  sortOrder: LibrarySortKey[]
  sortDirMap: Record<LibrarySortKey, 'asc' | 'desc'>
} {
  try {
    const raw = window.localStorage.getItem(LIB_SETTINGS_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    const rawOrder = Array.isArray(parsed?.sortOrder) ? parsed.sortOrder : []
    const sortOrder = rawOrder.filter(isLibrarySortKey)
    const rawDirMap = parsed?.sortDirMap && typeof parsed.sortDirMap === 'object' ? parsed.sortDirMap : {}
    const sortDirMap = { ...LIB_SORT_DEFAULT_DIR }
    for (const key of Object.keys(rawDirMap)) {
      if (!isLibrarySortKey(key)) continue
      const dir = rawDirMap[key]
      if (dir === 'asc' || dir === 'desc') sortDirMap[key] = dir
    }
    return { sortOrder: sortOrder.length > 0 ? sortOrder : ['added_at'], sortDirMap }
  } catch {
    return { sortOrder: ['added_at'], sortDirMap: { ...LIB_SORT_DEFAULT_DIR } }
  }
}

function sortLikeLibraryPage(items: LibraryItem[], werByVideoId: Record<string, number | null> = {}): LibraryItem[] {
  const { sortOrder, sortDirMap } = loadLibrarySortSettings()
  return [...items].sort((a, b) => {
    for (const key of sortOrder) {
      let va: string | number = ''
      let vb: string | number = ''
      if (key === 'title') { va = (a.title || '').toLowerCase(); vb = (b.title || '').toLowerCase() }
      else if (key === 'language') { va = a.language || ''; vb = b.language || '' }
      else if (key === 'duration') { va = a.duration_seconds ?? -1; vb = b.duration_seconds ?? -1 }
      else if (key === 'genre') { va = (a.genre || '').toLowerCase(); vb = (b.genre || '').toLowerCase() }
      else if (key === 'view_count') { va = a.view_count ?? -1; vb = b.view_count ?? -1 }
      else if (key === 'subtitle_languages') {
        va = (a.subtitle_languages || []).join(',').toLowerCase()
        vb = (b.subtitle_languages || []).join(',').toLowerCase()
      }
      else if (key === 'subtitles') { va = a.subtitles_local ? 1 : 0; vb = b.subtitles_local ? 1 : 0 }
      else if (key === 'audio') { va = a.audio_cached ? 1 : 0; vb = b.audio_cached ? 1 : 0 }
      else if (key === 'visible_in_menus') { va = a.visible_in_menus !== false ? 1 : 0; vb = b.visible_in_menus !== false ? 1 : 0 }
      else if (key === 'added_at') { va = a.upload_date ?? a.added_at ?? ''; vb = b.upload_date ?? b.added_at ?? '' }
      else if (key === 'wer') { va = werByVideoId[a.video_id] ?? 999; vb = werByVideoId[b.video_id] ?? 999 }
      const cmp = va < vb ? -1 : va > vb ? 1 : 0
      if (cmp !== 0) return sortDirMap[key] === 'asc' ? cmp : -cmp
    }
    return 0
  })
}

function computeTrialStatus(
  drop: number | undefined,
  rtf: number | undefined,
  fwMs: number | undefined | null,
  qPeakS: number | undefined | null,
  error?: string | null,
): 'ok' | 'borderline' | 'too_slow_for_slot' | 'fail' {
  const d = drop ?? 0
  const q = qPeakS ?? 0
  if (d > 0.35 || (fwMs != null && fwMs > 10000) || q > 3.0 || Boolean(error?.trim())) return 'fail'
  if ((fwMs != null && fwMs > 10000) || q > 3.0) return 'too_slow_for_slot'
  if (d > 0.10 || (rtf ?? 0) > 0.8 || (fwMs != null && fwMs > 5000)) return 'borderline'
  return 'ok'
}

function dbfsFromLinear(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return -120
  return Math.max(-120, Math.min(0, 20 * Math.log10(value)))
}

function computePcmStats(int16: Int16Array): {
  rmsDbfs: number
  peakDbfs: number
  clippingPct: number
  vadSpeech: boolean
} {
  if (int16.length === 0) {
    return { rmsDbfs: -120, peakDbfs: -120, clippingPct: 0, vadSpeech: false }
  }
  let sumSquares = 0
  let peak = 0
  let clipped = 0
  for (let i = 0; i < int16.length; i++) {
    const abs = Math.abs(int16[i])
    const normalized = abs / 32768
    sumSquares += normalized * normalized
    if (normalized > peak) peak = normalized
    if (abs >= 32700) clipped += 1
  }
  const rmsDbfs = dbfsFromLinear(Math.sqrt(sumSquares / int16.length))
  const peakDbfs = dbfsFromLinear(peak)
  const clippingPct = (clipped / int16.length) * 100
  return {
    rmsDbfs,
    peakDbfs,
    clippingPct,
    vadSpeech: rmsDbfs > -45 || peakDbfs > -35,
  }
}

const WS_BASE = `ws://${window.location.host}`
const SAMPLE_RATE = 16000
const WS_AUDIO_MAGIC = 0x54545341 // "ASTT" little-endian
const MIC_UI_STORAGE_KEY = 'astt.mic_ui.v1'

const MIC_REFERENCE_READING_TEXT = `Skákal pes,
přes oves, 
přes zelenou louku.
Šel za ním myslivec,
péro na klobouku.
Pejsku náš,
co děláš,
žes tak vesel stále.
Řek bych vám,
nevím sám,
hop a skákal dále.`

const MIC_REFERENCE_READING_TEXT_2 = `Medvědi nevědi, že tůristi nemaj zbraně,
až jednou procitnou, počíhají si někde na ně.
Výpravě v Doubravě malý grizzly ukáže se,
tůristé zajisté rozutíkají se po lese.
Na pěšině zbydou po nich tranzistoráky
a dívčí dřeváky a drahé foťáky,
medvědi je v městě vymění za zlaťáky,
za ty si koupí maliny, med a slané buráky.
Medvědi nevědi, že tůristi nemaj zbraně,
až jednou procitnou, počíhají si někde na ně.
Výpravě v Doubravě malý grizzly ukáže se,
tůristé zajisté rozutíkají se po lese.`

/**
 * Doporučené MIC defaulty z interních tuning výsledků (27. 3.-26. 4. 2026).
 * Nejsou to "tvrdé" backend defaulty — aplikují se pouze v MIC UI.
 */
const MIC_RECOMMENDED_DEFAULTS: Record<string, Record<string, unknown>> = {
  whisper_cpp_base: {
    language: 'cs',
    threads: 8,
    beam_size: 1,
    best_of: 1,
    no_fallback: false,
    initial_prompt: 'Aspergerův syndrom',
    analysis_interval_ms: 2000,
    analysis_window_seconds: 10,
    input_gain_db: 0.0,
    backpressure_high_s: 2.0,
    backpressure_low_s: 0.8,
  },
  whisper_cpp_small: {
    language: 'cs',
    threads: 4,
    beam_size: 5,
    best_of: 1,
    no_fallback: false,
    initial_prompt: '',
    analysis_interval_ms: 1200,
    analysis_window_seconds: 12,
    input_gain_db: 0.0,
    backpressure_high_s: 1.2,
    backpressure_low_s: 0.4,
  },
  whisper_cpp_large_v3_turbo: {
    language: 'cs',
    threads: 8,
    beam_size: 2,
    best_of: 1,
    no_fallback: false,
    initial_prompt: '',
    analysis_interval_ms: 1200,
    analysis_window_seconds: 12,
    input_gain_db: 0.0,
    backpressure_high_s: 1.2,
    backpressure_low_s: 0.4,
  },
  vosk_small_cs_0_4: {
    sample_rate: 16000,
    chunk_seconds: 0.4,
    set_words: false,
    input_gain_db: 1.0,
    backpressure_high_s: 1.4,
    backpressure_low_s: 0.5,
  },
}

const MIC_PARAM_HINTS: Record<string, string> = {
  language: 'Pro CZ test nastav `cs`; auto detekci nech jen když střídáš jazyky.',
  threads: 'Začni na 4 (slabší HW) až 8 (silnější CPU). Moc vysoko může zhoršit stabilitu.',
  beam_size: '1-2 = rychlé/live, 3-5 = přesnější ale pomalejší.',
  best_of: 'Drž 1-2 pro live. Vyšší hodnoty zvedají latenci bez velkého přínosu.',
  no_fallback: 'Pro stabilní live nech vypnuté. Zapni jen když chceš striktní dekódování.',
  initial_prompt: 'Krátký CZ kontext pomáhá u jmen a tématu; dlouhý prompt spíš škodí.',
  analysis_interval_ms: '1200-2000 ms je obvykle dobrý kompromis; whisper.cpp base v posledním testu držel nejlépe 2000 ms.',
  analysis_window_seconds: '10-14 s pro běžné live. Delší okno zlepší kontext, ale zvýší zpoždění.',
  input_gain_db: 'Drž kolem 0 dB. Zvyš jen při tichém vstupu, sniž při přebuzení a šumu.',
  backpressure_high_s: 'Vyšší hodnota = méně dropů, ale větší zpoždění. Běžně 1.0-2.0 s podle modelu.',
  backpressure_low_s: 'Hystereze návratu z backpressure; drž zhruba třetinu až polovinu high.',
  compute_type: 'Na CPU preferuj `int8`; vyšší přesnost typicky znamená pomalejší běh.',
  device: 'Pro starší kancelářské PC použij `cpu`; `cuda` jen pokud je stabilně dostupná.',
  sample_rate: 'Pro mic drž 16000 Hz, jinak roste režie bez jasného přínosu.',
  chunk_seconds: 'Pro VOSK se v posledním CZ mic testu osvědčilo 0.4 s; menší chunk zkus jen při honbě za nižší latencí.',
  set_words: 'Zapni jen když potřebuješ word timestampy, jinak nech vypnuté.',
  num_threads: 'Stejné doporučení jako `threads`: 4-8 podle CPU, bez přestřelení.',
  decoding_method: 'Pro live začni `greedy_search`; beam variantu testuj až když je rezerva výkonu.',
  provider: 'Na běžném HW preferuj `cpu`; jiné providery jen pokud jsou ověřeně stabilní.',
  model_arch: 'Na slabším HW `tiny/small`, `medium` jen pokud drží RTF pod 1.',
}

const TUNING_PARAM_EFFECT_HINTS: Record<string, TuningParamEffectHint> = {
  threads: {
    minus: 'Méně vláken = nižší zátěž PC a často stabilnější běh, ale model může být pomalejší.',
    plus: 'Více vláken = vyšší šance stíhat realtime, ale po určité hranici může růst režie a nestabilita.',
  },
  num_threads: {
    minus: 'Méně vláken = nižší zátěž PC a často stabilnější běh, ale model může být pomalejší.',
    plus: 'Více vláken = vyšší šance stíhat realtime, ale po určité hranici může růst režie a nestabilita.',
  },
  beam_size: {
    minus: 'Nižší beam = rychlejší přepis a menší latence, ale může klesnout přesnost.',
    plus: 'Vyšší beam = může zlepšit přesnost, ale zvyšuje latenci a riziko, že model nestihne online běh.',
  },
  best_of: {
    minus: 'Nižší best_of = rychlejší live běh s menší zátěží.',
    plus: 'Vyšší best_of = víc výpočtu pro výběr výsledku, obvykle vyšší latence a malý přínos pro live.',
  },
  analysis_interval_ms: {
    minus: 'Kratší interval = častější aktualizace a nižší zpoždění, ale vyšší zátěž a riziko backpressure.',
    plus: 'Delší interval = menší zátěž a stabilnější běh, ale pomalejší první/nový text.',
  },
  analysis_window_seconds: {
    minus: 'Kratší okno = nižší latence a zátěž, ale méně kontextu pro přesnost.',
    plus: 'Delší okno = víc kontextu a někdy lepší přesnost, ale vyšší latence a CPU/RAM zátěž.',
  },
  input_gain_db: {
    minus: 'Nižší gain = méně šumu a clippingu, ale tichá řeč může být hůř rozpoznaná.',
    plus: 'Vyšší gain = pomůže tichému vstupu, ale může přidat šum, clipping a falešnou řeč.',
  },
  backpressure_high_s: {
    minus: 'Nižší mez = dřívější ochrana fronty a menší zpoždění, ale větší riziko dropů.',
    plus: 'Vyšší mez = méně dropů při pomalém modelu, ale může narůst zpoždění přepisu.',
  },
  backpressure_low_s: {
    minus: 'Nižší návratová mez = rychlejší návrat z backpressure, ale častější kolísání.',
    plus: 'Vyšší návratová mez = stabilnější hystereze, ale déle trvá návrat do normálního toku.',
  },
  sample_rate: {
    minus: 'Nižší sample rate může snížit zátěž, ale u mic testů se očekává 16 kHz a změna může zhoršit kompatibilitu.',
    plus: 'Vyšší sample rate obvykle zvyšuje režii bez jasného přínosu pro současné mic modely.',
  },
  chunk_seconds: {
    minus: 'Menší chunk = nižší latence, ale víc overheadu a vyšší riziko nestability na slabším CPU.',
    plus: 'Větší chunk = stabilnější zpracování a méně overheadu, ale pomalejší reakce přepisu.',
  },
}

const TUNING_CUSTOM_RANGE_NO_PARAM_HINT =
  'Vlastní rozsahy ladění jsou zapnuté, ale není aktivní žádný parametr použitelný pro vybrané modely. V tabulce zaškrtni ve sloupci Zapnout aspoň jeden řádek, kde sloupec Modely ukazuje podporovaný model, nebo klikni Doporučené rozsahy. Pokud je u parametru napsáno, že ho žádný vybraný model nepodporuje, vyber jiný model nebo jiný parametr.'

const SEQUENCE_PARAM_PROFILES: Array<{
  id: SequenceParamProfileId
  label: string
  description: string
}> = [
  {
    id: 'recommended_per_model',
    label: 'Doporučené per model',
    description: 'Každý model dostane svůj doporučený MIC profil.',
  },
  {
    id: 'fast_online',
    label: 'Rychlý online',
    description: 'Nižší latence: menší beam, kratší okno, CPU/int8 kde to jde.',
  },
  {
    id: 'quality_online',
    label: 'Kvalitnější',
    description: 'Vyšší kvalita za cenu latence: delší okno, vyšší beam kde to model podporuje.',
  },
]

const TUNING_SWEEP_MODES: Array<{
  id: TuningSweepMode
  label: string
  description: string
}> = [
  {
    id: 'focused',
    label: 'Úzké doladění',
    description: 'Baseline + malé kroky kolem dvou nejpravděpodobnějších parametrů.',
  },
  {
    id: 'wide',
    label: 'Širší ověření',
    description: 'Přidá ještě bezpečné změny okna/beam/backpressure pro hledání rezervy.',
  },
]
const TUNING_SWEEP_REPEAT_OPTIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

function buildMicDefaultParams(model?: ModelDescriptor): Record<string, unknown> {
  if (!model) return {}

  const merged: Record<string, unknown> = {}
  for (const param of model.params) {
    merged[param.name] = param.default
  }

  const recommended = MIC_RECOMMENDED_DEFAULTS[model.model_id]
  if (!recommended) return merged

  for (const param of model.params) {
    if (Object.prototype.hasOwnProperty.call(recommended, param.name)) {
      merged[param.name] = recommended[param.name]
    }
  }
  return merged
}

function formatDurationHms(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds))
  const h = Math.floor(safe / 3600)
  const m = Math.floor((safe % 3600) / 60)
  const s = safe % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

function asFiniteNumber(value: unknown): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
  return value
}

function clipText(value: string, maxLen: number): string {
  const text = (value || '').trim()
  if (!text) return ''
  if (text.length <= maxLen) return text
  return `${text.slice(0, Math.max(1, maxLen - 1))}…`
}

function transcriptWords(value: string): string[] {
  return (value || '').trim().split(/\s+/).filter(Boolean)
}

function clipWords(value: string, maxWords: number): string {
  const text = (value || '').trim()
  if (!text) return ''
  const words = transcriptWords(text)
  if (words.length <= maxWords) return text
  return `${words.slice(0, maxWords).join(' ')}…`
}

const HISTORY_SORT_DEFAULT_DIR: Record<HistorySortKey, 'asc' | 'desc'> = {
  saved_at: 'desc',
  model_id: 'asc',
  mic_test_mode: 'asc',
  reference_label: 'asc',
  rtf: 'asc',
  drop_rate: 'asc',
}
const HISTORY_SORT_KEYS: HistorySortKey[] = ['saved_at', 'model_id', 'mic_test_mode', 'reference_label', 'rtf', 'drop_rate']
const REFERENCE_TEXT_IDS: ReferenceTextId[] = ['prepared_1', 'prepared_2', 'custom_1', 'custom_2']

type MicUiPersistedState = {
  version: 1
  modelId: string
  modelParamsById: Record<string, Record<string, unknown>>
  deviceIndex: number | null
  testMode: MicTestMode
  referenceVideoId: string
  referenceClipFromS: number
  referenceClipToS: number
  durationAnchor: ClipDurationAnchor
  customReferenceText1: string
  customReferenceText2: string
  selectedReferenceTextId: ReferenceTextId
  historyModeFilter: HistoryModeFilter
  historySortOrder: HistorySortKey[]
  showAllHistory: boolean
  mobileLoopEnabled: boolean
  mobileLoopEarlyStopSeconds: number
  mobileLoopPauseSeconds: number
  mobileLoopAudioStartDelaySeconds: number
  mobileLoopSyncFirstRound: boolean
  mobileLoopRepeatCount: number
  mobileLoopAutoStop: boolean
  mobileLoopAutoPlayOnSequenceStart: boolean
  autoModelCycleEnabled: boolean
  autoModelSelectedIds: string[]
  autoModelGraceSeconds: number
  autoModelSilenceStopSeconds: number
  orchestratorMode: MicOrchestratorMode
  sequenceCommonParamsEnabled: boolean
  sequenceCommonParams: Record<string, unknown>
  sequenceParamProfileLabel: string
  sequenceParamProfileDirty: boolean
  tuningSweepEnabled: boolean
  tuningSweepMode: TuningSweepMode
  tuningSweepStepSize: number
  tuningSweepRepeatCount: number
  tuningSweepMaxLagSeconds: number
  tuningSweepCustomRangesEnabled: boolean
  tuningSweepRangeConfigs: TuningSweepRangeConfigMap
}

function asObjectRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function asFiniteNumberOr(value: unknown, fallback: number): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  return fallback
}

function isHistorySortKey(value: unknown): value is HistorySortKey {
  return typeof value === 'string' && HISTORY_SORT_KEYS.includes(value as HistorySortKey)
}

function isReferenceTextId(value: unknown): value is ReferenceTextId {
  return typeof value === 'string' && REFERENCE_TEXT_IDS.includes(value as ReferenceTextId)
}

function isClipDurationAnchor(value: unknown): value is ClipDurationAnchor {
  return value === 'from' || value === 'to'
}

function normalizeHistorySortOrder(value: unknown): HistorySortKey[] {
  if (!Array.isArray(value)) return ['saved_at']
  const normalized: HistorySortKey[] = []
  for (const item of value) {
    if (!isHistorySortKey(item)) continue
    if (normalized.includes(item)) continue
    normalized.push(item)
  }
  return normalized.length > 0 ? normalized : ['saved_at']
}

function normalizeStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const out: string[] = []
  for (const item of value) {
    if (typeof item !== 'string') continue
    const trimmed = item.trim()
    if (!trimmed) continue
    if (out.includes(trimmed)) continue
    out.push(trimmed)
  }
  return out
}

function normalizeMicOrchestratorMode(value: unknown): MicOrchestratorMode {
  if (value === 'v7_cs_online') return 'v7_cs_online'
  return 'legacy_sequence'
}

function normalizeModelParamsById(value: unknown): Record<string, Record<string, unknown>> {
  const root = asObjectRecord(value)
  if (!root) return {}
  const normalized: Record<string, Record<string, unknown>> = {}
  for (const [modelId, rawParams] of Object.entries(root)) {
    const params = asObjectRecord(rawParams)
    if (!params) continue
    normalized[modelId] = { ...params }
  }
  return normalized
}

function readMicUiState(): Partial<MicUiPersistedState> {
  try {
    const raw = window.localStorage.getItem(MIC_UI_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return asObjectRecord(parsed) ?? {}
  } catch {
    return {}
  }
}

function writeMicUiState(state: MicUiPersistedState): void {
  try {
    window.localStorage.setItem(MIC_UI_STORAGE_KEY, JSON.stringify(state))
  } catch {
    // localStorage může být nedostupné (privacy mode, quota, policy)
  }
}

function buildMicParamsWithSaved(model: ModelDescriptor | undefined, saved?: Record<string, unknown>): Record<string, unknown> {
  const merged = buildMicDefaultParams(model)
  if (!model || !saved) return merged
  for (const param of model.params) {
    if (Object.prototype.hasOwnProperty.call(saved, param.name)) {
      merged[param.name] = saved[param.name]
    }
  }
  return merged
}

const COMMON_PARAM_ORDER = [
  'language',
  'lang',
  'threads',
  'num_threads',
  'beam_size',
  'best_of',
  'no_fallback',
  'compute_type',
  'device',
  'provider',
  'decoding_method',
  'analysis_interval_ms',
  'analysis_window_seconds',
  'input_gain_db',
  'backpressure_high_s',
  'backpressure_low_s',
  'sample_rate',
  'chunk_seconds',
  'set_words',
  'initial_prompt',
  'model_arch',
]

function buildCommonParamSpecs(models: ModelDescriptor[]): ParamSpec[] {
  const byName = new Map<string, ParamSpec>()
  for (const model of models) {
    for (const param of model.params) {
      const existing = byName.get(param.name)
      if (!existing) {
        byName.set(param.name, { ...param, options: [...(param.options ?? [])] })
        continue
      }
      byName.set(param.name, {
        ...existing,
        label: existing.label || param.label || param.name,
        description: existing.description || param.description || '',
        min: typeof existing.min === 'number' && typeof param.min === 'number'
          ? Math.min(existing.min, param.min)
          : existing.min ?? param.min,
        max: typeof existing.max === 'number' && typeof param.max === 'number'
          ? Math.max(existing.max, param.max)
          : existing.max ?? param.max,
        options: Array.from(new Set([...(existing.options ?? []), ...(param.options ?? [])])),
      })
    }
  }

  return Array.from(byName.values()).sort((a, b) => {
    const ai = COMMON_PARAM_ORDER.indexOf(a.name)
    const bi = COMMON_PARAM_ORDER.indexOf(b.name)
    if (ai >= 0 || bi >= 0) return (ai >= 0 ? ai : 999) - (bi >= 0 ? bi : 999)
    return a.label.localeCompare(b.label, 'cs')
  })
}

function buildParamsFromSpecs(specs: ParamSpec[]): Record<string, unknown> {
  const values: Record<string, unknown> = {}
  for (const spec of specs) {
    values[spec.name] = spec.default
  }
  return values
}

function pickModelSupportedParams(
  model: ModelDescriptor | undefined,
  values?: Record<string, unknown> | null,
): Record<string, unknown> {
  const picked: Record<string, unknown> = {}
  if (!model || !values) return picked
  for (const param of model.params) {
    if (Object.prototype.hasOwnProperty.call(values, param.name)) {
      picked[param.name] = values[param.name]
    }
  }
  return picked
}

function buildMicParamsWithCommon(
  model: ModelDescriptor | undefined,
  saved: Record<string, unknown> | undefined,
  commonEnabled: boolean,
  commonParams: Record<string, unknown>,
): Record<string, unknown> {
  const merged = buildMicParamsWithSaved(model, saved)
  if (!model || !commonEnabled) return merged
  return { ...merged, ...pickModelSupportedParams(model, commonParams) }
}

function clampProfileNumber(param: ParamSpec, value: number): number {
  let next = value
  if (typeof param.min === 'number') next = Math.max(param.min, next)
  if (typeof param.max === 'number') next = Math.min(param.max, next)
  return param.type === 'int' ? Math.round(next) : next
}

function profileSelectValue(param: ParamSpec, preferred: string[]): string | undefined {
  for (const value of preferred) {
    if (param.options.includes(value)) return value
  }
  return undefined
}

function profileLanguageValue(model: ModelDescriptor, param: ParamSpec): string | undefined {
  const langs = model.languages ?? []
  if (param.type === 'select') {
    if (param.options.includes('cs') && langs.includes('cs')) return 'cs'
    if (param.options.includes('cs') && langs.length === 0) return 'cs'
    const modelOption = langs.find((lang) => param.options.includes(lang))
    if (modelOption) return modelOption
    return typeof param.default === 'string' && param.options.includes(param.default) ? param.default : undefined
  }
  if (langs.includes('cs')) return 'cs'
  if (typeof param.default === 'string' && param.default.trim()) return param.default
  return langs[0]
}

function setProfileParam(
  model: ModelDescriptor,
  values: Record<string, unknown>,
  name: string,
  value: unknown,
) {
  const param = model.params.find((p) => p.name === name)
  if (!param) return
  if (param.type === 'select') {
    if (typeof value !== 'string') return
    if (!param.options.includes(value)) return
    values[name] = value
    return
  }
  if (param.type === 'int' || param.type === 'float') {
    if (typeof value !== 'number' || !Number.isFinite(value)) return
    values[name] = clampProfileNumber(param, value)
    return
  }
  if (param.type === 'bool') {
    values[name] = Boolean(value)
    return
  }
  values[name] = value
}

function buildParamsForSequenceProfile(profileId: SequenceParamProfileId, model: ModelDescriptor): Record<string, unknown> {
  const values = buildMicDefaultParams(model)
  if (profileId === 'recommended_per_model') return values

  for (const param of model.params) {
    if (param.name === 'language' || param.name === 'lang') {
      const lang = profileLanguageValue(model, param)
      if (lang) setProfileParam(model, values, param.name, lang)
    }
  }

  if (profileId === 'fast_online') {
    setProfileParam(model, values, 'threads', 4)
    setProfileParam(model, values, 'num_threads', 4)
    setProfileParam(model, values, 'beam_size', 1)
    setProfileParam(model, values, 'best_of', 1)
    setProfileParam(model, values, 'no_fallback', false)
    setProfileParam(model, values, 'analysis_interval_ms', 1000)
    setProfileParam(model, values, 'analysis_window_seconds', 8)
    setProfileParam(model, values, 'input_gain_db', 0)
    setProfileParam(model, values, 'backpressure_high_s', 1.0)
    setProfileParam(model, values, 'backpressure_low_s', 0.3)
    setProfileParam(model, values, 'sample_rate', 16000)
    setProfileParam(model, values, 'chunk_seconds', 0.2)
    setProfileParam(model, values, 'set_words', false)
    for (const param of model.params) {
      if (param.name === 'compute_type') {
        const value = profileSelectValue(param, ['int8', 'int8_float16', 'float16', 'float32'])
        if (value) setProfileParam(model, values, param.name, value)
      }
      if (param.name === 'device' || param.name === 'provider') {
        const value = profileSelectValue(param, ['cpu', 'auto', 'cuda', 'coreml'])
        if (value) setProfileParam(model, values, param.name, value)
      }
      if (param.name === 'decoding_method') {
        const value = profileSelectValue(param, ['greedy_search', 'modified_beam_search'])
        if (value) setProfileParam(model, values, param.name, value)
      }
    }
    return values
  }

  setProfileParam(model, values, 'threads', 6)
  setProfileParam(model, values, 'num_threads', 6)
  setProfileParam(model, values, 'beam_size', 3)
  setProfileParam(model, values, 'best_of', 2)
  setProfileParam(model, values, 'no_fallback', false)
  setProfileParam(model, values, 'analysis_interval_ms', 1400)
  setProfileParam(model, values, 'analysis_window_seconds', 14)
  setProfileParam(model, values, 'input_gain_db', 0)
  setProfileParam(model, values, 'backpressure_high_s', 1.4)
  setProfileParam(model, values, 'backpressure_low_s', 0.5)
  setProfileParam(model, values, 'sample_rate', 16000)
  setProfileParam(model, values, 'chunk_seconds', 0.25)
  setProfileParam(model, values, 'set_words', false)
  for (const param of model.params) {
    if (param.name === 'compute_type') {
      const value = profileSelectValue(param, ['int8', 'int8_float16', 'float16', 'float32'])
      if (value) setProfileParam(model, values, param.name, value)
    }
    if (param.name === 'device' || param.name === 'provider') {
      const value = profileSelectValue(param, ['cpu', 'auto', 'cuda', 'coreml'])
      if (value) setProfileParam(model, values, param.name, value)
    }
    if (param.name === 'decoding_method') {
      const value = profileSelectValue(param, ['modified_beam_search', 'greedy_search'])
      if (value) setProfileParam(model, values, param.name, value)
    }
  }
  return values
}

function normalizeTuningSweepMode(value: unknown): TuningSweepMode {
  return value === 'wide' ? 'wide' : 'focused'
}

function normalizeTuningSweepRepeatCount(value: unknown): number {
  const raw = typeof value === 'number' && Number.isFinite(value) ? value : 5
  return Math.max(1, Math.min(10, Math.floor(raw) || 5))
}

function normalizeTuningSweepStepSize(value: unknown): number {
  const raw = typeof value === 'number' && Number.isFinite(value) ? value : 1
  return Math.max(0.25, Math.min(4, Math.round(raw * 4) / 4))
}

function tuningParamSupportsRange(param: ParamSpec): boolean {
  return param.type === 'int' || param.type === 'float'
}

function numberParamDefault(param: ParamSpec, fallback = 0): number {
  return typeof param.default === 'number' && Number.isFinite(param.default)
    ? param.default
    : fallback
}

function decimalPlaces(value: number): number {
  if (!Number.isFinite(value)) return 0
  const text = String(value)
  const dot = text.indexOf('.')
  return dot >= 0 ? Math.min(6, text.length - dot - 1) : 0
}

function roundForParam(param: ParamSpec, value: number, step: number): number {
  if (param.type === 'int') return Math.round(value)
  const places = Math.max(2, decimalPlaces(step))
  return Number(value.toFixed(places))
}

function defaultTuningRangeConfig(param: ParamSpec): TuningSweepRangeConfig {
  const current = numberParamDefault(param, 0)
  const min = typeof param.min === 'number' && Number.isFinite(param.min) ? param.min : -999999
  const max = typeof param.max === 'number' && Number.isFinite(param.max) ? param.max : 999999
  const clamp = (value: number) => Math.max(min, Math.min(max, value))
  if (param.name === 'chunk_seconds') return { enabled: true, from: clamp(0.2), to: clamp(0.6), step: 0.05 }
  if (param.name === 'analysis_interval_ms') return { enabled: true, from: clamp(1000), to: clamp(2200), step: 200 }
  if (param.name === 'input_gain_db') return { enabled: false, from: clamp(-2), to: clamp(3), step: 1 }
  if (param.name === 'analysis_window_seconds') return { enabled: false, from: clamp(8), to: clamp(16), step: 2 }
  if (param.name === 'backpressure_high_s') return { enabled: false, from: clamp(1), to: clamp(2.4), step: 0.2 }
  if (param.name === 'backpressure_low_s') return { enabled: false, from: clamp(0.3), to: clamp(1), step: 0.1 }
  if (param.name === 'threads' || param.name === 'num_threads') return { enabled: false, from: clamp(4), to: clamp(8), step: 2 }
  if (param.name === 'beam_size') return { enabled: false, from: clamp(1), to: clamp(3), step: 1 }
  const step = param.type === 'int' ? 1 : 0.5
  return {
    enabled: false,
    from: clamp(current - step),
    to: clamp(current + step),
    step,
  }
}

function normalizeTuningRangeConfig(param: ParamSpec, raw?: Partial<TuningSweepRangeConfig> | null): TuningSweepRangeConfig {
  const defaults = defaultTuningRangeConfig(param)
  const min = typeof param.min === 'number' && Number.isFinite(param.min) ? param.min : -999999
  const max = typeof param.max === 'number' && Number.isFinite(param.max) ? param.max : 999999
  const clamp = (value: unknown, fallback: number) => {
    const n = typeof value === 'number' && Number.isFinite(value) ? value : fallback
    return Math.max(min, Math.min(max, n))
  }
  let from = clamp(raw?.from, defaults.from)
  let to = clamp(raw?.to, defaults.to)
  if (to < from) [from, to] = [to, from]
  const rawStep = typeof raw?.step === 'number' && Number.isFinite(raw.step) ? Math.abs(raw.step) : defaults.step
  const step = Math.max(param.type === 'int' ? 1 : 0.001, rawStep || defaults.step)
  return {
    enabled: typeof raw?.enabled === 'boolean' ? raw.enabled : defaults.enabled,
    from: roundForParam(param, from, step),
    to: roundForParam(param, to, step),
    step: roundForParam(param, step, step),
  }
}

function normalizeTuningRangeConfigMap(value: unknown): TuningSweepRangeConfigMap {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const out: TuningSweepRangeConfigMap = {}
  for (const [name, raw] of Object.entries(value as Record<string, unknown>)) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue
    const obj = raw as Record<string, unknown>
    const from = typeof obj.from === 'number' && Number.isFinite(obj.from) ? obj.from : undefined
    const to = typeof obj.to === 'number' && Number.isFinite(obj.to) ? obj.to : undefined
    const step = typeof obj.step === 'number' && Number.isFinite(obj.step) ? obj.step : undefined
    out[name] = {
      enabled: obj.enabled === true,
      from: from ?? 0,
      to: to ?? 0,
      step: step ?? 1,
    }
  }
  return out
}

function tuningRangeValues(param: ParamSpec, config: TuningSweepRangeConfig, maxValues = 40): number[] {
  if (!config.enabled || !tuningParamSupportsRange(param)) return []
  const normalized = normalizeTuningRangeConfig(param, config)
  const values: number[] = []
  const step = Math.max(param.type === 'int' ? 1 : 0.001, Math.abs(normalized.step))
  for (let value = normalized.from, guard = 0; value <= normalized.to + step / 2 && guard < maxValues; value += step, guard += 1) {
    const rounded = roundForParam(param, value, step)
    if (!values.some((existing) => sameParamValue(existing, rounded))) values.push(rounded)
  }
  return values
}

function modelSupportsParam(model: ModelDescriptor, name: string): boolean {
  return model.params.some((param) => param.name === name)
}

function tuningNumberParamValue(
  model: ModelDescriptor,
  baseline: Record<string, unknown>,
  name: string,
  fallback: number,
): number {
  const value = baseline[name]
  if (typeof value === 'number' && Number.isFinite(value)) return value
  const spec = model.params.find((param) => param.name === name)
  return typeof spec?.default === 'number' && Number.isFinite(spec.default) ? spec.default : fallback
}

function buildTuningVariant(
  model: ModelDescriptor,
  baseline: Record<string, unknown>,
  variantId: string,
  variantLabel: string,
  patch: Record<string, unknown>,
): { variantId: string; variantLabel: string; params: Record<string, unknown>; changedParams: Record<string, unknown> } | null {
  const params = { ...baseline }
  const changedParams: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(patch)) {
    if (!modelSupportsParam(model, name)) continue
    const before = params[name]
    setProfileParam(model, params, name, value)
    const after = params[name]
    if (!sameParamValue(before, after)) {
      changedParams[name] = after
    }
  }
  if (Object.keys(changedParams).length === 0) return null
  return { variantId, variantLabel, params, changedParams }
}

function addTuningVariant(
  variants: Array<{ variantId: string; variantLabel: string; params: Record<string, unknown>; changedParams: Record<string, unknown> }>,
  seen: Set<string>,
  model: ModelDescriptor,
  baseline: Record<string, unknown>,
  variantId: string,
  variantLabel: string,
  patch: Record<string, unknown>,
) {
  const variant = buildTuningVariant(model, baseline, variantId, variantLabel, patch)
  if (!variant) return
  const signature = JSON.stringify(
    Object.entries(pickModelSupportedParams(model, variant.params)).sort(([a], [b]) => a.localeCompare(b)),
  )
  if (seen.has(signature)) return
  seen.add(signature)
  variants.push(variant)
}

function buildTuningVariantsForModel(
  model: ModelDescriptor,
  baseline: Record<string, unknown>,
  mode: TuningSweepMode,
  stepSizeRaw: number,
): Array<{ variantId: string; variantLabel: string; params: Record<string, unknown>; changedParams: Record<string, unknown> }> {
  const baselineParams = pickModelSupportedParams(model, baseline)
  const stepSize = normalizeTuningSweepStepSize(stepSizeRaw)
  const variants: Array<{ variantId: string; variantLabel: string; params: Record<string, unknown>; changedParams: Record<string, unknown> }> = [
    { variantId: 'baseline', variantLabel: 'Baseline', params: { ...baselineParams }, changedParams: {} },
  ]
  const seen = new Set<string>([
    JSON.stringify(Object.entries(baselineParams).sort(([a], [b]) => a.localeCompare(b))),
  ])
  const id = model.model_id.toLowerCase()

  if (id.includes('vosk')) {
    const chunk = tuningNumberParamValue(model, baselineParams, 'chunk_seconds', 0.4)
    const gain = tuningNumberParamValue(model, baselineParams, 'input_gain_db', 1)
    const chunkStep = Number((0.05 * stepSize).toFixed(3))
    const gainStep = Number((1 * stepSize).toFixed(2))
    addTuningVariant(variants, seen, model, baselineParams, 'chunk_minus', `chunk -${chunkStep} s`, { chunk_seconds: Number((chunk - chunkStep).toFixed(3)) })
    addTuningVariant(variants, seen, model, baselineParams, 'chunk_plus', `chunk +${chunkStep} s`, { chunk_seconds: Number((chunk + chunkStep).toFixed(3)) })
    addTuningVariant(variants, seen, model, baselineParams, 'gain_minus', `gain -${gainStep} dB`, { input_gain_db: Number((gain - gainStep).toFixed(2)) })
    addTuningVariant(variants, seen, model, baselineParams, 'gain_plus', `gain +${gainStep} dB`, { input_gain_db: Number((gain + gainStep).toFixed(2)) })
    if (mode === 'wide') {
      const bpHighStep = Number((0.3 * stepSize).toFixed(2))
      const bpLowStep = Number((0.1 * stepSize).toFixed(2))
      addTuningVariant(variants, seen, model, baselineParams, 'bp_plus', `backpressure +${bpHighStep}/+${bpLowStep}`, {
        backpressure_high_s: Number((tuningNumberParamValue(model, baselineParams, 'backpressure_high_s', 1.4) + bpHighStep).toFixed(2)),
        backpressure_low_s: Number((tuningNumberParamValue(model, baselineParams, 'backpressure_low_s', 0.5) + bpLowStep).toFixed(2)),
      })
    }
    return variants
  }

  if (id.includes('whisper')) {
    const interval = tuningNumberParamValue(model, baselineParams, 'analysis_interval_ms', 2000)
    const high = tuningNumberParamValue(model, baselineParams, 'backpressure_high_s', 1.8)
    const low = tuningNumberParamValue(model, baselineParams, 'backpressure_low_s', 0.6)
    const intervalStep = Math.max(50, Math.round((200 * stepSize) / 50) * 50)
    const bpHighMinusStep = Number((0.3 * stepSize).toFixed(2))
    const bpLowMinusStep = Number((0.1 * stepSize).toFixed(2))
    const bpHighPlusStep = Number((0.4 * stepSize).toFixed(2))
    const bpLowPlusStep = Number((0.2 * stepSize).toFixed(2))
    addTuningVariant(variants, seen, model, baselineParams, 'interval_minus', `interval -${intervalStep} ms`, { analysis_interval_ms: interval - intervalStep })
    addTuningVariant(variants, seen, model, baselineParams, 'interval_plus', `interval +${intervalStep} ms`, { analysis_interval_ms: interval + intervalStep })
    addTuningVariant(variants, seen, model, baselineParams, 'bp_minus', `backpressure -${bpHighMinusStep}/-${bpLowMinusStep}`, {
      backpressure_high_s: Number((high - bpHighMinusStep).toFixed(2)),
      backpressure_low_s: Number((low - bpLowMinusStep).toFixed(2)),
    })
    addTuningVariant(variants, seen, model, baselineParams, 'bp_plus', `backpressure +${bpHighPlusStep}/+${bpLowPlusStep}`, {
      backpressure_high_s: Number((high + bpHighPlusStep).toFixed(2)),
      backpressure_low_s: Number((low + bpLowPlusStep).toFixed(2)),
    })
    if (mode === 'wide') {
      const windowStep = Math.max(1, Math.round(2 * stepSize))
      const beamStep = Math.max(1, Math.round(stepSize))
      const beam = tuningNumberParamValue(model, baselineParams, 'beam_size', 1)
      addTuningVariant(variants, seen, model, baselineParams, 'window_plus', `okno +${windowStep} s`, {
        analysis_window_seconds: tuningNumberParamValue(model, baselineParams, 'analysis_window_seconds', 10) + windowStep,
      })
      addTuningVariant(variants, seen, model, baselineParams, 'beam_plus', `beam +${beamStep}`, { beam_size: beam + beamStep })
    }
    return variants
  }

  const gain = tuningNumberParamValue(model, baselineParams, 'input_gain_db', 0)
  const gainStep = Number((1 * stepSize).toFixed(2))
  addTuningVariant(variants, seen, model, baselineParams, 'gain_minus', `gain -${gainStep} dB`, { input_gain_db: Number((gain - gainStep).toFixed(2)) })
  addTuningVariant(variants, seen, model, baselineParams, 'gain_plus', `gain +${gainStep} dB`, { input_gain_db: Number((gain + gainStep).toFixed(2)) })
  if (modelSupportsParam(model, 'analysis_interval_ms')) {
    const interval = tuningNumberParamValue(model, baselineParams, 'analysis_interval_ms', 1400)
    const intervalStep = Math.max(50, Math.round((200 * stepSize) / 50) * 50)
    addTuningVariant(variants, seen, model, baselineParams, 'interval_minus', `interval -${intervalStep} ms`, { analysis_interval_ms: interval - intervalStep })
    addTuningVariant(variants, seen, model, baselineParams, 'interval_plus', `interval +${intervalStep} ms`, { analysis_interval_ms: interval + intervalStep })
  }
  return variants
}

function buildTuningVariantsFromRangesForModel(
  model: ModelDescriptor,
  baseline: Record<string, unknown>,
  rangeConfigs: TuningSweepRangeConfigMap,
): Array<{ variantId: string; variantLabel: string; params: Record<string, unknown>; changedParams: Record<string, unknown> }> {
  const baselineParams = pickModelSupportedParams(model, baseline)
  const variants: Array<{ variantId: string; variantLabel: string; params: Record<string, unknown>; changedParams: Record<string, unknown> }> = [
    { variantId: 'baseline', variantLabel: 'Baseline', params: { ...baselineParams }, changedParams: {} },
  ]
  const seen = new Set<string>([
    JSON.stringify(Object.entries(baselineParams).sort(([a], [b]) => a.localeCompare(b))),
  ])

  for (const [paramName, rawConfig] of Object.entries(rangeConfigs)) {
    const modelParam = model.params.find((param) => param.name === paramName)
    if (!modelParam || !tuningParamSupportsRange(modelParam)) continue
    const config = normalizeTuningRangeConfig(modelParam, rawConfig)
    if (!config.enabled) continue
    const values = tuningRangeValues(modelParam, config)
    for (const value of values) {
      const before = baselineParams[paramName]
      if (sameParamValue(before, value)) continue
      addTuningVariant(
        variants,
        seen,
        model,
        baselineParams,
        `range_${paramName}_${formatParamValue(value).replace(/[^a-z0-9_.-]+/gi, '_')}`,
        `${modelParam.label || paramName} = ${formatParamValue(value)}`,
        { [paramName]: value },
      )
    }
  }

  return variants
}

function buildTuningSweepPlan(
  models: ModelDescriptor[],
  baselineParamsByModel: Map<string, Record<string, unknown>>,
  repeatCount: number,
  mode: TuningSweepMode,
  stepSizeRaw: number,
  customRangeConfigs?: TuningSweepRangeConfigMap | null,
): TuningSweepSlot[] {
  const repeats = normalizeTuningSweepRepeatCount(repeatCount)
  const stepSize = normalizeTuningSweepStepSize(stepSizeRaw)
  const variantsByModel = models.map((model) => ({
    model,
    variants: customRangeConfigs
      ? buildTuningVariantsFromRangesForModel(
        model,
        baselineParamsByModel.get(model.model_id) ?? buildMicDefaultParams(model),
        customRangeConfigs,
      )
      : buildTuningVariantsForModel(
        model,
        baselineParamsByModel.get(model.model_id) ?? buildMicDefaultParams(model),
        mode,
        stepSize,
      ),
  }))
  const maxVariants = Math.max(0, ...variantsByModel.map((item) => item.variants.length))
  const slots: TuningSweepSlot[] = []
  for (let repeatIndex = 1; repeatIndex <= repeats; repeatIndex += 1) {
    for (let variantIndex = 0; variantIndex < maxVariants; variantIndex += 1) {
      for (const { model, variants } of variantsByModel) {
        const variant = variants[variantIndex]
        if (!variant) continue
        slots.push({
          slotIndex: 0,
          slotTotal: 0,
          modelId: model.model_id,
          variantId: `${model.model_id}:${variant.variantId}`,
          variantLabel: variant.variantLabel,
          repeatIndex,
          repeatTotal: repeats,
          mode,
          stepSize,
          params: { ...variant.params },
          baselineParams: pickModelSupportedParams(model, baselineParamsByModel.get(model.model_id) ?? buildMicDefaultParams(model)),
          changedParams: { ...variant.changedParams },
        })
      }
    }
  }
  return slots.map((slot, index) => ({ ...slot, slotIndex: index, slotTotal: slots.length }))
}

function buildTuningSlotPayload(
  slot: TuningSweepSlot | null | undefined,
  seriesId: string | null | undefined,
  maxLagS: number | null | undefined,
): Record<string, unknown> {
  if (!slot) return {}
  return {
    tuning_series_id: seriesId ?? null,
    tuning_mode: slot.mode,
    tuning_step_size: slot.stepSize,
    tuning_slot_index: slot.slotIndex + 1,
    tuning_slot_total: slot.slotTotal,
    tuning_variant_id: slot.variantId,
    tuning_variant_label: slot.variantLabel,
    tuning_repeat_index: slot.repeatIndex,
    tuning_repeat_total: slot.repeatTotal,
    tuning_changed_params: slot.changedParams,
    tuning_baseline_params: slot.baselineParams,
    tuning_max_lag_s: maxLagS ?? null,
  }
}

function formatParamValue(value: unknown): string {
  if (value == null || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'ano' : 'ne'
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '—'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function formatRecommendedTuningValues(
  param: ParamSpec,
  supportedModels: ModelDescriptor[],
  recommendedParamsByModel: Map<string, Record<string, unknown>>,
): string {
  if (supportedModels.length === 0) return 'žádný vybraný model parametr nepodporuje'
  const entries = supportedModels.map((model) => {
    const recommendedParams = recommendedParamsByModel.get(model.model_id)
    const modelParam = model.params.find((p) => p.name === param.name)
    const value = recommendedParams && Object.prototype.hasOwnProperty.call(recommendedParams, param.name)
      ? recommendedParams[param.name]
      : modelParam?.default ?? param.default
    return {
      label: model.label,
      valueText: formatParamValue(value),
    }
  })
  const uniqueValues = new Set(entries.map((entry) => entry.valueText))
  if (uniqueValues.size === 1) {
    return `všechny podporované vybrané modely: ${entries[0]?.valueText ?? '—'}`
  }
  const visible = entries.slice(0, 6).map((entry) => `${entry.label}: ${entry.valueText}`)
  const suffix = entries.length > visible.length ? `; +${entries.length - visible.length} modelů` : ''
  return `${visible.join('; ')}${suffix}`
}

function buildTuningRangeParamTooltip(
  param: ParamSpec,
  supportedModels: ModelDescriptor[],
  recommendedParamsByModel: Map<string, Record<string, unknown>>,
): string {
  const effect = TUNING_PARAM_EFFECT_HINTS[param.name]
  return [
    `${param.label} (${param.name})`,
    param.description ? `Popis: ${param.description}` : '',
    MIC_PARAM_HINTS[param.name] ? `Doporučení: ${MIC_PARAM_HINTS[param.name]}` : '',
    `Výchozí doporučené: ${formatRecommendedTuningValues(param, supportedModels, recommendedParamsByModel)}`,
    `Mínus: ${effect?.minus ?? 'nižší testovaná hodnota; konkrétní dopad ověř v sérii testů.'}`,
    `Plus: ${effect?.plus ?? 'vyšší testovaná hodnota; konkrétní dopad ověř v sérii testů.'}`,
  ].filter(Boolean).join('\n')
}

function formatParamsSummary(params?: Record<string, unknown> | null, maxItems = 6): string {
  const entries = Object.entries(params ?? {}).filter(([, value]) => value !== undefined)
  if (entries.length === 0) return ''
  const head = entries.slice(0, maxItems).map(([key, value]) => `${key}=${formatParamValue(value)}`)
  const suffix = entries.length > maxItems ? ` +${entries.length - maxItems}` : ''
  return `${head.join(', ')}${suffix}`
}

function formatConclusionModels(models?: MicSequenceConclusionModel[]): string {
  if (!Array.isArray(models) || models.length === 0) return '—'
  return models.map((model) => {
    const id = model.model_id || 'unknown'
    const prefix = model.seq_index != null ? `#${model.seq_index} ` : ''
    const parts: string[] = []
    if (typeof model.rtf === 'number') parts.push(`RTF ${model.rtf.toFixed(2)}`)
    if (typeof model.drop_rate === 'number') parts.push(`drop ${(model.drop_rate * 100).toFixed(1)}%`)
    if (model.reason_code) parts.push(String(model.reason_code))
    return `${prefix}${id}${parts.length > 0 ? ` (${parts.join(', ')})` : ''}`
  }).join(', ')
}

type TuningSweepSummaryRow = {
  key: string
  modelId: string
  variantLabel: string
  changedParams: Record<string, unknown>
  count: number
  repeatTotal: number | null
  avgRtf: number | null
  avgDropRate: number | null
  avgFirstWordMs: number | null
  avgQueuePeakS: number | null
  failCount: number
  score: number | null
  verdict: string
}

function averageFinite(values: Array<number | null | undefined>): number | null {
  const finite = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  if (finite.length === 0) return null
  return finite.reduce((sum, value) => sum + value, 0) / finite.length
}

function buildTuningSweepSummaryRows(trials: MicSequenceTrial[]): TuningSweepSummaryRow[] {
  const groups = new Map<string, MicSequenceTrial[]>()
  for (const trial of trials) {
    if (!trial.tuning_variant_id && !trial.tuning_series_id) continue
    const key = `${trial.model_id}::${trial.tuning_variant_id ?? trial.tuning_variant_label ?? 'unknown'}`
    groups.set(key, [...(groups.get(key) ?? []), trial])
  }
  const rows: TuningSweepSummaryRow[] = []
  for (const [key, group] of groups.entries()) {
    const first = group[0]
    const avgRtf = averageFinite(group.map((trial) => trial.rtf))
    const avgDropRate = averageFinite(group.map((trial) => trial.drop_rate))
    const avgFirstWordMs = averageFinite(group.map((trial) => trial.first_word_wall_ms))
    const avgQueuePeakS = averageFinite(group.map((trial) => trial.queue_depth_peak_s))
    const failCount = group.filter((trial) => trial.trial_status === 'fail' || trial.trial_status === 'too_slow_for_slot').length
    const repeatTotal = typeof first.tuning_repeat_total === 'number' ? first.tuning_repeat_total : null
    const maxLagS = typeof first.tuning_max_lag_s === 'number' ? first.tuning_max_lag_s : null
    const lagLimitExceeded = maxLagS != null && avgFirstWordMs != null && avgFirstWordMs > maxLagS * 1000
    const score = avgRtf == null && avgDropRate == null && avgFirstWordMs == null && avgQueuePeakS == null
      ? null
      : (avgRtf ?? 1.5) * 2
        + (avgDropRate ?? 0.2) * 10
        + (avgFirstWordMs ?? 12000) / 12000
        + (avgQueuePeakS ?? 2) / 3
        + failCount * 2
    const incomplete = repeatTotal != null && group.length < repeatTotal
    const verdict = incomplete
      ? `neúplné ${group.length}/${repeatTotal}`
      : lagLimitExceeded
        ? `zpoždění > ${maxLagS}s`
        : failCount > 0 || (avgDropRate ?? 0) > 0.1 || (avgRtf ?? 0) > 1.0
        ? 'nestabilní'
        : 'kandidát'
    rows.push({
      key,
      modelId: first.model_id,
      variantLabel: first.tuning_variant_label || first.tuning_variant_id || '—',
      changedParams: first.tuning_changed_params ?? {},
      count: group.length,
      repeatTotal,
      avgRtf,
      avgDropRate,
      avgFirstWordMs,
      avgQueuePeakS,
      failCount,
      score,
      verdict,
    })
  }
  return rows.sort((a, b) => {
    const byModel = a.modelId.localeCompare(b.modelId, 'cs')
    if (byModel !== 0) return byModel
    return (a.score ?? Number.POSITIVE_INFINITY) - (b.score ?? Number.POSITIVE_INFINITY)
  })
}

function sameParamValue(a: unknown, b: unknown): boolean {
  if (typeof a === 'number' && typeof b === 'number') {
    return Number.isNaN(a) ? Number.isNaN(b) : Object.is(a, b)
  }
  return Object.is(a, b)
}

function buildBulkParamSpec(baseParam: ParamSpec, models: ModelDescriptor[]): ParamSpec | null {
  const specs = models
    .map((model) => model.params.find((param) => param.name === baseParam.name))
    .filter((param): param is ParamSpec => Boolean(param))
  if (specs.length === 0) return null
  if (specs.some((param) => param.type !== specs[0].type)) return null

  const minValues = specs.map((param) => param.min).filter((value): value is number => typeof value === 'number')
  const maxValues = specs.map((param) => param.max).filter((value): value is number => typeof value === 'number')
  let options = [...(baseParam.options ?? [])]
  if (specs[0].type === 'select') {
    options = specs.reduce<string[] | null>((acc, spec) => {
      if (acc == null) return [...spec.options]
      return acc.filter((option) => spec.options.includes(option))
    }, null) ?? []
  }

  return {
    ...baseParam,
    type: specs[0].type,
    min: minValues.length > 0 ? Math.max(...minValues) : null,
    max: maxValues.length > 0 ? Math.min(...maxValues) : null,
    options,
  }
}

function BulkParamInput({
  param,
  value,
  mixed,
  disabled,
  onChange,
}: {
  param: ParamSpec
  value: unknown
  mixed: boolean
  disabled: boolean
  onChange: (value: unknown) => void
}) {
  const cls = 'bg-gray-700 border border-gray-600 rounded px-1.5 py-0.5 text-sm text-white w-24 disabled:opacity-60'
  const title = mixed ? 'Různé hodnoty; změna nastaví stejnou hodnotu všem podporovaným modelům.' : undefined

  if (param.type === 'bool') {
    return (
      <input
        type="checkbox"
        checked={mixed ? false : Boolean(value ?? param.default)}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
        title={title}
        className="w-4 h-4 accent-blue-500 disabled:opacity-60"
      />
    )
  }

  if (param.type === 'select') {
    return (
      <select
        value={mixed ? '' : String(value ?? param.default)}
        onChange={(e) => {
          if (!e.target.value) return
          onChange(e.target.value)
        }}
        disabled={disabled || param.options.length === 0}
        title={title}
        className={cls}
      >
        {mixed && <option value="">různé</option>}
        {param.options.map((opt) => (
          <option key={opt} value={opt}>{opt}</option>
        ))}
      </select>
    )
  }

  if (param.type === 'int' || param.type === 'float') {
    return (
      <input
        type="number"
        step={param.type === 'int' ? 1 : 0.01}
        min={param.min ?? undefined}
        max={param.max ?? undefined}
        value={mixed ? '' : String(value ?? param.default)}
        placeholder={mixed ? 'různé' : undefined}
        onChange={(e) => {
          if (e.target.value === '') return
          const parsed = param.type === 'int' ? parseInt(e.target.value, 10) : parseFloat(e.target.value)
          if (!Number.isFinite(parsed)) return
          onChange(parsed)
        }}
        disabled={disabled}
        title={title}
        className={cls}
      />
    )
  }

  return (
    <input
      type="text"
      value={mixed ? '' : String(value ?? param.default ?? '')}
      placeholder={mixed ? 'různé' : undefined}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      title={title}
      className={cls}
    />
  )
}

function compareHistoryValue(
  key: HistorySortKey,
  a: SavedWebMicResult,
  b: SavedWebMicResult,
): number {
  if (key === 'saved_at') return new Date(a.saved_at).getTime() - new Date(b.saved_at).getTime()
  if (key === 'model_id') return a.model_id.localeCompare(b.model_id, 'cs')
  if (key === 'mic_test_mode') return a.mic_test_mode.localeCompare(b.mic_test_mode, 'cs')
  if (key === 'reference_label') return a.reference_label.localeCompare(b.reference_label, 'cs')
  if (key === 'rtf') return (a.rtf ?? Number.POSITIVE_INFINITY) - (b.rtf ?? Number.POSITIVE_INFINITY)
  return (a.drop_rate ?? Number.POSITIVE_INFINITY) - (b.drop_rate ?? Number.POSITIVE_INFINITY)
}

export function MicSession({ availableModels, library }: Props) {
  const persistedUi = useMemo(() => readMicUiState(), [])
  const initialModelParamsById = useMemo(() => normalizeModelParamsById(persistedUi.modelParamsById), [persistedUi.modelParamsById])
  const initialModelId = useMemo(() => {
    if (typeof persistedUi.modelId === 'string' && availableModels.some(m => m.model_id === persistedUi.modelId)) {
      return persistedUi.modelId
    }
    return availableModels[0]?.model_id ?? ''
  }, [availableModels, persistedUi.modelId])
  const initialModel = useMemo(
    () => availableModels.find(m => m.model_id === initialModelId) ?? availableModels[0],
    [availableModels, initialModelId],
  )
  const initialAutoModelSelectedIds = useMemo(() => {
    const persistedIds = normalizeStringArray(persistedUi.autoModelSelectedIds)
    const validIds = persistedIds.filter((id) => availableModels.some((m) => m.model_id === id))
    if (validIds.length > 0) return validIds
    return initialModelId ? [initialModelId] : []
  }, [availableModels, initialModelId, persistedUi.autoModelSelectedIds])
  const commonParamSpecs = useMemo(() => buildCommonParamSpecs(availableModels), [availableModels])
  const commonDefaultParams = useMemo(() => buildParamsFromSpecs(commonParamSpecs), [commonParamSpecs])

  const [paramsByModel, setParamsByModel] = useState<Record<string, Record<string, unknown>>>(() => initialModelParamsById)
  const [modelId, setModelId] = useState(initialModelId)
  const [params, setParams] = useState<Record<string, unknown>>(
    () => buildMicParamsWithSaved(initialModel, initialModelParamsById[initialModelId]),
  )
  const [sequenceCommonParamsEnabled, setSequenceCommonParamsEnabled] = useState(
    typeof persistedUi.sequenceCommonParamsEnabled === 'boolean' ? persistedUi.sequenceCommonParamsEnabled : false,
  )
  const [sequenceCommonParams, setSequenceCommonParams] = useState<Record<string, unknown>>(() => {
    const saved = asObjectRecord(persistedUi.sequenceCommonParams)
    return { ...commonDefaultParams, ...(saved ?? {}) }
  })
  const [sequenceParamProfileLabel, setSequenceParamProfileLabel] = useState(
    typeof persistedUi.sequenceParamProfileLabel === 'string' ? persistedUi.sequenceParamProfileLabel : '',
  )
  const [sequenceParamProfileDirty, setSequenceParamProfileDirty] = useState(
    typeof persistedUi.sequenceParamProfileDirty === 'boolean' ? persistedUi.sequenceParamProfileDirty : false,
  )
  const [tuningSweepEnabled, setTuningSweepEnabled] = useState(
    typeof persistedUi.tuningSweepEnabled === 'boolean' ? persistedUi.tuningSweepEnabled : false,
  )
  const [tuningSweepMode, setTuningSweepMode] = useState<TuningSweepMode>(
    normalizeTuningSweepMode(persistedUi.tuningSweepMode),
  )
  const [tuningSweepStepSize, setTuningSweepStepSize] = useState(
    normalizeTuningSweepStepSize(persistedUi.tuningSweepStepSize),
  )
  const [tuningSweepRepeatCount, setTuningSweepRepeatCount] = useState(
    normalizeTuningSweepRepeatCount(asFiniteNumberOr(persistedUi.tuningSweepRepeatCount, 5)),
  )
  const [tuningSweepMaxLagSeconds, setTuningSweepMaxLagSeconds] = useState(
    Math.max(1, Math.min(60, asFiniteNumberOr(persistedUi.tuningSweepMaxLagSeconds, 15))),
  )
  const [tuningSweepCustomRangesEnabled, setTuningSweepCustomRangesEnabled] = useState(
    typeof persistedUi.tuningSweepCustomRangesEnabled === 'boolean' ? persistedUi.tuningSweepCustomRangesEnabled : false,
  )
  const [tuningSweepRangeConfigs, setTuningSweepRangeConfigs] = useState<TuningSweepRangeConfigMap>(
    () => normalizeTuningRangeConfigMap(persistedUi.tuningSweepRangeConfigs),
  )
  const [tuningSweepActive, setTuningSweepActive] = useState(false)
  const [tuningSweepSeriesId, setTuningSweepSeriesId] = useState<string | null>(null)
  const [tuningSweepPlan, setTuningSweepPlan] = useState<TuningSweepSlot[]>([])
  const [autoModelCycleEnabled, setAutoModelCycleEnabled] = useState(
    typeof persistedUi.autoModelCycleEnabled === 'boolean' ? persistedUi.autoModelCycleEnabled : false,
  )
  const [autoModelSelectedIds, setAutoModelSelectedIds] = useState<string[]>(initialAutoModelSelectedIds)
  const [autoModelGraceSeconds, setAutoModelGraceSeconds] = useState(
    Math.max(5, Math.min(60, asFiniteNumberOr(persistedUi.autoModelGraceSeconds, 15))),
  )
  const [autoModelSilenceStopSeconds, setAutoModelSilenceStopSeconds] = useState(
    Math.max(2, Math.min(60, asFiniteNumberOr(persistedUi.autoModelSilenceStopSeconds, 15))),
  )
  const [orchestratorMode, setOrchestratorMode] = useState<MicOrchestratorMode>(
    normalizeMicOrchestratorMode(persistedUi.orchestratorMode),
  )
  const [autoModelSequenceActive, setAutoModelSequenceActive] = useState(false)
  const [autoModelSequenceIds, setAutoModelSequenceIds] = useState<string[]>([])
  const [autoModelSequenceIndex, setAutoModelSequenceIndex] = useState(0)
  const [autoModelSavedCount, setAutoModelSavedCount] = useState(0)
  const [status, setStatus] = useState<Status>('idle')
  const [transcript, setTranscript] = useState('')
  const [metrics, setMetrics] = useState<MicMetrics | null>(null)
  const [micInputProof, setMicInputProof] = useState<MicInputProof | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [devices, setDevices] = useState<AudioDevice[]>([])
  const [libraryWerByVideoId, setLibraryWerByVideoId] = useState<Record<string, number | null>>({})
  const [deviceIndex, setDeviceIndex] = useState<number | null>(
    typeof persistedUi.deviceIndex === 'number' && Number.isFinite(persistedUi.deviceIndex)
      ? Math.trunc(persistedUi.deviceIndex)
      : null,
  )
  const [testMode, setTestMode] = useState<MicTestMode>(
    persistedUi.testMode === 'reference_video' ? 'reference_video' : 'free_speech',
  )
  const [referenceVideoId, setReferenceVideoId] = useState(
    typeof persistedUi.referenceVideoId === 'string' ? persistedUi.referenceVideoId : '',
  )
  const [referenceClipFromS, setReferenceClipFromS] = useState(asFiniteNumberOr(persistedUi.referenceClipFromS, 0))
  const [referenceClipToS, setReferenceClipToS] = useState(asFiniteNumberOr(persistedUi.referenceClipToS, 33))
  const [durationAnchor, setDurationAnchor] = useState<ClipDurationAnchor>(
    isClipDurationAnchor(persistedUi.durationAnchor) ? persistedUi.durationAnchor : 'from',
  )
  const [customReferenceText1, setCustomReferenceText1] = useState(
    typeof persistedUi.customReferenceText1 === 'string' ? persistedUi.customReferenceText1 : '',
  )
  const [customReferenceText2, setCustomReferenceText2] = useState(
    typeof persistedUi.customReferenceText2 === 'string' ? persistedUi.customReferenceText2 : '',
  )
  const [selectedReferenceTextId, setSelectedReferenceTextId] = useState<ReferenceTextId>(
    isReferenceTextId(persistedUi.selectedReferenceTextId) ? persistedUi.selectedReferenceTextId : 'prepared_1',
  )
  const [saveMsg, setSaveMsg] = useState<string | null>(null)
  const [savedResults, setSavedResults] = useState<SavedWebMicResult[]>([])
  const [historyModeFilter, setHistoryModeFilter] = useState<HistoryModeFilter>(
    persistedUi.historyModeFilter === 'free_speech' || persistedUi.historyModeFilter === 'reference_video'
      ? persistedUi.historyModeFilter
      : 'all',
  )
  const [historySortOrder, setHistorySortOrder] = useState<HistorySortKey[]>(normalizeHistorySortOrder(persistedUi.historySortOrder))
  const [showAllHistory, setShowAllHistory] = useState(Boolean(persistedUi.showAllHistory))
  const [expandedHistoryRecordIds, setExpandedHistoryRecordIds] = useState<string[]>([])
  const [historyDeletingRecordId, setHistoryDeletingRecordId] = useState<string | null>(null)
  const [historyBulkDeleting, setHistoryBulkDeleting] = useState(false)
  const [mobileLoopEnabled, setMobileLoopEnabled] = useState(
    typeof persistedUi.mobileLoopEnabled === 'boolean' ? persistedUi.mobileLoopEnabled : true,
  )
  const [mobileLoopEarlyStopSeconds, setMobileLoopEarlyStopSeconds] = useState(
    asFiniteNumberOr(persistedUi.mobileLoopEarlyStopSeconds, 0),
  )
  const [mobileLoopPauseSeconds, setMobileLoopPauseSeconds] = useState(asFiniteNumberOr(persistedUi.mobileLoopPauseSeconds, 15))
  const [mobileLoopAudioStartDelaySeconds, setMobileLoopAudioStartDelaySeconds] = useState(
    asFiniteNumberOr(persistedUi.mobileLoopAudioStartDelaySeconds, 0),
  )
  const [mobileLoopSyncFirstRound, setMobileLoopSyncFirstRound] = useState(
    typeof persistedUi.mobileLoopSyncFirstRound === 'boolean' ? persistedUi.mobileLoopSyncFirstRound : true,
  )
  const [mobileLoopRepeatCount, setMobileLoopRepeatCount] = useState(asFiniteNumberOr(persistedUi.mobileLoopRepeatCount, 10))
  const [mobileLoopAutoStop, setMobileLoopAutoStop] = useState(
    typeof persistedUi.mobileLoopAutoStop === 'boolean' ? persistedUi.mobileLoopAutoStop : true,
  )
  const [mobileLoopAutoPlayOnSequenceStart, setMobileLoopAutoPlayOnSequenceStart] = useState(
    typeof persistedUi.mobileLoopAutoPlayOnSequenceStart === 'boolean'
      ? persistedUi.mobileLoopAutoPlayOnSequenceStart
      : true,
  )
  const [mobileLoopPackage, setMobileLoopPackage] = useState<MicMobileLoopPackageResponse | null>(null)
  const [mobileLoopPackageLoading, setMobileLoopPackageLoading] = useState(false)
  const [mobileLoopPackageError, setMobileLoopPackageError] = useState<string | null>(null)
  const [mobileLoopAudioPlaying, setMobileLoopAudioPlaying] = useState(false)
  const [mobileLoopAudioStatus, setMobileLoopAudioStatus] = useState<string | null>(null)
  const [mobileLoopHistory, setMobileLoopHistory] = useState<MicMobileLoopPackageListItem[]>([])
  const [mobileLoopHistoryLoading, setMobileLoopHistoryLoading] = useState(false)
  const [mobileLoopHistoryError, setMobileLoopHistoryError] = useState<string | null>(null)
  const [mobileLoopHistoryDeletingId, setMobileLoopHistoryDeletingId] = useState<string | null>(null)
  const [mobileLoopPairingCodeInput, setMobileLoopPairingCodeInput] = useState('')
  const [showAllMobileLoopHistory, setShowAllMobileLoopHistory] = useState(false)
  const [expandedMobileLoopPackageIds, setExpandedMobileLoopPackageIds] = useState<string[]>([])
  const [recordingStartedAtPerfMs, setRecordingStartedAtPerfMs] = useState<number | null>(null)
  const [recordingElapsedS, setRecordingElapsedS] = useState(0)
  const [seqReport, setSeqReport] = useState<MicSequenceReport | null>(null)
  const [seqReportToken, setSeqReportToken] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const activeSessionIdRef = useRef<string | null>(null)
  const clientSequenceRunIdRef = useRef<string | null>(null)
  const clientSequenceAnchorAtPerfMsRef = useRef<number | null>(null)
  const saveGuardRef = useRef<Set<string>>(new Set())
  const autoModelSavedSlotsRef = useRef<Set<number>>(new Set())
  const autoStopFiredRef = useRef(false)
  const stopGraceCheckInFlightRef = useRef(false)
  const finalizedSessionIdsRef = useRef<Set<string>>(new Set())
  const stopRequestedAtPerfMsRef = useRef<number | null>(null)
  const trialStartedAtPerfMsRef = useRef<number | null>(null)
  const trialDeadlineAtPerfMsRef = useRef<number | null>(null)
  const trialHardLimitAtPerfMsRef = useRef<number | null>(null)
  const trialAdaptiveEarlyStopMsRef = useRef(0)
  const sequenceAnchorAtPerfMsRef = useRef<number | null>(null)
  const lastTranscriptUpdateAtPerfMsRef = useRef<number | null>(null)
  const currentTranscriptTextRef = useRef('')
  const silenceMinCaptureNotifiedRef = useRef(false)
  const activeSessionLoopConfigRef = useRef<ActiveSessionLoopConfig>({
    enabled: false,
    speechS: null,
    captureSpeechS: null,
    earlyStopS: null,
    pauseS: null,
    audioStartDelayS: null,
    syncFirstRound: null,
    measuredRounds: null,
    autoStop: null,
    packageId: null,
  })
  const activeSessionModelParamsRef = useRef<Record<string, unknown>>({})
  const activeSessionCommonParamsRef = useRef<Record<string, unknown>>({})
  const activeSessionParamProfileRef = useRef<string | null>(null)
  const autoModelSequenceTokenRef = useRef<string | null>(null)
  const tuningSweepPlanRef = useRef<TuningSweepSlot[]>([])
  const tuningSweepSeriesIdRef = useRef<string | null>(null)
  const autoModelAdvanceLockRef = useRef(false)
  const autoModelAdvanceTimerRef = useRef<number | null>(null)
  const autoModelRetryCountRef = useRef(0)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const processorRef = useRef<ScriptProcessorNode | null>(null)
  const workletNodeRef = useRef<AudioWorkletNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const mobileLoopAudioRef = useRef<HTMLAudioElement | null>(null)
  const mobileLoopPackageRef = useRef<MicMobileLoopPackageResponse | null>(null)
  const micInputProofRef = useRef<MicInputProof | null>(null)
  const micInputProofUiUpdatedAtRef = useRef(0)

  const selectedModel = availableModels.find(m => m.model_id === modelId)
  const referenceLibrary = useMemo(
    () =>
      sortLikeLibraryPage(
        (library ?? []).filter(v => !!v.video_id && v.visible_in_menus !== false),
        libraryWerByVideoId,
      ),
    [library, libraryWerByVideoId],
  )
  const selectedReferenceVideo = referenceLibrary.find(v => v.video_id === referenceVideoId)
  const selectedAudioDevice = deviceIndex !== null
    ? devices.find(d => d.index === deviceIndex) ?? devices[deviceIndex] ?? null
    : null
  const selectedMicDeviceLabel = selectedAudioDevice
    ? selectedAudioDevice.name
    : 'výchozí mikrofon'
  const selectedModelRecommendedParams = useMemo(
    () => buildMicDefaultParams(selectedModel),
    [selectedModel],
  )
  const effectiveSequenceParamProfileLabel = sequenceParamProfileLabel
    ? `${sequenceParamProfileLabel}${sequenceParamProfileDirty ? ' + ruční úpravy' : ''}`
    : ''
  const persistedParamsByModel = useMemo(() => {
    if (!modelId) return { ...paramsByModel }
    return { ...paramsByModel, [modelId]: { ...params } }
  }, [paramsByModel, modelId, params])
  const clipFromS = Math.max(0, Number.isFinite(referenceClipFromS) ? referenceClipFromS : 0)
  const clipToS = Math.max(clipFromS + 1, Number.isFinite(referenceClipToS) ? referenceClipToS : clipFromS + 33)
  const clipDurationS = Math.max(1, clipToS - clipFromS)
  const loopSpeechS = clipDurationS
  const loopEarlyStopMaxS = Math.max(0, loopSpeechS - 1)
  const loopEarlyStopS = Math.max(0, Math.min(loopEarlyStopMaxS, Number.isFinite(mobileLoopEarlyStopSeconds) ? mobileLoopEarlyStopSeconds : 0))
  const loopCaptureSpeechS = Math.max(1, loopSpeechS - loopEarlyStopS)
  const loopPauseS = Math.max(0, Math.min(3600, Math.floor(mobileLoopPauseSeconds) || 15))
  const loopAudioStartDelayS = Math.max(0, Math.min(30, Number.isFinite(mobileLoopAudioStartDelaySeconds) ? mobileLoopAudioStartDelaySeconds : 0))
  const loopRepeatCount = Math.max(1, Math.min(200, Math.floor(mobileLoopRepeatCount) || 1))
  const loopSyncRounds = mobileLoopSyncFirstRound ? 1 : 0
  const loopCycleS = Math.max(1, loopSpeechS + loopPauseS)
  const loopTotalRounds = loopSyncRounds + loopRepeatCount
  const loopPlanS = loopCycleS * loopTotalRounds
  const mobileLoopPackageMatchesPlan = useCallback((pkg: MicMobileLoopPackageResponse | null) => {
    if (!pkg || !referenceVideoId) return false
    const sameNumber = (a: number, b: number, tolerance = 0.01) => Math.abs(a - b) <= tolerance
    return (
      pkg.video_id === referenceVideoId
      && sameNumber(pkg.clip_from_s, clipFromS)
      && sameNumber(pkg.clip_to_s, clipToS)
      && sameNumber(pkg.pause_s, loopPauseS)
      && pkg.measured_rounds === loopRepeatCount
      && pkg.sync_rounds === loopSyncRounds
    )
  }, [referenceVideoId, clipFromS, clipToS, loopPauseS, loopRepeatCount, loopSyncRounds])
  const mobileLoopPackageMatchesCurrentPlan = useMemo(
    () => mobileLoopPackageMatchesPlan(mobileLoopPackage),
    [mobileLoopPackageMatchesPlan, mobileLoopPackage],
  )
  const autoModelLeadStartSeconds = 2.5
  const autoModelPreparationSeconds = 10
  const autoModelHardTrialBaseSeconds = Math.min(65, loopCaptureSpeechS + 5)
  const autoModelHardTrialSeconds = autoModelHardTrialBaseSeconds + (mobileLoopEnabled ? loopAudioStartDelayS : 0)
  const autoModelLatencyGuardSeconds = 10
  const autoModelAdaptiveMaxCutSeconds = Math.max(1, loopPauseS - 2)
  const autoModelSilenceMinAudioFraction = 0.75
  const autoModelMinSilenceStopElapsedS = Math.max(
    autoModelSilenceStopSeconds,
    (mobileLoopEnabled ? loopAudioStartDelayS : 0) + loopSpeechS * autoModelSilenceMinAudioFraction,
  )
  const autoModelSlotSeconds = loopCycleS
  const effectiveTrialPlanS = autoModelSequenceActive ? loopCycleS : loopPlanS + loopAudioStartDelayS
  const uiLocked = status === 'recording' || status === 'connecting' || status === 'stopping' || autoModelSequenceActive || tuningSweepActive

  const resetMicInputProof = useCallback((deviceLabel: string) => {
    const empty: MicInputProof = {
      device_label: deviceLabel,
      sample_rate: SAMPLE_RATE,
      chunk_count: 0,
      audio_payload_bytes: 0,
      ws_payload_bytes: 0,
      rms_dbfs: null,
      peak_dbfs: null,
      clipping_pct: 0,
      vad_speech: false,
      silence_ms: 0,
      last_chunk_age_ms: null,
    }
    micInputProofRef.current = empty
    micInputProofUiUpdatedAtRef.current = 0
    setMicInputProof(empty)
  }, [])

  const recordMicAudioChunk = useCallback((int16: Int16Array, wsPayloadBytes: number, captureTsMs: number) => {
    const prev = micInputProofRef.current
    if (!prev) return
    const stats = computePcmStats(int16)
    const chunkDurationMs = (int16.length / SAMPLE_RATE) * 1000
    const next: MicInputProof = {
      ...prev,
      chunk_count: prev.chunk_count + 1,
      audio_payload_bytes: prev.audio_payload_bytes + int16.byteLength,
      ws_payload_bytes: prev.ws_payload_bytes + wsPayloadBytes,
      rms_dbfs: stats.rmsDbfs,
      peak_dbfs: stats.peakDbfs,
      clipping_pct: stats.clippingPct,
      vad_speech: stats.vadSpeech,
      silence_ms: stats.vadSpeech ? 0 : prev.silence_ms + chunkDurationMs,
      last_chunk_age_ms: Math.max(0, performance.timeOrigin + performance.now() - captureTsMs),
    }
    micInputProofRef.current = next
    const now = performance.now()
    if (now - micInputProofUiUpdatedAtRef.current >= 200) {
      micInputProofUiUpdatedAtRef.current = now
      setMicInputProof(next)
    }
  }, [])

  const autoModelSelectedOrdered = useMemo(
    () => autoModelSelectedIds.filter((id) => availableModels.some((m) => m.model_id === id)),
    [autoModelSelectedIds, availableModels],
  )
  const autoModelSelectionIndexById = useMemo(() => {
    const map = new Map<string, number>()
    autoModelSelectedOrdered.forEach((id, idx) => map.set(id, idx))
    return map
  }, [autoModelSelectedOrdered])
  const autoModelDisplayOrdered = useMemo(() => {
    const selected = autoModelSelectedOrdered
      .map((id) => availableModels.find((m) => m.model_id === id))
      .filter((m): m is ModelDescriptor => Boolean(m))
    const rest = availableModels.filter((m) => !autoModelSelectionIndexById.has(m.model_id))
    return [...selected, ...rest]
  }, [autoModelSelectedOrdered, availableModels, autoModelSelectionIndexById])
  const sequenceMatrixModels = useMemo(() => {
    const ids = autoModelCycleEnabled && autoModelSelectedOrdered.length > 0
      ? autoModelSelectedOrdered
      : (modelId ? [modelId] : [])
    return ids
      .map((id) => availableModels.find((m) => m.model_id === id))
      .filter((m): m is ModelDescriptor => Boolean(m))
  }, [autoModelCycleEnabled, autoModelSelectedOrdered, modelId, availableModels])
  const sequenceCommonParamSpecs = useMemo(() => buildCommonParamSpecs(sequenceMatrixModels), [sequenceMatrixModels])
  const sequenceCommonDefaultParams = useMemo(() => buildParamsFromSpecs(sequenceCommonParamSpecs), [sequenceCommonParamSpecs])
  const editableMatrixParamsByModel = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>()
    for (const matrixModel of sequenceMatrixModels) {
      map.set(
        matrixModel.model_id,
        buildMicParamsWithSaved(matrixModel, persistedParamsByModel[matrixModel.model_id]),
      )
    }
    return map
  }, [sequenceMatrixModels, persistedParamsByModel])
  const recommendedMatrixParamsByModel = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>()
    for (const matrixModel of sequenceMatrixModels) {
      map.set(matrixModel.model_id, buildMicDefaultParams(matrixModel))
    }
    return map
  }, [sequenceMatrixModels])
  const effectiveMatrixParamsByModel = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>()
    for (const matrixModel of sequenceMatrixModels) {
      map.set(
        matrixModel.model_id,
        buildMicParamsWithCommon(
          matrixModel,
          persistedParamsByModel[matrixModel.model_id],
          sequenceCommonParamsEnabled,
          sequenceCommonParams,
        ),
      )
    }
    return map
  }, [sequenceMatrixModels, persistedParamsByModel, sequenceCommonParamsEnabled, sequenceCommonParams])
  const tuningSweepRangeParamSpecs = useMemo(
    () => sequenceCommonParamSpecs.filter(tuningParamSupportsRange),
    [sequenceCommonParamSpecs],
  )
  const tuningSweepRangeRows = useMemo(() => (
    tuningSweepRangeParamSpecs.map((param) => {
      const config = normalizeTuningRangeConfig(param, tuningSweepRangeConfigs[param.name])
      const supportedModels = sequenceMatrixModels.filter((m) => modelSupportsParam(m, param.name))
      return {
        param,
        config,
        values: tuningRangeValues(param, config),
        supportedModels,
        tooltip: buildTuningRangeParamTooltip(param, supportedModels, recommendedMatrixParamsByModel),
      }
    })
  ), [tuningSweepRangeParamSpecs, tuningSweepRangeConfigs, sequenceMatrixModels, recommendedMatrixParamsByModel])
  const tuningSweepEnabledRangeCount = tuningSweepRangeRows.filter((row) => row.config.enabled && row.supportedModels.length > 0).length
  const tuningSweepCustomRangeConfigForPlan = useMemo(() => {
    if (!tuningSweepCustomRangesEnabled) return null
    const configs: TuningSweepRangeConfigMap = {}
    for (const row of tuningSweepRangeRows) {
      configs[row.param.name] = row.config
    }
    return configs
  }, [tuningSweepCustomRangesEnabled, tuningSweepRangeRows])
  const tuningSweepPreviewPlan = useMemo(
    () => buildTuningSweepPlan(
      sequenceMatrixModels,
      effectiveMatrixParamsByModel,
      tuningSweepRepeatCount,
      tuningSweepMode,
      tuningSweepStepSize,
      tuningSweepCustomRangeConfigForPlan,
    ),
    [
      sequenceMatrixModels,
      effectiveMatrixParamsByModel,
      tuningSweepRepeatCount,
      tuningSweepMode,
      tuningSweepStepSize,
      tuningSweepCustomRangeConfigForPlan,
    ],
  )
  const tuningSweepVariantPreview = useMemo(() => {
    const seen = new Set<string>()
    const rows: TuningSweepSlot[] = []
    for (const slot of tuningSweepPreviewPlan) {
      const key = `${slot.modelId}:${slot.variantId}`
      if (seen.has(key)) continue
      seen.add(key)
      rows.push(slot)
      if (rows.length >= 12) break
    }
    return rows
  }, [tuningSweepPreviewPlan])
  const tuningSweepEstimatedSeconds = tuningSweepPreviewPlan.length * autoModelSlotSeconds
  const plannedAudioPackageTrialCount = useMemo(() => {
    if (!autoModelCycleEnabled) return 1
    if (tuningSweepEnabled) return Math.max(1, tuningSweepPreviewPlan.length)
    return Math.max(1, autoModelSelectedOrdered.length)
  }, [autoModelCycleEnabled, tuningSweepEnabled, tuningSweepPreviewPlan.length, autoModelSelectedOrdered.length])
  const tuningSweepReportRows = useMemo(
    () => buildTuningSweepSummaryRows(seqReport?.trials ?? []),
    [seqReport],
  )
  const seqReportProfileLabel = useMemo(() => {
    const profiles = Array.from(new Set(
      (seqReport?.trials ?? [])
        .map((trial) => (typeof trial.sequence_param_profile === 'string' ? trial.sequence_param_profile.trim() : ''))
        .filter(Boolean),
    ))
    if (profiles.length === 0) return ''
    if (profiles.length === 1) return profiles[0]
    const visible = profiles.slice(0, 3).join(', ')
    return `${profiles.length} profilů: ${visible}${profiles.length > 3 ? '...' : ''}`
  }, [seqReport])
  const seqReportPauseValidation = seqReport?.pause_validation ?? seqReport?.summary?.pause_validation ?? null
  const seqReportConclusion = seqReport?.conclusion ?? seqReport?.summary?.conclusion ?? null

  useEffect(() => {
    setSequenceCommonParams((prev) => {
      let changed = false
      const next = { ...prev }
      for (const [key, value] of Object.entries(commonDefaultParams)) {
        if (Object.prototype.hasOwnProperty.call(next, key)) continue
        next[key] = value
        changed = true
      }
      return changed ? next : prev
    })
  }, [commonDefaultParams])

  const applyClipDuration = useCallback((nextDurationSeconds: number) => {
    const duration = Math.max(1, Number.isFinite(nextDurationSeconds) ? nextDurationSeconds : 1)
    if (durationAnchor === 'to') {
      const newFrom = Math.max(0, clipToS - duration)
      setReferenceClipFromS(newFrom)
      return
    }
    const newTo = Math.max(clipFromS + 1, clipFromS + duration)
    setReferenceClipToS(newTo)
  }, [durationAnchor, clipFromS, clipToS])

  const updateTuningRangeConfig = useCallback((paramName: string, patch: Partial<TuningSweepRangeConfig>) => {
    const param = tuningSweepRangeParamSpecs.find((item) => item.name === paramName)
    if (!param) return
    setTuningSweepRangeConfigs((prev) => {
      const current = normalizeTuningRangeConfig(param, prev[paramName])
      return {
        ...prev,
        [paramName]: normalizeTuningRangeConfig(param, { ...current, ...patch }),
      }
    })
  }, [tuningSweepRangeParamSpecs])

  const applyRecommendedTuningRanges = useCallback(() => {
    const next: TuningSweepRangeConfigMap = {}
    for (const param of tuningSweepRangeParamSpecs) {
      next[param.name] = defaultTuningRangeConfig(param)
    }
    setTuningSweepRangeConfigs(next)
    setTuningSweepCustomRangesEnabled(true)
  }, [tuningSweepRangeParamSpecs])

  const disableAllTuningRanges = useCallback(() => {
    setTuningSweepRangeConfigs((prev) => {
      const next: TuningSweepRangeConfigMap = {}
      for (const param of tuningSweepRangeParamSpecs) {
        next[param.name] = normalizeTuningRangeConfig(param, { ...(prev[param.name] ?? {}), enabled: false })
      }
      return next
    })
  }, [tuningSweepRangeParamSpecs])

  const loopRuntime = useMemo(() => {
    const elapsed = Math.max(0, recordingElapsedS)
    const rawRoundIdx = Math.floor(elapsed / loopCycleS)
    const roundNumber = Math.min(loopTotalRounds, rawRoundIdx + 1)
    const roundElapsedS = elapsed - rawRoundIdx * loopCycleS
    const inSpeechPhase = roundElapsedS < loopSpeechS
    const phaseRemainingS = inSpeechPhase
      ? Math.max(0, loopSpeechS - roundElapsedS)
      : Math.max(0, loopCycleS - roundElapsedS)
    const roundType: 'sync' | 'measure' = roundNumber <= loopSyncRounds ? 'sync' : 'measure'
    const measuredRoundIndex = Math.max(0, roundNumber - loopSyncRounds)
    return {
      elapsed,
      roundNumber,
      roundType,
      measuredRoundIndex,
      inSpeechPhase,
      phaseRemainingS,
      planRemainingS: Math.max(0, loopPlanS - elapsed),
      completedPct: Math.max(0, Math.min(100, (elapsed / loopPlanS) * 100)),
    }
  }, [recordingElapsedS, loopCycleS, loopSpeechS, loopSyncRounds, loopTotalRounds, loopPlanS])

  const referenceTexts = useMemo(() => ([
    { id: 'prepared_1' as const, label: 'Připravený text 1', text: MIC_REFERENCE_READING_TEXT, editable: false },
    { id: 'prepared_2' as const, label: 'Připravený text 2', text: MIC_REFERENCE_READING_TEXT_2, editable: false },
    { id: 'custom_1' as const, label: 'Vlastní text 1', text: customReferenceText1, editable: true },
    { id: 'custom_2' as const, label: 'Vlastní text 2', text: customReferenceText2, editable: true },
  ]), [customReferenceText1, customReferenceText2])

  const selectedReferenceText = referenceTexts.find(t => t.id === selectedReferenceTextId)
  const logSequenceEvent = useCallback((event: string, payload: Record<string, unknown> = {}) => {
    void api.mic.logSequenceEvent({
      event,
      payload: {
        client_ts_ms: performance.timeOrigin + performance.now(),
        ...payload,
      },
    }).catch(() => {})
  }, [])
  const buildSequencePlanPayload = useCallback((
    token: string | null,
    queueModelIds: string[],
    selectedModelIds: string[],
    sequenceIndex?: number,
    modelForTrial?: string,
  ): Record<string, unknown> => {
    const anchorPerf = clientSequenceAnchorAtPerfMsRef.current
    const clientGlobalTimelineMs = anchorPerf == null ? 0 : Math.max(0, performance.now() - anchorPerf)
    const clientRunId = clientSequenceRunIdRef.current ?? (token ? `run_client_${token}` : null)
    const tuningSlot = typeof sequenceIndex === 'number' ? tuningSweepPlanRef.current[sequenceIndex] : null
    return {
      run_id: clientRunId,
      sequence_id: token,
      global_timeline_ms: Math.round(clientGlobalTimelineMs * 10) / 10,
      sequence_token: token,
      sequence_index: typeof sequenceIndex === 'number' ? sequenceIndex + 1 : null,
      sequence_total: queueModelIds.length || null,
      model_id: modelForTrial ?? null,
      selected_model_ids: selectedModelIds,
      queue_model_ids: queueModelIds,
      mic_test_mode: testMode,
      orchestrator_mode: orchestratorMode,
      reference_video_id: referenceVideoId || null,
      reference_clip_from_s: clipFromS,
      reference_clip_to_s: clipToS,
      reference_clip_duration_s: clipDurationS,
      reference_text_id: testMode === 'free_speech' ? selectedReferenceTextId : null,
      mobile_loop_enabled: mobileLoopEnabled,
      mobile_loop_speech_s: loopSpeechS,
      mobile_loop_capture_speech_s: loopCaptureSpeechS,
      mobile_loop_early_stop_s: loopEarlyStopS,
      mobile_loop_pause_s: loopPauseS,
      mobile_loop_audio_start_delay_s: loopAudioStartDelayS,
      mobile_loop_cycle_s: loopCycleS,
      mobile_loop_plan_s: loopPlanS,
      mobile_loop_sync_first_round: mobileLoopSyncFirstRound,
      mobile_loop_measured_rounds: loopRepeatCount,
      mobile_loop_total_rounds: loopTotalRounds,
      mobile_loop_auto_stop: mobileLoopAutoStop,
      mobile_loop_auto_play_on_sequence_start: mobileLoopAutoPlayOnSequenceStart,
      mobile_loop_package_id: mobileLoopEnabled ? (mobileLoopPackageRef.current?.package_id ?? null) : null,
      mobile_loop_pairing_code: mobileLoopEnabled ? (mobileLoopPackageRef.current?.pairing_code ?? null) : null,
      mobile_loop_audio_start_source: mobileLoopEnabled
        ? (mobileLoopAutoPlayOnSequenceStart ? 'browser_audio_plan' : 'external_mobile_loop')
        : 'none',
      mobile_loop_audio_start_known: mobileLoopEnabled
        ? (mobileLoopAutoPlayOnSequenceStart || loopAudioStartDelayS > 0)
        : false,
      mobile_loop_audio_start_expected: mobileLoopEnabled
        ? (
          mobileLoopAutoPlayOnSequenceStart
            ? 'browser_audio_plan_on_start_sequence'
            : (loopAudioStartDelayS > 0 ? 'manual_offset_after_start_sequence' : 'manual_start_together_with_start_sequence')
        )
        : 'not_applicable',
      auto_model_sequence_lead_start_s: autoModelLeadStartSeconds,
      auto_model_sequence_preparation_s: autoModelPreparationSeconds,
      auto_model_sequence_hard_trial_base_s: autoModelHardTrialBaseSeconds,
      auto_model_sequence_hard_trial_s: autoModelHardTrialSeconds,
      auto_model_sequence_effective_hard_trial_s: autoModelHardTrialSeconds,
      auto_model_sequence_silence_stop_s: autoModelSilenceStopSeconds,
      auto_model_sequence_silence_min_elapsed_s: autoModelMinSilenceStopElapsedS,
      auto_model_sequence_silence_min_audio_fraction: autoModelSilenceMinAudioFraction,
      auto_model_sequence_grace_s: autoModelGraceSeconds,
      auto_model_sequence_slot_s: autoModelSlotSeconds,
      auto_model_sequence_latency_guard_s: autoModelLatencyGuardSeconds,
      auto_model_sequence_adaptive_max_cut_s: autoModelAdaptiveMaxCutSeconds,
      sequence_common_params_enabled: sequenceCommonParamsEnabled,
      sequence_param_profile: effectiveSequenceParamProfileLabel || null,
      tuning_custom_ranges_enabled: tuningSweepCustomRangesEnabled,
      tuning_custom_range_params: tuningSweepCustomRangesEnabled
        ? tuningSweepRangeRows
          .filter((row) => row.config.enabled && row.supportedModels.length > 0)
          .map((row) => ({
            param: row.param.name,
            from: row.config.from,
            to: row.config.to,
            step: row.config.step,
            values: row.values,
            supported_models: row.supportedModels.map((m) => m.model_id),
          }))
        : [],
      ...buildTuningSlotPayload(tuningSlot, tuningSweepSeriesIdRef.current, tuningSweepMaxLagSeconds),
    }
  }, [
    testMode,
    orchestratorMode,
    referenceVideoId,
    clipFromS,
    clipToS,
    clipDurationS,
    selectedReferenceTextId,
    mobileLoopEnabled,
    loopSpeechS,
    loopCaptureSpeechS,
    loopEarlyStopS,
    loopPauseS,
    loopAudioStartDelayS,
    loopCycleS,
    loopPlanS,
    mobileLoopSyncFirstRound,
    loopRepeatCount,
    loopTotalRounds,
    mobileLoopAutoStop,
    mobileLoopAutoPlayOnSequenceStart,
    autoModelLeadStartSeconds,
    autoModelPreparationSeconds,
    autoModelHardTrialBaseSeconds,
    autoModelHardTrialSeconds,
    autoModelSilenceStopSeconds,
    autoModelMinSilenceStopElapsedS,
    autoModelSilenceMinAudioFraction,
    autoModelGraceSeconds,
    autoModelSlotSeconds,
    autoModelLatencyGuardSeconds,
    autoModelAdaptiveMaxCutSeconds,
    sequenceCommonParamsEnabled,
    effectiveSequenceParamProfileLabel,
    tuningSweepCustomRangesEnabled,
    tuningSweepRangeRows,
    tuningSweepMaxLagSeconds,
  ])
  const applyMobileLoopAudioStartDelay = useCallback((
    rawDelayS: number,
    source: 'manual_input' | 'mark_now' = 'manual_input',
  ) => {
    const delayS = Math.max(0, Math.min(30, Number.isFinite(rawDelayS) ? rawDelayS : 0))
    setMobileLoopAudioStartDelaySeconds(delayS)

    const loopCfg = activeSessionLoopConfigRef.current
    if (loopCfg.enabled) {
      loopCfg.audioStartDelayS = delayS
    }

    const trialStartedPerf = trialStartedAtPerfMsRef.current
    const hardTrialS = autoModelHardTrialBaseSeconds + (mobileLoopEnabled ? delayS : 0)
    const silenceMinElapsedS = Math.max(
      autoModelSilenceStopSeconds,
      (mobileLoopEnabled ? delayS : 0) + loopSpeechS * autoModelSilenceMinAudioFraction,
    )
    let sequenceAnchorPerf: number | null = sequenceAnchorAtPerfMsRef.current
    let trialDeadlinePerf: number | null = trialDeadlineAtPerfMsRef.current
    let trialHardLimitPerf: number | null = trialHardLimitAtPerfMsRef.current

    if (trialStartedPerf != null) {
      const delayMs = delayS * 1000
      trialHardLimitPerf = trialStartedPerf + hardTrialS * 1000
      trialHardLimitAtPerfMsRef.current = trialHardLimitPerf

      if (autoModelSequenceActive || autoModelSequenceTokenRef.current) {
        const currentIndex = Math.max(0, autoModelSequenceIndex)
        sequenceAnchorPerf = trialStartedPerf + delayMs - currentIndex * loopCycleS * 1000
        sequenceAnchorAtPerfMsRef.current = sequenceAnchorPerf
        trialDeadlinePerf = Math.max(
          trialStartedPerf + 1000,
          sequenceAnchorPerf + (currentIndex + 1) * loopCycleS * 1000 - autoModelLeadStartSeconds * 1000,
        )
        trialDeadlineAtPerfMsRef.current = trialDeadlinePerf
      } else {
        trialDeadlinePerf = trialStartedPerf + (loopPlanS + delayS) * 1000
        trialDeadlineAtPerfMsRef.current = trialDeadlinePerf
      }
    }

    const sessionId = activeSessionIdRef.current
    const token = autoModelSequenceTokenRef.current
    const eventPayload: Record<string, unknown> = {
      session_id: sessionId,
      model_id: modelId,
      client_audio_start_delay_s: delayS,
      mobile_loop_audio_start_delay_s: delayS,
      mobile_loop_audio_start_known: delayS > 0,
      mobile_loop_audio_start_source: source === 'mark_now' ? 'manual_mark_during_trial' : 'manual_input',
      auto_model_sequence_hard_trial_base_s: autoModelHardTrialBaseSeconds,
      auto_model_sequence_hard_trial_s: hardTrialS,
      auto_model_sequence_effective_hard_trial_s: hardTrialS,
      auto_model_sequence_silence_min_elapsed_s: silenceMinElapsedS,
      client_sequence_anchor_perf_ms: sequenceAnchorPerf,
      client_trial_deadline_perf_ms: trialDeadlinePerf,
      client_trial_hard_limit_perf_ms: trialHardLimitPerf,
      ui_message: `Start audia nastaven +${delayS.toFixed(1)}s.`,
    }
    if (token) {
      logSequenceEvent('client_sequence_audio_start_marked', {
        ...buildSequencePlanPayload(
          token,
          autoModelSequenceIds,
          autoModelSelectedOrdered,
          autoModelSequenceIndex,
          modelId,
        ),
        ...eventPayload,
      })
    } else if (sessionId) {
      logSequenceEvent('mobile_loop_audio_start_marked', eventPayload)
    }

    if (source === 'mark_now') {
      setSaveMsg(`Start audia označen: +${delayS.toFixed(1)}s.`)
    }
  }, [
    autoModelHardTrialBaseSeconds,
    autoModelLeadStartSeconds,
    autoModelSelectedOrdered,
    autoModelSequenceActive,
    autoModelSequenceIds,
    autoModelSequenceIndex,
    autoModelSilenceMinAudioFraction,
    autoModelSilenceStopSeconds,
    buildSequencePlanPayload,
    logSequenceEvent,
    loopCycleS,
    loopPlanS,
    loopSpeechS,
    mobileLoopEnabled,
    modelId,
  ])
  const markMobileLoopAudioStartedNow = useCallback(() => {
    const trialStartedPerf = trialStartedAtPerfMsRef.current
    if (trialStartedPerf == null) {
      setSaveMsg('Start audia nejde označit před spuštěním trialu.')
      return
    }
    applyMobileLoopAudioStartDelay((performance.now() - trialStartedPerf) / 1000, 'mark_now')
  }, [applyMobileLoopAudioStartDelay])
  const markSequenceProfileManual = useCallback(() => {
    setSequenceParamProfileLabel((prev) => prev || 'Ruční nastavení')
    setSequenceParamProfileDirty(true)
  }, [])
  const updateModelParams = useCallback((nextValues: Record<string, unknown>) => {
    markSequenceProfileManual()
    setParams(nextValues)
    setParamsByModel((prev) => (
      modelId
        ? { ...prev, [modelId]: { ...nextValues } }
        : prev
    ))
  }, [modelId, markSequenceProfileManual])
  const updateSequenceCommonParams = useCallback((nextValues: Record<string, unknown>) => {
    markSequenceProfileManual()
    setSequenceCommonParams(nextValues)
  }, [markSequenceProfileManual])
  const updateMatrixModelParam = useCallback((targetModel: ModelDescriptor, paramName: string, value: unknown) => {
    markSequenceProfileManual()
    setParamsByModel((prev) => {
      const baseSaved = targetModel.model_id === modelId ? params : prev[targetModel.model_id]
      const nextValues = {
        ...buildMicParamsWithSaved(targetModel, baseSaved),
        [paramName]: value,
      }
      return { ...prev, [targetModel.model_id]: nextValues }
    })
    if (targetModel.model_id === modelId) {
      setParams((prev) => ({
        ...buildMicParamsWithSaved(targetModel, prev),
        [paramName]: value,
      }))
    }
  }, [modelId, params, markSequenceProfileManual])
  const updateMatrixParamForAll = useCallback((paramName: string, value: unknown) => {
    markSequenceProfileManual()
    setParamsByModel((prev) => {
      const next = { ...prev }
      for (const matrixModel of sequenceMatrixModels) {
        if (!matrixModel.params.some((param) => param.name === paramName)) continue
        const baseSaved = matrixModel.model_id === modelId ? params : prev[matrixModel.model_id]
        next[matrixModel.model_id] = {
          ...buildMicParamsWithSaved(matrixModel, baseSaved),
          [paramName]: value,
        }
      }
      return next
    })
    if (selectedModel?.params.some((param) => param.name === paramName)) {
      setParams((prev) => ({
        ...buildMicParamsWithSaved(selectedModel, prev),
        [paramName]: value,
      }))
    }
  }, [sequenceMatrixModels, modelId, params, selectedModel, markSequenceProfileManual])
  const applyRecommendedMatrixParamForAll = useCallback((paramName: string) => {
    markSequenceProfileManual()
    setParamsByModel((prev) => {
      const next = { ...prev }
      for (const matrixModel of sequenceMatrixModels) {
        if (!matrixModel.params.some((param) => param.name === paramName)) continue
        const recommended = buildMicDefaultParams(matrixModel)
        if (!Object.prototype.hasOwnProperty.call(recommended, paramName)) continue
        const baseSaved = matrixModel.model_id === modelId ? params : prev[matrixModel.model_id]
        next[matrixModel.model_id] = {
          ...buildMicParamsWithSaved(matrixModel, baseSaved),
          [paramName]: recommended[paramName],
        }
      }
      return next
    })
    if (selectedModel?.params.some((param) => param.name === paramName)) {
      const recommended = buildMicDefaultParams(selectedModel)
      if (Object.prototype.hasOwnProperty.call(recommended, paramName)) {
        setParams((prev) => ({
          ...buildMicParamsWithSaved(selectedModel, prev),
          [paramName]: recommended[paramName],
        }))
      }
    }
  }, [sequenceMatrixModels, modelId, params, selectedModel, markSequenceProfileManual])
  const applySequenceParamProfile = useCallback((profileId: SequenceParamProfileId) => {
    const profile = SEQUENCE_PARAM_PROFILES.find((item) => item.id === profileId)
    if (!profile) return
    setSequenceCommonParamsEnabled(false)
    setParamsByModel((prev) => {
      const next = { ...prev }
      for (const matrixModel of sequenceMatrixModels) {
        next[matrixModel.model_id] = buildParamsForSequenceProfile(profileId, matrixModel)
      }
      return next
    })
    const activeProfileParams = selectedModel
      ? buildParamsForSequenceProfile(profileId, selectedModel)
      : {}
    if (selectedModel) setParams(activeProfileParams)
    setSequenceParamProfileLabel(profile.label)
    setSequenceParamProfileDirty(false)
    setSaveMsg(`Použit profil nastavení: ${profile.label}. Společné override hodnoty jsou vypnuté.`)
  }, [sequenceMatrixModels, selectedModel])

  const loadSavedHistory = useCallback(async () => {
    try {
      const { records } = await api.mic.listManualRecords({ limit: 200 })
      const mapped: SavedWebMicResult[] = records.flatMap((r) => {
        try {
          const metrics = (r.metrics ?? {}) as Record<string, unknown>
          const rawMode = typeof metrics.mic_test_mode === 'string' ? metrics.mic_test_mode : ''
          const mode: SavedWebMicResult['mic_test_mode'] =
            rawMode === 'free_speech' || rawMode === 'reference_video' ? rawMode : 'unknown'
          const modelParamsUsed = asObjectRecord(metrics.model_params_used)
          const commonParamsUsed = asObjectRecord(metrics.sequence_common_params_used)
          const sequenceParamProfile = typeof metrics.sequence_param_profile === 'string'
            ? metrics.sequence_param_profile
            : null
          const tuningChangedParams = asObjectRecord(metrics.tuning_changed_params)
          return [{
            record_id: r.record_id,
            saved_at: r.saved_at,
            model_id: r.model_id,
            reference_label: typeof metrics.reference_label === 'string' && metrics.reference_label.trim()
              ? metrics.reference_label
              : '—',
            mic_test_mode: mode,
            transcript: r.transcript || '',
            transcript_source: typeof metrics.transcript_source === 'string' ? metrics.transcript_source : null,
            note: r.note || '',
            quality_assessment: r.quality_assessment || '',
            source: r.source || '',
            rtf: asFiniteNumber(metrics.rtf),
            drop_rate: asFiniteNumber(metrics.drop_rate),
            reason_code: typeof metrics.reason_code === 'string' ? metrics.reason_code : null,
            first_word_wall_ms: asFiniteNumber(metrics.first_word_wall_ms) ?? null,
            p50_fin_ms: asFiniteNumber(metrics.segment_finalize_ms_p50) ?? null,
            p95_fin_ms: asFiniteNumber(metrics.segment_finalize_ms_p95) ?? null,
            q_peak_s: asFiniteNumber(metrics.queue_depth_peak_s) ?? null,
            rss_peak_mb: asFiniteNumber(metrics.worker_rss_peak_mb) ?? null,
            trial_status: computeTrialStatus(
              asFiniteNumber(metrics.drop_rate),
              asFiniteNumber(metrics.rtf),
              asFiniteNumber(metrics.first_word_wall_ms) ?? null,
              asFiniteNumber(metrics.queue_depth_peak_s) ?? null,
              typeof metrics.error === 'string' ? metrics.error : null,
            ),
            mobile_loop_package_id: typeof metrics.mobile_loop_package_id === 'string'
              ? metrics.mobile_loop_package_id
              : null,
            sequence_token: typeof metrics.auto_model_sequence_token === 'string' && metrics.auto_model_sequence_token.trim()
              ? metrics.auto_model_sequence_token.trim()
              : null,
            sequence_index: asFiniteNumber(metrics.auto_model_sequence_index) ?? null,
            orchestrator_mode: normalizeMicOrchestratorMode(metrics.mic_orchestrator_mode),
            model_params_used: modelParamsUsed ? { ...modelParamsUsed } : null,
            sequence_common_params_enabled: typeof metrics.sequence_common_params_enabled === 'boolean'
              ? metrics.sequence_common_params_enabled
              : null,
            sequence_common_params_used: commonParamsUsed ? { ...commonParamsUsed } : null,
            sequence_param_profile: sequenceParamProfile,
            tuning_series_id: typeof metrics.tuning_series_id === 'string' ? metrics.tuning_series_id : null,
            tuning_variant_id: typeof metrics.tuning_variant_id === 'string' ? metrics.tuning_variant_id : null,
            tuning_variant_label: typeof metrics.tuning_variant_label === 'string' ? metrics.tuning_variant_label : null,
            tuning_step_size: asFiniteNumber(metrics.tuning_step_size) ?? null,
            tuning_repeat_index: asFiniteNumber(metrics.tuning_repeat_index) ?? null,
            tuning_repeat_total: asFiniteNumber(metrics.tuning_repeat_total) ?? null,
            tuning_changed_params: tuningChangedParams ? { ...tuningChangedParams } : null,
          }]
        } catch (e) {
          console.error('[loadSavedHistory] chyba při mapování záznamu', r.record_id, e)
          return []
        }
      })
      setSavedResults(mapped)
    } catch (e) {
      console.error('[loadSavedHistory] chyba při načítání historie', e)
    }
  }, [])

  const loadMobileLoopHistory = useCallback(async () => {
    setMobileLoopHistoryLoading(true)
    setMobileLoopHistoryError(null)
    try {
      const { packages } = await api.mic.listMobileLoopPackages({ limit: 200 })
      setMobileLoopHistory(packages)
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setMobileLoopHistoryError(message)
      setMobileLoopHistory([])
    } finally {
      setMobileLoopHistoryLoading(false)
    }
  }, [])

  const mobileLoopHistoryVisibleRows = useMemo(
    () => (showAllMobileLoopHistory ? mobileLoopHistory : mobileLoopHistory.slice(0, 7)),
    [mobileLoopHistory, showAllMobileLoopHistory],
  )
  const hiddenMobileLoopHistoryCount = Math.max(0, mobileLoopHistory.length - 7)

  useEffect(() => {
    if (availableModels.length === 0) return
    if (selectedModel) return
    const fallback = availableModels[0]
    setModelId(fallback.model_id)
    setParams(buildMicParamsWithSaved(fallback, paramsByModel[fallback.model_id]))
  }, [availableModels, selectedModel, paramsByModel])

  useEffect(() => {
    const visible = (library ?? []).filter(v => !!v.video_id && v.visible_in_menus !== false)
    const { sortOrder } = loadLibrarySortSettings()
    if (!sortOrder.includes('wer') || visible.length === 0) {
      setLibraryWerByVideoId({})
      return
    }
    let cancelled = false
    Promise.all(
      visible.map(async (item) => {
        try {
          const results = await api.library.latestResults(item.video_id)
          return [item.video_id, typeof results?.[0]?.wer === 'number' ? results[0].wer : null] as const
        } catch {
          return [item.video_id, null] as const
        }
      }),
    ).then((rows) => {
      if (cancelled) return
      setLibraryWerByVideoId(Object.fromEntries(rows))
    })
    return () => { cancelled = true }
  }, [library])

  useEffect(() => {
    setAutoModelSelectedIds((prev) => {
      const valid = prev.filter((id) => availableModels.some((m) => m.model_id === id))
      if (valid.length > 0) return valid
      return modelId ? [modelId] : []
    })
  }, [availableModels, modelId])

  useEffect(() => {
    mobileLoopPackageRef.current = mobileLoopPackage
  }, [mobileLoopPackage])

  useEffect(() => {
    if (!modelId) return
    const payload: MicUiPersistedState = {
      version: 1,
      modelId,
      modelParamsById: persistedParamsByModel,
      deviceIndex,
      testMode,
      referenceVideoId,
      referenceClipFromS,
      referenceClipToS,
      durationAnchor,
      customReferenceText1,
      customReferenceText2,
      selectedReferenceTextId,
      historyModeFilter,
      historySortOrder: normalizeHistorySortOrder(historySortOrder),
      showAllHistory,
      mobileLoopEnabled,
      mobileLoopEarlyStopSeconds,
      mobileLoopPauseSeconds,
      mobileLoopAudioStartDelaySeconds,
      mobileLoopSyncFirstRound,
      mobileLoopRepeatCount,
      mobileLoopAutoStop,
      mobileLoopAutoPlayOnSequenceStart,
      autoModelCycleEnabled,
      autoModelSelectedIds,
      autoModelGraceSeconds,
      autoModelSilenceStopSeconds,
      orchestratorMode,
      sequenceCommonParamsEnabled,
      sequenceCommonParams,
      sequenceParamProfileLabel,
      sequenceParamProfileDirty,
      tuningSweepEnabled,
      tuningSweepMode,
      tuningSweepStepSize,
      tuningSweepRepeatCount,
      tuningSweepMaxLagSeconds,
      tuningSweepCustomRangesEnabled,
      tuningSweepRangeConfigs,
    }
    writeMicUiState(payload)
  }, [
    modelId,
    persistedParamsByModel,
    deviceIndex,
    testMode,
    referenceVideoId,
    referenceClipFromS,
    referenceClipToS,
    durationAnchor,
    customReferenceText1,
    customReferenceText2,
    selectedReferenceTextId,
    historyModeFilter,
    historySortOrder,
    showAllHistory,
    mobileLoopEnabled,
    mobileLoopEarlyStopSeconds,
    mobileLoopPauseSeconds,
    mobileLoopAudioStartDelaySeconds,
    mobileLoopSyncFirstRound,
    mobileLoopRepeatCount,
    mobileLoopAutoStop,
    mobileLoopAutoPlayOnSequenceStart,
    autoModelCycleEnabled,
    autoModelSelectedIds,
    autoModelGraceSeconds,
    autoModelSilenceStopSeconds,
    orchestratorMode,
    sequenceCommonParamsEnabled,
    sequenceCommonParams,
    sequenceParamProfileLabel,
    sequenceParamProfileDirty,
    tuningSweepEnabled,
    tuningSweepMode,
    tuningSweepStepSize,
    tuningSweepRepeatCount,
    tuningSweepMaxLagSeconds,
    tuningSweepCustomRangesEnabled,
    tuningSweepRangeConfigs,
  ])

  // Načti dostupná audio zařízení
  useEffect(() => {
    const refresh = () => {
      api.mic.devices().then(setDevices).catch(() => setDevices([]))
    }
    refresh()
    try {
      navigator.mediaDevices?.addEventListener('devicechange', refresh)
    } catch {}
    return () => {
      try {
        navigator.mediaDevices?.removeEventListener('devicechange', refresh)
      } catch {}
    }
  }, [])

  useEffect(() => {
    void loadSavedHistory()
  }, [loadSavedHistory])

  useEffect(() => {
    void loadMobileLoopHistory()
  }, [loadMobileLoopHistory])

  useEffect(() => {
    if (!seqReportToken) return
    let cancelled = false
    const refresh = async () => {
      try {
        const report = await api.mic.getSequenceReport(seqReportToken)
        if (!cancelled) setSeqReport(report)
      } catch {
        // report může krátce neexistovat (první trial ještě nezapsán)
      }
    }
    void refresh()
    const intervalMs = autoModelSequenceActive || status === 'recording' || status === 'stopping' ? 1200 : 3000
    const timer = window.setInterval(() => { void refresh() }, intervalMs)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [seqReportToken, autoModelSequenceActive, status])

  useEffect(() => {
    setMobileLoopPackage((current) => {
      if (!current || mobileLoopPackageMatchesPlan(current)) return current
      mobileLoopPackageRef.current = null
      return null
    })
    setMobileLoopPackageError(null)
  }, [mobileLoopPackageMatchesPlan])

  const visibleHistory = useMemo(() => {
    const filtered = savedResults.filter((row) => {
      if (historyModeFilter === 'all') return true
      return row.mic_test_mode === historyModeFilter
    })
    const sorted = [...filtered].sort((a, b) => {
      for (const key of historySortOrder) {
        const base = compareHistoryValue(key, a, b)
        if (base === 0) continue
        const dir = HISTORY_SORT_DEFAULT_DIR[key]
        return dir === 'desc' ? -base : base
      }
      return new Date(b.saved_at).getTime() - new Date(a.saved_at).getTime()
    })
    return sorted
  }, [savedResults, historyModeFilter, historySortOrder])

  const historyVisibleRows = showAllHistory ? visibleHistory : visibleHistory.slice(0, 7)
  const hiddenHistoryCount = Math.max(0, visibleHistory.length - 7)

  function toggleHistorySortKey(key: HistorySortKey) {
    setHistorySortOrder((prev) => {
      const idx = prev.indexOf(key)
      if (idx >= 0) {
        const copy = [...prev]
        copy.splice(idx, 1)
        return copy.length > 0 ? copy : ['saved_at']
      }
      return [...prev, key]
    })
  }

  function toggleHistoryTranscript(recordId: string) {
    setExpandedHistoryRecordIds((prev) => (
      prev.includes(recordId)
        ? prev.filter((id) => id !== recordId)
        : [...prev, recordId]
    ))
  }

  async function deleteHistoryRecord(recordId: string) {
    const ok = window.confirm(`Opravdu smazat záznam ${recordId}?`)
    if (!ok) return
    setHistoryDeletingRecordId(recordId)
    try {
      await api.mic.deleteManualRecord(recordId)
      setSavedResults((prev) => prev.filter((row) => row.record_id !== recordId))
      setExpandedHistoryRecordIds((prev) => prev.filter((id) => id !== recordId))
      setSaveMsg(`Záznam ${recordId} smazán.`)
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setSaveMsg(`Mazání záznamu selhalo: ${message}`)
    } finally {
      setHistoryDeletingRecordId(null)
    }
  }

  async function clearHistoryRecords(scope: 'all' | 'filtered') {
    const modeFilter = scope === 'filtered' && historyModeFilter !== 'all' ? historyModeFilter : undefined
    const label = modeFilter ? `filtrované záznamy (${modeFilter})` : 'celou historii pokusů'
    const ok = window.confirm(`Opravdu smazat ${label}?`)
    if (!ok) return

    setHistoryBulkDeleting(true)
    try {
      const result = await api.mic.clearManualRecords(
        modeFilter ? { mic_test_mode: modeFilter } : undefined,
      )
      setExpandedHistoryRecordIds([])
      await loadSavedHistory()
      setSaveMsg(`Smazáno ${result.deleted} záznamů, zbývá ${result.remaining}.`)
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setSaveMsg(`Hromadné mazání selhalo: ${message}`)
    } finally {
      setHistoryBulkDeleting(false)
    }
  }

  const historySortColumns: Array<{ key: HistorySortKey; label: string }> = [
    { key: 'saved_at', label: 'Čas' },
    { key: 'model_id', label: 'Model' },
    { key: 'mic_test_mode', label: 'Režim' },
    { key: 'reference_label', label: 'Reference' },
    { key: 'rtf', label: 'RTF' },
    { key: 'drop_rate', label: 'Drop' },
  ]

  const createMobileLoopPackage = useCallback(async (options: { repeatCount?: number } = {}) => {
    setMobileLoopPackageError(null)
    mobileLoopPackageRef.current = null
    setMobileLoopPackage(null)
    const requestedRepeatCount = Math.max(
      1,
      Math.min(200, Math.floor(options.repeatCount ?? loopRepeatCount) || 1),
    )
    if (!referenceVideoId) {
      setMobileLoopPackageError('Nejprve vyber video z knihovny.')
      return null
    }
    if (clipToS <= clipFromS) {
      setMobileLoopPackageError("Neplatná pasáž: 'do' musí být větší než 'od'.")
      return null
    }
    setMobileLoopPackageLoading(true)
    try {
      const result = await api.mic.createMobileLoopPackage({
        video_id: referenceVideoId,
        clip_from_s: clipFromS,
        clip_to_s: clipToS,
        pause_s: loopPauseS,
        repeat_count: requestedRepeatCount,
        include_sync_round: mobileLoopSyncFirstRound,
      })
      mobileLoopPackageRef.current = result
      setMobileLoopPackage(result)
      setShowAllMobileLoopHistory(false)
      await loadMobileLoopHistory()
      return result
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      if (message.includes('/mic/mobile-loop-packages') && message.includes('405')) {
        setMobileLoopPackageError(
          'Backend vrací 405: běží starší verze bez POST /api/mic/mobile-loop-packages. Restartuj backend/web app.',
        )
      } else if (message.includes('/mic/mobile-loop-packages') && message.includes('404')) {
        setMobileLoopPackageError(
          'Backend nezná /api/mic/mobile-loop-packages (404). Spusť backend z aktuální verze projektu.',
        )
      } else {
        setMobileLoopPackageError(message)
      }
      return null
    } finally {
      setMobileLoopPackageLoading(false)
    }
  }, [
    referenceVideoId,
    clipFromS,
    clipToS,
    loopPauseS,
    loopRepeatCount,
    mobileLoopSyncFirstRound,
    loadMobileLoopHistory,
  ])

  const createMobileLoopPackageForCurrentTest = useCallback(async () => {
    if (plannedAudioPackageTrialCount > 200) {
      setMobileLoopPackageError(
        `Plán testu má ${plannedAudioPackageTrialCount} trialů, ale mobilní balíček podporuje nejvýše 200 měřených kol.`,
      )
      return null
    }
    setMobileLoopEnabled(true)
    setMobileLoopRepeatCount(plannedAudioPackageTrialCount)
    return createMobileLoopPackage({ repeatCount: plannedAudioPackageTrialCount })
  }, [createMobileLoopPackage, plannedAudioPackageTrialCount])

  function toggleMobileLoopPackageDetails(packageId: string) {
    setExpandedMobileLoopPackageIds((prev) => (
      prev.includes(packageId)
        ? prev.filter((id) => id !== packageId)
        : [...prev, packageId]
    ))
  }

  function applyMobileLoopPackageToForm(pkg: MicMobileLoopPackageListItem) {
    setTestMode('reference_video')
    setMobileLoopEnabled(true)
    setReferenceVideoId(pkg.video_id || '')
    setReferenceClipFromS(Math.max(0, pkg.clip_from_s))
    setReferenceClipToS(Math.max(Math.max(0, pkg.clip_from_s) + 1, pkg.clip_to_s))
    setMobileLoopPauseSeconds(Math.max(0, pkg.pause_s))
    setMobileLoopRepeatCount(Math.max(1, pkg.measured_rounds))
    setMobileLoopSyncFirstRound(pkg.sync_rounds > 0)
    setMobileLoopPairingCodeInput(pkg.pairing_code || '')
    setMobileLoopPackage({
      package_id: pkg.package_id,
      pairing_code: pkg.pairing_code,
      created_at: pkg.created_at,
      video_id: pkg.video_id,
      video_title: pkg.video_title,
      clip_from_s: pkg.clip_from_s,
      clip_to_s: pkg.clip_to_s,
      clip_duration_s: pkg.clip_duration_s,
      pause_s: pkg.pause_s,
      measured_rounds: pkg.measured_rounds,
      sync_rounds: pkg.sync_rounds,
      total_rounds: pkg.total_rounds,
      total_duration_s: pkg.total_duration_s,
      wav_url: pkg.wav_url,
      download_url: pkg.download_url,
      instructions: pkg.instructions,
      reference_excerpt: pkg.reference_excerpt,
    })
    mobileLoopPackageRef.current = {
      package_id: pkg.package_id,
      pairing_code: pkg.pairing_code,
      created_at: pkg.created_at,
      video_id: pkg.video_id,
      video_title: pkg.video_title,
      clip_from_s: pkg.clip_from_s,
      clip_to_s: pkg.clip_to_s,
      clip_duration_s: pkg.clip_duration_s,
      pause_s: pkg.pause_s,
      measured_rounds: pkg.measured_rounds,
      sync_rounds: pkg.sync_rounds,
      total_rounds: pkg.total_rounds,
      total_duration_s: pkg.total_duration_s,
      wav_url: pkg.wav_url,
      download_url: pkg.download_url,
      instructions: pkg.instructions,
      reference_excerpt: pkg.reference_excerpt,
    }
    setMobileLoopPackageError(null)
  }

  function normalizeMobileLoopPairingCode(value: string) {
    const trimmed = value.trim()
    const packageIdMatch = trimmed.match(/loop_([A-Za-z0-9]{6})-/)
    if (packageIdMatch) return packageIdMatch[1]
    const directMatch = trimmed.match(/^([A-Za-z0-9]{6})(?:-\d+x)?$/)
    if (directMatch) return directMatch[1]
    return trimmed.replace(/[^A-Za-z0-9]/g, '').slice(0, 6)
  }

  async function applyMobileLoopPackageByPairingCode() {
    const code = normalizeMobileLoopPairingCode(mobileLoopPairingCodeInput)
    if (code.length !== 6) {
      setMobileLoopHistoryError('Zadej 6místný párovací kód balíčku, např. aB3dE9.')
      return
    }

    setMobileLoopHistoryError(null)
    let packages = mobileLoopHistory
    let match = packages.find((pkg) => pkg.pairing_code === code)

    if (!match) {
      setMobileLoopHistoryLoading(true)
      try {
        const response = await api.mic.listMobileLoopPackages({ limit: 500 })
        packages = response.packages
        setMobileLoopHistory(packages)
        match = packages.find((pkg) => pkg.pairing_code === code)
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e)
        setMobileLoopHistoryError(`Načtení balíčků selhalo: ${message}`)
        return
      } finally {
        setMobileLoopHistoryLoading(false)
      }
    }

    if (!match) {
      setMobileLoopHistoryError(`Balíček s kódem ${code} není v historii.`)
      return
    }

    setMobileLoopPairingCodeInput(code)
    applyMobileLoopPackageToForm(match)
  }

  const stopMobileLoopAudioPlayback = useCallback((message = 'Audio zastaveno.') => {
    const audio = mobileLoopAudioRef.current
    if (audio) {
      try { audio.pause() } catch {}
      try { audio.currentTime = 0 } catch {}
      audio.onended = null
      audio.onerror = null
    }
    mobileLoopAudioRef.current = null
    setMobileLoopAudioPlaying(false)
    setMobileLoopAudioStatus(message)
  }, [])

  const ensureCurrentMobileLoopPackage = useCallback(async () => {
    const currentPackage = mobileLoopPackageRef.current
    if (currentPackage && mobileLoopPackageMatchesPlan(currentPackage)) return currentPackage
    return createMobileLoopPackage()
  }, [createMobileLoopPackage, mobileLoopPackageMatchesPlan])

  const playMobileLoopAudioPlan = useCallback(async (source: 'manual' | 'sequence_start' = 'manual') => {
    if (!mobileLoopEnabled) {
      setMobileLoopAudioStatus('Audio loop není zapnutý.')
      return null
    }
    if (!referenceVideoId) {
      setMobileLoopAudioStatus('Nejprve vyber referenční video.')
      return null
    }
    setMobileLoopAudioStatus('Připravuji audio plán...')
    const pkg = await ensureCurrentMobileLoopPackage()
    if (!pkg) {
      setMobileLoopAudioStatus('Audio plán se nepodařilo připravit.')
      return null
    }

    const previousAudio = mobileLoopAudioRef.current
    if (previousAudio) {
      try { previousAudio.pause() } catch {}
      previousAudio.onended = null
      previousAudio.onerror = null
    }

    const audio = new Audio(pkg.wav_url)
    audio.preload = 'auto'
    mobileLoopAudioRef.current = audio
    audio.onended = () => {
      if (mobileLoopAudioRef.current === audio) {
        mobileLoopAudioRef.current = null
        setMobileLoopAudioPlaying(false)
        setMobileLoopAudioStatus('Audio plán dohrál.')
      }
    }
    audio.onerror = () => {
      if (mobileLoopAudioRef.current === audio) {
        mobileLoopAudioRef.current = null
        setMobileLoopAudioPlaying(false)
      }
      setMobileLoopAudioStatus('Přehrání audio plánu selhalo.')
    }

    try {
      await audio.play()
      setMobileLoopAudioPlaying(true)
      setMobileLoopAudioStatus(source === 'sequence_start'
        ? 'Audio plán spuštěn při Start sekvenci.'
        : 'Audio plán se přehrává.')
      logSequenceEvent('client_mobile_loop_audio_play_started', {
        source,
        package_id: pkg.package_id,
        pairing_code: pkg.pairing_code ?? null,
        wav_url: pkg.wav_url,
        reference_video_id: referenceVideoId,
        clip_from_s: clipFromS,
        clip_to_s: clipToS,
        pause_s: loopPauseS,
        measured_rounds: loopRepeatCount,
        sync_rounds: loopSyncRounds,
      })
      return pkg
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      if (mobileLoopAudioRef.current === audio) {
        mobileLoopAudioRef.current = null
      }
      setMobileLoopAudioPlaying(false)
      setMobileLoopAudioStatus(`Přehrání zablokováno nebo selhalo: ${message}`)
      return null
    }
  }, [
    mobileLoopEnabled,
    referenceVideoId,
    ensureCurrentMobileLoopPackage,
    logSequenceEvent,
    clipFromS,
    clipToS,
    loopPauseS,
    loopRepeatCount,
    loopSyncRounds,
  ])

  async function deleteMobileLoopPackage(packageId: string) {
    const ok = window.confirm(`Opravdu smazat balíček ${packageId}?`)
    if (!ok) return

    setMobileLoopHistoryDeletingId(packageId)
    setMobileLoopHistoryError(null)
    try {
      await api.mic.deleteMobileLoopPackage(packageId)
      setMobileLoopHistory((prev) => prev.filter((row) => row.package_id !== packageId))
      setExpandedMobileLoopPackageIds((prev) => prev.filter((id) => id !== packageId))
      if (mobileLoopPackage?.package_id === packageId) {
        mobileLoopPackageRef.current = null
        setMobileLoopPackage(null)
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setMobileLoopHistoryError(`Smazání balíčku selhalo: ${message}`)
    } finally {
      setMobileLoopHistoryDeletingId(null)
    }
  }

  function _buildFramedPcmPayload(int16: Int16Array, captureTsMs: number): ArrayBuffer {
    const payload = new ArrayBuffer(12 + int16.byteLength)
    const view = new DataView(payload)
    view.setUint32(0, WS_AUDIO_MAGIC, true)
    view.setFloat64(4, captureTsMs, true)
    new Int16Array(payload, 12).set(int16)
    return payload
  }

  async function persistWebMicResult({
    sessionId,
    finalMsg,
    modelIdForSession,
    referenceLabel,
    referenceText,
    autoSequenceMeta,
    source,
  }: {
    sessionId: string
    finalMsg: Record<string, unknown>
    modelIdForSession: string
    referenceLabel: string
    referenceText: string
    autoSequenceMeta?: AutoModelSequenceMeta | null
    source?: string
  }) {
    if (!sessionId || saveGuardRef.current.has(sessionId)) return
    saveGuardRef.current.add(sessionId)

    const loopCfg = activeSessionLoopConfigRef.current
    const finalTranscriptText = typeof finalMsg.text === 'string' ? finalMsg.text : ''
    const proof = micInputProofRef.current
    const metrics: Record<string, unknown> = {
      mic_session_id: sessionId,
      mic_test_mode: testMode,
      transcript_source: finalTranscriptText.trim() ? 'mic_ws_final' : 'none',
      reference_label: referenceLabel,
      reference_text: referenceText,
      mic_input_device_label: proof?.device_label ?? selectedMicDeviceLabel,
      mic_input_sample_rate: proof?.sample_rate ?? SAMPLE_RATE,
      mic_input_chunk_count: proof?.chunk_count ?? 0,
      mic_input_audio_payload_bytes: proof?.audio_payload_bytes ?? 0,
      mic_input_ws_payload_bytes: proof?.ws_payload_bytes ?? 0,
      mic_input_rms_dbfs: proof?.rms_dbfs ?? null,
      mic_input_peak_dbfs: proof?.peak_dbfs ?? null,
      mic_input_clipping_pct: proof?.clipping_pct ?? null,
      mic_input_vad_speech: proof?.vad_speech ?? false,
      mic_input_silence_ms: proof?.silence_ms ?? null,
      model_params_used: activeSessionModelParamsRef.current,
      sequence_common_params_enabled: sequenceCommonParamsEnabled,
      sequence_common_params_used: activeSessionCommonParamsRef.current,
      sequence_param_profile: activeSessionParamProfileRef.current,
      mobile_loop_enabled: loopCfg.enabled,
      mobile_loop_speech_s: loopCfg.speechS,
      mobile_loop_capture_speech_s: loopCfg.captureSpeechS,
      mobile_loop_early_stop_s: loopCfg.earlyStopS,
      mobile_loop_pause_s: loopCfg.pauseS,
      mobile_loop_audio_start_delay_s: loopCfg.audioStartDelayS,
      mobile_loop_sync_first_round: loopCfg.syncFirstRound,
      mobile_loop_measured_rounds: loopCfg.measuredRounds,
      mobile_loop_autostop: loopCfg.autoStop,
      mobile_loop_auto_play_on_sequence_start: mobileLoopAutoPlayOnSequenceStart,
      mobile_loop_package_id: loopCfg.packageId,
      auto_model_sequence_token: autoSequenceMeta?.sequence_token ?? null,
      auto_model_sequence_index: autoSequenceMeta ? autoSequenceMeta.sequence_index + 1 : null,
      auto_model_sequence_total: autoSequenceMeta?.sequence_total ?? null,
      ...buildTuningSlotPayload(
        autoSequenceMeta?.tuning_slot,
        autoSequenceMeta?.tuning_series_id ?? tuningSweepSeriesIdRef.current,
        tuningSweepMaxLagSeconds,
      ),
      mic_orchestrator_mode: orchestratorMode,
      run_id: finalMsg.run_id,
      sequence_id: finalMsg.sequence_id,
      global_timeline_ms: finalMsg.global_timeline_ms,
      first_word_latency_ms: finalMsg.first_word_latency_ms,
      first_word_wall_ms: finalMsg.first_word_wall_ms,
      first_word_audio_ms: finalMsg.first_word_audio_ms,
      rtf: finalMsg.rtf,
      elapsed_s: finalMsg.elapsed_s,
      processing_ms_p95: finalMsg.processing_ms_p95,
      segment_finalize_ms_p50: finalMsg.segment_finalize_ms_p50,
      segment_finalize_ms_p95: finalMsg.segment_finalize_ms_p95,
      capture_jitter_ms_p95: finalMsg.capture_jitter_ms_p95,
      capture_lag_ms_p95: finalMsg.capture_lag_ms_p95,
      queue_depth_peak_s: finalMsg.queue_depth_peak_s,
      backpressure_events: finalMsg.backpressure_events,
      drop_rate: finalMsg.drop_rate,
      worker_rss_peak_mb: finalMsg.worker_rss_peak_mb,
      reason_code: finalMsg.reason_code,
      error: finalMsg.error,
      sequence_timing: (
        finalMsg.sequence_timing != null
        && typeof finalMsg.sequence_timing === 'object'
        && !Array.isArray(finalMsg.sequence_timing)
      ) ? finalMsg.sequence_timing : undefined,
    }
    const note = loopCfg.enabled
      ? `web_mic | mode=${testMode} | ref=${referenceLabel} | loop=${loopCfg.speechS ?? '-'}-${loopCfg.earlyStopS ?? 0}+${loopCfg.pauseS ?? '-'}s | sync1=${loopCfg.syncFirstRound ? 'on' : 'off'} | rounds=${loopCfg.measuredRounds ?? '-'} | pkg=${loopCfg.packageId ?? '-'}`
      : `web_mic | mode=${testMode} | ref=${referenceLabel}`
    const noteWithOrchestrator = `${note} | orchestrator=${orchestratorMode}`
    const noteWithSequence = autoSequenceMeta
      ? `${noteWithOrchestrator} | seq=${autoSequenceMeta.sequence_index + 1}/${autoSequenceMeta.sequence_total}`
      : noteWithOrchestrator
    const noteWithTuning = autoSequenceMeta?.tuning_slot
      ? `${noteWithSequence} | tune=${autoSequenceMeta.tuning_slot.variantLabel} ${autoSequenceMeta.tuning_slot.repeatIndex}/${autoSequenceMeta.tuning_slot.repeatTotal}`
      : noteWithSequence
    const rtfVal = typeof finalMsg.rtf === 'number' ? finalMsg.rtf : null
    const dropVal = typeof finalMsg.drop_rate === 'number' ? finalMsg.drop_rate : null
    const hasError = typeof finalMsg.error === 'string' && finalMsg.error.trim().length > 0
    const hasReason = typeof finalMsg.reason_code === 'string' && finalMsg.reason_code.trim().length > 0
    const quality = hasError || hasReason || (rtfVal != null && rtfVal > 1) || (dropVal != null && dropVal > 0.1)
      ? 'degraded'
      : 'ok'

    try {
      const saved = await api.mic.saveManualRecord({
        model_id: modelIdForSession,
        metrics,
        note: noteWithTuning,
        quality_assessment: quality,
        transcript: finalTranscriptText,
        source: source ?? 'web_mic_auto',
      })
      if (autoSequenceMeta) {
        if (autoSequenceMeta.sequence_token === autoModelSequenceTokenRef.current) {
          autoModelSavedSlotsRef.current.add(autoSequenceMeta.sequence_index)
          const savedCount = autoModelSavedSlotsRef.current.size
          setAutoModelSavedCount(savedCount)
          setSaveMsg(`Uloženo ${savedCount}/${autoSequenceMeta.sequence_total} (${saved.record_id}).`)
          logSequenceEvent('client_sequence_trial_saved', {
            sequence_token: autoSequenceMeta.sequence_token,
            sequence_index: autoSequenceMeta.sequence_index + 1,
            sequence_total: autoSequenceMeta.sequence_total,
            session_id: sessionId,
            model_id: modelIdForSession,
            record_id: saved.record_id,
            saved_count: savedCount,
            quality_assessment: quality,
            reason_code: finalMsg.reason_code,
            rtf: finalMsg.rtf,
            drop_rate: finalMsg.drop_rate,
            elapsed_s: finalMsg.elapsed_s,
            ...buildTuningSlotPayload(
              autoSequenceMeta.tuning_slot,
              autoSequenceMeta.tuning_series_id ?? tuningSweepSeriesIdRef.current,
              tuningSweepMaxLagSeconds,
            ),
            ui_message: `Uloženo ${savedCount}/${autoSequenceMeta.sequence_total} (${saved.record_id}).`,
          })
          // Fetch sequence report po každém uloženém trialu
          const seqToken = autoSequenceMeta.sequence_token
          setSeqReportToken(seqToken)
          api.mic.getSequenceReport(seqToken).then(setSeqReport).catch(() => {})
        }
      } else {
        setSaveMsg(`Výsledek uložen (${saved.record_id}).`)
      }
      await loadSavedHistory()
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setSaveMsg(`Uložení výsledku selhalo: ${message}`)
    }
  }

  const startSession = useCallback(async (modelOverrideId?: string, autoSequenceMeta?: AutoModelSequenceMeta | null) => {
    const activeModelId = modelOverrideId ?? modelId
    const activeModel = availableModels.find((m) => m.model_id === activeModelId)
    const tuningSlot = autoSequenceMeta?.tuning_slot ?? null
    const activeSavedParams = tuningSlot?.params ?? (modelOverrideId ? paramsByModel[activeModelId] : params)
    const activeParams = tuningSlot
      ? buildMicParamsWithSaved(activeModel, activeSavedParams)
      : buildMicParamsWithCommon(
        activeModel,
        activeSavedParams,
        sequenceCommonParamsEnabled,
        sequenceCommonParams,
      )

    setError(null)
    setTranscript('')
    setMetrics(null)
    setMicInputProof(null)
    micInputProofRef.current = null
    setSaveMsg(null)
    setRecordingElapsedS(0)
    setRecordingStartedAtPerfMs(null)
    autoStopFiredRef.current = false
    stopGraceCheckInFlightRef.current = false
    setStatus('connecting')
    activeSessionIdRef.current = null

    try {
      if (!activeModel || !activeModelId) {
        setError('Vyber model pro MIC test.')
        setStatus('error')
        return
      }
      if (testMode === 'free_speech') {
        const chosen = referenceTexts.find(t => t.id === selectedReferenceTextId)
        if (!chosen || !chosen.text.trim()) {
          setError('Vyber a případně vyplň referenční text, který budeš číst.')
          setStatus('error')
          return
        }
      }
      if (testMode === 'reference_video' && !referenceVideoId) {
        setError('Pro referenční mic test vyber video.')
        setStatus('error')
        return
      }
      const sessionParams: Record<string, unknown> = {
        ...activeParams,
        mic_test_mode: testMode,
        mic_orchestrator_mode: orchestratorMode,
      }
      const activeModelParamsUsed = pickModelSupportedParams(activeModel, activeParams)
      const activeCommonParamsUsed = sequenceCommonParamsEnabled
        ? pickModelSupportedParams(activeModel, sequenceCommonParams)
        : {}
      const activeParamProfile = tuningSlot
        ? `${effectiveSequenceParamProfileLabel || 'Ruční nastavení'} | ladění: ${tuningSlot.variantLabel}`
        : effectiveSequenceParamProfileLabel || null
      activeSessionModelParamsRef.current = activeModelParamsUsed
      activeSessionCommonParamsRef.current = activeCommonParamsUsed
      activeSessionParamProfileRef.current = activeParamProfile
      sessionParams.model_params_used = activeModelParamsUsed
      sessionParams.sequence_common_params_enabled = sequenceCommonParamsEnabled
      sessionParams.sequence_common_params_used = activeCommonParamsUsed
      sessionParams.sequence_param_profile = activeParamProfile
      if (tuningSlot) {
        Object.assign(
          sessionParams,
          buildTuningSlotPayload(
            tuningSlot,
            autoSequenceMeta?.tuning_series_id ?? tuningSweepSeriesIdRef.current,
            tuningSweepMaxLagSeconds,
          ),
        )
      }
      const activeLoopSyncFirstRound = mobileLoopEnabled ? (autoSequenceMeta ? false : mobileLoopSyncFirstRound) : null
      const activeLoopMeasuredRounds = mobileLoopEnabled ? (autoSequenceMeta ? 1 : loopRepeatCount) : null
      const activeLoopPackageId = mobileLoopEnabled ? (mobileLoopPackageRef.current?.package_id ?? null) : null
      if (mobileLoopEnabled) {
        sessionParams.mobile_loop_enabled = true
        sessionParams.mobile_loop_speech_s = loopSpeechS
        sessionParams.mobile_loop_capture_speech_s = loopCaptureSpeechS
        sessionParams.mobile_loop_early_stop_s = loopEarlyStopS
        sessionParams.mobile_loop_pause_s = loopPauseS
        sessionParams.mobile_loop_audio_start_delay_s = loopAudioStartDelayS
        sessionParams.mobile_loop_sync_first_round = activeLoopSyncFirstRound
        sessionParams.mobile_loop_measured_rounds = activeLoopMeasuredRounds
        sessionParams.mobile_loop_auto_stop = mobileLoopAutoStop
        sessionParams.mobile_loop_auto_play_on_sequence_start = mobileLoopAutoPlayOnSequenceStart
        sessionParams.mobile_loop_package_id = activeLoopPackageId
        sessionParams.mobile_loop_audio_start_source = mobileLoopAutoPlayOnSequenceStart
          ? 'browser_audio_plan'
          : 'external_mobile_loop'
        sessionParams.mobile_loop_audio_start_known = mobileLoopAutoPlayOnSequenceStart || loopAudioStartDelayS > 0
      }
      if (autoSequenceMeta) {
        const sequenceQueue = autoSequenceMeta.queue_model_ids ?? autoModelSequenceIds
        const sequenceSelected = autoSequenceMeta.selected_model_ids ?? autoModelSelectedOrdered
        sessionParams.auto_model_sequence_token = autoSequenceMeta.sequence_token
        sessionParams.auto_model_sequence_index = autoSequenceMeta.sequence_index + 1
        sessionParams.auto_model_sequence_total = autoSequenceMeta.sequence_total
        sessionParams.auto_model_sequence_queue = sequenceQueue
        sessionParams.auto_model_sequence_selected_models = sequenceSelected
        sessionParams.auto_model_sequence_lead_start_s = autoModelLeadStartSeconds
        sessionParams.auto_model_sequence_preparation_s = autoModelPreparationSeconds
        sessionParams.auto_model_sequence_hard_trial_base_s = autoModelHardTrialBaseSeconds
        sessionParams.auto_model_sequence_hard_trial_s = autoModelHardTrialSeconds
        sessionParams.auto_model_sequence_effective_hard_trial_s = autoModelHardTrialSeconds
        sessionParams.auto_model_sequence_silence_stop_s = autoModelSilenceStopSeconds
        sessionParams.auto_model_sequence_silence_min_elapsed_s = autoModelMinSilenceStopElapsedS
        sessionParams.auto_model_sequence_silence_min_audio_fraction = autoModelSilenceMinAudioFraction
        sessionParams.auto_model_sequence_grace_s = autoModelGraceSeconds
        sessionParams.auto_model_sequence_slot_s = autoModelSlotSeconds
        sessionParams.auto_model_sequence_latency_guard_s = autoModelLatencyGuardSeconds
        sessionParams.auto_model_sequence_adaptive_max_cut_s = autoModelAdaptiveMaxCutSeconds
      }
      activeSessionLoopConfigRef.current = {
        enabled: mobileLoopEnabled,
        speechS: mobileLoopEnabled ? loopSpeechS : null,
        captureSpeechS: mobileLoopEnabled ? loopCaptureSpeechS : null,
        earlyStopS: mobileLoopEnabled ? loopEarlyStopS : null,
        pauseS: mobileLoopEnabled ? loopPauseS : null,
        audioStartDelayS: mobileLoopEnabled ? loopAudioStartDelayS : null,
        syncFirstRound: activeLoopSyncFirstRound,
        measuredRounds: activeLoopMeasuredRounds,
        autoStop: mobileLoopEnabled ? mobileLoopAutoStop : null,
        packageId: activeLoopPackageId,
      }
      if (testMode === 'free_speech' && selectedReferenceText) {
        sessionParams.reference_text_id = selectedReferenceText.id
        sessionParams.reference_text_label = selectedReferenceText.label
        sessionParams.reference_text = selectedReferenceText.text
      }
      if (testMode === 'reference_video' && referenceVideoId) {
        const referenceSampleSeconds = mobileLoopEnabled ? loopCaptureSpeechS : clipDurationS
        sessionParams.reference_video_id = referenceVideoId
        sessionParams.reference_clip_start_s = Math.max(0, Math.floor(clipFromS))
        sessionParams.reference_sample_seconds = Math.max(1, Math.floor(referenceSampleSeconds))
      }
      if (autoSequenceMeta) {
        const sequenceQueue = autoSequenceMeta.queue_model_ids ?? autoModelSequenceIds
        const sequenceSelected = autoSequenceMeta.selected_model_ids ?? autoModelSelectedOrdered
        logSequenceEvent('client_sequence_session_create_requested', {
          ...buildSequencePlanPayload(
            autoSequenceMeta.sequence_token,
            sequenceQueue,
            sequenceSelected,
            autoSequenceMeta.sequence_index,
            activeModelId,
          ),
          model_params_used: activeModelParamsUsed,
          sequence_common_params_used: activeCommonParamsUsed,
        })
      }
      // 1. Vytvoř backend session
      const createResp = await api.mic.createSession(activeModelId, sessionParams)
      const { session_id } = createResp
      activeSessionIdRef.current = session_id
      if (autoSequenceMeta) {
        const sequenceQueue = autoSequenceMeta.queue_model_ids ?? autoModelSequenceIds
        const sequenceSelected = autoSequenceMeta.selected_model_ids ?? autoModelSelectedOrdered
        logSequenceEvent('client_sequence_session_created', {
          ...buildSequencePlanPayload(
            autoSequenceMeta.sequence_token,
            sequenceQueue,
            sequenceSelected,
            autoSequenceMeta.sequence_index,
            activeModelId,
          ),
          session_id,
          preflight_ok: createResp.preflight_ok !== false,
          preflight_errors: createResp.preflight_errors ?? [],
          preflight_warnings: createResp.preflight_warnings ?? [],
        })
      }
      if (createResp.preflight_ok === false) {
        const reasons = (createResp.preflight_errors ?? []).filter(Boolean)
        const detail = reasons.length > 0 ? reasons.join(', ') : 'preflight_failed'
        setError(`MIC preflight zablokoval start: ${detail}`)
        setStatus('error')
        return
      }
      if ((createResp.preflight_warnings ?? []).length > 0) {
        setSaveMsg(`Preflight warning: ${(createResp.preflight_warnings ?? []).join(', ')}`)
      }

      // 2. Otevři WebSocket
      const ws = new WebSocket(`${WS_BASE}/api/mic/sessions/${session_id}/stream`)
      wsRef.current = ws
      let wsFailureHandled = false
      let wsCompleted = false
      const referenceLabel = testMode === 'free_speech'
        ? (selectedReferenceText?.label ?? 'Neurčeno')
        : `Video ${referenceVideoId || '-'}`
      const referenceText = testMode === 'free_speech'
        ? (selectedReferenceText?.text ?? '')
        : `video_id=${referenceVideoId}; from=${clipFromS}; to=${clipToS}; len_src=${clipDurationS}; len_capture=${loopCaptureSpeechS}`
      const estimateTrialElapsedS = () => {
        const started = trialStartedAtPerfMsRef.current
        if (started == null) return 0
        return Math.max(0, (performance.now() - started) / 1000)
      }
      const persistFailureResult = (
        failureReason: string,
        failureError: string,
        prefix: string,
        failureExtra?: Record<string, unknown>,
      ) => {
        void persistWebMicResult({
          sessionId: session_id,
          finalMsg: {
            text: '',
            elapsed_s: estimateTrialElapsedS(),
            reason_code: failureReason,
            error: failureError,
            ...(failureExtra ?? {}),
          },
          modelIdForSession: activeModelId,
          referenceLabel,
          referenceText,
          autoSequenceMeta,
          source: 'web_mic_auto_error',
        })
        if (autoSequenceMeta) {
          setSaveMsg(`Sekvence ${autoSequenceMeta.sequence_index + 1}/${autoSequenceMeta.sequence_total}: ${prefix}.`)
        }
      }
      const explainWsFailure = async (prefix: string) => {
        try {
          const session = await api.mic.getSession(session_id)
          const parts: string[] = []
          if (session.status) parts.push(`status=${session.status}`)
          if (session.reason_code) parts.push(`reason=${session.reason_code}`)
          if (session.error) parts.push(`error=${session.error}`)
          const sequenceTiming = (
            session.sequence_timing != null
            && typeof session.sequence_timing === 'object'
            && !Array.isArray(session.sequence_timing)
          ) ? session.sequence_timing : undefined
          const sessionFinal = asObjectRecord(session.final)
          const sessionTranscript = typeof sessionFinal?.text === 'string' ? sessionFinal.text : ''
          if (sequenceTiming) parts.push('sequence_timing=logged')
          const reasonCode = session.reason_code || 'ws_transport_error'
          const reasonError = session.error || prefix
          persistFailureResult(reasonCode, reasonError, prefix, {
            text: sessionTranscript,
            sequence_timing: sequenceTiming,
            // metriky ze session — jediný zdroj pravdy
            rtf: session.rtf,
            elapsed_s: session.elapsed_s,
            total_audio_s: session.total_audio_s,
            drop_rate: session.drop_rate,
            first_word_latency_ms: session.first_word_latency_ms,
            first_word_wall_ms: session.first_word_wall_ms,
            first_word_audio_ms: session.first_word_audio_ms,
            segment_finalize_ms_p50: session.segment_finalize_ms_p50,
            segment_finalize_ms_p95: session.segment_finalize_ms_p95,
            queue_depth_peak_s: session.queue_depth_peak_s,
            backpressure_events: session.backpressure_events,
            worker_rss_peak_mb: session.worker_rss_peak_mb,
            chunk_count: session.chunk_count,
            dropped_chunks: session.dropped_chunks,
          })
          const detail = parts.length > 0 ? ` | ${parts.join(' | ')}` : ''
          setError(`${prefix} [model=${activeModelId}, session=${session_id}]${detail}`)
        } catch (e) {
          const message = e instanceof Error ? e.message : String(e)
          persistFailureResult('ws_transport_error', message, prefix)
          setError(`${prefix} [model=${activeModelId}, session=${session_id}] | detail_lookup_failed=${message}`)
        }
      }
      const handleWsFailure = (prefix: string) => {
        if (wsFailureHandled || wsCompleted) return
        wsFailureHandled = true
        setStatus('error')
        stopRequestedAtPerfMsRef.current = null
        trialHardLimitAtPerfMsRef.current = null
        setRecordingStartedAtPerfMs(null)
        _stopAudio()
        void explainWsFailure(prefix)
      }
      const completeWithFinal = (msg: Record<string, unknown>) => {
        if (wsFailureHandled || wsCompleted) return
        wsCompleted = true
        finalizedSessionIdsRef.current.add(session_id)
        const finalText = String(msg.text ?? '')
        setTranscript(finalText)
        currentTranscriptTextRef.current = finalText
        lastTranscriptUpdateAtPerfMsRef.current = performance.now()
        setMetrics({
          latency_ms: msg.first_word_latency_ms as number | undefined,
          first_word_audio_ms: msg.first_word_audio_ms as number | undefined,
          first_word_wall_ms: msg.first_word_wall_ms as number | undefined,
          rtf: msg.rtf as number | undefined,
          elapsed_s: msg.elapsed_s as number | undefined,
          processing_ms_p95: msg.processing_ms_p95 as number | undefined,
          segment_finalize_ms_p95: msg.segment_finalize_ms_p95 as number | undefined,
          capture_jitter_ms_p95: msg.capture_jitter_ms_p95 as number | undefined,
          capture_lag_ms_p95: msg.capture_lag_ms_p95 as number | undefined,
          queue_depth_peak_ms: (typeof msg.queue_depth_peak_s === 'number') ? msg.queue_depth_peak_s * 1000 : undefined,
          backpressure_events: msg.backpressure_events as number | undefined,
          drop_rate: msg.drop_rate as number | undefined,
          worker_rss_peak_mb: msg.worker_rss_peak_mb as number | undefined,
          reason_code: msg.reason_code as string | null | undefined,
        })
        setStatus('done')
        stopGraceCheckInFlightRef.current = false
        stopRequestedAtPerfMsRef.current = null
        trialHardLimitAtPerfMsRef.current = null
        setRecordingStartedAtPerfMs(null)
        _stopAudio()
        void persistWebMicResult({
          sessionId: session_id,
          finalMsg: msg,
          modelIdForSession: activeModelId,
          referenceLabel,
          referenceText,
          autoSequenceMeta,
        })
      }

      ws.onopen = () => {
        const nowPerf = performance.now()
        const leadMs = autoModelLeadStartSeconds * 1000
        const audioStartDelayMs = mobileLoopEnabled ? loopAudioStartDelayS * 1000 : 0
        if (autoSequenceMeta && autoSequenceMeta.sequence_index === 0) {
          sequenceAnchorAtPerfMsRef.current = nowPerf + audioStartDelayMs
        }
        const sequenceAnchorPerf = sequenceAnchorAtPerfMsRef.current ?? (nowPerf + audioStartDelayMs)
        const nextSlotStopPerf = autoSequenceMeta
          ? (sequenceAnchorPerf + (autoSequenceMeta.sequence_index + 1) * loopCycleS * 1000 - leadMs)
          : (nowPerf + effectiveTrialPlanS * 1000)
        trialStartedAtPerfMsRef.current = nowPerf
        trialHardLimitAtPerfMsRef.current = nowPerf + autoModelHardTrialSeconds * 1000
        trialDeadlineAtPerfMsRef.current = Math.max(nowPerf + 1000, nextSlotStopPerf)
        trialAdaptiveEarlyStopMsRef.current = 0
        lastTranscriptUpdateAtPerfMsRef.current = nowPerf
        currentTranscriptTextRef.current = ''
        silenceMinCaptureNotifiedRef.current = false
        stopRequestedAtPerfMsRef.current = null
        setRecordingStartedAtPerfMs(nowPerf)
        setRecordingElapsedS(0)
        setStatus('recording')
        if (autoSequenceMeta) {
          const sequenceQueue = autoSequenceMeta.queue_model_ids ?? autoModelSequenceIds
          const sequenceSelected = autoSequenceMeta.selected_model_ids ?? autoModelSelectedOrdered
          logSequenceEvent('client_sequence_trial_ws_opened', {
            ...buildSequencePlanPayload(
              autoSequenceMeta.sequence_token,
              sequenceQueue,
              sequenceSelected,
              autoSequenceMeta.sequence_index,
              activeModelId,
            ),
            session_id,
            client_trial_started_perf_ms: nowPerf,
            client_sequence_anchor_perf_ms: sequenceAnchorPerf,
            client_planned_slot_stop_perf_ms: nextSlotStopPerf,
            client_trial_deadline_perf_ms: trialDeadlineAtPerfMsRef.current,
            client_trial_hard_limit_perf_ms: trialHardLimitAtPerfMsRef.current,
            ui_message: `Auto sekvence: model ${autoSequenceMeta.sequence_index + 1}/${autoSequenceMeta.sequence_total} (${activeModelId}).`,
          })
        }
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'partial') {
            if (autoSequenceMeta && typeof msg.first_word_wall_ms === 'number') {
              const overMs = msg.first_word_wall_ms - autoModelLatencyGuardSeconds * 1000
              if (overMs > 0) {
                const adaptiveCutMs = Math.min(overMs, autoModelAdaptiveMaxCutSeconds * 1000)
                trialAdaptiveEarlyStopMsRef.current = Math.max(trialAdaptiveEarlyStopMsRef.current, adaptiveCutMs)
              }
            }
            if (typeof msg.text === 'string') {
              setTranscript(msg.text)
              if (msg.text !== currentTranscriptTextRef.current) {
                currentTranscriptTextRef.current = msg.text
                lastTranscriptUpdateAtPerfMsRef.current = performance.now()
              }
            }
            setMetrics((prev) => ({
              ...(prev ?? {}),
              latency_ms: (typeof msg.first_word_latency_ms === 'number') ? msg.first_word_latency_ms : prev?.latency_ms,
              first_word_audio_ms: (typeof msg.first_word_audio_ms === 'number') ? msg.first_word_audio_ms : prev?.first_word_audio_ms,
              first_word_wall_ms: (typeof msg.first_word_wall_ms === 'number') ? msg.first_word_wall_ms : prev?.first_word_wall_ms,
              queue_depth_peak_ms: (typeof msg.queue_depth_peak_ms === 'number') ? msg.queue_depth_peak_ms : prev?.queue_depth_peak_ms,
              backpressure_events: (typeof msg.backpressure_events === 'number') ? msg.backpressure_events : prev?.backpressure_events,
              drop_rate: (typeof msg.drop_rate === 'number') ? msg.drop_rate : prev?.drop_rate,
              worker_rss_peak_mb: (typeof msg.worker_rss_peak_mb === 'number') ? msg.worker_rss_peak_mb : prev?.worker_rss_peak_mb,
              reason_code: (typeof msg.reason_code === 'string' ? msg.reason_code : prev?.reason_code) ?? null,
            }))
          } else if (msg.type === 'started') {
            // no-op
          } else if (msg.type === 'final') {
            completeWithFinal(msg as Record<string, unknown>)
          } else if (msg.error) {
            wsFailureHandled = true
            const reasonCode = (typeof msg.reason_code === 'string' && msg.reason_code.trim().length > 0)
              ? msg.reason_code
              : 'stream_error'
            const errText = typeof msg.error === 'string' ? msg.error : 'Neznámá stream chyba'
            persistFailureResult(reasonCode, errText, `WS chyba modelu ${activeModelId}`)
            setError(msg.reason_code ? `${msg.error} (${msg.reason_code})` : msg.error)
            setStatus('error')
            stopRequestedAtPerfMsRef.current = null
            trialHardLimitAtPerfMsRef.current = null
            setRecordingStartedAtPerfMs(null)
            _stopAudio()
          }
        } catch {
          // ignoruj neJSON zprávy
        }
      }

      ws.onerror = () => {
        handleWsFailure('WebSocket chyba při streamu')
      }

      ws.onclose = (event) => {
        if (wsCompleted || wsFailureHandled) return
        const parts: string[] = []
        if (typeof event.code === 'number') parts.push(`code=${event.code}`)
        if (event.reason) parts.push(`reason=${event.reason}`)
        parts.push(`clean=${event.wasClean ? '1' : '0'}`)
        const suffix = parts.length > 0 ? ` (${parts.join(', ')})` : ''
        if (event.code === 1000 && event.wasClean) {
          void api.mic.getSession(session_id)
            .then((session) => {
              if (wsCompleted || wsFailureHandled || finalizedSessionIdsRef.current.has(session_id)) return
              const finalMsg = asObjectRecord(session.final)
              if (session.status === 'stopped' && finalMsg && !session.error) {
                completeWithFinal(finalMsg)
                return
              }
              if (stopRequestedAtPerfMsRef.current != null) return
              handleWsFailure(`WebSocket spojení bylo neočekávaně ukončeno${suffix}`)
            })
            .catch(() => {
              if (stopRequestedAtPerfMsRef.current != null) return
              handleWsFailure(`WebSocket spojení bylo neočekávaně ukončeno${suffix}`)
            })
          return
        }
        if (stopRequestedAtPerfMsRef.current != null) return
        handleWsFailure(`WebSocket spojení bylo neočekávaně ukončeno${suffix}`)
      }

      // 3. Otevři mikrofon
      const constraints: MediaStreamConstraints = {
        audio: selectedAudioDevice
          ? { deviceId: { exact: selectedAudioDevice.name } }
          : true,
      }
      const stream = await navigator.mediaDevices.getUserMedia(constraints)
      streamRef.current = stream
      const streamTrackLabel = stream.getAudioTracks()[0]?.label || selectedMicDeviceLabel
      resetMicInputProof(streamTrackLabel)
      stream.getTracks().forEach((track) => {
        track.onended = () => {
          setError('Mikrofon byl odpojen nebo zakázán.')
          setStatus('error')
          try {
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ action: 'stop' }))
            }
          } catch {}
          _stopAudio()
        }
      })

      const audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE })
      audioCtxRef.current = audioCtx
      const source = audioCtx.createMediaStreamSource(stream)
      sourceRef.current = source

      // Preferuj AudioWorklet (separátní audio thread), fallback na ScriptProcessor.
      const supportsWorklet = typeof AudioWorkletNode !== 'undefined' && !!audioCtx.audioWorklet
      if (supportsWorklet) {
        try {
          const workletCode = `
            class AsttPcmWorklet extends AudioWorkletProcessor {
              process(inputs) {
                const input = inputs[0];
                if (!input || input.length === 0 || !input[0]) return true;
                const channel = input[0];
                const int16 = new Int16Array(channel.length);
                for (let i = 0; i < channel.length; i++) {
                  const v = Math.max(-1, Math.min(1, channel[i]));
                  int16[i] = v < 0 ? Math.round(v * 32768) : Math.round(v * 32767);
                }
                this.port.postMessage(int16.buffer, [int16.buffer]);
                return true;
              }
            }
            registerProcessor('astt-pcm-worklet', AsttPcmWorklet);
          `
          const blobUrl = URL.createObjectURL(new Blob([workletCode], { type: 'application/javascript' }))
          await audioCtx.audioWorklet.addModule(blobUrl)
          URL.revokeObjectURL(blobUrl)
          const worklet = new AudioWorkletNode(audioCtx, 'astt-pcm-worklet', {
            numberOfInputs: 1,
            numberOfOutputs: 0,
            channelCount: 1,
          })
          worklet.port.onmessage = (ev: MessageEvent<ArrayBuffer>) => {
            if (ws.readyState !== WebSocket.OPEN || !(ev.data instanceof ArrayBuffer)) return
            const int16 = new Int16Array(ev.data)
            const captureTsMs = performance.timeOrigin + performance.now()
            const payload = _buildFramedPcmPayload(int16, captureTsMs)
            ws.send(payload)
            recordMicAudioChunk(int16, payload.byteLength, captureTsMs)
          }
          workletNodeRef.current = worklet
          source.connect(worklet)
          return
        } catch {
          // Fallback níže.
        }
      }

      const bufferSize = 4096
      const processor = audioCtx.createScriptProcessor(bufferSize, 1, 1)
      processorRef.current = processor
      processor.onaudioprocess = (e) => {
        if (ws.readyState !== WebSocket.OPEN) return
        const float32 = e.inputBuffer.getChannelData(0)
        const int16 = new Int16Array(float32.length)
        for (let i = 0; i < float32.length; i++) {
          const v = Math.max(-1, Math.min(1, float32[i]))
          int16[i] = v < 0 ? Math.round(v * 32768) : Math.round(v * 32767)
        }
        const captureTsMs = performance.timeOrigin + performance.now()
        const payload = _buildFramedPcmPayload(int16, captureTsMs)
        ws.send(payload)
        recordMicAudioChunk(int16, payload.byteLength, captureTsMs)
      }
      source.connect(processor)
      processor.connect(audioCtx.destination)

    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
      setStatus('error')
      setRecordingStartedAtPerfMs(null)
      activeSessionIdRef.current = null
    }
  }, [
    modelId,
    params,
    paramsByModel,
    sequenceCommonParamsEnabled,
    sequenceCommonParams,
    effectiveSequenceParamProfileLabel,
    availableModels,
    deviceIndex,
    devices,
    status,
    testMode,
    referenceVideoId,
    clipFromS,
    clipToS,
    clipDurationS,
    mobileLoopEnabled,
    loopSpeechS,
    loopCaptureSpeechS,
    loopEarlyStopS,
    loopPauseS,
    loopAudioStartDelayS,
    mobileLoopSyncFirstRound,
    loopRepeatCount,
    mobileLoopAutoStop,
    mobileLoopAutoPlayOnSequenceStart,
    loopCycleS,
    effectiveTrialPlanS,
    autoModelSequenceIds,
    autoModelSelectedOrdered,
    autoModelLeadStartSeconds,
    autoModelPreparationSeconds,
    autoModelHardTrialBaseSeconds,
    autoModelHardTrialSeconds,
    autoModelSilenceStopSeconds,
    autoModelMinSilenceStopElapsedS,
    autoModelSilenceMinAudioFraction,
    autoModelGraceSeconds,
    autoModelSlotSeconds,
    autoModelLatencyGuardSeconds,
    autoModelAdaptiveMaxCutSeconds,
    tuningSweepMaxLagSeconds,
    orchestratorMode,
    selectedReferenceTextId,
    selectedReferenceText,
    referenceTexts,
    selectedAudioDevice,
    selectedMicDeviceLabel,
    resetMicInputProof,
    recordMicAudioChunk,
    logSequenceEvent,
    buildSequencePlanPayload,
    persistWebMicResult,
  ])

  function toggleAutoModelSelection(modelKey: string) {
    setAutoModelSelectedIds((prev) => (
      prev.includes(modelKey)
        ? prev.filter((id) => id !== modelKey)
        : [...prev, modelKey]
    ))
  }

  const start = useCallback(async () => {
    if (tuningSweepEnabled && !autoModelCycleEnabled) {
      setError('Pro automatické ladění zapni Auto střídání STT modelů.')
      setStatus('error')
      return
    }
    if (autoModelCycleEnabled) {
      const selectedModelIds = autoModelSelectedOrdered
      if (selectedModelIds.length === 0) {
        setError('Pro auto-střídání vyber alespoň jeden model.')
        setStatus('error')
        return
      }
      const sweepPlan = tuningSweepEnabled ? tuningSweepPreviewPlan : []
      if (tuningSweepEnabled && sweepPlan.length === 0) {
        setError('Pro automatické ladění není sestaven žádný slot. Vyber alespoň jeden podporovaný model.')
        setStatus('error')
        return
      }
      if (tuningSweepEnabled && tuningSweepCustomRangesEnabled && tuningSweepEnabledRangeCount === 0) {
        setError(TUNING_CUSTOM_RANGE_NO_PARAM_HINT)
        setStatus('error')
        return
      }
      const shouldAutoPlayLoop = (
        mobileLoopAutoPlayOnSequenceStart
        && mobileLoopEnabled
        && testMode === 'reference_video'
      )
      if (shouldAutoPlayLoop) {
        const preparedPackage = await ensureCurrentMobileLoopPackage()
        if (!preparedPackage) {
          setError('Audio plán se nepodařilo připravit. Zkontroluj referenční video a zkus Přehrát audio plán.')
          setStatus('error')
          return
        }
      }
      const queue = tuningSweepEnabled ? sweepPlan.map((slot) => slot.modelId) : [...selectedModelIds]
      const token = `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`
      const tuningSeriesId = tuningSweepEnabled ? `tune_${token}` : null
      autoModelSequenceTokenRef.current = token
      tuningSweepSeriesIdRef.current = tuningSeriesId
      tuningSweepPlanRef.current = tuningSweepEnabled ? sweepPlan : []
      clientSequenceRunIdRef.current = `run_client_${token}`
      clientSequenceAnchorAtPerfMsRef.current = performance.now()
      sequenceAnchorAtPerfMsRef.current = null
      autoModelAdvanceLockRef.current = false
      setAutoModelSequenceIds(queue)
      setAutoModelSequenceIndex(0)
      setAutoModelSavedCount(0)
      autoModelSavedSlotsRef.current.clear()
      autoModelRetryCountRef.current = 0
      setSeqReport(null)
      setSeqReportToken(token)
      setAutoModelSequenceActive(true)
      setTuningSweepActive(tuningSweepEnabled)
      setTuningSweepSeriesId(tuningSeriesId)
      setTuningSweepPlan(tuningSweepEnabled ? sweepPlan : [])

      const firstModelId = queue[0]
      const firstModel = availableModels.find((m) => m.model_id === firstModelId)
      const firstTuningSlot = tuningSweepEnabled ? sweepPlan[0] : null
      setModelId(firstModelId)
      setParams(firstTuningSlot?.params ?? buildMicParamsWithSaved(firstModel, paramsByModel[firstModelId]))
      const audioRoundWarning = tuningSweepEnabled && mobileLoopEnabled && loopRepeatCount < queue.length
        ? ` Pozor: audio loop má v UI ${loopRepeatCount} měřených kol, plán má ${queue.length} trialů.`
        : ''
      const firstMsg = tuningSweepEnabled
        ? `Ladění parametrů: trial 1/${queue.length} (${firstModelId}, ${firstTuningSlot?.variantLabel ?? 'baseline'}).${audioRoundWarning}`
        : `Auto sekvence: model 1/${queue.length} (${firstModelId}).`
      setSaveMsg(firstMsg)
      logSequenceEvent('client_sequence_started', {
        ...buildSequencePlanPayload(token, queue, selectedModelIds, 0, firstModelId),
        ui_message: firstMsg,
      })
      if (tuningSweepEnabled) {
        logSequenceEvent('client_tuning_sweep_started', {
          ...buildSequencePlanPayload(token, queue, selectedModelIds, 0, firstModelId),
          ui_message: firstMsg,
        })
      }
      logSequenceEvent('client_sequence_trial_start_requested', {
        ...buildSequencePlanPayload(token, queue, selectedModelIds, 0, firstModelId),
        ui_message: firstMsg,
      })
      await startSession(firstModelId, {
        sequence_token: token,
        sequence_index: 0,
        sequence_total: queue.length,
        queue_model_ids: queue,
        selected_model_ids: selectedModelIds,
        tuning_series_id: tuningSeriesId,
        tuning_slot: firstTuningSlot,
      })
      if (shouldAutoPlayLoop) {
        void playMobileLoopAudioPlan('sequence_start')
      }
      return
    }

    tuningSweepPlanRef.current = []
    tuningSweepSeriesIdRef.current = null
    setTuningSweepActive(false)
    setTuningSweepSeriesId(null)
    setTuningSweepPlan([])
    await startSession()
  }, [
    autoModelCycleEnabled,
    availableModels,
    autoModelSelectedOrdered,
    paramsByModel,
    tuningSweepEnabled,
    tuningSweepPreviewPlan,
    tuningSweepCustomRangesEnabled,
    tuningSweepEnabledRangeCount,
    mobileLoopEnabled,
    mobileLoopAutoPlayOnSequenceStart,
    loopRepeatCount,
    testMode,
    ensureCurrentMobileLoopPackage,
    playMobileLoopAudioPlan,
    logSequenceEvent,
    buildSequencePlanPayload,
    startSession,
  ])

  const stopAutoModelSequence = useCallback((note = 'Auto sekvence zastavena.', clientReason = 'stopped') => {
    const token = autoModelSequenceTokenRef.current
    if (token) {
      const tuningActive = tuningSweepPlanRef.current.length > 0 || Boolean(tuningSweepSeriesIdRef.current)
      logSequenceEvent(clientReason === 'completed' ? 'client_sequence_completed' : 'client_sequence_stopped', {
        sequence_token: token,
        sequence_index: autoModelSequenceIndex + 1,
        sequence_total: autoModelSequenceIds.length || null,
        saved_count: autoModelSavedSlotsRef.current.size,
        ui_message: note,
        client_reason: clientReason,
        tuning_series_id: tuningSweepSeriesIdRef.current,
        tuning_active: tuningActive,
      })
      if (tuningActive) {
        logSequenceEvent(clientReason === 'completed' ? 'client_tuning_sweep_completed' : 'client_tuning_sweep_stopped', {
          sequence_token: token,
          sequence_total: autoModelSequenceIds.length || null,
          saved_count: autoModelSavedSlotsRef.current.size,
          tuning_series_id: tuningSweepSeriesIdRef.current,
          ui_message: note,
          client_reason: clientReason,
        })
      }
    }
    autoModelSequenceTokenRef.current = null
    tuningSweepPlanRef.current = []
    tuningSweepSeriesIdRef.current = null
    clientSequenceRunIdRef.current = null
    clientSequenceAnchorAtPerfMsRef.current = null
    autoModelAdvanceLockRef.current = false
    stopGraceCheckInFlightRef.current = false
    stopRequestedAtPerfMsRef.current = null
    trialStartedAtPerfMsRef.current = null
    trialDeadlineAtPerfMsRef.current = null
    trialHardLimitAtPerfMsRef.current = null
    trialAdaptiveEarlyStopMsRef.current = 0
    sequenceAnchorAtPerfMsRef.current = null
    lastTranscriptUpdateAtPerfMsRef.current = null
    currentTranscriptTextRef.current = ''
    activeSessionLoopConfigRef.current = {
      enabled: false,
      speechS: null,
      captureSpeechS: null,
      earlyStopS: null,
      pauseS: null,
      audioStartDelayS: null,
      syncFirstRound: null,
      measuredRounds: null,
      autoStop: null,
      packageId: null,
    }
    activeSessionModelParamsRef.current = {}
    activeSessionCommonParamsRef.current = {}
    activeSessionParamProfileRef.current = null
    silenceMinCaptureNotifiedRef.current = false
    if (autoModelAdvanceTimerRef.current != null) {
      window.clearTimeout(autoModelAdvanceTimerRef.current)
      autoModelAdvanceTimerRef.current = null
    }
    setAutoModelSequenceActive(false)
    setAutoModelSequenceIds([])
    setAutoModelSequenceIndex(0)
    setAutoModelSavedCount(0)
    setTuningSweepActive(false)
    setTuningSweepSeriesId(null)
    setTuningSweepPlan([])
    autoModelSavedSlotsRef.current.clear()
    setSaveMsg(note)
    if (clientReason === 'completed' || clientReason === 'user_stop' || clientReason === 'stopped') {
      stopMobileLoopAudioPlayback(clientReason === 'completed' ? 'Audio zastaveno po dokončení sekvence.' : 'Audio zastaveno se sekvencí.')
    }
  }, [
    autoModelSequenceIds.length,
    autoModelSequenceIndex,
    logSequenceEvent,
    stopMobileLoopAudioPlayback,
  ])

  const requestTrialStop = useCallback((note?: string, stopContext: Record<string, unknown> = {}) => {
    if (note) setSaveMsg(note)
    setStatus('stopping')
    const nowPerf = performance.now()
    stopRequestedAtPerfMsRef.current = nowPerf
    const trialStarted = trialStartedAtPerfMsRef.current
    const trialElapsedS = trialStarted == null ? null : Math.max(0, (nowPerf - trialStarted) / 1000)
    const sessionId = activeSessionIdRef.current
    const loopCfg = activeSessionLoopConfigRef.current
    const activeAudioStartDelayS = loopCfg.enabled && typeof loopCfg.audioStartDelayS === 'number'
      ? Math.max(0, Math.min(30, loopCfg.audioStartDelayS))
      : null
    const effectiveHardTrialS = activeAudioStartDelayS == null
      ? autoModelHardTrialSeconds
      : autoModelHardTrialBaseSeconds + activeAudioStartDelayS
    const effectiveSilenceMinElapsedS = activeAudioStartDelayS == null
      ? autoModelMinSilenceStopElapsedS
      : Math.max(
        autoModelSilenceStopSeconds,
        activeAudioStartDelayS + loopSpeechS * autoModelSilenceMinAudioFraction,
      )
    const activeTuningSlot = tuningSweepPlanRef.current[autoModelSequenceIndex] ?? null
    const activeTuningPayload = buildTuningSlotPayload(
      activeTuningSlot,
      tuningSweepSeriesIdRef.current,
      tuningSweepMaxLagSeconds,
    )
    const runtimeStopContext = {
      mobile_loop_audio_start_delay_s: activeAudioStartDelayS,
      mobile_loop_audio_start_known: activeAudioStartDelayS != null && activeAudioStartDelayS > 0,
      mobile_loop_audio_start_source: activeAudioStartDelayS != null && activeAudioStartDelayS > 0
        ? 'manual_mark_or_offset'
        : 'manual_start_together_with_start_sequence',
      auto_model_sequence_hard_trial_base_s: autoModelHardTrialBaseSeconds,
      auto_model_sequence_hard_trial_s: effectiveHardTrialS,
      auto_model_sequence_effective_hard_trial_s: effectiveHardTrialS,
      auto_model_sequence_silence_min_elapsed_s: effectiveSilenceMinElapsedS,
      auto_model_sequence_silence_min_audio_fraction: autoModelSilenceMinAudioFraction,
    }
    if (autoModelSequenceActive || autoModelSequenceTokenRef.current) {
      logSequenceEvent('client_sequence_trial_stop_requested', {
        sequence_token: autoModelSequenceTokenRef.current,
        sequence_index: autoModelSequenceIndex + 1,
        sequence_total: autoModelSequenceIds.length || null,
        session_id: sessionId,
        model_id: modelId,
        ui_message: note ?? null,
        client_stop_elapsed_s: trialElapsedS,
        ...runtimeStopContext,
        ...activeTuningPayload,
        ...stopContext,
      })
    }
    setRecordingStartedAtPerfMs(null)
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        action: 'stop',
        client_stop_note: note ?? null,
        client_stop_elapsed_s: trialElapsedS,
        ...runtimeStopContext,
        ...activeTuningPayload,
        ...stopContext,
      }))
    }
    _stopAudio()
  }, [
    autoModelHardTrialBaseSeconds,
    autoModelHardTrialSeconds,
    autoModelSequenceActive,
    autoModelSequenceIndex,
    autoModelSequenceIds.length,
    autoModelSilenceMinAudioFraction,
    autoModelSilenceStopSeconds,
    autoModelMinSilenceStopElapsedS,
    logSequenceEvent,
    loopSpeechS,
    modelId,
    tuningSweepMaxLagSeconds,
  ])

  const stop = useCallback(() => {
    if (autoModelSequenceActive) {
      requestTrialStop('Auto sekvence zastavena uživatelem.', { client_stop_reason: 'user_stop' })
      stopAutoModelSequence('Auto sekvence zastavena uživatelem.', 'user_stop')
      stopMobileLoopAudioPlayback('Audio zastaveno uživatelem.')
      return
    }
    requestTrialStop()
  }, [autoModelSequenceActive, stopAutoModelSequence, requestTrialStop, stopMobileLoopAudioPlayback])

  function _stopAudio() {
    try { workletNodeRef.current?.disconnect() } catch {}
    try { processorRef.current?.disconnect() } catch {}
    try { sourceRef.current?.disconnect() } catch {}
    try { audioCtxRef.current?.close() } catch {}
    try { streamRef.current?.getTracks().forEach(t => t.stop()) } catch {}
    workletNodeRef.current = null
    processorRef.current = null
    sourceRef.current = null
    audioCtxRef.current = null
    streamRef.current = null
  }

  useEffect(() => {
    if (status !== 'recording' || recordingStartedAtPerfMs == null) return
    const updateElapsed = () => {
      const elapsed = Math.max(0, (performance.now() - recordingStartedAtPerfMs) / 1000)
      setRecordingElapsedS(elapsed)
    }
    updateElapsed()
    const timer = window.setInterval(updateElapsed, 200)
    return () => window.clearInterval(timer)
  }, [status, recordingStartedAtPerfMs])

  useEffect(() => {
    if (status !== 'recording') return
    if (!mobileLoopEnabled || !mobileLoopAutoStop) return
    if (autoStopFiredRef.current) return
    if (recordingElapsedS < loopPlanS) return
    autoStopFiredRef.current = true
    setSaveMsg(`Auto-stop: dokončen plán mobil loop (${formatDurationHms(loopPlanS)}).`)
    stop()
  }, [status, mobileLoopEnabled, mobileLoopAutoStop, recordingElapsedS, loopPlanS, stop])

  useEffect(() => {
    if (!autoModelSequenceActive) return
    if (status !== 'recording') return

    const timer = window.setInterval(() => {
      if (stopRequestedAtPerfMsRef.current != null) return
      const deadlinePerf = trialDeadlineAtPerfMsRef.current
      if (deadlinePerf == null) return
      const hardLimitPerf = trialHardLimitAtPerfMsRef.current ?? Number.POSITIVE_INFINITY
      const reserveMs = autoModelPreparationSeconds * 1000
      const adaptiveCutMs = trialAdaptiveEarlyStopMsRef.current
      const userEarlyCutMs = Math.max(0, loopEarlyStopS) * 1000
      const effectiveCutMs = Math.max(reserveMs, adaptiveCutMs, userEarlyCutMs)
      const slotStopPerf = deadlinePerf - effectiveCutMs
      const effectiveStopPerf = Math.min(
        hardLimitPerf,
        Math.max((trialStartedAtPerfMsRef.current ?? deadlinePerf) + 1000, slotStopPerf),
      )
      if (performance.now() < effectiveStopPerf) return
      const stopNote = adaptiveCutMs > Math.max(reserveMs, userEarlyCutMs)
        ? `, zkráceno o ${(adaptiveCutMs / 1000).toFixed(1)}s (latence > ${autoModelLatencyGuardSeconds}s)`
        : `, rezerva ${(reserveMs / 1000).toFixed(1)}s + user-cut ${(userEarlyCutMs / 1000).toFixed(1)}s`
      requestTrialStop(`Auto sekvence: konec slotu ${formatDurationHms(loopCycleS)}${stopNote}.`, {
        client_stop_reason: 'slot_end',
        client_planned_slot_s: loopCycleS,
        client_effective_cut_s: effectiveCutMs / 1000,
        client_reserve_s: reserveMs / 1000,
        client_user_early_cut_s: userEarlyCutMs / 1000,
        client_adaptive_cut_s: adaptiveCutMs / 1000,
      })
    }, 200)

    return () => window.clearInterval(timer)
  }, [autoModelSequenceActive, status, loopCycleS, loopEarlyStopS, autoModelLatencyGuardSeconds, autoModelPreparationSeconds, requestTrialStop])

  useEffect(() => {
    if (!autoModelSequenceActive) return
    if (status !== 'recording') return

    const timer = window.setInterval(() => {
      if (stopRequestedAtPerfMsRef.current != null) return
      const hardLimitPerf = trialHardLimitAtPerfMsRef.current
      if (hardLimitPerf == null) return
      if (performance.now() < hardLimitPerf) return
      requestTrialStop(`Auto sekvence: hard cap ${autoModelHardTrialSeconds}s (forced stop).`, {
        client_stop_reason: 'hard_cap',
        client_hard_trial_s: autoModelHardTrialSeconds,
      })
    }, 150)

    return () => window.clearInterval(timer)
  }, [autoModelSequenceActive, status, autoModelHardTrialSeconds, requestTrialStop])

  useEffect(() => {
    if (!autoModelSequenceActive) return
    if (status !== 'recording') return

    const timer = window.setInterval(() => {
      if (stopRequestedAtPerfMsRef.current != null) return
      const nowPerf = performance.now()
      const trialStartedPerf = trialStartedAtPerfMsRef.current
      const lastUpdatePerf = lastTranscriptUpdateAtPerfMsRef.current ?? trialStartedPerf
      if (lastUpdatePerf == null || trialStartedPerf == null) return
      const trialElapsedS = (nowPerf - trialStartedPerf) / 1000
      const silentS = (nowPerf - lastUpdatePerf) / 1000
      if (silentS < autoModelSilenceStopSeconds) return
      if (trialElapsedS < autoModelMinSilenceStopElapsedS) {
        if (!silenceMinCaptureNotifiedRef.current) {
          silenceMinCaptureNotifiedRef.current = true
          logSequenceEvent('client_sequence_silence_stop_deferred', {
            sequence_token: autoModelSequenceTokenRef.current,
            sequence_index: autoModelSequenceIndex + 1,
            sequence_total: autoModelSequenceIds.length || null,
            session_id: activeSessionIdRef.current,
            model_id: modelId,
            client_silence_stop_s: autoModelSilenceStopSeconds,
            client_observed_silence_s: silentS,
            client_trial_elapsed_s: trialElapsedS,
            client_min_elapsed_before_silence_stop_s: autoModelMinSilenceStopElapsedS,
            client_min_audio_fraction_before_silence_stop: autoModelSilenceMinAudioFraction,
            ui_message: `Auto sekvence: stop při mezeře odložen do ${autoModelMinSilenceStopElapsedS.toFixed(1)}s (75 % audia).`,
          })
        }
        return
      }
      setError(`Auto sekvence: ${autoModelSilenceStopSeconds}s bez nového přepisu.`)
      requestTrialStop(`Auto sekvence: ${autoModelSilenceStopSeconds}s bez nového textu (stop trialu).`, {
        client_stop_reason: 'silence_no_new_text',
        client_silence_stop_s: autoModelSilenceStopSeconds,
        client_observed_silence_s: silentS,
        client_trial_elapsed_s: trialElapsedS,
        client_min_elapsed_before_silence_stop_s: autoModelMinSilenceStopElapsedS,
        client_min_audio_fraction_before_silence_stop: autoModelSilenceMinAudioFraction,
      })
    }, 250)

    return () => window.clearInterval(timer)
  }, [
    autoModelSequenceActive,
    status,
    autoModelSilenceStopSeconds,
    autoModelMinSilenceStopElapsedS,
    autoModelSilenceMinAudioFraction,
    autoModelSequenceIndex,
    autoModelSequenceIds.length,
    logSequenceEvent,
    modelId,
    requestTrialStop,
  ])

  useEffect(() => {
    if (!autoModelSequenceActive) return
    if (status !== 'stopping') return
    const stopIssued = stopRequestedAtPerfMsRef.current
    if (stopIssued == null) return

    const timer = window.setInterval(() => {
      const nowPerf = performance.now()
      const elapsedStopS = (nowPerf - stopIssued) / 1000
      const anchorPerf = sequenceAnchorAtPerfMsRef.current
      const leadMs = autoModelLeadStartSeconds * 1000
      const nextTargetStartPerf = anchorPerf == null
        ? null
        : (anchorPerf + (autoModelSequenceIndex + 1) * loopCycleS * 1000 - leadMs)
      const timeToNextStartS = nextTargetStartPerf == null ? Number.POSITIVE_INFINITY : (nextTargetStartPerf - nowPerf) / 1000
      const trialStartedPerf = trialStartedAtPerfMsRef.current
      const trialElapsedS = trialStartedPerf == null ? 0 : (nowPerf - trialStartedPerf) / 1000
      const remainingHardS = Math.max(0, autoModelHardTrialSeconds - trialElapsedS)
      const cappedGraceS = Math.min(
        autoModelGraceSeconds,
        Math.max(0.1, timeToNextStartS - 0.2),
        Math.max(0.1, remainingHardS),
      )
      if (elapsedStopS < cappedGraceS) return
      const forceSkip = () => {
        stopRequestedAtPerfMsRef.current = null
        stopGraceCheckInFlightRef.current = false
        try {
          wsRef.current?.close()
        } catch {}
        _stopAudio()
        setError(`Model nestihl doběhnout do ${cappedGraceS.toFixed(1)}s po stopu (auto-skip).`)
        setStatus('error')
        setSaveMsg(`Auto sekvence: přeskočen model po timeoutu doběhu (${cappedGraceS.toFixed(1)}s).`)
        logSequenceEvent('client_sequence_trial_skipped', {
          sequence_token: autoModelSequenceTokenRef.current,
          sequence_index: autoModelSequenceIndex + 1,
          sequence_total: autoModelSequenceIds.length || null,
          session_id: activeSessionIdRef.current,
          model_id: modelId,
          elapsed_stop_s: elapsedStopS,
          capped_grace_s: cappedGraceS,
          time_to_next_start_s: timeToNextStartS,
          remaining_hard_s: remainingHardS,
          ...buildTuningSlotPayload(
            tuningSweepPlanRef.current[autoModelSequenceIndex] ?? null,
            tuningSweepSeriesIdRef.current,
            tuningSweepMaxLagSeconds,
          ),
          ui_message: `Auto sekvence: přeskočen model po timeoutu doběhu (${cappedGraceS.toFixed(1)}s).`,
        })
      }
      const sessionId = activeSessionIdRef.current
      if (sessionId && !stopGraceCheckInFlightRef.current) {
        stopGraceCheckInFlightRef.current = true
        void api.mic.getSession(sessionId)
          .then((session) => {
            const finalMsg = asObjectRecord(session.final)
            if (session.status === 'stopped' && finalMsg && !session.error) {
              setSaveMsg('Auto sekvence: model doběhl po stopu, čekám na finální zprávu.')
              return
            }
            forceSkip()
          })
          .catch(() => forceSkip())
        return
      }
      if (stopGraceCheckInFlightRef.current) return
      stopRequestedAtPerfMsRef.current = null
      forceSkip()
    }, 300)

    return () => window.clearInterval(timer)
  }, [autoModelSequenceActive, status, autoModelGraceSeconds, autoModelSequenceIndex, autoModelSequenceIds.length, loopCycleS, autoModelLeadStartSeconds, autoModelHardTrialSeconds, logSequenceEvent, modelId, tuningSweepMaxLagSeconds])

  useEffect(() => {
    if (!autoModelSequenceActive) return
    if (status !== 'done' && status !== 'error') return
    if (autoModelAdvanceLockRef.current) return

    const token = autoModelSequenceTokenRef.current
    if (!token) return

    // Retry při start_recording_failed (max 1×)
    const isStartFailed = status === 'error' && typeof error === 'string' && error.includes('start_recording_failed')
    if (isStartFailed && autoModelRetryCountRef.current < 1) {
      autoModelRetryCountRef.current += 1
      const retryModelId = autoModelSequenceIds[autoModelSequenceIndex]
      const retryModel = availableModels.find((m) => m.model_id === retryModelId)
      const retryTuningSlot = tuningSweepPlanRef.current[autoModelSequenceIndex] ?? null
      setSaveMsg(`Retry (${autoModelRetryCountRef.current}/1): ${retryModelId}`)
      logSequenceEvent('client_sequence_trial_retry', {
        ...buildSequencePlanPayload(token, autoModelSequenceIds, autoModelSelectedOrdered, autoModelSequenceIndex, retryModelId),
        retry_count: autoModelRetryCountRef.current,
        ui_message: `Retry (${autoModelRetryCountRef.current}/1): ${retryModelId}`,
      })
      autoModelAdvanceLockRef.current = true
      autoModelAdvanceTimerRef.current = window.setTimeout(() => {
        autoModelAdvanceTimerRef.current = null
        if (autoModelSequenceTokenRef.current !== token) { autoModelAdvanceLockRef.current = false; return }
        setModelId(retryModelId)
        setParams(retryTuningSlot?.params ?? buildMicParamsWithSaved(retryModel, paramsByModel[retryModelId]))
        void startSession(retryModelId, {
          sequence_token: token,
          sequence_index: autoModelSequenceIndex,
          sequence_total: autoModelSequenceIds.length,
          queue_model_ids: autoModelSequenceIds,
          selected_model_ids: autoModelSelectedOrdered,
          tuning_series_id: tuningSweepSeriesIdRef.current,
          tuning_slot: retryTuningSlot,
        }).finally(() => { autoModelAdvanceLockRef.current = false })
      }, 3000)
      return
    }
    autoModelRetryCountRef.current = 0

    const nextIndex = autoModelSequenceIndex + 1
    if (nextIndex >= autoModelSequenceIds.length) {
      stopAutoModelSequence(`Auto sekvence dokončena (${autoModelSequenceIds.length}/${autoModelSequenceIds.length}).`, 'completed')
      return
    }

    const anchorPerf = sequenceAnchorAtPerfMsRef.current ?? performance.now()
    sequenceAnchorAtPerfMsRef.current = anchorPerf
    const nowPerf = performance.now()
    const leadMs = autoModelLeadStartSeconds * 1000
    const targetStartPerf = anchorPerf + nextIndex * loopCycleS * 1000 - leadMs
    const waitMs = Math.max(0, targetStartPerf - nowPerf)
    const nextModelId = autoModelSequenceIds[nextIndex]
    const nextTuningSlot = tuningSweepPlanRef.current[nextIndex] ?? null
    logSequenceEvent('client_sequence_next_scheduled', {
      ...buildSequencePlanPayload(token, autoModelSequenceIds, autoModelSelectedOrdered, nextIndex, nextModelId),
      client_wait_ms: waitMs,
      client_target_start_perf_ms: targetStartPerf,
      client_pause_from_now_s: waitMs / 1000,
      ui_message: `Další model ${nextIndex + 1}/${autoModelSequenceIds.length} (${nextModelId}) za ${(waitMs / 1000).toFixed(1)}s.`,
    })

    autoModelAdvanceLockRef.current = true
    autoModelAdvanceTimerRef.current = window.setTimeout(() => {
      autoModelAdvanceTimerRef.current = null
      if (autoModelSequenceTokenRef.current !== token) {
        autoModelAdvanceLockRef.current = false
        return
      }

      const nextModel = availableModels.find((m) => m.model_id === nextModelId)
      setAutoModelSequenceIndex(nextIndex)
      setModelId(nextModelId)
      setParams(nextTuningSlot?.params ?? buildMicParamsWithSaved(nextModel, paramsByModel[nextModelId]))
      const nextMsg = nextTuningSlot
        ? `Ladění parametrů: trial ${nextIndex + 1}/${autoModelSequenceIds.length} (${nextModelId}, ${nextTuningSlot.variantLabel}, opak. ${nextTuningSlot.repeatIndex}/${nextTuningSlot.repeatTotal}).`
        : `Auto sekvence: model ${nextIndex + 1}/${autoModelSequenceIds.length} (${nextModelId}).`
      setSaveMsg(nextMsg)
      logSequenceEvent('client_sequence_trial_start_requested', {
        ...buildSequencePlanPayload(token, autoModelSequenceIds, autoModelSelectedOrdered, nextIndex, nextModelId),
        ui_message: nextMsg,
      })

      void startSession(nextModelId, {
        sequence_token: token,
        sequence_index: nextIndex,
        sequence_total: autoModelSequenceIds.length,
        queue_model_ids: autoModelSequenceIds,
        selected_model_ids: autoModelSelectedOrdered,
        tuning_series_id: tuningSweepSeriesIdRef.current,
        tuning_slot: nextTuningSlot,
      }).finally(() => {
        autoModelAdvanceLockRef.current = false
      })
    }, waitMs)
  }, [
    autoModelSequenceActive,
    autoModelSequenceIds,
    autoModelSequenceIndex,
    status,
    loopCycleS,
    autoModelLeadStartSeconds,
    autoModelSelectedOrdered,
    availableModels,
    paramsByModel,
    logSequenceEvent,
    buildSequencePlanPayload,
    startSession,
    stopAutoModelSequence,
    error,
  ])

  // Cleanup při unmount
  useEffect(() => () => {
    if (autoModelAdvanceTimerRef.current != null) {
      window.clearTimeout(autoModelAdvanceTimerRef.current)
      autoModelAdvanceTimerRef.current = null
    }
    const loopAudio = mobileLoopAudioRef.current
    if (loopAudio) {
      try { loopAudio.pause() } catch {}
      loopAudio.onended = null
      loopAudio.onerror = null
      mobileLoopAudioRef.current = null
    }
    _stopAudio()
    wsRef.current?.close()
  }, [])

  return (
    <div className="bg-gray-800 rounded-lg p-4 space-y-4">
      <h3 className="text-white font-semibold text-lg">Mic — live přepis</h3>

      <div className="rounded-lg border border-red-900/60 border-l-4 border-l-red-600 bg-red-950/10 p-3 flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-red-100">Test přepisu sekvencí</div>
            <div className="text-[11px] text-gray-400">
              Postupuj shora dolů: zdroj zvuku → audio plán → mikrofon a modely → auto sekvence/ladění → start.
            </div>
          </div>
          <span className="rounded-full border border-red-700 bg-red-950/50 px-2 py-1 text-[11px] text-red-200">
            ● Start sekvenci
          </span>
        </div>

      {/* Výběr modelu a parametrů */}
      <div className="order-3 rounded border border-gray-700 bg-gray-900/50 p-3 space-y-3">
        <div>
          <div className="text-sm font-semibold text-gray-100">3. Mikrofon a modely</div>
          <div className="text-[11px] text-gray-500">
            Vyber skutečný mikrofon, aktuální model a parametry, které se použijí pro ruční i sekvenční test.
          </div>
        </div>
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(260px,1fr)_minmax(240px,0.9fr)_minmax(380px,1.6fr)] gap-3 items-start">
        <div className="min-w-0">
          <label className="block text-xs text-gray-400 mb-1">Model</label>
          <select
            value={modelId}
            onChange={e => {
              const nextModelId = e.target.value
              setModelId(nextModelId)
              const nextModel = availableModels.find(m => m.model_id === nextModelId)
              setParams(buildMicParamsWithSaved(nextModel, paramsByModel[nextModelId]))
            }}
            disabled={uiLocked}
            className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-white"
          >
            {availableModels.map(m => (
              <option key={m.model_id} value={m.model_id}>
                {m.label} [{m.languages.join(', ')}]
              </option>
            ))}
          </select>
          <div className="mt-1 text-[11px] text-gray-500">
            Výchozí mic: VOSK `chunk=0.4, gain=+1`; whisper.cpp base `threads=8, beam=1, interval=2000 ms` (data 26. 4.).
          </div>
        </div>

        <div className="min-w-0">
          <label className="block text-xs text-gray-400 mb-1">Mikrofon</label>
          {devices.length > 0 ? (
            <select
              value={deviceIndex ?? ''}
              onChange={e => setDeviceIndex(e.target.value === '' ? null : Number(e.target.value))}
              disabled={uiLocked}
              className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-white"
            >
              <option value="">výchozí</option>
              {devices.map(d => (
                <option key={d.index} value={d.index}>{d.name}</option>
              ))}
            </select>
          ) : (
            <div className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-gray-400">nenačteno</div>
          )}
        </div>

        <div className="space-y-2 min-w-0">
          {selectedModel && selectedModel.params.length > 0 && (
            <details className="text-sm rounded border border-gray-700 bg-gray-900/40 px-3 py-2">
              <summary className="text-gray-300 cursor-pointer hover:text-gray-100 select-none">
                Parametry aktuálního modelu
              </summary>
              <div className="mt-2 border-l border-gray-700 pl-2">
                <ModelParamsForm
                  modelId={modelId}
                  params={selectedModel.params}
                  values={params}
                  onChange={updateModelParams}
                  compact
                  hints={MIC_PARAM_HINTS}
                  disabled={uiLocked}
                  recommendations={selectedModelRecommendedParams}
                  onApplyRecommended={(name, value) => updateModelParams({ ...params, [name]: value })}
                />
              </div>
            </details>
          )}

          {sequenceCommonParamSpecs.length > 0 && (
            <details className="text-sm rounded border border-gray-700 bg-gray-900/40 px-3 py-2">
              <summary className="text-gray-300 cursor-pointer hover:text-gray-100 select-none">
                Společné parametry sekvence
              </summary>
              <div className="mt-2 space-y-2">
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <label className="inline-flex items-center gap-2 text-gray-300">
                    <input
                      type="checkbox"
                      checked={sequenceCommonParamsEnabled}
                      onChange={(e) => {
                        markSequenceProfileManual()
                        setSequenceCommonParamsEnabled(e.target.checked)
                      }}
                      disabled={uiLocked}
                      className="accent-blue-500"
                    />
                    Použít společné hodnoty
                  </label>
                  <button
                    type="button"
                    onClick={() => updateSequenceCommonParams({
                      ...sequenceCommonParams,
                      ...pickModelSupportedParams(selectedModel, params),
                    })}
                    disabled={uiLocked || !selectedModel}
                    className="px-2 py-1 rounded border border-gray-700 text-gray-300 hover:text-white disabled:opacity-50"
                  >
                    Načíst z aktuálního
                  </button>
                  <button
                    type="button"
                    onClick={() => updateSequenceCommonParams({ ...sequenceCommonParams, ...sequenceCommonDefaultParams })}
                    disabled={uiLocked}
                    className="px-2 py-1 rounded border border-gray-700 text-gray-300 hover:text-white disabled:opacity-50"
                  >
                    Výchozí
                  </button>
                </div>
                <div className="border-l border-gray-700 pl-2">
                  <ModelParamsForm
                    modelId="__sequence_common__"
                    params={sequenceCommonParamSpecs}
                    values={sequenceCommonParams}
                    onChange={updateSequenceCommonParams}
                    compact
                    hints={MIC_PARAM_HINTS}
                    disabled={uiLocked}
                  />
                </div>
              </div>
            </details>
          )}

          {sequenceCommonParamSpecs.length > 0 && sequenceMatrixModels.length > 0 && (
            <details className="text-sm rounded border border-gray-700 bg-gray-900/40 px-3 py-2">
              <summary className="text-gray-300 cursor-pointer hover:text-gray-100 select-none">
                UI matice parametrů
              </summary>
              <div className="mt-2 space-y-2">
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="text-gray-400">Použít profil:</span>
                  {SEQUENCE_PARAM_PROFILES.map((profile) => (
                    <button
                      key={profile.id}
                      type="button"
                      onClick={() => applySequenceParamProfile(profile.id)}
                      disabled={uiLocked || sequenceMatrixModels.length === 0}
                      title={profile.description}
                      className="px-2 py-1 rounded border border-emerald-800 text-emerald-200 hover:text-white hover:border-emerald-600 disabled:opacity-50"
                    >
                      {profile.label}
                    </button>
                  ))}
                  {effectiveSequenceParamProfileLabel && (
                    <span className="rounded border border-gray-700 px-2 py-1 text-gray-300">
                      aktivní: {effectiveSequenceParamProfileLabel}
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-gray-500">
                  Profil vyplní každému vybranému modelu jen podporované parametry. Potom můžeš upravit sloupec `Všem`
                  nebo jednotlivé buňky jako výjimky.
                </div>
                <div className="overflow-x-auto">
                <table className="min-w-full text-[11px] text-gray-300 border-collapse">
                  <thead>
                    <tr className="border-b border-gray-700 text-gray-500">
                      <th className="text-left py-1 pr-3">Parametr</th>
                      <th
                        className="text-left py-1 pr-3"
                        title="Nastaví stejnou hodnotu všem podporovaným modelům; doporučení se použije pro každý model podle jeho vlastního profilu."
                      >
                        Všem
                      </th>
                      {sequenceMatrixModels.map((m) => (
                        <th key={m.model_id} className="text-left py-1 pr-3 max-w-[150px] truncate" title={m.label}>
                          {m.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sequenceCommonParamSpecs.map((param) => {
                      const bulkSpec = buildBulkParamSpec(param, sequenceMatrixModels)
                      const bulkModels = sequenceMatrixModels.filter((m) => (
                        m.params.some((modelParam) => modelParam.name === param.name)
                      ))
                      const bulkValues = bulkModels.map((m) => editableMatrixParamsByModel.get(m.model_id)?.[param.name])
                      const firstBulkValue = bulkValues[0]
                      const bulkMixed = bulkValues.length > 1 && bulkValues.some((value) => !sameParamValue(value, firstBulkValue))
                      const bulkRecommendedValues = bulkModels.map((m) => recommendedMatrixParamsByModel.get(m.model_id)?.[param.name])
                      const firstBulkRecommendedValue = bulkRecommendedValues[0]
                      const bulkRecommendedMixed = bulkRecommendedValues.length > 1
                        && bulkRecommendedValues.some((value) => !sameParamValue(value, firstBulkRecommendedValue))
                      return (
                        <tr key={param.name} className="border-b border-gray-800/80">
                          <td className="py-1 pr-3 text-gray-400 whitespace-nowrap" title={param.name}>
                            {param.label}
                          </td>
                          <td
                            className="py-1 pr-3 whitespace-nowrap text-gray-300"
                            title={`Nastaví ${param.name} všem vybraným modelům, které ho podporují (${bulkModels.length}/${sequenceMatrixModels.length}).`}
                          >
                            {bulkSpec ? (
                              <div className="space-y-1">
                                <BulkParamInput
                                  param={bulkSpec}
                                  value={firstBulkValue}
                                  mixed={bulkMixed}
                                  disabled={uiLocked || bulkModels.length === 0}
                                  onChange={(value) => updateMatrixParamForAll(param.name, value)}
                                />
                                <div className="flex items-center gap-1 text-[10px] text-emerald-300">
                                  <span title={bulkRecommendedMixed ? 'Doporučení se liší podle modelu.' : `Doporučeno: ${formatParamValue(firstBulkRecommendedValue)}`}>
                                    dop: {bulkRecommendedMixed ? 'dle modelů' : formatParamValue(firstBulkRecommendedValue)}
                                  </span>
                                  <button
                                    type="button"
                                    onClick={() => applyRecommendedMatrixParamForAll(param.name)}
                                    disabled={uiLocked || bulkModels.length === 0}
                                    className="rounded border border-emerald-800 px-1 py-0 text-[10px] text-emerald-200 hover:text-white disabled:opacity-50"
                                  >
                                    Použít
                                  </button>
                                </div>
                              </div>
                            ) : '—'}
                          </td>
                          {sequenceMatrixModels.map((m) => {
                            const modelParam = m.params.find((p) => p.name === param.name)
                            const supported = Boolean(modelParam)
                            const editableParams = editableMatrixParamsByModel.get(m.model_id)
                            const effectiveParams = effectiveMatrixParamsByModel.get(m.model_id)
                            const editableValue = editableParams?.[param.name]
                            const effectiveValue = effectiveParams?.[param.name]
                            const recommendedValue = recommendedMatrixParamsByModel.get(m.model_id)?.[param.name]
                            const commonUsed = sequenceCommonParamsEnabled
                              && Object.prototype.hasOwnProperty.call(sequenceCommonParams, param.name)
                              && supported
                            return (
                              <td
                                key={`${m.model_id}:${param.name}`}
                                className={`py-1 pr-3 whitespace-nowrap ${supported ? 'text-gray-200' : 'text-gray-600'}`}
                                title={supported ? `${param.name}=${formatParamValue(editableValue)}` : 'Model parametr nepodporuje'}
                              >
                                {modelParam ? (
                                  <div className="space-y-0.5">
                                    <ParamInput
                                      param={modelParam}
                                      value={editableValue}
                                      onChange={(value) => updateMatrixModelParam(m, modelParam.name, value)}
                                      compact
                                      disabled={uiLocked}
                                    />
                                    {commonUsed && (
                                      <div
                                        className="text-[10px] text-blue-300"
                                        title={`Při startu se použije společná hodnota ${formatParamValue(effectiveValue)}`}
                                      >
                                        společně: {formatParamValue(effectiveValue)}
                                      </div>
                                    )}
                                    <div className="flex items-center gap-1 text-[10px] text-emerald-300">
                                      <span title={`Doporučeno: ${formatParamValue(recommendedValue)}`}>
                                        dop: {formatParamValue(recommendedValue)}
                                      </span>
                                      <button
                                        type="button"
                                        onClick={() => updateMatrixModelParam(m, modelParam.name, recommendedValue)}
                                        disabled={uiLocked || sameParamValue(editableValue, recommendedValue)}
                                        className="rounded border border-emerald-800 px-1 py-0 text-[10px] text-emerald-200 hover:text-white disabled:opacity-50"
                                      >
                                        Použít
                                      </button>
                                    </div>
                                  </div>
                                ) : '—'}
                              </td>
                            )
                          })}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                </div>
              </div>
            </details>
          )}
        </div>
      </div>
      </div>

      <div className="order-4 bg-gray-900/50 border border-gray-700 rounded p-3 space-y-3">
        <div>
          <div className="text-sm font-semibold text-gray-100">4. Auto sekvence a ladění</div>
          <div className="text-[11px] text-gray-500">
            Tady se určí, které modely poběží za sebou a zda se kolem jejich nastavení spustí automatické ladění.
          </div>
        </div>
        <div className="flex items-center justify-between gap-2">
          <label className="text-xs text-gray-300 inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={autoModelCycleEnabled}
              onChange={e => setAutoModelCycleEnabled(e.target.checked)}
              disabled={uiLocked}
              className="accent-blue-500"
            />
            Auto střídání STT modelů (sekvenčně)
          </label>
          {autoModelSequenceActive && (
            <span className="text-[11px] text-blue-300">
              {tuningSweepActive ? 'Ladění' : 'Sekvence'} {Math.min(autoModelSequenceIndex + 1, autoModelSequenceIds.length)}/{autoModelSequenceIds.length}
              {' '}| uloženo {Math.min(autoModelSavedCount, autoModelSequenceIds.length)}/{autoModelSequenceIds.length}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-gray-400">Orchestrátor režim:</span>
          <button
            type="button"
            onClick={() => setOrchestratorMode('legacy_sequence')}
            disabled={uiLocked}
            className={`px-2 py-1 rounded border ${
              orchestratorMode === 'legacy_sequence'
                ? 'border-blue-500 text-blue-200 bg-blue-900/30'
                : 'border-gray-700 text-gray-300 hover:text-white'
            }`}
          >
            Legacy (stávající)
          </button>
          <button
            type="button"
            onClick={() => setOrchestratorMode('v7_cs_online')}
            disabled={uiLocked}
            className={`px-2 py-1 rounded border ${
              orchestratorMode === 'v7_cs_online'
                ? 'border-blue-500 text-blue-200 bg-blue-900/30'
                : 'border-gray-700 text-gray-300 hover:text-white'
            }`}
          >
            V7 (nový kontrakt)
          </button>
          <span className="text-gray-500">
            Legacy je výchozí; V7 přidá `run_id/sequence_id/global_timeline_ms`.
          </span>
        </div>
        {!autoModelCycleEnabled && (
          <div className="rounded border border-blue-900/70 bg-blue-950/20 px-3 py-2 text-[11px] text-blue-100">
            <div className="font-semibold">Výběr testovaných modelů a automatické ladění jsou schované, dokud není zapnutá auto sekvence.</div>
            <div className="mt-1 text-blue-200/80">
              Pro sérii testů zapni auto střídání. Potom se zobrazí seznam modelů, pořadí běhu a rozbalovací `Automatické ladění parametrů`.
            </div>
            <button
              type="button"
              onClick={() => setAutoModelCycleEnabled(true)}
              disabled={uiLocked}
              className="mt-2 rounded border border-blue-600 bg-blue-900/30 px-2 py-1 text-blue-100 hover:border-blue-400 hover:text-white disabled:opacity-60"
            >
              Zapnout výběr testovaných modelů
            </button>
          </div>
        )}
        {autoModelCycleEnabled && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setAutoModelSelectedIds(availableModels.map((m) => m.model_id))}
                disabled={uiLocked}
                className="px-2 py-1 border border-gray-700 rounded text-[11px] text-gray-300 hover:text-white disabled:opacity-60"
              >
                Vybrat všechny
              </button>
              <button
                type="button"
                onClick={() => setAutoModelSelectedIds(modelId ? [modelId] : [])}
                disabled={uiLocked}
                className="px-2 py-1 border border-gray-700 rounded text-[11px] text-gray-300 hover:text-white disabled:opacity-60"
              >
                Jen aktuální
              </button>
              {autoModelSequenceActive && (
                <button
                  type="button"
                  onClick={stop}
                  className="px-2 py-1 border border-red-700 rounded text-[11px] text-red-300 hover:text-red-200"
                >
                  Zastavit sekvenci
                </button>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <label className="text-[11px] text-gray-400">Max doběh po stop (s)</label>
              <input
                type="number"
                min={5}
                max={60}
                step={1}
                value={autoModelGraceSeconds}
                onChange={(e) => setAutoModelGraceSeconds(Math.max(5, Math.min(60, Math.floor(Number(e.target.value) || 15))))}
                disabled={uiLocked}
                className="w-20 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-[11px] text-white disabled:opacity-60"
              />
              <span className="text-[11px] text-gray-500">po překročení se model přeskočí</span>
              <label className="text-[11px] text-gray-400">Stop při mezeře (s)</label>
              <input
                type="number"
                min={2}
                max={60}
                step={1}
                value={autoModelSilenceStopSeconds}
                onChange={(e) => setAutoModelSilenceStopSeconds(Math.max(2, Math.min(60, Math.floor(Number(e.target.value) || 15))))}
                disabled={uiLocked}
                className="w-20 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-[11px] text-white disabled:opacity-60"
              />
              <span className="text-[11px] text-gray-500">
                bez nového textu, nejdřív po {autoModelMinSilenceStopElapsedS.toFixed(1)}s (75 % audia)
              </span>
              <span className="text-[11px] text-gray-500">
                hard cap trialu {autoModelHardTrialSeconds.toFixed(0)}s
                {loopAudioStartDelayS > 0 ? ` (včetně start audia +${loopAudioStartDelayS.toFixed(1)}s)` : ''}
              </span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-1">
              {autoModelDisplayOrdered.map((m) => {
                const checked = autoModelSelectedIds.includes(m.model_id)
                const selectedOrder = autoModelSelectionIndexById.get(m.model_id)
                return (
                  <label
                    key={m.model_id}
                    className={`inline-flex items-center gap-2 rounded border px-2 py-1 text-[11px] ${
                      checked ? 'border-blue-600 bg-blue-900/20 text-blue-100' : 'border-gray-700 text-gray-300'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleAutoModelSelection(m.model_id)}
                      disabled={uiLocked}
                      className="accent-blue-500"
                    />
                    <span className="truncate">{m.label}</span>
                    {selectedOrder != null && (
                      <span className="ml-auto rounded bg-blue-950/60 border border-blue-700 px-1.5 py-0 text-[10px] text-blue-200">
                        #{selectedOrder + 1}
                      </span>
                    )}
                  </label>
                )
              })}
            </div>
            <details
              open={tuningSweepEnabled}
              className="rounded border border-emerald-900/70 bg-emerald-950/10 px-3 py-2 text-[11px]"
            >
              <summary className="cursor-pointer select-none text-emerald-200 hover:text-emerald-100">
                Automatické ladění parametrů
              </summary>
              <div className="mt-2 space-y-2">
                <div className="flex flex-wrap items-center gap-3">
                  <label className="inline-flex items-center gap-2 text-gray-300">
                    <input
                      type="checkbox"
                      checked={tuningSweepEnabled}
                      onChange={(e) => setTuningSweepEnabled(e.target.checked)}
                      disabled={uiLocked}
                      className="accent-emerald-500"
                    />
                    Zapnout sweep kolem aktuálního nastavení
                  </label>
                  <label
                    className="inline-flex items-center gap-1 text-gray-400"
                    title="Kolikrát se zopakuje každá varianta nastavení (1 až 10). Více opakování lépe ověří stabilitu výsledku."
                  >
                    <span className="cursor-help underline decoration-dotted underline-offset-2">Opakování varianty</span>
                    <select
                      value={tuningSweepRepeatCount}
                      onChange={(e) => setTuningSweepRepeatCount(normalizeTuningSweepRepeatCount(Number(e.target.value)))}
                      disabled={uiLocked}
                      className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-gray-100"
                    >
                      {TUNING_SWEEP_REPEAT_OPTIONS.map((count) => (
                        <option key={count} value={count}>{count}×</option>
                      ))}
                    </select>
                  </label>
                  <label className="inline-flex items-center gap-1 text-gray-400">
                    Režim
                    <select
                      value={tuningSweepMode}
                      onChange={(e) => setTuningSweepMode(normalizeTuningSweepMode(e.target.value))}
                      disabled={uiLocked}
                      className="bg-gray-800 border border-gray-600 rounded px-2 py-1 text-gray-100"
                    >
                      {TUNING_SWEEP_MODES.map((mode) => (
                        <option key={mode.id} value={mode.id}>{mode.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="inline-flex items-center gap-1 text-gray-400">
                    Velikost kroku
                    <input
                      type="number"
                      min={0.25}
                      max={4}
                      step={0.25}
                      value={tuningSweepStepSize}
                      onChange={(e) => setTuningSweepStepSize(normalizeTuningSweepStepSize(Number(e.target.value)))}
                      disabled={uiLocked}
                      className="w-16 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-gray-100"
                    />
                    <span className="text-gray-500">×</span>
                  </label>
                  <div className="inline-flex overflow-hidden rounded border border-gray-700">
                    {[0.5, 1, 2].map((step) => (
                      <button
                        key={step}
                        type="button"
                        onClick={() => setTuningSweepStepSize(step)}
                        disabled={uiLocked}
                        className={`px-2 py-1 text-[11px] ${
                          sameParamValue(tuningSweepStepSize, step)
                            ? 'bg-emerald-900/50 text-emerald-100'
                            : 'bg-gray-900 text-gray-400 hover:text-white'
                        } disabled:opacity-60`}
                      >
                        {step}×
                      </button>
                    ))}
                  </div>
                  <label
                    className="inline-flex items-center gap-1 text-gray-400"
                    title="Limit pro vyhodnocení zpoždění: když průměr prvního slova překročí tuto hodnotu, varianta se označí jako zpožděná. Trial tím sám nezastavuje."
                  >
                    <span className="cursor-help underline decoration-dotted underline-offset-2">Max lag (s)</span>
                    <input
                      type="number"
                      min={1}
                      max={60}
                      step={1}
                      value={tuningSweepMaxLagSeconds}
                      onChange={(e) => setTuningSweepMaxLagSeconds(Math.max(1, Math.min(60, Number(e.target.value) || 15)))}
                      disabled={uiLocked}
                      className="w-16 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-gray-100"
                    />
                  </label>
                </div>
                <div className="rounded border border-gray-800 bg-gray-950/40 p-2 space-y-2">
                  <div className="flex flex-wrap items-center gap-3">
                    <label
                      className="inline-flex items-center gap-2 text-gray-300"
                      title="Místo pevných předvoleb vybereš přesné parametry a rozsahy hodnot. Každý model použije jen parametry, které podporuje."
                    >
                      <input
                        type="checkbox"
                        checked={tuningSweepCustomRangesEnabled}
                        onChange={(e) => setTuningSweepCustomRangesEnabled(e.target.checked)}
                        disabled={uiLocked}
                        className="accent-emerald-500"
                      />
                      Vlastní rozsahy parametrů
                    </label>
                    <button
                      type="button"
                      onClick={applyRecommendedTuningRanges}
                      disabled={uiLocked || tuningSweepRangeParamSpecs.length === 0}
                      className="rounded border border-emerald-800 px-2 py-1 text-[11px] text-emerald-200 hover:text-white disabled:opacity-50"
                    >
                      Doporučené rozsahy
                    </button>
                    <button
                      type="button"
                      onClick={disableAllTuningRanges}
                      disabled={uiLocked || tuningSweepRangeParamSpecs.length === 0}
                      className="rounded border border-gray-700 px-2 py-1 text-[11px] text-gray-300 hover:text-white disabled:opacity-50"
                    >
                      Vypnout parametry
                    </button>
                    <span className="text-gray-500">
                      {tuningSweepCustomRangesEnabled
                        ? `aktivní parametry: ${tuningSweepEnabledRangeCount}`
                        : 'používají se pevné předvolby režimu'}
                    </span>
                  </div>
                  {tuningSweepCustomRangesEnabled && (
                    tuningSweepEnabledRangeCount === 0 && (
                      <div className="rounded border border-amber-800 bg-amber-950/30 px-2 py-1 text-[11px] text-amber-200">
                        {TUNING_CUSTOM_RANGE_NO_PARAM_HINT}
                      </div>
                    )
                  )}
                  {tuningSweepCustomRangesEnabled && (
                    tuningSweepRangeRows.length === 0 ? (
                      <div className="text-gray-500">Vybrané modely nemají žádné číselné parametry pro rozsahové ladění.</div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="min-w-full border-collapse text-[11px] text-gray-300">
                          <thead>
                            <tr className="border-b border-gray-800 text-gray-500">
                              <th className="py-1 pr-3 text-left">Parametr</th>
                              <th className="py-1 pr-3 text-left">Zapnout</th>
                              <th className="py-1 pr-3 text-left">Od</th>
                              <th className="py-1 pr-3 text-left">Do</th>
                              <th className="py-1 pr-3 text-left">Krok</th>
                              <th className="py-1 pr-3 text-left">Hodnoty</th>
                              <th className="py-1 text-left">Modely</th>
                            </tr>
                          </thead>
                          <tbody>
                            {tuningSweepRangeRows.map(({ param, config, values, supportedModels, tooltip }) => {
                              const inputStep = param.type === 'int' ? 1 : 0.01
                              return (
                                <tr key={param.name} className="border-b border-gray-900/80">
                                  <td className="py-1 pr-3 text-gray-200 whitespace-nowrap" title={tooltip}>
                                    {param.label}
                                    <div className="text-[10px] text-gray-500">{param.name}</div>
                                  </td>
                                  <td className="py-1 pr-3">
                                    <input
                                      type="checkbox"
                                      checked={config.enabled}
                                      onChange={(e) => updateTuningRangeConfig(param.name, { enabled: e.target.checked })}
                                      disabled={uiLocked || supportedModels.length === 0}
                                      className="accent-emerald-500"
                                    />
                                  </td>
                                  <td className="py-1 pr-3">
                                    <input
                                      type="number"
                                      step={inputStep}
                                      value={config.from}
                                      onChange={(e) => updateTuningRangeConfig(param.name, { from: Number(e.target.value) })}
                                      disabled={uiLocked || !config.enabled || supportedModels.length === 0}
                                      className="w-20 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-gray-100 disabled:opacity-50"
                                    />
                                  </td>
                                  <td className="py-1 pr-3">
                                    <input
                                      type="number"
                                      step={inputStep}
                                      value={config.to}
                                      onChange={(e) => updateTuningRangeConfig(param.name, { to: Number(e.target.value) })}
                                      disabled={uiLocked || !config.enabled || supportedModels.length === 0}
                                      className="w-20 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-gray-100 disabled:opacity-50"
                                    />
                                  </td>
                                  <td className="py-1 pr-3">
                                    <input
                                      type="number"
                                      min={param.type === 'int' ? 1 : 0.001}
                                      step={inputStep}
                                      value={config.step}
                                      onChange={(e) => updateTuningRangeConfig(param.name, { step: Number(e.target.value) })}
                                      disabled={uiLocked || !config.enabled || supportedModels.length === 0}
                                      className="w-20 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-gray-100 disabled:opacity-50"
                                    />
                                  </td>
                                  <td className="py-1 pr-3 text-gray-400">
                                    {config.enabled ? values.map((value) => formatParamValue(value)).join(', ') || '—' : '—'}
                                  </td>
                                  <td className="py-1 text-gray-400 max-w-[260px] truncate" title={supportedModels.map((m) => m.label).join(', ')}>
                                    {supportedModels.length > 0
                                      ? `${supportedModels.length}/${sequenceMatrixModels.length}: ${supportedModels.map((m) => m.label).join(', ')}`
                                      : 'žádný vybraný model nepodporuje'}
                                  </td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>
                    )
                  )}
                  {tuningSweepCustomRangesEnabled && (
                    <div className="text-[10px] text-gray-500">
                      Plán dělá jednoparametrové varianty proti baseline. Kombinace více změn najednou se záměrně negenerují, aby počet trialů zůstal kontrolovatelný.
                    </div>
                  )}
                </div>
                <div className="text-gray-400">
                  Plán: <strong className="text-gray-200">{tuningSweepPreviewPlan.length}</strong> trialů,
                  modelů {sequenceMatrixModels.length},
                  {tuningSweepCustomRangesEnabled
                    ? ` vlastní rozsahy (${tuningSweepEnabledRangeCount} parametrů)`
                    : ` krok ${tuningSweepStepSize}×`},
                  odhad {formatDurationHms(tuningSweepEstimatedSeconds)}.
                  {tuningSweepSeriesId ? ` Série: ${tuningSweepSeriesId}.` : ''}
                </div>
                {mobileLoopEnabled && loopRepeatCount < tuningSweepPreviewPlan.length && tuningSweepEnabled && (
                  <div className="rounded border border-amber-800 bg-amber-950/30 px-2 py-1 text-amber-200">
                    Audio loop má v UI {loopRepeatCount} měřených kol, plán ladění má {tuningSweepPreviewPlan.length} trialů.
                    Pro fyzický test vytvoř nebo pusť audio s alespoň {tuningSweepPreviewPlan.length} koly.
                  </div>
                )}
                {tuningSweepVariantPreview.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="min-w-full border-collapse text-[11px] text-gray-300">
                      <thead>
                        <tr className="border-b border-gray-800 text-gray-500">
                          <th className="py-1 pr-3 text-left">Model</th>
                          <th className="py-1 pr-3 text-left">Varianta</th>
                          <th className="py-1 pr-3 text-left">Změna proti baseline</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tuningSweepVariantPreview.map((slot) => (
                          <tr key={`${slot.modelId}:${slot.variantId}`} className="border-b border-gray-900/80">
                            <td className="py-1 pr-3 text-gray-200">{slot.modelId}</td>
                            <td className="py-1 pr-3 text-emerald-300">{slot.variantLabel}</td>
                            <td className="py-1 pr-3 text-gray-400">
                              {formatParamsSummary(slot.changedParams, 6) || 'beze změny'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </details>
            <div className="text-[11px] text-gray-400">
              Pořadí běhu: podle pořadí naklikání modelů. Vybráno: {autoModelSelectedOrdered.length}.
              {tuningSweepEnabled ? ` Ladění rozšíří frontu na ${tuningSweepPreviewPlan.length} trialů.` : ''}
              Audio kola: {loopRepeatCount}. Slot: {formatDurationHms(autoModelSlotSeconds)} (řeč {Math.round(loopSpeechS)}s + pauza {loopPauseS}s),
              sběr řeči: ~{Math.round(loopCaptureSpeechS)}s (konec dříve o {loopEarlyStopS.toFixed(1)}s),
              další start ~{autoModelLeadStartSeconds.toFixed(1)}s před slotem, max doběh {autoModelGraceSeconds}s.
              Sekvence STT spustí jen vybrané modely; audio kola navíc se nepřepisují.
              Stop má pevnou přípravu {autoModelPreparationSeconds}s + hard cap {autoModelHardTrialSeconds.toFixed(0)}s/trial.
              {loopAudioStartDelayS > 0 ? ` Základ hard capu po startu audia: ${autoModelHardTrialBaseSeconds.toFixed(0)}s.` : ''}
              Při latenci prvního slova nad {autoModelLatencyGuardSeconds}s se trial zkrátí ještě víc (adaptivně).
              {(tuningSweepEnabled ? tuningSweepPreviewPlan.length : autoModelSelectedOrdered.length) !== loopRepeatCount && (
                <span className="block text-amber-300">
                  Pozor: počet plánovaných trialů ({tuningSweepEnabled ? tuningSweepPreviewPlan.length : autoModelSelectedOrdered.length}) se liší od počtu audio kol ({loopRepeatCount}).
                </span>
              )}
            </div>
          </>
        )}
      </div>

      <div className="order-1 bg-gray-900/60 border border-gray-700 rounded p-3 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-gray-100">1. Zdroj zvuku a reference</div>
            <div className="text-[11px] text-gray-500">
              Volný mic test používá přímou řeč do mikrofonu; referenční video slouží jako řízený externí zdroj zvuku.
            </div>
          </div>
          <span className="rounded border border-gray-700 px-2 py-1 text-[11px] text-gray-300">
            {testMode === 'reference_video' ? 'externí audio → mikrofon' : 'interní řeč → mikrofon'}
          </span>
        </div>
        <div className="inline-flex rounded border border-gray-600 overflow-hidden text-xs">
          <button
            type="button"
            onClick={() => setTestMode('free_speech')}
            disabled={uiLocked}
            className={`px-3 py-1 ${testMode === 'free_speech' ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'} disabled:opacity-60`}
          >
            Volný mic test
          </button>
          <button
            type="button"
            onClick={() => setTestMode('reference_video')}
            disabled={uiLocked}
            className={`px-3 py-1 border-l border-gray-600 ${testMode === 'reference_video' ? 'bg-red-600 text-white' : 'bg-red-900/30 text-red-200 hover:bg-red-800/40'} disabled:opacity-60`}
          >
            Referenční video
          </button>
        </div>
        {testMode === 'free_speech' && (
          <div className="space-y-2">
            <div className="flex items-center gap-4 text-xs">
              <p className="text-gray-400">Mluv přímo do mikrofonu. Bez referenčního videa.</p>
              <div className="text-gray-500">Vyber text, který právě čteš. Výsledek se uloží s tímto odkazem.</div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
              {referenceTexts.map(ref => (
                <label key={ref.id} className={`rounded border p-2 cursor-pointer h-full flex flex-col ${selectedReferenceTextId === ref.id ? 'border-blue-500 bg-blue-900/20' : 'border-gray-700 bg-gray-900'}`}>
                  <div className="flex items-center justify-between mb-1">
                    <div className="text-[11px] text-gray-400">{ref.label}</div>
                    <input
                      type="radio"
                      name="mic_reference_text"
                      value={ref.id}
                      checked={selectedReferenceTextId === ref.id}
                      onChange={() => setSelectedReferenceTextId(ref.id)}
                      className="accent-blue-500"
                      disabled={uiLocked}
                    />
                  </div>
                  {ref.editable ? (
                    <textarea
                      value={ref.text}
                      onChange={e => {
                        if (ref.id === 'custom_1') setCustomReferenceText1(e.target.value)
                        if (ref.id === 'custom_2') setCustomReferenceText2(e.target.value)
                      }}
                      placeholder="Sem napiš vlastní referenční text pro čtení do mic."
                      disabled={uiLocked}
                      className="w-full flex-1 min-h-0 bg-gray-950 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 resize-none"
                    />
                  ) : (
                    <pre className="text-sm text-gray-100 leading-relaxed whitespace-pre-wrap font-sans flex-1">{ref.text}</pre>
                  )}
                </label>
              ))}
            </div>
          </div>
        )}
        {testMode === 'reference_video' && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2 items-end">
              <div>
                <label className="block text-xs text-gray-400 mb-1">Video ř. dle knihovny</label>
                <select
                  value={referenceVideoId}
                  onChange={e => setReferenceVideoId(e.target.value)}
                  disabled={uiLocked}
                  className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-xs text-white min-w-[280px]"
                >
                  <option value="">vyber video</option>
                  {referenceLibrary.map(v => (
                    <option key={v.video_id} value={v.video_id}>
                      {videoLabel(v.title, v.video_id)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Od (s)</label>
                <input
                  type="number"
                  min={0}
                  value={referenceClipFromS}
                  onFocus={() => setDurationAnchor('from')}
                  onChange={e => {
                    setDurationAnchor('from')
                    setReferenceClipFromS(Math.max(0, Number(e.target.value) || 0))
                  }}
                  disabled={uiLocked}
                  className="w-24 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-xs text-white"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Do (s)</label>
                <input
                  type="number"
                  min={1}
                  value={referenceClipToS}
                  onFocus={() => setDurationAnchor('to')}
                  onChange={e => {
                    setDurationAnchor('to')
                    setReferenceClipToS(Math.max(1, Number(e.target.value) || 1))
                  }}
                  disabled={uiLocked}
                  className="w-24 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-xs text-white"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-400 mb-1">Délka (s)</label>
                <input
                  type="number"
                  min={1}
                  step={0.1}
                  value={clipDurationS.toFixed(1)}
                  onChange={e => applyClipDuration(Number(e.target.value))}
                  disabled={uiLocked}
                  className="w-24 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-xs text-white"
                />
                <div className="mt-1 text-[10px] text-gray-500">
                  Kotva: {durationAnchor === 'from' ? 'Od (s)' : 'Do (s)'}
                </div>
              </div>
              {referenceVideoId && (
                <button
                  type="button"
                  onClick={() => void api.openDir.subtitlesVideo(referenceVideoId)}
                  className="px-2 py-1 border border-gray-600 rounded text-xs text-gray-300 hover:text-white hover:border-gray-400"
                  title="Otevřít složku s titulky vybraného videa"
                >
                  📁 Titulky
                </button>
              )}
            </div>
            <p className="text-xs text-gray-400">
              Pusť z mobilu vybrané video od {clipFromS.toFixed(1)}s do {clipToS.toFixed(1)}s.
              {' '}Sběr lze ukončit dříve o {loopEarlyStopS.toFixed(1)}s.
              {selectedReferenceVideo ? ` (${videoLabel(selectedReferenceVideo.title, selectedReferenceVideo.video_id)})` : ''}
            </p>
            <div className="rounded border border-gray-700 border-l-4 border-l-amber-500 bg-gray-900/70 p-2 space-y-2">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-xs text-amber-200 font-semibold">2. Tvorba audio loop balíčku</div>
                  <div className="text-[11px] text-gray-500">
                    Audio plán určuje, co se má z externího zdroje přehrát a jak se podle toho nastaví sekvenční test.
                  </div>
                </div>
                <label className="text-xs text-gray-300 inline-flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={mobileLoopEnabled}
                    onChange={e => setMobileLoopEnabled(e.target.checked)}
                    disabled={uiLocked}
                    className="accent-blue-500"
                  />
                  Zapnuto
                </label>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-7 gap-2">
                <div className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5">
                  <div className="text-[11px] text-gray-400">Pasáž (od-do)</div>
                  <div className="text-xs text-gray-200">{clipFromS.toFixed(1)}s → {clipToS.toFixed(1)}s</div>
                </div>
                <div>
                  <label className="block text-[11px] text-gray-400 mb-1">Konec dříve (s)</label>
                  <input
                    type="number"
                    min={0}
                    max={Math.max(0, Math.floor(loopEarlyStopMaxS))}
                    step={0.5}
                    value={mobileLoopEarlyStopSeconds}
                    onChange={e => setMobileLoopEarlyStopSeconds(Math.max(0, Math.min(loopEarlyStopMaxS, Number(e.target.value) || 0)))}
                    disabled={!mobileLoopEnabled || uiLocked}
                    className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs text-white disabled:opacity-60"
                  />
                </div>
                <div>
                  <label className="block text-[11px] text-gray-400 mb-1">Pauza (s)</label>
                  <input
                    type="number"
                    min={0}
                    max={3600}
                    value={mobileLoopPauseSeconds}
                    onChange={e => setMobileLoopPauseSeconds(Math.max(0, Math.min(3600, Number(e.target.value) || 15)))}
                    disabled={!mobileLoopEnabled || uiLocked}
                    className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs text-white disabled:opacity-60"
                  />
                </div>
                <div>
                  <label className="block text-[11px] text-gray-400 mb-1">Start audia + (s)</label>
                  <input
                    type="number"
                    min={0}
                    max={30}
                    step={0.5}
                    value={mobileLoopAudioStartDelaySeconds}
                    onChange={e => applyMobileLoopAudioStartDelay(Number(e.target.value) || 0, 'manual_input')}
                    disabled={!mobileLoopEnabled || (uiLocked && status !== 'recording')}
                    className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs text-white disabled:opacity-60"
                  />
                  <button
                    type="button"
                    onClick={markMobileLoopAudioStartedNow}
                    disabled={!mobileLoopEnabled || status !== 'recording'}
                    className="mt-1 w-full rounded border border-blue-700 bg-blue-950/40 px-2 py-1 text-[11px] text-blue-100 hover:border-blue-500 disabled:opacity-50"
                    title="Klikni v okamžiku, kdy z mobilu reálně začne hrát testovací audio."
                  >
                    Audio začalo teď
                  </button>
                </div>
                <div>
                  <label className="block text-[11px] text-gray-400 mb-1">Opakování</label>
                  <input
                    type="number"
                    min={1}
                    max={200}
                    value={mobileLoopRepeatCount}
                    onChange={e => setMobileLoopRepeatCount(Math.max(1, Math.min(200, Math.floor(Number(e.target.value) || 1))))}
                    disabled={!mobileLoopEnabled || uiLocked}
                    className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs text-white disabled:opacity-60"
                  />
                </div>
                <label className="text-xs text-gray-300 inline-flex items-center gap-2 mt-5">
                  <input
                    type="checkbox"
                    checked={mobileLoopSyncFirstRound}
                    onChange={e => setMobileLoopSyncFirstRound(e.target.checked)}
                    disabled={!mobileLoopEnabled || uiLocked}
                    className="accent-blue-500"
                  />
                  1. kolo jen sync
                </label>
                <label className="text-xs text-gray-300 inline-flex items-center gap-2 mt-5">
                  <input
                    type="checkbox"
                    checked={mobileLoopAutoStop}
                    onChange={e => setMobileLoopAutoStop(e.target.checked)}
                    disabled={!mobileLoopEnabled || uiLocked}
                    className="accent-blue-500"
                  />
                  Auto-stop po plánu
                </label>
              </div>
              <div className="flex flex-wrap gap-2 items-center">
                <button
                  type="button"
                  onClick={() => void createMobileLoopPackage()}
                  disabled={!mobileLoopEnabled || uiLocked || mobileLoopPackageLoading || !referenceVideoId}
                  className="px-2 py-1 border border-blue-600 rounded text-xs text-blue-200 hover:text-white hover:border-blue-400 disabled:opacity-50"
                >
                  {mobileLoopPackageLoading ? 'Generuji...' : '⬇ Vytvořit soubor pro mobil'}
                </button>
                <button
                  type="button"
                  onClick={() => void createMobileLoopPackageForCurrentTest()}
                  disabled={uiLocked || mobileLoopPackageLoading || !referenceVideoId || plannedAudioPackageTrialCount > 200}
                  className="px-2 py-1 border border-amber-700 rounded text-xs text-amber-200 hover:text-white hover:border-amber-500 disabled:opacity-50"
                  title={`Nastaví opakování na ${plannedAudioPackageTrialCount}x podle aktuální sekvence/ladění a vytvoří balíček bez názvů modelů.`}
                >
                  Balíček podle testu ({plannedAudioPackageTrialCount}x)
                </button>
                <button
                  type="button"
                  onClick={() => void playMobileLoopAudioPlan('manual')}
                  disabled={!mobileLoopEnabled || mobileLoopPackageLoading || !referenceVideoId || status === 'connecting' || status === 'stopping'}
                  className="px-2 py-1 border border-emerald-700 rounded text-xs text-emerald-200 hover:text-white hover:border-emerald-500 disabled:opacity-50"
                >
                  ▶ Přehrát audio plán
                </button>
                <button
                  type="button"
                  onClick={() => stopMobileLoopAudioPlayback()}
                  disabled={!mobileLoopAudioPlaying}
                  className="px-2 py-1 border border-gray-600 rounded text-xs text-gray-200 hover:text-white hover:border-gray-400 disabled:opacity-50"
                >
                  ■ Zastavit audio
                </button>
                <label
                  className="inline-flex items-center gap-2 text-xs text-gray-300"
                  title="Při kliknutí na Start sekvenci se přehraje aktuální audio plán z prohlížeče."
                >
                  <input
                    type="checkbox"
                    checked={mobileLoopAutoPlayOnSequenceStart}
                    onChange={e => setMobileLoopAutoPlayOnSequenceStart(e.target.checked)}
                    disabled={!mobileLoopEnabled || uiLocked}
                    className="accent-emerald-500"
                  />
                  Spustit audio automaticky při Start sekvenci
                </label>
                {mobileLoopPackage && (
                  <>
                    <a
                      href={mobileLoopPackage.download_url}
                      className="px-2 py-1 border border-gray-600 rounded text-xs text-gray-200 hover:text-white"
                    >
                      Stáhnout ZIP
                    </a>
                    <a
                      href={mobileLoopPackage.wav_url}
                      className="px-2 py-1 border border-gray-600 rounded text-xs text-gray-200 hover:text-white"
                    >
                      Stáhnout WAV
                    </a>
                  </>
                )}
              </div>
              {mobileLoopAudioStatus && (
                <div className={`text-[11px] ${mobileLoopAudioPlaying ? 'text-emerald-300' : 'text-gray-400'}`}>
                  {mobileLoopAudioStatus}
                  {mobileLoopPackage && !mobileLoopPackageMatchesCurrentPlan ? ' Aktuální formulář se liší od posledního balíčku; přehrání vytvoří nový.' : ''}
                </div>
              )}
              {mobileLoopEnabled && (
                <div className="text-[11px] text-gray-400">
                  Plán: {mobileLoopSyncFirstRound ? '1 sync kolo + ' : ''}{loopRepeatCount} měřené kolo(a),
                  režim {loopSpeechS.toFixed(1)}-{loopEarlyStopS.toFixed(1)}+{loopPauseS}s
                  {' '}=&gt; sběr {loopCaptureSpeechS.toFixed(1)}s + pauza {loopPauseS}s, start audia +{loopAudioStartDelayS.toFixed(1)}s, slot {loopCycleS.toFixed(1)}s, celkem {formatDurationHms(loopPlanS + loopAudioStartDelayS)}.
                </div>
              )}
              {mobileLoopPackageError && (
                <div className="text-xs text-red-300">
                  {mobileLoopPackageError}
                </div>
              )}
              {mobileLoopPackage && (
                <div className="rounded border border-gray-700 bg-gray-950/60 p-2 text-[11px] text-gray-300 space-y-1">
                  <div>
                    {mobileLoopPackage.pairing_code && (
                      <>
                        Kód <span className="text-emerald-300 font-mono">{mobileLoopPackage.pairing_code}-{mobileLoopPackage.measured_rounds}x</span>,
                        {' '}
                      </>
                    )}
                    Balíček <span className="text-gray-100 font-mono">{mobileLoopPackage.package_id}</span>,
                    délka {mobileLoopPackage.total_duration_s.toFixed(1)}s,
                    kola {mobileLoopPackage.total_rounds} (sync {mobileLoopPackage.sync_rounds} + měřená {mobileLoopPackage.measured_rounds}).
                  </div>
                  {mobileLoopPackage.reference_excerpt && (
                    <div className="text-gray-400">
                      Reference: {clipText(mobileLoopPackage.reference_excerpt, 260)}
                    </div>
                  )}
                </div>
              )}
              {mobileLoopEnabled && (
                <div className="rounded border border-gray-700 bg-gray-950/50 p-2 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[11px] text-gray-300 font-semibold">
                      Historie loop balíčků (specifikace + reference)
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-1">
                      <input
                        type="text"
                        value={mobileLoopPairingCodeInput}
                        onChange={(e) => setMobileLoopPairingCodeInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') void applyMobileLoopPackageByPairingCode()
                        }}
                        disabled={uiLocked}
                        placeholder="kód balíčku"
                        maxLength={32}
                        className="w-28 rounded border border-gray-700 bg-gray-900 px-2 py-1 text-[11px] text-gray-100 placeholder:text-gray-600 disabled:opacity-60"
                        title="Zadej 6místný kód z názvu balíčku, např. aB3dE9 nebo aB3dE9-30x."
                      />
                      <button
                        type="button"
                        onClick={() => void applyMobileLoopPackageByPairingCode()}
                        disabled={mobileLoopHistoryLoading || uiLocked}
                        className="px-2 py-1 border border-emerald-800 rounded text-[11px] text-emerald-200 hover:text-white disabled:opacity-60"
                      >
                        Použít kód
                      </button>
                      <button
                        type="button"
                        onClick={() => void loadMobileLoopHistory()}
                        disabled={mobileLoopHistoryLoading || uiLocked}
                        className="px-2 py-1 border border-gray-700 rounded text-[11px] text-gray-300 hover:text-white disabled:opacity-60"
                      >
                        {mobileLoopHistoryLoading ? 'Načítám...' : '↻ Obnovit'}
                      </button>
                    </div>
                  </div>
                  {mobileLoopHistoryError && (
                    <div className="text-[11px] text-red-300">
                      {mobileLoopHistoryError}
                    </div>
                  )}
                  {mobileLoopHistory.length === 0 ? (
                    <div className="text-[11px] text-gray-500">Zatím bez balíčku.</div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="min-w-full text-[11px] text-gray-300">
                        <thead>
                          <tr className="text-gray-500 border-b border-gray-800">
                            <th className="text-left py-1 pr-2">Čas</th>
                            <th className="text-left py-1 pr-2">Balíček</th>
                            <th className="text-left py-1 pr-2">Video</th>
                            <th className="text-left py-1 pr-2">Specifikace</th>
                            <th className="text-left py-1 pr-2">Reference</th>
                            <th className="text-left py-1">Akce</th>
                          </tr>
                        </thead>
                        <tbody>
                          {mobileLoopHistoryVisibleRows.map((pkg) => {
                            const isExpanded = expandedMobileLoopPackageIds.includes(pkg.package_id)
                            return (
                              <Fragment key={pkg.package_id}>
                                <tr className="border-b border-gray-800/80 align-top">
                                  <td className="py-1 pr-2 whitespace-nowrap text-gray-400">
                                    {formatDateTimeMedium(pkg.created_at)}
                                  </td>
                                  <td className="py-1 pr-2 font-mono text-gray-200">
                                    {pkg.pairing_code && (
                                      <div className="text-emerald-300">
                                        {pkg.pairing_code}-{pkg.measured_rounds}x
                                      </div>
                                    )}
                                    <div>{pkg.package_id}</div>
                                  </td>
                                  <td className="py-1 pr-2">
                                    <div>{videoLabel(pkg.video_title || pkg.video_id, pkg.video_id)}</div>
                                  </td>
                                  <td className="py-1 pr-2">
                                    <div>
                                      {pkg.clip_from_s.toFixed(1)}-{pkg.clip_to_s.toFixed(1)}s
                                      {' | '}pauza {pkg.pause_s.toFixed(1)}s
                                      {' | '}měř. {pkg.measured_rounds}
                                      {' | '}sync {pkg.sync_rounds}
                                    </div>
                                    <div className="text-gray-500">celkem {pkg.total_duration_s.toFixed(1)}s</div>
                                  </td>
                                  <td className="py-1 pr-2 max-w-[260px]">
                                    {pkg.reference_excerpt ? clipText(pkg.reference_excerpt, 120) : '—'}
                                  </td>
                                  <td className="py-1 whitespace-nowrap">
                                    <div className="flex flex-wrap gap-1">
                                      <button
                                        type="button"
                                        onClick={() => applyMobileLoopPackageToForm(pkg)}
                                        className="px-2 py-0.5 border border-blue-600 rounded text-blue-200 hover:text-white hover:border-blue-400"
                                        title="Nastavit parametry z tohoto balíčku pro nový test"
                                      >
                                        Použít
                                      </button>
                                      <a
                                        href={pkg.download_url}
                                        className={`px-2 py-0.5 border rounded ${pkg.zip_exists ? 'border-gray-700 text-gray-200 hover:text-white' : 'border-gray-800 text-gray-500 pointer-events-none'}`}
                                      >
                                        ZIP
                                      </a>
                                      <a
                                        href={pkg.wav_url}
                                        className={`px-2 py-0.5 border rounded ${pkg.wav_exists ? 'border-gray-700 text-gray-200 hover:text-white' : 'border-gray-800 text-gray-500 pointer-events-none'}`}
                                      >
                                        WAV
                                      </a>
                                      <button
                                        type="button"
                                        onClick={() => toggleMobileLoopPackageDetails(pkg.package_id)}
                                        className="px-2 py-0.5 border border-gray-700 rounded text-gray-300 hover:text-white"
                                      >
                                        {isExpanded ? 'Skrýt' : 'Detail'}
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => { void deleteMobileLoopPackage(pkg.package_id) }}
                                        disabled={mobileLoopHistoryDeletingId === pkg.package_id || uiLocked}
                                        className="px-2 py-0.5 border border-red-700 rounded text-red-300 hover:text-red-200 disabled:opacity-60"
                                      >
                                        {mobileLoopHistoryDeletingId === pkg.package_id ? 'Mažu...' : 'Smazat'}
                                      </button>
                                    </div>
                                  </td>
                                </tr>
                                {isExpanded && (
                                  <tr className="border-b border-gray-800/80">
                                    <td colSpan={6} className="py-2 pr-1">
                                      <div className="rounded border border-gray-700 bg-gray-950/70 p-2 space-y-2">
                                        <div>
                                          <div className="text-gray-500 mb-1">Reference (z balíčku)</div>
                                          <div className="whitespace-pre-wrap text-gray-100">
                                            {pkg.reference_excerpt || '—'}
                                          </div>
                                        </div>
                                        <div>
                                          <div className="text-gray-500 mb-1">Instrukce / specifikace</div>
                                          <div className="whitespace-pre-wrap text-gray-300">
                                            {pkg.instructions?.trim() || '—'}
                                          </div>
                                        </div>
                                      </div>
                                    </td>
                                  </tr>
                                )}
                              </Fragment>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {hiddenMobileLoopHistoryCount > 0 && (
                    <div>
                      <button
                        type="button"
                        onClick={() => setShowAllMobileLoopHistory((v) => !v)}
                        className="text-[11px] px-2 py-1 rounded border border-gray-700 text-gray-300 hover:text-white"
                      >
                        {showAllMobileLoopHistory
                          ? 'Skrýt starší balíčky'
                          : `Zobrazit dalších ${hiddenMobileLoopHistoryCount} balíčků`}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Ovládání */}
      <div className="order-5 rounded border border-red-900/50 bg-red-950/10 p-3 space-y-3">
        <div>
          <div className="text-sm font-semibold text-red-100">5. Spuštění</div>
          <div className="text-[11px] text-gray-500">
            Start použije všechny výše nastavené části: zdroj/reference, audio plán, mikrofon, modely i případné ladění.
          </div>
        </div>
      <div className="flex gap-2 items-center">
        {status === 'idle' || status === 'done' || status === 'error' ? (
          <button
            onClick={start}
            disabled={autoModelSequenceActive}
            className="px-4 py-2 bg-red-600 hover:bg-red-500 disabled:opacity-60 disabled:hover:bg-red-600 text-white rounded font-medium text-sm"
          >
            {tuningSweepEnabled && autoModelCycleEnabled
              ? (status === 'done' || status === 'error' ? '● Znovu ladění' : '● Start ladění')
              : autoModelCycleEnabled
              ? (status === 'done' || status === 'error' ? '● Znovu sekvenci' : '● Start sekvenci')
              : (status === 'done' || status === 'error' ? '● Znovu' : '● Start')}
          </button>
        ) : status === 'recording' ? (
          <button
            onClick={stop}
            className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white rounded font-medium text-sm"
          >
            {autoModelSequenceActive ? '■ Stop sekvenci' : '■ Stop'}
          </button>
        ) : (
          <button disabled className="px-4 py-2 bg-gray-700 text-gray-500 rounded font-medium text-sm">
            {status === 'connecting' ? 'Připojuji...' : 'Zastavuji...'}
          </button>
        )}

        {/* Status indikátor */}
        <span className="text-sm">
          {status === 'recording' && <span className="text-red-400 animate-pulse">● Nahrávám</span>}
          {status === 'done' && <span className="text-green-400">✓ Hotovo</span>}
          {status === 'error' && <span className="text-red-400">✗ Chyba</span>}
        </span>
        {autoModelSequenceActive && (
          <span className="text-xs text-blue-300">
            {tuningSweepActive ? 'Ladění' : 'Sekvence modelů'}: {Math.min(autoModelSequenceIndex + 1, autoModelSequenceIds.length)}/{autoModelSequenceIds.length}
            {' '}| uloženo {Math.min(autoModelSavedCount, autoModelSequenceIds.length)}/{autoModelSequenceIds.length}
          </span>
        )}
      </div>
      </div>
      {status === 'recording' && testMode === 'reference_video' && mobileLoopEnabled && (
        <div className="order-6 bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200 space-y-1">
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            <span>Kolo: <strong>{loopRuntime.roundNumber}/{loopTotalRounds}</strong></span>
            <span>
              Typ: <strong>{loopRuntime.roundType === 'sync' ? 'SYNC' : `MĚŘENÍ ${Math.max(1, loopRuntime.measuredRoundIndex)}/${loopRepeatCount}`}</strong>
            </span>
            <span>
              Fáze: <strong>{loopRuntime.inSpeechPhase ? 'ŘEČ' : 'PAUZA'}</strong> ({Math.ceil(loopRuntime.phaseRemainingS)} s)
            </span>
            <span>Zbývá: <strong>{formatDurationHms(loopRuntime.planRemainingS)}</strong></span>
            <span>Běží: <strong>{formatDurationHms(loopRuntime.elapsed)}</strong></span>
          </div>
          <div className="h-1.5 bg-gray-800 rounded overflow-hidden">
            <div
              className="h-full bg-blue-500 transition-all duration-200"
              style={{ width: `${loopRuntime.completedPct.toFixed(2)}%` }}
            />
          </div>
        </div>
      )}

      </div>

      {/* Chyba */}
      {error && (
        <div className="bg-red-900/40 border border-red-700 rounded p-2 text-sm text-red-300">
          {error}
        </div>
      )}
      {saveMsg && (
        <div className="bg-blue-900/30 border border-blue-700 rounded p-2 text-sm text-blue-200">
          {saveMsg}
        </div>
      )}

      {/* Live přepis */}
      {(transcript || status === 'recording') && (
        <div className="bg-gray-900 rounded p-3">
          <div className="text-xs text-gray-500 mb-1">Přepis</div>
          <p className="text-white text-sm leading-relaxed min-h-6">
            {transcript || <span className="text-gray-600 italic">čekám na řeč...</span>}
          </p>
        </div>
      )}

      {/* Důkaz mic vstupu */}
      {micInputProof && (
        <div className="bg-gray-900/70 border border-gray-700 rounded p-3">
          <div className="text-xs text-gray-400 mb-2">
            Mic důkaz: <span className="text-gray-200">{micInputProof.device_label}</span>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-gray-300">
            <span>RMS: <strong>{micInputProof.rms_dbfs != null ? `${micInputProof.rms_dbfs.toFixed(1)} dBFS` : '—'}</strong></span>
            <span>Peak: <strong>{micInputProof.peak_dbfs != null ? `${micInputProof.peak_dbfs.toFixed(1)} dBFS` : '—'}</strong></span>
            <span className={micInputProof.vad_speech ? 'text-green-300' : 'text-gray-400'}>
              VAD: <strong>{micInputProof.vad_speech ? 'řeč/signál' : 'ticho'}</strong>
            </span>
            <span>Clipping: <strong className={micInputProof.clipping_pct > 0.1 ? 'text-red-300' : ''}>{micInputProof.clipping_pct.toFixed(3)}%</strong></span>
            <span>Ticho: <strong>{Math.round(micInputProof.silence_ms)} ms</strong></span>
            <span>Chunky: <strong>{micInputProof.chunk_count}</strong></span>
            <span>Audio: <strong>{(micInputProof.audio_payload_bytes / 1024).toFixed(1)} kB</strong></span>
            <span>WS: <strong>{(micInputProof.ws_payload_bytes / 1024).toFixed(1)} kB</strong></span>
            <span>SR: <strong>{micInputProof.sample_rate} Hz</strong></span>
            <span>Posl. chunk: <strong>{micInputProof.last_chunk_age_ms != null ? `${Math.round(micInputProof.last_chunk_age_ms)} ms` : '—'}</strong></span>
          </div>
        </div>
      )}

      {/* Metriky */}
      {metrics && (
        <div className="flex gap-4 text-sm text-gray-300">
          {metrics.latency_ms != null && (
            <span>Latence: <strong>{Math.round(metrics.latency_ms)} ms</strong></span>
          )}
          {metrics.first_word_audio_ms != null && (
            <span>První slovo (audio): <strong>{Math.round(metrics.first_word_audio_ms)} ms</strong></span>
          )}
          {metrics.first_word_wall_ms != null && (
            <span>První slovo (wall): <strong>{Math.round(metrics.first_word_wall_ms)} ms</strong></span>
          )}
          {metrics.rtf != null && (
            <span className={metrics.rtf > 1 ? 'text-orange-400' : 'text-green-400'}>
              RTF: <strong>{metrics.rtf.toFixed(3)}</strong>
              {metrics.rtf > 1 ? ' ⚠️' : ' ✓'}
            </span>
          )}
          {metrics.elapsed_s != null && (
            <span>Čas: <strong>{metrics.elapsed_s.toFixed(1)} s</strong></span>
          )}
          {metrics.segment_finalize_ms_p95 != null && (
            <span>P95 finalize: <strong>{Math.round(metrics.segment_finalize_ms_p95)} ms</strong></span>
          )}
          {metrics.processing_ms_p95 != null && (
            <span>P95 chunk proc: <strong>{Math.round(metrics.processing_ms_p95)} ms</strong></span>
          )}
          {metrics.capture_jitter_ms_p95 != null && (
            <span>P95 jitter: <strong>{Math.round(metrics.capture_jitter_ms_p95)} ms</strong></span>
          )}
          {metrics.capture_lag_ms_p95 != null && (
            <span>P95 capture lag: <strong>{Math.round(metrics.capture_lag_ms_p95)} ms</strong></span>
          )}
          {metrics.queue_depth_peak_ms != null && (
            <span>Peak debt: <strong>{Math.round(metrics.queue_depth_peak_ms)} ms</strong></span>
          )}
          {metrics.backpressure_events != null && (
            <span>Backpressure: <strong>{metrics.backpressure_events}</strong></span>
          )}
          {metrics.drop_rate != null && (
            <span>Drop: <strong>{(metrics.drop_rate * 100).toFixed(2)} %</strong></span>
          )}
          {metrics.worker_rss_peak_mb != null && (
            <span>RSS peak: <strong>{Math.round(metrics.worker_rss_peak_mb)} MB</strong></span>
          )}
          {metrics.reason_code && (
            <span className="text-yellow-300">Reason: <strong>{metrics.reason_code}</strong></span>
          )}
        </div>
      )}

      {/* Sekvenční report */}
      {seqReport && seqReport.trials.length > 0 && (
        <div className="bg-gray-900 border border-gray-700 rounded p-3">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs text-gray-400 font-semibold">
              Sekvenční report — {seqReport.trials_count}/{seqReport.sequence_total ?? '?'} trialů
            </div>
            <a
              href={`/api/mic/sequences/${encodeURIComponent(seqReportToken ?? '')}/export.csv`}
              download
              className="text-xs text-blue-400 hover:text-blue-300 underline"
            >
              Stáhnout CSV
            </a>
          </div>
          {seqReport.summary && (
            <div className="mb-2 text-[11px] text-gray-400">
              OK {seqReport.summary.counts?.ok ?? 0}
              {' | '}borderline {seqReport.summary.counts?.borderline ?? 0}
              {' | '}slow {seqReport.summary.counts?.too_slow_for_slot ?? 0}
              {' | '}fail {seqReport.summary.counts?.fail ?? 0}
              {' | '}running {seqReport.summary.running ?? 0}
              {typeof seqReport.summary.avg_rtf === 'number' ? ` | avg RTF ${seqReport.summary.avg_rtf.toFixed(3)}` : ''}
              {typeof seqReport.summary.avg_drop_rate === 'number' ? ` | avg drop ${(seqReport.summary.avg_drop_rate * 100).toFixed(1)}%` : ''}
            </div>
          )}
          {seqReportProfileLabel && (
            <div className="mb-2 text-[11px] text-emerald-300">
              Profil nastavení: <strong>{seqReportProfileLabel}</strong>
            </div>
          )}
          {seqReportPauseValidation && (seqReportPauseValidation.checked_points ?? 0) > 0 && (
            <div className={`mb-2 text-[11px] ${seqReportPauseValidation.ok ? 'text-emerald-300' : 'text-red-300'}`}>
              Pauzy: <strong>{seqReportPauseValidation.ok ? 'OK' : 'POZOR'}</strong>
              {typeof seqReportPauseValidation.planned_pause_s === 'number'
                ? ` | plán ${seqReportPauseValidation.planned_pause_s.toFixed(1)}s`
                : ''}
              {typeof seqReportPauseValidation.observed_pause_min_s === 'number' && typeof seqReportPauseValidation.observed_pause_max_s === 'number'
                ? ` | real ${seqReportPauseValidation.observed_pause_min_s.toFixed(1)}-${seqReportPauseValidation.observed_pause_max_s.toFixed(1)}s`
                : ''}
              {typeof seqReportPauseValidation.max_abs_deviation_s === 'number'
                ? ` | max odchylka ${seqReportPauseValidation.max_abs_deviation_s.toFixed(1)}s`
                : ''}
              {Array.isArray(seqReportPauseValidation.violations) && seqReportPauseValidation.violations.length > 0
                ? ` | problém: #${seqReportPauseValidation.violations[0].seq_index ?? '?'} ${seqReportPauseValidation.violations[0].model_id ?? ''} (${seqReportPauseValidation.violations[0].observed_pause_s?.toFixed(1) ?? '?'}s)`
                : ''}
            </div>
          )}
          {seqReportConclusion && (
            <div className="mb-2 rounded border border-gray-700 bg-gray-950/60 p-2 text-[11px] text-gray-300">
              <div className="font-semibold text-gray-100">
                Závěr: {seqReportConclusion.headline || '—'}
              </div>
              <div className="mt-1 grid grid-cols-1 lg:grid-cols-2 gap-x-4 gap-y-1">
                <div>
                  <span className="text-emerald-300">Použitelné:</span>{' '}
                  {formatConclusionModels(seqReportConclusion.usable_models)}
                </div>
                <div>
                  <span className="text-yellow-300">Hraniční:</span>{' '}
                  {formatConclusionModels(seqReportConclusion.borderline_models)}
                </div>
                <div>
                  <span className="text-red-300">Selhalo výkonem:</span>{' '}
                  {formatConclusionModels(seqReportConclusion.performance_failed_models)}
                </div>
                <div>
                  <span className="text-amber-300">Text nehodnotit kvůli dropům:</span>{' '}
                  {formatConclusionModels(seqReportConclusion.quality_not_reliable_models)}
                </div>
              </div>
              {Array.isArray(seqReportConclusion.notes) && seqReportConclusion.notes.length > 0 && (
                <div className="mt-1 text-gray-400">
                  {seqReportConclusion.notes.join(' ')}
                </div>
              )}
            </div>
          )}
          {seqReport.readiness && (
            <div className={`mb-2 text-[11px] ${seqReport.readiness.pass ? 'text-emerald-300' : 'text-amber-300'}`}>
              Readiness: <strong>{seqReport.readiness.pass ? 'PASS' : 'FAIL'}</strong>
              {Array.isArray(seqReport.readiness.failed_checks) && seqReport.readiness.failed_checks.length > 0
                ? ` | ${seqReport.readiness.failed_checks.join(', ')}`
                : ''}
            </div>
          )}
          {tuningSweepReportRows.length > 0 && (
            <div className="mb-3 rounded border border-emerald-900/70 bg-emerald-950/10 p-2">
              <div className="mb-1 text-[11px] font-semibold text-emerald-200">
                Souhrn ladění parametrů
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-[11px] text-gray-300 border-collapse">
                  <thead>
                    <tr className="border-b border-gray-800 text-gray-500">
                      <th className="py-1 pr-3 text-left">Model</th>
                      <th className="py-1 pr-3 text-left">Varianta</th>
                      <th className="py-1 pr-3 text-left">Změna</th>
                      <th className="py-1 pr-3 text-right">n</th>
                      <th className="py-1 pr-3 text-right">RTF</th>
                      <th className="py-1 pr-3 text-right">Drop%</th>
                      <th className="py-1 pr-3 text-right">1.slovo ms</th>
                      <th className="py-1 pr-3 text-right">Q s</th>
                      <th className="py-1 pr-3 text-right">Skóre</th>
                      <th className="py-1 text-left">Verdikt</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tuningSweepReportRows.map((row) => (
                      <tr key={row.key} className="border-b border-gray-900/80">
                        <td className="py-1 pr-3 text-gray-200">{row.modelId}</td>
                        <td className="py-1 pr-3 text-emerald-300">{row.variantLabel}</td>
                        <td className="py-1 pr-3 text-gray-400 max-w-[220px] truncate" title={formatParamsSummary(row.changedParams, 50)}>
                          {formatParamsSummary(row.changedParams, 5) || 'baseline'}
                        </td>
                        <td className="py-1 pr-3 text-right">{row.count}{row.repeatTotal ? `/${row.repeatTotal}` : ''}</td>
                        <td className="py-1 pr-3 text-right">{row.avgRtf != null ? row.avgRtf.toFixed(3) : '—'}</td>
                        <td className="py-1 pr-3 text-right">{row.avgDropRate != null ? (row.avgDropRate * 100).toFixed(1) : '—'}</td>
                        <td className="py-1 pr-3 text-right">{row.avgFirstWordMs != null ? Math.round(row.avgFirstWordMs) : '—'}</td>
                        <td className="py-1 pr-3 text-right">{row.avgQueuePeakS != null ? row.avgQueuePeakS.toFixed(2) : '—'}</td>
                        <td className="py-1 pr-3 text-right">{row.score != null ? row.score.toFixed(2) : '—'}</td>
                        <td className={`py-1 ${row.verdict === 'kandidát' ? 'text-emerald-300' : row.verdict.startsWith('neúplné') ? 'text-amber-300' : 'text-red-300'}`}>
                          {row.verdict}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-gray-300 border-collapse">
              <thead>
                <tr className="text-gray-500 border-b border-gray-700">
                  <th className="text-left pr-2 py-1">#</th>
                  <th className="text-left pr-2 py-1">Model</th>
                  <th className="text-left pr-2 py-1">Profil</th>
                  <th className="text-left pr-2 py-1">Ladění</th>
                  <th className="text-right pr-2 py-1">Pauza s</th>
                  <th className="text-left pr-2 py-1">Parametry</th>
                  <th className="text-left pr-2 py-1">Status</th>
                  <th className="text-left pr-2 py-1">Fáze</th>
                  <th className="text-right pr-2 py-1">RTF</th>
                  <th className="text-right pr-2 py-1">Drop%</th>
                  <th className="text-right pr-2 py-1">1.slovo ms</th>
                  <th className="text-right pr-2 py-1">P50 fin ms</th>
                  <th className="text-right pr-2 py-1">P95 fin ms</th>
                  <th className="text-right pr-2 py-1">Q-peak s</th>
                  <th className="text-right py-1">RAM MB</th>
                  <th className="text-left py-1 pl-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {seqReport.trials.map((t) => {
                  const statusColor: Record<MicTrialStatus, string> = {
                    ok: 'text-green-400',
                    borderline: 'text-yellow-400',
                    too_slow_for_slot: 'text-orange-400',
                    fail: 'text-red-400',
                  }
                  const cls = statusColor[t.trial_status] ?? 'text-gray-400'
                  const pauseDeviation = typeof t.pause_deviation_s === 'number' ? t.pause_deviation_s : null
                  const pauseWarn = pauseDeviation != null && Math.abs(pauseDeviation) > 2
                  return (
                    <tr key={t.session_id} className="border-b border-gray-800 hover:bg-gray-800/40">
                      <td className="pr-2 py-0.5">{t.seq_index ?? '—'}</td>
                      <td className="pr-2 py-0.5 max-w-[140px] truncate" title={t.model_id}>{t.model_id}</td>
                      <td className="pr-2 py-0.5 max-w-[150px] truncate text-[11px] text-emerald-300" title={t.sequence_param_profile ?? undefined}>
                        {t.sequence_param_profile || '—'}
                      </td>
                      <td
                        className="pr-2 py-0.5 max-w-[170px] truncate text-[11px] text-blue-300"
                        title={formatParamsSummary(t.tuning_changed_params, 50)}
                      >
                        {t.tuning_variant_label
                          ? `${t.tuning_variant_label}${t.tuning_repeat_index && t.tuning_repeat_total ? ` ${t.tuning_repeat_index}/${t.tuning_repeat_total}` : ''}`
                          : '—'}
                      </td>
                      <td
                        className={`text-right pr-2 py-0.5 ${pauseWarn ? 'text-red-300 font-semibold' : 'text-gray-400'}`}
                        title={pauseDeviation != null ? `Odchylka od plánu: ${pauseDeviation.toFixed(1)}s` : undefined}
                      >
                        {t.observed_pause_after_prev_stop_s != null ? t.observed_pause_after_prev_stop_s.toFixed(1) : '—'}
                      </td>
                      <td
                        className="pr-2 py-0.5 max-w-[240px] truncate text-[11px] text-gray-400"
                        title={formatParamsSummary(t.model_params_used, 50)}
                      >
                        {formatParamsSummary(t.model_params_used, 5) || '—'}
                      </td>
                      <td className={`pr-2 py-0.5 font-semibold ${cls}`}>{t.trial_status}</td>
                      <td className="pr-2 py-0.5 text-gray-400">{t.phase ?? t.status ?? '—'}</td>
                      <td className="text-right pr-2 py-0.5">{t.rtf != null ? t.rtf.toFixed(3) : '—'}</td>
                      <td className="text-right pr-2 py-0.5">{t.drop_rate != null ? (t.drop_rate * 100).toFixed(1) : '—'}</td>
                      <td className="text-right pr-2 py-0.5">{t.first_word_wall_ms != null ? Math.round(t.first_word_wall_ms) : '—'}</td>
                      <td className="text-right pr-2 py-0.5">{t.segment_finalize_ms_p50 != null ? Math.round(t.segment_finalize_ms_p50) : '—'}</td>
                      <td className="text-right pr-2 py-0.5">{t.segment_finalize_ms_p95 != null ? Math.round(t.segment_finalize_ms_p95) : '—'}</td>
                      <td className="text-right pr-2 py-0.5">{t.queue_depth_peak_s != null ? t.queue_depth_peak_s.toFixed(2) : '—'}</td>
                      <td className="text-right py-0.5">{t.worker_rss_peak_mb != null ? Math.round(t.worker_rss_peak_mb) : '—'}</td>
                      <td className="py-0.5 pl-2 text-gray-400">{t.reason_code || '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="bg-gray-900 rounded p-3">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <div className="text-xs text-gray-500">Historie uložených pokusů (perzistentní)</div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-gray-500">Filtr:</span>
            <button
              type="button"
              onClick={() => { setHistoryModeFilter('all'); setShowAllHistory(false) }}
              title="Zobrazit všechny záznamy (volný řeč i referenční)"
              className={`px-2 py-1 rounded border ${historyModeFilter === 'all' ? 'border-blue-500 text-blue-200 bg-blue-900/30' : 'border-gray-700 text-gray-300 hover:text-white'}`}
            >
              vše
            </button>
            <button
              type="button"
              onClick={() => { setHistoryModeFilter('free_speech'); setShowAllHistory(false) }}
              title="Filtrovat pouze záznamy z režimu volného řeči"
              className={`px-2 py-1 rounded border ${historyModeFilter === 'free_speech' ? 'border-blue-500 text-blue-200 bg-blue-900/30' : 'border-gray-700 text-gray-300 hover:text-white'}`}
            >
              volný
            </button>
            <button
              type="button"
              onClick={() => { setHistoryModeFilter('reference_video'); setShowAllHistory(false) }}
              title="Filtrovat pouze záznamy z referenčního video režimu"
              className={`px-2 py-1 rounded border ${historyModeFilter === 'reference_video' ? 'border-blue-500 text-blue-200 bg-blue-900/30' : 'border-gray-700 text-gray-300 hover:text-white'}`}
            >
              referenční
            </button>
            {historyModeFilter !== 'all' && (
              <button
                type="button"
                onClick={() => { void clearHistoryRecords('filtered') }}
                disabled={historyBulkDeleting || visibleHistory.length === 0}
                className="px-2 py-1 rounded border border-red-700 text-red-300 hover:text-red-200 disabled:opacity-50"
                title="Smazat pouze záznamy podle aktuálního filtru"
              >
                {historyBulkDeleting ? 'Mažu...' : 'Smazat filtr'}
              </button>
            )}
            <button
              type="button"
              onClick={() => { void clearHistoryRecords('all') }}
              disabled={historyBulkDeleting || savedResults.length === 0}
              className="px-2 py-1 rounded border border-red-800 text-red-300 hover:text-red-200 disabled:opacity-50"
              title="Smazat celou historii uložených pokusů"
            >
              {historyBulkDeleting ? 'Mažu...' : 'Smazat vše'}
            </button>
          </div>
        </div>
        <div className="mb-2 text-[11px] text-gray-500 space-y-1">
          <div>Řazení: pořadí podle zakliknutí checkboxu u názvu sloupce.</div>
          <div className="flex flex-wrap gap-2">
            {historySortColumns.map((col) => {
              const idx = historySortOrder.indexOf(col.key)
              const active = idx >= 0
              return (
                <label key={col.key} className={`inline-flex items-center gap-1 px-2 py-1 rounded border ${active ? 'border-blue-600 bg-blue-900/20 text-blue-200' : 'border-gray-700 text-gray-400'}`}>
                  <input
                    type="checkbox"
                    checked={active}
                    onChange={() => toggleHistorySortKey(col.key)}
                    className="accent-blue-500"
                  />
                  <span>{col.label}</span>
                  {active && <span className="text-[10px] text-blue-300">#{idx + 1}</span>}
                </label>
              )
            })}
          </div>
        </div>
        {visibleHistory.length === 0 ? (
          <div className="text-xs text-gray-500">Zatím bez záznamu.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs text-gray-300">
              <thead>
                <tr className="text-gray-500 border-b border-gray-800">
                  <th className="text-left py-1 pr-3">Čas</th>
                  <th className="text-left py-1 pr-3">Model</th>
                  <th className="text-left py-1 pr-3">Režim</th>
                  <th className="text-left py-1 pr-3">Reference</th>
                  <th className="text-left py-1 pr-3">Balíček</th>
                  <th className="text-right py-1 pr-3">RTF</th>
                  <th className="text-right py-1 pr-3">Drop</th>
                  <th className="text-left py-1 pr-3">Status</th>
                  <th className="text-right py-1 pr-3">1.slovo ms</th>
                  <th className="text-right py-1 pr-3">P50 ms</th>
                  <th className="text-right py-1 pr-3">P95 ms</th>
                  <th className="text-right py-1 pr-3">Q-peak s</th>
                  <th className="text-right py-1 pr-3">RAM MB</th>
                  <th className="text-left py-1 pr-3">Reason</th>
                  <th className="text-left py-1 pr-3">Profil</th>
                  <th className="text-left py-1 pr-3">Parametry</th>
                  <th className="text-left py-1">Přepis</th>
                  <th className="text-left py-1 pl-2">Akce</th>
                </tr>
              </thead>
              <tbody>
                {historyVisibleRows.map((r, idx) => {
                  const hasTranscript = !!r.transcript?.trim()
                  const isExpanded = expandedHistoryRecordIds.includes(r.record_id)
                  const canExpand = hasTranscript && transcriptWords(r.transcript).length > 8
                  const prev = idx > 0 ? historyVisibleRows[idx - 1] : null
                  const separatorBefore = Boolean(
                    prev
                    && (prev.sequence_token || r.sequence_token)
                    && prev.sequence_token !== r.sequence_token,
                  )
                  return (
                    <Fragment key={r.record_id}>
                      <tr
                        key={r.record_id}
                        className={`${separatorBefore ? 'border-t-2 border-red-600' : ''} border-b border-gray-800/80 align-top`}
                      >
                        <td className="py-1 pr-3 text-gray-400 whitespace-nowrap">{formatDateTimeMedium(r.saved_at)}</td>
                        <td className="py-1 pr-3 whitespace-nowrap">{r.model_id}</td>
                        <td className="py-1 pr-3 whitespace-nowrap">
                          {r.mic_test_mode === 'free_speech' ? 'volný' : r.mic_test_mode === 'reference_video' ? 'referenční' : '—'}
                        </td>
                        <td className="py-1 pr-3">{r.reference_label}</td>
                        <td className="py-1 pr-3 text-[11px] text-gray-400 font-mono">
                          {r.mobile_loop_package_id || '—'}
                        </td>
                        <td className="py-1 pr-3 text-right">{r.rtf != null ? r.rtf.toFixed(3) : '—'}</td>
                        <td className="py-1 pr-3 text-right">{r.drop_rate != null ? `${(r.drop_rate * 100).toFixed(1)}%` : '—'}</td>
                        <td className={`py-1 pr-3 font-semibold ${r.trial_status === 'ok' ? 'text-green-400' : r.trial_status === 'borderline' ? 'text-yellow-400' : r.trial_status === 'too_slow_for_slot' ? 'text-orange-400' : r.trial_status === 'fail' ? 'text-red-400' : 'text-gray-500'}`}>
                          {r.trial_status ?? '—'}
                        </td>
                        <td className="py-1 pr-3 text-right">{r.first_word_wall_ms != null ? Math.round(r.first_word_wall_ms) : '—'}</td>
                        <td className="py-1 pr-3 text-right">{r.p50_fin_ms != null ? Math.round(r.p50_fin_ms) : '—'}</td>
                        <td className="py-1 pr-3 text-right">{r.p95_fin_ms != null ? Math.round(r.p95_fin_ms) : '—'}</td>
                        <td className="py-1 pr-3 text-right">{r.q_peak_s != null ? r.q_peak_s.toFixed(2) : '—'}</td>
                        <td className="py-1 pr-3 text-right">{r.rss_peak_mb != null ? Math.round(r.rss_peak_mb) : '—'}</td>
                        <td className="py-1 pr-3 text-gray-400 text-[11px]">{r.reason_code || '—'}</td>
                        <td className="py-1 pr-3 text-emerald-300 text-[11px] max-w-[180px] truncate" title={r.sequence_param_profile ?? undefined}>
                          <div>{r.sequence_param_profile || '—'}</div>
                          {r.tuning_variant_label && (
                            <div className="text-blue-300">
                              ladění: {r.tuning_variant_label}
                              {r.tuning_step_size ? ` krok ${r.tuning_step_size}x` : ''}
                              {r.tuning_repeat_index && r.tuning_repeat_total ? ` ${r.tuning_repeat_index}/${r.tuning_repeat_total}` : ''}
                            </div>
                          )}
                        </td>
                        <td
                          className="py-1 pr-3 text-gray-400 text-[11px] max-w-[260px]"
                          title={`${formatParamsSummary(r.model_params_used, 50)}${r.tuning_changed_params ? ` | změna: ${formatParamsSummary(r.tuning_changed_params, 50)}` : ''}`}
                        >
                          {r.model_params_used ? (
                            <div className="space-y-0.5">
                              {r.sequence_common_params_enabled && (
                                <div className="text-blue-300">společné</div>
                              )}
                              {r.tuning_changed_params && Object.keys(r.tuning_changed_params).length > 0 && (
                                <div className="text-emerald-300 truncate">změna: {formatParamsSummary(r.tuning_changed_params, 4)}</div>
                              )}
                              <div className="truncate">{formatParamsSummary(r.model_params_used, 6) || '—'}</div>
                            </div>
                          ) : '—'}
                        </td>
                        <td className="py-1 text-gray-200 max-w-xs">
                          {hasTranscript ? (
                            <div className="space-y-1">
                              {r.transcript_source === 'mic_ws_final' && (
                                <div className="text-[10px] text-green-300">zdroj: mic_ws_final</div>
                              )}
                              <div>{isExpanded ? r.transcript.trim() : clipWords(r.transcript, 8)}</div>
                              {canExpand && (
                                <button
                                  type="button"
                                  onClick={() => toggleHistoryTranscript(r.record_id)}
                                  className="text-[11px] text-blue-300 hover:text-blue-200 underline"
                                >
                                  {isExpanded ? 'Skrýt celý přepis' : 'Zobrazit celý přepis'}
                                </button>
                              )}
                            </div>
                          ) : (
                            <span className="text-gray-500" title="Mic řádek bez ověřeného final.text z WebSocket session nemůže zobrazit přepis.">
                              bez ověřeného mic textu
                            </span>
                          )}
                        </td>
                        <td className="py-1 pl-2 whitespace-nowrap">
                          <button
                            type="button"
                            onClick={() => { void deleteHistoryRecord(r.record_id) }}
                            disabled={historyDeletingRecordId === r.record_id || historyBulkDeleting}
                            className="px-2 py-0.5 border border-red-700 rounded text-red-300 hover:text-red-200 disabled:opacity-50"
                          >
                            {historyDeletingRecordId === r.record_id ? 'Mažu...' : 'Smazat'}
                          </button>
                        </td>
                      </tr>
                      {isExpanded && hasTranscript && (
                        <tr className="border-b border-gray-800/80">
                          <td colSpan={18} className="py-2 pl-2 pr-1">
                            <div className="rounded border border-gray-700 bg-gray-950/70 p-2 whitespace-pre-wrap text-[12px] text-gray-100">
                              {r.transcript.trim()}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        {hiddenHistoryCount > 0 && (
          <div className="mt-2">
            <button
              type="button"
              onClick={() => setShowAllHistory(v => !v)}
              className="text-xs px-2 py-1 rounded border border-gray-700 text-gray-300 hover:text-white"
            >
              {showAllHistory ? 'Skrýt starší pokusy' : `Zobrazit dalších ${hiddenHistoryCount} pokusů`}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
