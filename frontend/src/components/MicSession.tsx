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
  AudioDevice,
  LibraryItem,
  MicMobileLoopPackageResponse,
  MicMobileLoopPackageListItem,
  MicSequenceReport,
  MicTrialStatus,
} from '../types'
import { api } from '../api/client'
import { ModelParamsForm } from './ModelParamsForm'
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

type ReferenceTextId = 'prepared_1' | 'prepared_2' | 'custom_1' | 'custom_2'
type ClipDurationAnchor = 'from' | 'to'

type SavedWebMicResult = {
  record_id: string
  saved_at: string
  model_id: string
  reference_label: string
  mic_test_mode: 'free_speech' | 'reference_video' | 'unknown'
  transcript: string
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
}

type AutoModelSequenceMeta = {
  sequence_token: string
  sequence_index: number
  sequence_total: number
}

type ActiveSessionLoopConfig = {
  enabled: boolean
  speechS: number | null
  captureSpeechS: number | null
  earlyStopS: number | null
  pauseS: number | null
  syncFirstRound: boolean | null
  measuredRounds: number | null
  autoStop: boolean | null
  packageId: string | null
}

type HistoryModeFilter = 'all' | 'free_speech' | 'reference_video'
type HistorySortKey = 'saved_at' | 'model_id' | 'mic_test_mode' | 'reference_label' | 'rtf' | 'drop_rate'

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
 * Doporučené MIC defaulty z interních tuning výsledků (27.-29. 3. 2026).
 * Nejsou to "tvrdé" backend defaulty — aplikují se pouze v MIC UI.
 */
const MIC_RECOMMENDED_DEFAULTS: Record<string, Record<string, unknown>> = {
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
}

const MIC_PARAM_HINTS: Record<string, string> = {
  language: 'Pro CZ test nastav `cs`; auto detekci nech jen když střídáš jazyky.',
  threads: 'Začni na 4 (slabší HW) až 8 (silnější CPU). Moc vysoko může zhoršit stabilitu.',
  beam_size: '1-2 = rychlé/live, 3-5 = přesnější ale pomalejší.',
  best_of: 'Drž 1-2 pro live. Vyšší hodnoty zvedají latenci bez velkého přínosu.',
  no_fallback: 'Pro stabilní live nech vypnuté. Zapni jen když chceš striktní dekódování.',
  initial_prompt: 'Krátký CZ kontext pomáhá u jmen a tématu; dlouhý prompt spíš škodí.',
  analysis_interval_ms: '1200-1600 ms je obvykle dobrý kompromis mezi plynulostí a zátěží.',
  analysis_window_seconds: '10-14 s pro běžné live. Delší okno zlepší kontext, ale zvýší zpoždění.',
  input_gain_db: 'Drž kolem 0 dB. Zvyš jen při tichém vstupu, sniž při přebuzení a šumu.',
  backpressure_high_s: 'Vyšší hodnota = méně dropů, ale větší zpoždění. Běžně 1.0-1.4 s.',
  backpressure_low_s: 'Hystereze návratu z backpressure; drž zhruba třetinu až polovinu high.',
  compute_type: 'Na CPU preferuj `int8`; vyšší přesnost typicky znamená pomalejší běh.',
  device: 'Pro starší kancelářské PC použij `cpu`; `cuda` jen pokud je stabilně dostupná.',
  sample_rate: 'Pro mic drž 16000 Hz, jinak roste režie bez jasného přínosu.',
  chunk_seconds: 'Pro live drž 0.1-0.3 s; větší chunk zvyšuje latenci.',
  set_words: 'Zapni jen když potřebuješ word timestampy, jinak nech vypnuté.',
  num_threads: 'Stejné doporučení jako `threads`: 4-8 podle CPU, bez přestřelení.',
  decoding_method: 'Pro live začni `greedy_search`; beam variantu testuj až když je rezerva výkonu.',
  provider: 'Na běžném HW preferuj `cpu`; jiné providery jen pokud jsou ověřeně stabilní.',
  model_arch: 'Na slabším HW `tiny/small`, `medium` jen pokud drží RTF pod 1.',
}

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
  mobileLoopSyncFirstRound: boolean
  mobileLoopRepeatCount: number
  mobileLoopAutoStop: boolean
  autoModelCycleEnabled: boolean
  autoModelSelectedIds: string[]
  autoModelGraceSeconds: number
  autoModelSilenceStopSeconds: number
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

  const [paramsByModel, setParamsByModel] = useState<Record<string, Record<string, unknown>>>(() => initialModelParamsById)
  const [modelId, setModelId] = useState(initialModelId)
  const [params, setParams] = useState<Record<string, unknown>>(
    () => buildMicParamsWithSaved(initialModel, initialModelParamsById[initialModelId]),
  )
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
  const [autoModelSequenceActive, setAutoModelSequenceActive] = useState(false)
  const [autoModelSequenceIds, setAutoModelSequenceIds] = useState<string[]>([])
  const [autoModelSequenceIndex, setAutoModelSequenceIndex] = useState(0)
  const [autoModelSavedCount, setAutoModelSavedCount] = useState(0)
  const [status, setStatus] = useState<Status>('idle')
  const [transcript, setTranscript] = useState('')
  const [metrics, setMetrics] = useState<MicMetrics | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [devices, setDevices] = useState<AudioDevice[]>([])
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
  const [mobileLoopSyncFirstRound, setMobileLoopSyncFirstRound] = useState(
    typeof persistedUi.mobileLoopSyncFirstRound === 'boolean' ? persistedUi.mobileLoopSyncFirstRound : true,
  )
  const [mobileLoopRepeatCount, setMobileLoopRepeatCount] = useState(asFiniteNumberOr(persistedUi.mobileLoopRepeatCount, 10))
  const [mobileLoopAutoStop, setMobileLoopAutoStop] = useState(
    typeof persistedUi.mobileLoopAutoStop === 'boolean' ? persistedUi.mobileLoopAutoStop : true,
  )
  const [mobileLoopPackage, setMobileLoopPackage] = useState<MicMobileLoopPackageResponse | null>(null)
  const [mobileLoopPackageLoading, setMobileLoopPackageLoading] = useState(false)
  const [mobileLoopPackageError, setMobileLoopPackageError] = useState<string | null>(null)
  const [mobileLoopHistory, setMobileLoopHistory] = useState<MicMobileLoopPackageListItem[]>([])
  const [mobileLoopHistoryLoading, setMobileLoopHistoryLoading] = useState(false)
  const [mobileLoopHistoryError, setMobileLoopHistoryError] = useState<string | null>(null)
  const [mobileLoopHistoryDeletingId, setMobileLoopHistoryDeletingId] = useState<string | null>(null)
  const [showAllMobileLoopHistory, setShowAllMobileLoopHistory] = useState(false)
  const [expandedMobileLoopPackageIds, setExpandedMobileLoopPackageIds] = useState<string[]>([])
  const [recordingStartedAtPerfMs, setRecordingStartedAtPerfMs] = useState<number | null>(null)
  const [recordingElapsedS, setRecordingElapsedS] = useState(0)
  const [seqReport, setSeqReport] = useState<MicSequenceReport | null>(null)
  const [seqReportToken, setSeqReportToken] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const saveGuardRef = useRef<Set<string>>(new Set())
  const autoModelSavedSlotsRef = useRef<Set<number>>(new Set())
  const autoStopFiredRef = useRef(false)
  const stopRequestedAtPerfMsRef = useRef<number | null>(null)
  const trialStartedAtPerfMsRef = useRef<number | null>(null)
  const trialDeadlineAtPerfMsRef = useRef<number | null>(null)
  const trialHardLimitAtPerfMsRef = useRef<number | null>(null)
  const trialAdaptiveEarlyStopMsRef = useRef(0)
  const sequenceAnchorAtPerfMsRef = useRef<number | null>(null)
  const lastTranscriptUpdateAtPerfMsRef = useRef<number | null>(null)
  const currentTranscriptTextRef = useRef('')
  const activeSessionLoopConfigRef = useRef<ActiveSessionLoopConfig>({
    enabled: false,
    speechS: null,
    captureSpeechS: null,
    earlyStopS: null,
    pauseS: null,
    syncFirstRound: null,
    measuredRounds: null,
    autoStop: null,
    packageId: null,
  })
  const autoModelSequenceTokenRef = useRef<string | null>(null)
  const autoModelAdvanceLockRef = useRef(false)
  const autoModelAdvanceTimerRef = useRef<number | null>(null)
  const autoModelRetryCountRef = useRef(0)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const processorRef = useRef<ScriptProcessorNode | null>(null)
  const workletNodeRef = useRef<AudioWorkletNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)

  const selectedModel = availableModels.find(m => m.model_id === modelId)
  const referenceLibrary = useMemo(
    () =>
      (library ?? [])
        .filter(v => !!v.video_id && v.visible_in_menus !== false)
        // Stejné základní řazení jako v knihovně: newest (upload/added) nahoře.
        .sort((a, b) => {
          const aKey = String(a.upload_date ?? a.added_at ?? '')
          const bKey = String(b.upload_date ?? b.added_at ?? '')
          const byDateDesc = bKey.localeCompare(aKey, 'cs')
          if (byDateDesc !== 0) return byDateDesc
          return videoLabel(a.title, a.video_id).localeCompare(videoLabel(b.title, b.video_id), 'cs')
        }),
    [library],
  )
  const selectedReferenceVideo = referenceLibrary.find(v => v.video_id === referenceVideoId)
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
  const loopRepeatCount = Math.max(1, Math.min(200, Math.floor(mobileLoopRepeatCount) || 1))
  const loopSyncRounds = mobileLoopSyncFirstRound ? 1 : 0
  const loopCycleS = Math.max(1, loopSpeechS + loopPauseS)
  const loopTotalRounds = loopSyncRounds + loopRepeatCount
  const loopPlanS = loopCycleS * loopTotalRounds
  const autoModelLeadStartSeconds = 2.5
  const autoModelPreparationSeconds = 10
  const autoModelHardTrialSeconds = Math.min(65, loopCaptureSpeechS + 5)
  const autoModelLatencyGuardSeconds = 10
  const autoModelAdaptiveMaxCutSeconds = Math.max(1, loopPauseS - 2)
  const autoModelSlotSeconds = loopCycleS
  const effectiveTrialPlanS = autoModelSequenceActive ? loopCycleS : loopPlanS
  const uiLocked = status === 'recording' || status === 'connecting' || status === 'stopping' || autoModelSequenceActive
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
  const updateModelParams = useCallback((nextValues: Record<string, unknown>) => {
    setParams(nextValues)
    setParamsByModel((prev) => (
      modelId
        ? { ...prev, [modelId]: { ...nextValues } }
        : prev
    ))
  }, [modelId])

  const loadSavedHistory = useCallback(async () => {
    try {
      const { records } = await api.mic.listManualRecords({ limit: 200 })
      const mapped: SavedWebMicResult[] = records.flatMap((r) => {
        try {
          const metrics = (r.metrics ?? {}) as Record<string, unknown>
          const rawMode = typeof metrics.mic_test_mode === 'string' ? metrics.mic_test_mode : ''
          const mode: SavedWebMicResult['mic_test_mode'] =
            rawMode === 'free_speech' || rawMode === 'reference_video' ? rawMode : 'unknown'
          return [{
            record_id: r.record_id,
            saved_at: r.saved_at,
            model_id: r.model_id,
            reference_label: typeof metrics.reference_label === 'string' && metrics.reference_label.trim()
              ? metrics.reference_label
              : '—',
            mic_test_mode: mode,
            transcript: r.transcript || '',
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
    setAutoModelSelectedIds((prev) => {
      const valid = prev.filter((id) => availableModels.some((m) => m.model_id === id))
      if (valid.length > 0) return valid
      return modelId ? [modelId] : []
    })
  }, [availableModels, modelId])

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
      mobileLoopSyncFirstRound,
      mobileLoopRepeatCount,
      mobileLoopAutoStop,
      autoModelCycleEnabled,
      autoModelSelectedIds,
      autoModelGraceSeconds,
      autoModelSilenceStopSeconds,
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
    mobileLoopSyncFirstRound,
    mobileLoopRepeatCount,
    mobileLoopAutoStop,
    autoModelCycleEnabled,
    autoModelSelectedIds,
    autoModelGraceSeconds,
    autoModelSilenceStopSeconds,
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
    setMobileLoopPackage(null)
    setMobileLoopPackageError(null)
  }, [referenceVideoId, clipFromS, clipToS, loopPauseS, loopRepeatCount, mobileLoopSyncFirstRound])

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

  const createMobileLoopPackage = useCallback(async () => {
    setMobileLoopPackageError(null)
    setMobileLoopPackage(null)
    if (!referenceVideoId) {
      setMobileLoopPackageError('Nejprve vyber video z knihovny.')
      return
    }
    if (clipToS <= clipFromS) {
      setMobileLoopPackageError("Neplatná pasáž: 'do' musí být větší než 'od'.")
      return
    }
    setMobileLoopPackageLoading(true)
    try {
      const result = await api.mic.createMobileLoopPackage({
        video_id: referenceVideoId,
        clip_from_s: clipFromS,
        clip_to_s: clipToS,
        pause_s: loopPauseS,
        repeat_count: loopRepeatCount,
        include_sync_round: mobileLoopSyncFirstRound,
      })
      setMobileLoopPackage(result)
      setShowAllMobileLoopHistory(false)
      await loadMobileLoopHistory()
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
    setMobileLoopPackage({
      package_id: pkg.package_id,
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
    setMobileLoopPackageError(null)
  }

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
    const metrics: Record<string, unknown> = {
      mic_session_id: sessionId,
      mic_test_mode: testMode,
      reference_label: referenceLabel,
      reference_text: referenceText,
      mobile_loop_enabled: loopCfg.enabled,
      mobile_loop_speech_s: loopCfg.speechS,
      mobile_loop_capture_speech_s: loopCfg.captureSpeechS,
      mobile_loop_early_stop_s: loopCfg.earlyStopS,
      mobile_loop_pause_s: loopCfg.pauseS,
      mobile_loop_sync_first_round: loopCfg.syncFirstRound,
      mobile_loop_measured_rounds: loopCfg.measuredRounds,
      mobile_loop_autostop: loopCfg.autoStop,
      mobile_loop_package_id: loopCfg.packageId,
      auto_model_sequence_token: autoSequenceMeta?.sequence_token ?? null,
      auto_model_sequence_index: autoSequenceMeta ? autoSequenceMeta.sequence_index + 1 : null,
      auto_model_sequence_total: autoSequenceMeta?.sequence_total ?? null,
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
    const noteWithSequence = autoSequenceMeta
      ? `${note} | seq=${autoSequenceMeta.sequence_index + 1}/${autoSequenceMeta.sequence_total}`
      : note
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
        note: noteWithSequence,
        quality_assessment: quality,
        transcript: String(finalMsg.text ?? transcript ?? ''),
        source: source ?? 'web_mic_auto',
      })
      if (autoSequenceMeta) {
        if (autoSequenceMeta.sequence_token === autoModelSequenceTokenRef.current) {
          autoModelSavedSlotsRef.current.add(autoSequenceMeta.sequence_index)
          const savedCount = autoModelSavedSlotsRef.current.size
          setAutoModelSavedCount(savedCount)
          setSaveMsg(`Uloženo ${savedCount}/${autoSequenceMeta.sequence_total} (${saved.record_id}).`)
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
    const activeParams = modelOverrideId
      ? buildMicParamsWithSaved(activeModel, paramsByModel[activeModelId])
      : params

    setError(null)
    setTranscript('')
    setMetrics(null)
    setSaveMsg(null)
    setRecordingElapsedS(0)
    setRecordingStartedAtPerfMs(null)
    autoStopFiredRef.current = false
    setStatus('connecting')

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
      }
      const activeLoopSyncFirstRound = mobileLoopEnabled ? (autoSequenceMeta ? false : mobileLoopSyncFirstRound) : null
      const activeLoopMeasuredRounds = mobileLoopEnabled ? (autoSequenceMeta ? 1 : loopRepeatCount) : null
      const activeLoopPackageId = mobileLoopEnabled ? (mobileLoopPackage?.package_id ?? null) : null
      if (mobileLoopEnabled) {
        sessionParams.mobile_loop_enabled = true
        sessionParams.mobile_loop_speech_s = loopSpeechS
        sessionParams.mobile_loop_capture_speech_s = loopCaptureSpeechS
        sessionParams.mobile_loop_early_stop_s = loopEarlyStopS
        sessionParams.mobile_loop_pause_s = loopPauseS
        sessionParams.mobile_loop_sync_first_round = activeLoopSyncFirstRound
        sessionParams.mobile_loop_measured_rounds = activeLoopMeasuredRounds
        sessionParams.mobile_loop_auto_stop = mobileLoopAutoStop
      }
      if (autoSequenceMeta) {
        sessionParams.auto_model_sequence_token = autoSequenceMeta.sequence_token
        sessionParams.auto_model_sequence_index = autoSequenceMeta.sequence_index + 1
        sessionParams.auto_model_sequence_total = autoSequenceMeta.sequence_total
      }
      activeSessionLoopConfigRef.current = {
        enabled: mobileLoopEnabled,
        speechS: mobileLoopEnabled ? loopSpeechS : null,
        captureSpeechS: mobileLoopEnabled ? loopCaptureSpeechS : null,
        earlyStopS: mobileLoopEnabled ? loopEarlyStopS : null,
        pauseS: mobileLoopEnabled ? loopPauseS : null,
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
      // 1. Vytvoř backend session
      const { session_id } = await api.mic.createSession(activeModelId, sessionParams)

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
            text: currentTranscriptTextRef.current || '',
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
          if (sequenceTiming) parts.push('sequence_timing=logged')
          const reasonCode = session.reason_code || 'ws_transport_error'
          const reasonError = session.error || prefix
          persistFailureResult(reasonCode, reasonError, prefix, {
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

      ws.onopen = () => {
        const nowPerf = performance.now()
        const leadMs = autoModelLeadStartSeconds * 1000
        if (autoSequenceMeta && autoSequenceMeta.sequence_index === 0) {
          sequenceAnchorAtPerfMsRef.current = nowPerf
        }
        const sequenceAnchorPerf = sequenceAnchorAtPerfMsRef.current ?? nowPerf
        const nextSlotStopPerf = autoSequenceMeta
          ? (sequenceAnchorPerf + (autoSequenceMeta.sequence_index + 1) * loopCycleS * 1000 - leadMs)
          : (nowPerf + effectiveTrialPlanS * 1000)
        trialStartedAtPerfMsRef.current = nowPerf
        trialHardLimitAtPerfMsRef.current = nowPerf + autoModelHardTrialSeconds * 1000
        trialDeadlineAtPerfMsRef.current = Math.max(nowPerf + 1000, nextSlotStopPerf)
        trialAdaptiveEarlyStopMsRef.current = 0
        lastTranscriptUpdateAtPerfMsRef.current = nowPerf
        currentTranscriptTextRef.current = ''
        stopRequestedAtPerfMsRef.current = null
        setRecordingStartedAtPerfMs(nowPerf)
        setRecordingElapsedS(0)
        setStatus('recording')
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
            wsCompleted = true
            setTranscript(msg.text || '')
            currentTranscriptTextRef.current = String(msg.text || '')
            lastTranscriptUpdateAtPerfMsRef.current = performance.now()
            setMetrics({
              latency_ms: msg.first_word_latency_ms,
              first_word_audio_ms: msg.first_word_audio_ms,
              first_word_wall_ms: msg.first_word_wall_ms,
              rtf: msg.rtf,
              elapsed_s: msg.elapsed_s,
              processing_ms_p95: msg.processing_ms_p95,
              segment_finalize_ms_p95: msg.segment_finalize_ms_p95,
              capture_jitter_ms_p95: msg.capture_jitter_ms_p95,
              capture_lag_ms_p95: msg.capture_lag_ms_p95,
              queue_depth_peak_ms: (typeof msg.queue_depth_peak_s === 'number') ? msg.queue_depth_peak_s * 1000 : undefined,
              backpressure_events: msg.backpressure_events,
              drop_rate: msg.drop_rate,
              worker_rss_peak_mb: msg.worker_rss_peak_mb,
              reason_code: msg.reason_code,
            })
            setStatus('done')
            stopRequestedAtPerfMsRef.current = null
            trialHardLimitAtPerfMsRef.current = null
            setRecordingStartedAtPerfMs(null)
            _stopAudio()
            void persistWebMicResult({
              sessionId: session_id,
              finalMsg: msg as Record<string, unknown>,
              modelIdForSession: activeModelId,
              referenceLabel,
              referenceText,
              autoSequenceMeta,
            })
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
        if (stopRequestedAtPerfMsRef.current != null) return
        const parts: string[] = []
        if (typeof event.code === 'number') parts.push(`code=${event.code}`)
        if (event.reason) parts.push(`reason=${event.reason}`)
        parts.push(`clean=${event.wasClean ? '1' : '0'}`)
        const suffix = parts.length > 0 ? ` (${parts.join(', ')})` : ''
        handleWsFailure(`WebSocket spojení bylo neočekávaně ukončeno${suffix}`)
      }

      // 3. Otevři mikrofon
      const constraints: MediaStreamConstraints = {
        audio: deviceIndex !== null
          ? { deviceId: { exact: devices[deviceIndex]?.name } }
          : true,
      }
      const stream = await navigator.mediaDevices.getUserMedia(constraints)
      streamRef.current = stream
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
            ws.send(_buildFramedPcmPayload(int16, captureTsMs))
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
        ws.send(_buildFramedPcmPayload(int16, captureTsMs))
      }
      source.connect(processor)
      processor.connect(audioCtx.destination)

    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
      setStatus('error')
      setRecordingStartedAtPerfMs(null)
    }
  }, [
    modelId,
    params,
    paramsByModel,
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
    mobileLoopSyncFirstRound,
    loopRepeatCount,
    mobileLoopAutoStop,
    loopCycleS,
    effectiveTrialPlanS,
    autoModelLeadStartSeconds,
    autoModelHardTrialSeconds,
    autoModelLatencyGuardSeconds,
    autoModelAdaptiveMaxCutSeconds,
    selectedReferenceTextId,
    selectedReferenceText,
    referenceTexts,
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
    if (autoModelCycleEnabled) {
      const selectedModelIds = autoModelSelectedOrdered
      if (selectedModelIds.length === 0) {
        setError('Pro auto-střídání vyber alespoň jeden model.')
        setStatus('error')
        return
      }
      const queueLength = Math.max(1, loopRepeatCount)
      const queue = Array.from({ length: queueLength }, (_, idx) => selectedModelIds[idx % selectedModelIds.length])
      const token = `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`
      autoModelSequenceTokenRef.current = token
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

      const firstModelId = queue[0]
      const firstModel = availableModels.find((m) => m.model_id === firstModelId)
      setModelId(firstModelId)
      setParams(buildMicParamsWithSaved(firstModel, paramsByModel[firstModelId]))
      setSaveMsg(`Auto sekvence: model 1/${queue.length} (${firstModelId}).`)
      await startSession(firstModelId, {
        sequence_token: token,
        sequence_index: 0,
        sequence_total: queue.length,
      })
      return
    }

    await startSession()
  }, [
    autoModelCycleEnabled,
    availableModels,
    autoModelSelectedOrdered,
    loopRepeatCount,
    paramsByModel,
    startSession,
  ])

  const stopAutoModelSequence = useCallback((note = 'Auto sekvence zastavena.') => {
    autoModelSequenceTokenRef.current = null
    autoModelAdvanceLockRef.current = false
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
      syncFirstRound: null,
      measuredRounds: null,
      autoStop: null,
      packageId: null,
    }
    if (autoModelAdvanceTimerRef.current != null) {
      window.clearTimeout(autoModelAdvanceTimerRef.current)
      autoModelAdvanceTimerRef.current = null
    }
    setAutoModelSequenceActive(false)
    setAutoModelSequenceIds([])
    setAutoModelSequenceIndex(0)
    setAutoModelSavedCount(0)
    autoModelSavedSlotsRef.current.clear()
    setSaveMsg(note)
  }, [])

  const requestTrialStop = useCallback((note?: string) => {
    if (note) setSaveMsg(note)
    setStatus('stopping')
    stopRequestedAtPerfMsRef.current = performance.now()
    setRecordingStartedAtPerfMs(null)
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: 'stop' }))
    }
    _stopAudio()
  }, [])

  const stop = useCallback(() => {
    if (autoModelSequenceActive) {
      stopAutoModelSequence('Auto sekvence zastavena uživatelem.')
    }
    requestTrialStop()
  }, [autoModelSequenceActive, stopAutoModelSequence, requestTrialStop])

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
      requestTrialStop(`Auto sekvence: konec slotu ${formatDurationHms(loopCycleS)}${stopNote}.`)
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
      requestTrialStop(`Auto sekvence: hard cap ${autoModelHardTrialSeconds}s (forced stop).`)
    }, 150)

    return () => window.clearInterval(timer)
  }, [autoModelSequenceActive, status, autoModelHardTrialSeconds, requestTrialStop])

  useEffect(() => {
    if (!autoModelSequenceActive) return
    if (status !== 'recording') return

    const timer = window.setInterval(() => {
      if (stopRequestedAtPerfMsRef.current != null) return
      const nowPerf = performance.now()
      const lastUpdatePerf = lastTranscriptUpdateAtPerfMsRef.current ?? trialStartedAtPerfMsRef.current
      if (lastUpdatePerf == null) return
      const silentS = (nowPerf - lastUpdatePerf) / 1000
      if (silentS < autoModelSilenceStopSeconds) return
      setError(`Auto sekvence: ${autoModelSilenceStopSeconds}s bez nového přepisu.`)
      requestTrialStop(`Auto sekvence: ${autoModelSilenceStopSeconds}s bez nového textu (stop trialu).`)
    }, 250)

    return () => window.clearInterval(timer)
  }, [autoModelSequenceActive, status, autoModelSilenceStopSeconds, requestTrialStop])

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
      stopRequestedAtPerfMsRef.current = null
      try {
        wsRef.current?.close()
      } catch {}
      _stopAudio()
      setError(`Model nestihl doběhnout do ${cappedGraceS.toFixed(1)}s po stopu (auto-skip).`)
      setStatus('error')
      setSaveMsg(`Auto sekvence: přeskočen model po timeoutu doběhu (${cappedGraceS.toFixed(1)}s).`)
    }, 300)

    return () => window.clearInterval(timer)
  }, [autoModelSequenceActive, status, autoModelGraceSeconds, autoModelSequenceIndex, loopCycleS, autoModelLeadStartSeconds, autoModelHardTrialSeconds])

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
      setSaveMsg(`Retry (${autoModelRetryCountRef.current}/1): ${retryModelId}`)
      autoModelAdvanceLockRef.current = true
      autoModelAdvanceTimerRef.current = window.setTimeout(() => {
        autoModelAdvanceTimerRef.current = null
        if (autoModelSequenceTokenRef.current !== token) { autoModelAdvanceLockRef.current = false; return }
        setModelId(retryModelId)
        setParams(buildMicParamsWithSaved(retryModel, paramsByModel[retryModelId]))
        void startSession(retryModelId, {
          sequence_token: token,
          sequence_index: autoModelSequenceIndex,
          sequence_total: autoModelSequenceIds.length,
        }).finally(() => { autoModelAdvanceLockRef.current = false })
      }, 3000)
      return
    }
    autoModelRetryCountRef.current = 0

    const nextIndex = autoModelSequenceIndex + 1
    if (nextIndex >= autoModelSequenceIds.length) {
      stopAutoModelSequence(`Auto sekvence dokončena (${autoModelSequenceIds.length}/${autoModelSequenceIds.length}).`)
      return
    }

    const anchorPerf = sequenceAnchorAtPerfMsRef.current ?? performance.now()
    sequenceAnchorAtPerfMsRef.current = anchorPerf
    const nowPerf = performance.now()
    const leadMs = autoModelLeadStartSeconds * 1000
    const targetStartPerf = anchorPerf + nextIndex * loopCycleS * 1000 - leadMs
    const waitMs = Math.max(0, targetStartPerf - nowPerf)

    autoModelAdvanceLockRef.current = true
    autoModelAdvanceTimerRef.current = window.setTimeout(() => {
      autoModelAdvanceTimerRef.current = null
      if (autoModelSequenceTokenRef.current !== token) {
        autoModelAdvanceLockRef.current = false
        return
      }

      const nextModelId = autoModelSequenceIds[nextIndex]
      const nextModel = availableModels.find((m) => m.model_id === nextModelId)
      setAutoModelSequenceIndex(nextIndex)
      setModelId(nextModelId)
      setParams(buildMicParamsWithSaved(nextModel, paramsByModel[nextModelId]))
      setSaveMsg(`Auto sekvence: model ${nextIndex + 1}/${autoModelSequenceIds.length} (${nextModelId}).`)

      void startSession(nextModelId, {
        sequence_token: token,
        sequence_index: nextIndex,
        sequence_total: autoModelSequenceIds.length,
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
    availableModels,
    paramsByModel,
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
    _stopAudio()
    wsRef.current?.close()
  }, [])

  return (
    <div className="bg-gray-800 rounded-lg p-4 space-y-4">
      <h3 className="text-white font-semibold text-lg">Mic — live přepis</h3>

      {/* Výběr modelu */}
      <div className="flex gap-3 flex-wrap">
        <div>
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
            className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-white"
          >
            {availableModels.map(m => (
              <option key={m.model_id} value={m.model_id}>
                {m.label} [{m.languages.join(', ')}]
              </option>
            ))}
          </select>
          <div className="mt-1 text-[11px] text-gray-500">
            Výchozí mic: small `threads=4, beam=5`; turbo `threads=8, beam=2` (data 27.-29. 3.).
          </div>
        </div>

        {devices.length > 0 && (
          <div>
            <label className="block text-xs text-gray-400 mb-1">Mikrofon</label>
            <select
              value={deviceIndex ?? ''}
              onChange={e => setDeviceIndex(e.target.value === '' ? null : Number(e.target.value))}
              disabled={uiLocked}
              className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-white"
            >
              <option value="">výchozí</option>
              {devices.map(d => (
                <option key={d.index} value={d.index}>{d.name}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="bg-gray-900/50 border border-gray-700 rounded p-3 space-y-2">
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
              Sekvence {Math.min(autoModelSequenceIndex + 1, autoModelSequenceIds.length)}/{autoModelSequenceIds.length}
              {' '}| uloženo {Math.min(autoModelSavedCount, autoModelSequenceIds.length)}/{autoModelSequenceIds.length}
            </span>
          )}
        </div>
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
                  onClick={() => stopAutoModelSequence('Auto sekvence zastavena uživatelem.')}
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
              <span className="text-[11px] text-gray-500">bez nového textu</span>
              <span className="text-[11px] text-gray-500">hard cap trialu {autoModelHardTrialSeconds.toFixed(0)}s</span>
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
            <div className="text-[11px] text-gray-400">
              Pořadí běhu: podle pořadí naklikání modelů. Vybráno: {autoModelSelectedOrdered.length}.
              Kola: {loopRepeatCount}. Slot: {formatDurationHms(autoModelSlotSeconds)} (řeč {Math.round(loopSpeechS)}s + pauza {loopPauseS}s),
              sběr řeči: ~{Math.round(loopCaptureSpeechS)}s (konec dříve o {loopEarlyStopS.toFixed(1)}s),
              další start ~{autoModelLeadStartSeconds.toFixed(1)}s před slotem, max doběh {autoModelGraceSeconds}s.
              Pokud je vybraných modelů méně než kol, jedou dokola.
              Stop má pevnou přípravu {autoModelPreparationSeconds}s + hard cap {autoModelHardTrialSeconds.toFixed(0)}s/trial.
              Při latenci prvního slova nad {autoModelLatencyGuardSeconds}s se trial zkrátí ještě víc (adaptivně).
            </div>
          </>
        )}
      </div>

      <div className="bg-gray-900/60 border border-gray-700 rounded p-3 space-y-2">
        <div className="text-xs text-gray-400">Režim mic testu</div>
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
            <div className="rounded border border-gray-700 bg-gray-900/70 p-2 space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-xs text-gray-300 font-semibold">Mobil loop asistent</div>
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
              <div className="grid grid-cols-1 md:grid-cols-6 gap-2">
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
              {mobileLoopEnabled && (
                <div className="text-[11px] text-gray-400">
                  Plán: {mobileLoopSyncFirstRound ? '1 sync kolo + ' : ''}{loopRepeatCount} měřené kolo(a),
                  režim {loopSpeechS.toFixed(1)}-{loopEarlyStopS.toFixed(1)}+{loopPauseS}s
                  {' '}=&gt; sběr {loopCaptureSpeechS.toFixed(1)}s + pauza {loopPauseS}s, slot {loopCycleS.toFixed(1)}s, celkem {formatDurationHms(loopPlanS)}.
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
                    <button
                      type="button"
                      onClick={() => void loadMobileLoopHistory()}
                      disabled={mobileLoopHistoryLoading || uiLocked}
                      className="px-2 py-1 border border-gray-700 rounded text-[11px] text-gray-300 hover:text-white disabled:opacity-60"
                    >
                      {mobileLoopHistoryLoading ? 'Načítám...' : '↻ Obnovit'}
                    </button>
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
                                    {pkg.package_id}
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

      {/* Parametry modelu */}
      {selectedModel && selectedModel.params.length > 0 && (
        <details className="text-sm">
          <summary className="text-gray-400 cursor-pointer hover:text-gray-200 select-none">
            Parametry modelu
          </summary>
          <div className="mt-2 pl-2 border-l border-gray-600">
            <ModelParamsForm
              modelId={modelId}
              params={selectedModel.params}
              values={params}
              onChange={updateModelParams}
              compact
              hints={MIC_PARAM_HINTS}
            />
          </div>
        </details>
      )}

      {/* Ovládání */}
      <div className="flex gap-2 items-center">
        {status === 'idle' || status === 'done' || status === 'error' ? (
          <button
            onClick={start}
            disabled={autoModelSequenceActive}
            className="px-4 py-2 bg-red-600 hover:bg-red-500 disabled:opacity-60 disabled:hover:bg-red-600 text-white rounded font-medium text-sm"
          >
            {autoModelCycleEnabled
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
            Sekvence modelů: {Math.min(autoModelSequenceIndex + 1, autoModelSequenceIds.length)}/{autoModelSequenceIds.length}
            {' '}| uloženo {Math.min(autoModelSavedCount, autoModelSequenceIds.length)}/{autoModelSequenceIds.length}
          </span>
        )}
      </div>
      {status === 'recording' && testMode === 'reference_video' && mobileLoopEnabled && (
        <div className="bg-gray-900 border border-gray-700 rounded p-2 text-xs text-gray-200 space-y-1">
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
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-gray-300 border-collapse">
              <thead>
                <tr className="text-gray-500 border-b border-gray-700">
                  <th className="text-left pr-2 py-1">#</th>
                  <th className="text-left pr-2 py-1">Model</th>
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
                  return (
                    <tr key={t.session_id} className="border-b border-gray-800 hover:bg-gray-800/40">
                      <td className="pr-2 py-0.5">{t.seq_index ?? '—'}</td>
                      <td className="pr-2 py-0.5 max-w-[140px] truncate" title={t.model_id}>{t.model_id}</td>
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
                  <th className="text-left py-1">Přepis</th>
                  <th className="text-left py-1 pl-2">Akce</th>
                </tr>
              </thead>
              <tbody>
                {historyVisibleRows.map((r, idx) => {
                  const hasTranscript = !!r.transcript?.trim()
                  const isExpanded = expandedHistoryRecordIds.includes(r.record_id)
                  const canExpand = hasTranscript && r.transcript.trim().length > 180
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
                        <td className="py-1 text-gray-200 max-w-xs">
                          {hasTranscript ? (
                            <div className="space-y-1">
                              <div>{isExpanded ? r.transcript.trim() : clipText(r.transcript, 120)}</div>
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
                          ) : '—'}
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
                          <td colSpan={16} className="py-2 pl-2 pr-1">
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
