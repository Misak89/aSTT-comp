import { useEffect, useRef, useState } from 'react'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts'
import { api } from '../api/client'
import type {
  AudioDevice,
  LibraryItem,
  ModelDescriptor,
  TuningDecisionReport,
  TuningEventItem,
  TuningEventStats,
  TuningEventValidation,
  TuningInputMode,
  TuningJobStatus,
  TuningMicCalibrationCheckResponse,
  TuningTrialResult,
} from '../types'
import { WerBadge } from '../components/WerBadge'
import { videoLabel } from '../utils'
import { formatClockHms, formatFileStampWithSeconds } from '../lib/time'

type Strategy = 'grid' | 'ablation' | 'random' | 'smart'

// Parametry (bez initial_prompt — ten má vlastní UI)
const WHISPER_PARAM_DEFS = [
  {
    name: 'beam_size', label: 'Beam size', type: 'int', values: [1, 2, 3, 5, 8, 10, 12], default: 5,
    description: 'Počet kandidátních sekvencí při beam search. Vyšší = přesnější přepis, pomalejší. beam=1 je greedy (nejrychlejší, nejhorší WER). beam=5 je dobrý kompromis. ⚠ Hodnoty 10 a 12 mohou selhat u menších modelů (small, base) — whisper-cli vrátí "failed to process audio".',
  },
  {
    name: 'best_of', label: 'Best of', type: 'int', values: [1, 2, 3, 5], default: 5,
    description: 'Počet vzorkování při temperature > 0. Má vliv jen pokud model použije temperature fallback (ne při beam search s no_fallback=true). Při no_fallback=true nemá žádný efekt.',
    valueDescriptions: { 1: 'bez vzorkování', 5: 'výchozí whisper.cpp' },
  },
  {
    name: 'threads', label: 'Vlákna CPU', type: 'int', values: [2, 4, 6, 8], default: 4,
    description: 'Počet CPU vláken pro inference. Více vláken = nižší RTF (rychlejší zpracování). Optimum závisí na CPU — obvykle polovina fyzických jader.',
  },
  {
    name: 'no_fallback', label: 'Bez fallbacku', type: 'bool', values: [true, false], default: true,
    description: 'Zakáže temperature fallback. Pokud je true, whisper nikdy nezkusí vyšší temperature při nejistém výsledku — rychlejší, deterministické. false = whisper může zopakovat dekódování s vyšší teplotou.',
    valueDescriptions: { true: 'deterministické, rychlejší', false: 'může opakovat s vyšší temp.' },
  },
  {
    name: 'chunk_seconds', label: 'Chunk (s)', type: 'int', values: [5, 10, 15, 20, 30], default: 30,
    description: 'Délka chunks pro replay/perceived delay výpočet. Pro whisper batch nemění dekódování textu, ale mění UX zpoždění. V3 srovnání drž typicky na 15/30.',
  },
]

// Doménová knihovna initial_prompt šablon
type PromptTemplate = { id: string; label: string; domain: string; color: string; prompts: { label: string; text: string }[] }
const PROMPT_LIBRARY: PromptTemplate[] = [
  {
    id: 'none', label: 'Bez promptu', domain: 'baseline', color: 'gray',
    prompts: [{ label: 'Prázdný (baseline)', text: '' }],
  },
  {
    id: 'czech_general', label: 'Čeština — obecně', domain: 'general', color: 'blue',
    prompts: [
      { label: 'Obecný rozhovor', text: 'Rozhovor v cestine:' },
      { label: 'Kontext krátký', text: 'Dobry den, toto je rozhovor v cestine.' },
      { label: 'Delší kontext', text: 'Toto je rozhovor dvou lidi v ceskem jazyce. Mluvi plynne cesky.' },
    ],
  },
  {
    id: 'topic_auto', label: 'Téma z názvu videa', domain: 'auto', color: 'violet',
    prompts: [], // generováno dynamicky dle vybraného videa
  },
  {
    id: 'medicine', label: 'Medicína', domain: 'medical', color: 'red',
    prompts: [
      { label: 'Obecné lékařské vyšetření', text: 'Toto je lekarske vysetreni. Lekar hovori s pacientem o zdravotnim stavu, symptomech a lecbe.' },
      { label: 'Kardiologie', text: 'Toto je kardiologicke vysetreni. Lekar kardiolog diskutuje o srdecnich onemocnenich, EKG, krevnim tlaku a lecbe.' },
      { label: 'Neurologie', text: 'Toto je neurologicke vysetreni. Neurolog hodnoti reflexy, pohybove funkce a neurologicke symptomy pacienta.' },
      { label: 'Psychiatrie', text: 'Toto je psychiatricka konzultace. Psychiatr hovori s pacientem o dusevnim zdravi, nalade a psychologickych symptomech.' },
      { label: 'Praktický lékař', text: 'Ordinace praktickeho lekare. Pacient popisuje potize, lekar doporucuje vysetreni a lecbu.' },
      { label: 'Onkologie', text: 'Onkologicka konzultace. Lekar diskutuje o diagnoze nadoroveho onemocneni, chemoterapii a prognoze.' },
      { label: 'Operace / chirurgie', text: 'Chirurgicke pracoviste. Lekari diskutuji o operacnim zakroku, priprave pacienta a postoperacni peci.' },
      { label: 'Alergologie a imunologie', text: 'Alergologicka a imunologicka konzultace. Lekar hodnoti alergicke reakce, imunitni system, precitlivelos a imunoterapii pacienta.' },
    ],
  },
  {
    id: 'technology', label: 'Technologie', domain: 'tech', color: 'indigo',
    prompts: [
      { label: 'IT a software', text: 'Technicky rozhovor o softwaru, programovani a informatice.' },
      { label: 'Hardware a počítače', text: 'Rozhovor o pocitacovem hardwaru, procesorech, grafickych kartach a technologiich.' },
      { label: 'Herní průmysl', text: 'Rozhovor o videohrach, vyvoji her a hernim prumyslu.' },
      { label: 'Umělá inteligence', text: 'Diskuse o umele inteligenci, strojovem uceni a neuronovych sitich.' },
    ],
  },
  {
    id: 'sport', label: 'Sport', domain: 'sport', color: 'green',
    prompts: [
      { label: 'Obecný sport', text: 'Sportovni rozhovor. Sportovci a treneri diskutuji o vykonu, treninku a soutezich.' },
      { label: 'Fotbal', text: 'Fotbalovy rozhovor. Hraci a treneri diskutuji o zapasech, taktice a lize.' },
      { label: 'Esport', text: 'Rozhovor s profesionalnim hracem esportu o turnajich, strategii a tymove spolupraci.' },
    ],
  },
  {
    id: 'podcast', label: 'Podcast / pořad', domain: 'media', color: 'orange',
    prompts: [
      { label: 'Obecný podcast', text: 'Podcastovy rozhovor. Moderator diskutuje s hostem o ruznych tematech.' },
      { label: 'Věda a vzdělávání', text: 'Vzdelavaci porad. Odbornik vysvetluje vedecka temata srozumitelne pro verejnost.' },
      { label: 'Byznys a ekonomika', text: 'Obchodni rozhovor o ekonomice, podnikani, investicich a financnich trzich.' },
      { label: 'Politika a společnost', text: 'Politicka diskuse. Politici a novinari hovori o spolecenskych otazkach a vladni politice.' },
    ],
  },
]

const LOAD_PROFILE_PRESETS: Record<string, { cpu: number; ram: number }> = {
  light: { cpu: 25, ram: 35 },
  medium: { cpu: 45, ram: 55 },
  heavy: { cpu: 65, ram: 75 },
}
const CONSTRAINTS_PROFILE_PRESETS: Record<string, { cores: number; ram: number; priority: string }> = {
  weak_cap: { cores: 2, ram: 4096, priority: 'idle' },
  mid_cap: { cores: 4, ram: 8192, priority: 'below_normal' },
}

const LS_KEY = 'tuning_config_v1'
const isLargeWhisperV3Model = (modelId: string): boolean => modelId.startsWith('whisper_cpp_large_v3')
type TuningVideoSortKey = 'title' | 'language' | 'duration' | 'upload_date' | 'genre' | 'view_count'
const TUNING_VIDEO_SORT_LABELS: Array<{ key: TuningVideoSortKey; label: string }> = [
  { key: 'title', label: 'Název' },
  { key: 'language', label: 'Jazyk' },
  { key: 'duration', label: 'Délka' },
  { key: 'upload_date', label: 'Datum' },
  { key: 'genre', label: 'Žánr' },
  { key: 'view_count', label: 'Zhlédnutí' },
]
const TUNING_VIDEO_SORT_DEFAULT_DIR: Record<TuningVideoSortKey, 'asc' | 'desc'> = {
  title: 'asc',
  language: 'asc',
  duration: 'asc',
  upload_date: 'desc',
  genre: 'asc',
  view_count: 'desc',
}

function loadConfig() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}') } catch { return {} }
}

function parseIsoToMs(iso: string | null | undefined): number | null {
  if (!iso) return null
  const ms = Date.parse(iso)
  return Number.isFinite(ms) ? ms : null
}

function jobElapsedSeconds(job: TuningJobStatus, nowMs: number): number | null {
  const startMs = parseIsoToMs(job.created_at)
  if (startMs == null) return null
  const isActive = job.status === 'running' || job.status === 'pending'
  const endMs = isActive ? nowMs : (parseIsoToMs(job.updated_ts ?? null) ?? nowMs)
  return Math.max(0, (endMs - startMs) / 1000)
}

function formatElapsedShort(totalSeconds: number): string {
  const whole = Math.max(0, Math.floor(totalSeconds))
  const h = Math.floor(whole / 3600)
  const m = Math.floor((whole % 3600) / 60)
  const s = whole % 60
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  return `${m}:${s.toString().padStart(2, '0')}`
}

function formatClockHHMMSS(iso: string | null | undefined): string {
  const ms = parseIsoToMs(iso)
  if (ms == null) return '–'
  return formatClockHms(ms)
}

type LatencyLane = 'strict_live' | 'probe_online' | 'batch_proxy' | 'mixed' | 'unknown'

function normalizeLatencyLane(
  latencyLane: string | null | undefined,
  latencyQuality: string | null | undefined,
  perceivedDelayQuality: string | null | undefined,
): LatencyLane {
  const lane = String(latencyLane ?? '').trim().toLowerCase()
  if (lane === 'strict_live' || lane === 'probe_online' || lane === 'batch_proxy' || lane === 'mixed') {
    return lane
  }
  if (lane === 'unknown') return 'unknown'

  const q = String(latencyQuality ?? '').trim().toLowerCase()
  if (q === 'measured_live') return 'strict_live'
  if (q === 'probe_online') return 'probe_online'
  if (q === 'proxy_offline') return 'batch_proxy'
  if (q === 'mixed') return 'mixed'
  if (q === 'unknown') return 'unknown'

  const pq = String(perceivedDelayQuality ?? '').trim().toLowerCase()
  if (pq === 'low') return 'batch_proxy'
  return 'unknown'
}

function isApproxLatencyLane(lane: LatencyLane): boolean {
  return lane === 'batch_proxy' || lane === 'mixed' || lane === 'unknown'
}

function decisionPoolLabel(pool: TuningDecisionReport['selected_pool'] | string | null | undefined): string {
  switch (pool) {
    case 'strict_live':
      return 'strict_live'
    case 'probe_online_fallback':
      return 'probe_online fallback'
    case 'batch_proxy_fallback':
      return 'batch_proxy fallback'
    case 'mixed_fallback':
      return 'mixed fallback'
    case 'unknown_fallback':
      return 'unknown fallback'
    case 'fallback_all':
      return 'fallback_all'
    default:
      return 'unknown'
  }
}

function formatLaneCounts(laneCounts: Record<string, number> | undefined): string {
  if (!laneCounts) return '—'
  const order: LatencyLane[] = ['strict_live', 'probe_online', 'batch_proxy', 'mixed', 'unknown']
  const parts = order
    .filter((lane) => (laneCounts[lane] ?? 0) > 0)
    .map((lane) => `${lane}:${laneCounts[lane]}`)
  return parts.length > 0 ? parts.join(', ') : '—'
}

function formatEventPayloadShort(payload: Record<string, unknown> | null | undefined): string {
  if (!payload) return ''
  const keys = ['status', 'message', 'trial_idx', 'model_id', 'error', 'best_trial_idx']
  const picked: Record<string, unknown> = {}
  for (const k of keys) {
    if (Object.prototype.hasOwnProperty.call(payload, k)) picked[k] = (payload as any)[k]
  }
  const base = Object.keys(picked).length > 0 ? picked : payload
  const raw = JSON.stringify(base)
  if (!raw) return ''
  return raw.length > 180 ? `${raw.slice(0, 177)}...` : raw
}

function formatJobHistoryName(job: TuningJobStatus): string {
  const ms = parseIsoToMs(job.created_at)
  if (ms == null) return job.job_id
  return formatFileStampWithSeconds(ms)
}

function estimateRemainingSeconds(job: TuningJobStatus, nowMs: number): number | null {
  if (job.status !== 'running' && job.status !== 'pending') return null
  if (job.total_trials <= 0) return null
  const done = Math.max(0, job.completed_trials)
  const remaining = Math.max(0, job.total_trials - done)
  if (remaining <= 0) return 0
  if (done <= 0) return null
  const elapsed = jobElapsedSeconds(job, nowMs)
  if (elapsed == null || elapsed <= 0) return null
  const avgPerTrial = elapsed / done
  return Math.max(0, avgPerTrial * remaining)
}

function textWordCount(text: string | null | undefined): number {
  if (!text) return 0
  return (text.match(/\S+/g) ?? []).length
}

function textCharCount(text: string | null | undefined): number {
  if (!text) return 0
  return [...text].length
}

type WordDiffItem = { op: string; ref: string | null; hyp: string | null; is_soft: boolean }
type WordDiffAlignedCell = {
  key: string
  top: string
  bottom: string
  topClass: string
  bottomClass: string
  widthCh: number
  title?: string
}

function visualLen(value: string): number {
  return Math.max(1, [...(value || '')].length)
}

function buildWordDiffAlignedCells(items: WordDiffItem[]): WordDiffAlignedCell[] {
  return items.map((d, idx) => {
    const op = d.op
    if (op === '=' || op === 'equal') {
      const token = d.hyp ?? d.ref ?? ''
      return {
        key: `eq-${idx}`,
        top: token,
        bottom: d.ref ?? token,
        topClass: 'text-gray-700',
        bottomClass: 'text-gray-500 italic',
        widthCh: visualLen(token) + 1,
      }
    }
    if (op === 'S' || op === 'replace') {
      const top = d.hyp ?? ''
      const bottom = d.ref ?? ''
      const isSoft = !!d.is_soft
      return {
        key: `sub-${idx}`,
        top,
        bottom,
        topClass: isSoft
          ? 'bg-green-100 text-green-700 rounded px-0.5'
          : 'bg-sky-100 text-sky-700 rounded px-0.5 font-semibold',
        bottomClass: 'bg-gray-100 text-gray-500 italic rounded px-0.5',
        widthCh: Math.max(visualLen(top), visualLen(bottom)) + 1,
        title: isSoft ? `Drobná záměna: "${bottom}" → "${top}"` : `Záměna: "${bottom}" → "${top}"`,
      }
    }
    if (op === 'D' || op === 'delete') {
      const bottom = d.ref ? `[${d.ref}]` : ''
      return {
        key: `del-${idx}`,
        top: '',
        bottom,
        topClass: 'text-transparent',
        bottomClass: 'bg-red-200 text-red-800 rounded px-0.5 font-semibold italic',
        widthCh: visualLen(bottom) + 1,
      }
    }
    if (op === 'I' || op === 'insert') {
      const top = d.hyp ?? ''
      return {
        key: `ins-${idx}`,
        top,
        bottom: '',
        topClass: 'bg-gray-100 text-gray-500 rounded px-0.5 line-through',
        bottomClass: 'text-transparent italic',
        widthCh: visualLen(top) + 1,
      }
    }
    const fallback = d.hyp ?? d.ref ?? ''
    return {
      key: `raw-${idx}`,
      top: fallback,
      bottom: d.ref ?? '',
      topClass: 'text-gray-700',
      bottomClass: 'text-gray-500 italic',
      widthCh: visualLen(fallback) + 1,
    }
  })
}

export function TuningPage() {
  const [library, setLibrary] = useState<LibraryItem[]>([])
  const [registry, setRegistry] = useState<ModelDescriptor[]>([])
  const [jobs, setJobs] = useState<TuningJobStatus[]>([])

  const cfg = loadConfig()
  const [selectedModels, setSelectedModels] = useState<string[]>(() => {
    if (cfg.selectedModels?.length) return cfg.selectedModels
    if (cfg.selectedModel) return [cfg.selectedModel]  // backward compat
    return ['whisper_cpp_small']
  })
  const [selectedVideos, setSelectedVideos] = useState<string[]>(cfg.selectedVideos ?? [])
  const [strategy, setStrategy] = useState<Strategy>(cfg.strategy ?? 'ablation')
  const [sampleSeconds, setSampleSeconds] = useState<number>(cfg.sampleSeconds ?? 60)
  const [maxTrials, setMaxTrials] = useState<number>(cfg.maxTrials ?? 16)
  const [label, setLabel] = useState<string>(cfg.label ?? '')
  const [hardwareProfile, setHardwareProfile] = useState<string>(cfg.hardwareProfile ?? 'auto')
  const [hardwareNote, setHardwareNote] = useState<string>(cfg.hardwareNote ?? '')
  const [constraintsProfile, setConstraintsProfile] = useState<string>(cfg.constraintsProfile ?? 'none')
  const [constraintsCpuCores, setConstraintsCpuCores] = useState<number>(cfg.constraintsCpuCores ?? 4)
  const [constraintsRamLimitMb, setConstraintsRamLimitMb] = useState<number>(cfg.constraintsRamLimitMb ?? 8192)
  const [constraintsPriority, setConstraintsPriority] = useState<string>(cfg.constraintsPriority ?? 'below_normal')
  const [loadProfile, setLoadProfile] = useState<string>(cfg.loadProfile ?? 'none')
  const [loadCpuTargetPct, setLoadCpuTargetPct] = useState<number>(cfg.loadCpuTargetPct ?? 45)
  const [loadRamTargetPct, setLoadRamTargetPct] = useState<number>(cfg.loadRamTargetPct ?? 55)
  const [validateBeamPreflight, setValidateBeamPreflight] = useState<boolean>(cfg.validateBeamPreflight ?? true)
  const [selectedJob, setSelectedJob] = useState<TuningJobStatus | null>(null)
  const [showAllHistoryJobs, setShowAllHistoryJobs] = useState<boolean>(false)
  const [msg, setMsg] = useState('')
  // Výběr hodnot pro každý parametr
  const [paramValues, setParamValues] = useState<Record<string, Set<unknown>>>(() => {
    const saved: Record<string, unknown[]> = cfg.paramValues ?? {}
    return Object.fromEntries(
      WHISPER_PARAM_DEFS.map(p => [p.name, new Set(saved[p.name] ?? [p.default])])
    )
  })
  // Prompt management
  const [selectedPrompts, setSelectedPrompts] = useState<Set<string>>(() => {
    // Odfiltruj uložené prompty s diakritikou (staré hodnoty z localStorage)
    const saved: string[] = cfg.selectedPrompts ?? ['']
    const clean = saved.filter((p: string) => /^[\x00-\x7F]*$/.test(p))
    return new Set(clean.length > 0 ? clean : [''])
  })
  const [customPrompt, setCustomPrompt] = useState<string>(() => {
    const p = cfg.customPrompt ?? ''
    return /^[\x00-\x7F]*$/.test(p) ? p : ''
  })
  const [promptPanelCollapsed, setPromptPanelCollapsed] = useState<boolean>(cfg.promptPanelCollapsed ?? false)
  const [clipSeed, setClipSeed] = useState<number>(cfg.clipSeed ?? 42)
  const [clipStartSeconds, setClipStartSeconds] = useState<number | null>(cfg.clipStartSeconds ?? null)
  const [repeatTopK, setRepeatTopK] = useState<number>(cfg.repeatTopK ?? 0)
  const [repeatRuns, setRepeatRuns] = useState<number>(cfg.repeatRuns ?? 1)
  const [evaluationMode, setEvaluationMode] = useState<'heuristic' | 'heuristic+llm'>(cfg.evaluationMode ?? 'heuristic')
  const [inputMode, setInputMode] = useState<TuningInputMode>(cfg.inputMode ?? 'replay')
  const [micDevices, setMicDevices] = useState<AudioDevice[]>([])
  const [micDeviceId, setMicDeviceId] = useState<string>(cfg.micDeviceId ?? '')
  const [micChunkSeconds, setMicChunkSeconds] = useState<number>(cfg.micChunkSeconds ?? 0.2)
  const [micPrepareSeconds, setMicPrepareSeconds] = useState<number>(cfg.micPrepareSeconds ?? 4)
  const [micDistanceCm, setMicDistanceCm] = useState<number>(cfg.micDistanceCm ?? 30)
  const [micPhoneVolumePct, setMicPhoneVolumePct] = useState<number>(cfg.micPhoneVolumePct ?? 70)
  const [micInputGainPct, setMicInputGainPct] = useState<number>(cfg.micInputGainPct ?? 70)
  const [micEnvironment, setMicEnvironment] = useState<'quiet' | 'office_noise'>(cfg.micEnvironment ?? 'quiet')
  const [micDeviceNote, setMicDeviceNote] = useState<string>(cfg.micDeviceNote ?? '')
  const [micChecklistConfirmed, setMicChecklistConfirmed] = useState<boolean>(cfg.micChecklistConfirmed ?? false)
  const [calibrationRmsDbfs, setCalibrationRmsDbfs] = useState<number>(cfg.calibrationRmsDbfs ?? -18)
  const [calibrationClippingPct, setCalibrationClippingPct] = useState<number>(cfg.calibrationClippingPct ?? 0.0)
  const [calibrationNoiseFloorDbfs, setCalibrationNoiseFloorDbfs] = useState<number>(cfg.calibrationNoiseFloorDbfs ?? -50)
  const [calibrationResult, setCalibrationResult] = useState<TuningMicCalibrationCheckResponse | null>(cfg.calibrationResult ?? null)
  const [calibrationCheckedAt, setCalibrationCheckedAt] = useState<string | null>(cfg.calibrationCheckedAt ?? null)
  const [checkingCalibration, setCheckingCalibration] = useState<boolean>(false)
  const isVideoSortKey = (value: unknown): value is TuningVideoSortKey => (
    value === 'title' ||
    value === 'language' ||
    value === 'duration' ||
    value === 'upload_date' ||
    value === 'genre' ||
    value === 'view_count'
  )
  const legacyVideoSortBy: TuningVideoSortKey = isVideoSortKey(cfg.videoSortBy) ? cfg.videoSortBy : 'title'
  const initialVideoSortOrder: TuningVideoSortKey[] = (
    Array.isArray(cfg.videoSortOrder) && cfg.videoSortOrder.length > 0
      ? (cfg.videoSortOrder.filter(isVideoSortKey) as TuningVideoSortKey[])
      : [legacyVideoSortBy]
  )
  const [videoSortOrder, setVideoSortOrder] = useState<TuningVideoSortKey[]>(
    initialVideoSortOrder.length > 0 ? initialVideoSortOrder : ['title']
  )
  const [videoSortDirMap, setVideoSortDirMap] = useState<Record<TuningVideoSortKey, 'asc' | 'desc'>>(() => ({
    ...TUNING_VIDEO_SORT_DEFAULT_DIR,
    ...(cfg.videoSortDirMap ?? {}),
    ...(legacyVideoSortBy ? { [legacyVideoSortBy]: cfg.videoSortDir ?? TUNING_VIDEO_SORT_DEFAULT_DIR[legacyVideoSortBy] } : {}),
  }))
  const [nowMs, setNowMs] = useState<number>(() => Date.now())
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function toggleVideoSortPriority(col: TuningVideoSortKey) {
    setVideoSortOrder(prev => (
      prev.includes(col)
        ? prev.filter(k => k !== col)
        : [...prev, col]
    ))
  }

  function toggleVideoSortDir(col: TuningVideoSortKey) {
    setVideoSortDirMap(prev => ({ ...prev, [col]: prev[col] === 'asc' ? 'desc' : 'asc' }))
  }

  const menuLibrary = library.filter(v => v.visible_in_menus !== false)
  const sortedLibrary = [...menuLibrary.filter(v => v.subtitles_local)].sort((a, b) => {
    const activeOrder: TuningVideoSortKey[] = videoSortOrder.length > 0 ? videoSortOrder : ['title']
    for (const key of activeOrder) {
      let cmp = 0
      if (key === 'title') cmp = (a.title || '').localeCompare(b.title || '', 'cs')
      else if (key === 'language') cmp = (a.language || '').localeCompare(b.language || '', 'cs')
      else if (key === 'duration') {
        const aDur = a.duration_seconds ?? -1
        const bDur = b.duration_seconds ?? -1
        cmp = aDur === bDur ? 0 : (aDur < bDur ? -1 : 1)
      } else if (key === 'upload_date') {
        const aDate = a.upload_date ?? a.added_at ?? ''
        const bDate = b.upload_date ?? b.added_at ?? ''
        cmp = aDate < bDate ? -1 : aDate > bDate ? 1 : 0
      } else if (key === 'genre') cmp = (a.genre || '').localeCompare(b.genre || '', 'cs')
      else if (key === 'view_count') {
        const aViews = a.view_count ?? -1
        const bViews = b.view_count ?? -1
        cmp = aViews === bViews ? 0 : (aViews < bViews ? -1 : 1)
      }
      if (cmp !== 0) return videoSortDirMap[key] === 'asc' ? cmp : -cmp
    }
    return 0
  })

  useEffect(() => {
    const visibleIds = new Set(menuLibrary.map(v => v.video_id))
    setSelectedVideos(prev => prev.filter(id => visibleIds.has(id)))
  }, [menuLibrary])

  const sortedHistoryJobs = [...jobs].sort((a, b) => {
    const prio = (s: string): number => (
      s === 'running' ? 0 :
      s === 'pending' ? 1 : 2
    )
    const pa = prio(a.status)
    const pb = prio(b.status)
    if (pa !== pb) return pa - pb
    const ta = parseIsoToMs(a.created_at) ?? 0
    const tb = parseIsoToMs(b.created_at) ?? 0
    return tb - ta
  })
  const historyHiddenCount = Math.max(0, sortedHistoryJobs.length - 5)
  const visibleHistoryJobs = showAllHistoryJobs ? sortedHistoryJobs : sortedHistoryJobs.slice(0, 5)

  useEffect(() => {
    Promise.all([api.library.list(), api.models.registry(), api.tuning.listJobs()])
      .then(([lib, reg, j]) => { setLibrary(lib); setRegistry(reg); setJobs(j) })
  }, [])

  useEffect(() => {
    api.mic.devices()
      .then(setMicDevices)
      .catch(() => setMicDevices([]))
  }, [])

  useEffect(() => {
    const iv = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(iv)
  }, [])

  // Uložit konfiguraci do localStorage při každé změně
  useEffect(() => {
    const cfg = {
      selectedModels, selectedVideos, strategy, sampleSeconds, maxTrials, label, clipSeed, clipStartSeconds, repeatTopK, repeatRuns, evaluationMode,
      inputMode,
      micDeviceId, micChunkSeconds, micPrepareSeconds,
      micDistanceCm, micPhoneVolumePct, micInputGainPct, micEnvironment, micDeviceNote, micChecklistConfirmed,
      calibrationRmsDbfs, calibrationClippingPct, calibrationNoiseFloorDbfs, calibrationResult, calibrationCheckedAt,
      hardwareProfile, hardwareNote,
      constraintsProfile, constraintsCpuCores, constraintsRamLimitMb, constraintsPriority,
      loadProfile, loadCpuTargetPct, loadRamTargetPct,
      validateBeamPreflight,
      paramValues: Object.fromEntries(
        Object.entries(paramValues).map(([k, v]) => [k, [...v]])
      ),
      selectedPrompts: [...selectedPrompts],
      customPrompt, promptPanelCollapsed, videoSortOrder, videoSortDirMap,
    }
    localStorage.setItem(LS_KEY, JSON.stringify(cfg))
  }, [selectedModels, selectedVideos, strategy, sampleSeconds, maxTrials, label, clipSeed, clipStartSeconds, repeatTopK, repeatRuns, evaluationMode,
      inputMode, micDeviceId, micChunkSeconds, micPrepareSeconds, micDistanceCm, micPhoneVolumePct, micInputGainPct, micEnvironment, micDeviceNote, micChecklistConfirmed,
      calibrationRmsDbfs, calibrationClippingPct, calibrationNoiseFloorDbfs, calibrationResult, calibrationCheckedAt,
      hardwareProfile, hardwareNote,
      constraintsProfile, constraintsCpuCores, constraintsRamLimitMb, constraintsPriority,
      loadProfile, loadCpuTargetPct, loadRamTargetPct,
      validateBeamPreflight,
      paramValues, selectedPrompts, customPrompt, promptPanelCollapsed, videoSortOrder, videoSortDirMap])

  function toggleParamValue(paramName: string, value: unknown) {
    setParamValues(prev => {
      const next = new Set(prev[paramName])
      next.has(value) ? next.delete(value) : next.add(value)
      if (next.size === 0) next.add(value)
      return { ...prev, [paramName]: next }
    })
  }

  function togglePrompt(text: string) {
    setSelectedPrompts(prev => {
      const next = new Set(prev)
      next.has(text) ? next.delete(text) : next.add(text)
      if (next.size === 0) next.add('')
      return next
    })
  }

  function autoPromptFromTitle(title: string): string {
    // ASCII-only — whisper-cli na Windows crashuje při non-ASCII v -p
    const ascii = title.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^\x00-\x7F]/g, '')
    return `Toto je rozhovor v cestine. Tema: ${ascii}.`
  }

  function allSelectedPrompts(): string[] {
    const prompts = new Set(selectedPrompts)
    if (customPrompt.trim()) prompts.add(customPrompt.trim())
    return [...prompts]
  }

  function promptVariantLabel(variantCount: number): string {
    if (variantCount === 1) return '1 varianta'
    if (variantCount < 5) return `${variantCount} varianty`
    return `${variantCount} variant`
  }

  function promptCollapsedSummary(prompts: string[]): string {
    const hasEmpty = prompts.includes('')
    const nonEmpty = prompts.filter(p => p !== '')
    const shorten = (text: string): string => (text.length > 32 ? `${text.slice(0, 32)}...` : text)
    if (hasEmpty && nonEmpty.length === 0) return 'Bez promptu'
    if (hasEmpty) return `Bez promptu + ${nonEmpty.length} další`
    if (nonEmpty.length <= 1) return shorten(nonEmpty[0] ?? 'Bez promptu')
    return `${shorten(nonEmpty[0])} + ${nonEmpty.length - 1} další`
  }

  function resolveLoadTargets(): { profile: string; cpu: number | null; ram: number | null } {
    if (loadProfile === 'none') return { profile: 'none', cpu: null, ram: null }
    if (loadProfile === 'custom') {
      const cpu = Math.max(0, Math.min(95, Number.isFinite(loadCpuTargetPct) ? loadCpuTargetPct : 0))
      const ram = Math.max(0, Math.min(95, Number.isFinite(loadRamTargetPct) ? loadRamTargetPct : 0))
      if (cpu <= 0 && ram <= 0) return { profile: 'none', cpu: null, ram: null }
      return { profile: 'custom', cpu, ram }
    }
    const preset = LOAD_PROFILE_PRESETS[loadProfile]
    if (!preset) return { profile: 'none', cpu: null, ram: null }
    return { profile: loadProfile, cpu: preset.cpu, ram: preset.ram }
  }

  function resolveConstraintsTargets(): { profile: string; cores: number | null; ramMb: number | null; priority: string } {
    const normPriority = constraintsPriority === 'idle' || constraintsPriority === 'normal' ? constraintsPriority : 'below_normal'
    if (constraintsProfile === 'none') return { profile: 'none', cores: null, ramMb: null, priority: normPriority }
    if (constraintsProfile === 'custom') {
      const cores = Math.max(1, Math.min(128, Number.isFinite(constraintsCpuCores) ? constraintsCpuCores : 1))
      const ramMb = Math.max(256, Math.min(262144, Number.isFinite(constraintsRamLimitMb) ? constraintsRamLimitMb : 256))
      if (cores <= 0 && ramMb <= 0) return { profile: 'none', cores: null, ramMb: null, priority: normPriority }
      return { profile: 'custom', cores, ramMb, priority: normPriority }
    }
    const preset = CONSTRAINTS_PROFILE_PRESETS[constraintsProfile]
    if (!preset) return { profile: 'none', cores: null, ramMb: null, priority: normPriority }
    return { profile: constraintsProfile, cores: preset.cores, ramMb: preset.ram, priority: preset.priority }
  }

  function countTrials(): number {
    const modelCount = Math.max(1, selectedModels.length)
    const promptCount = Math.max(1, allSelectedPrompts().length)
    const repeatExtraFactor = repeatTopK > 0 && repeatRuns > 1 ? repeatTopK * (repeatRuns - 1) : 0
    if (strategy === 'ablation') {
      let n = 1
      for (const p of WHISPER_PARAM_DEFS) {
        n += Math.max(0, (paramValues[p.name]?.size ?? 1) - 1)
      }
      n += Math.max(0, promptCount - 1)
      return (n * modelCount) + repeatExtraFactor
    }
    if (strategy === 'grid') {
      const beamValues = [...(paramValues['beam_size'] ?? [5])] as number[]
      const bestOfValues = [...(paramValues['best_of'] ?? [5])] as number[]
      const otherCount = WHISPER_PARAM_DEFS
        .filter(p => p.name !== 'beam_size' && p.name !== 'best_of')
        .reduce((acc, p) => acc * (paramValues[p.name]?.size ?? 1), 1)
      // Odfiltruj neplatné kombinace beam_size × best_of (best_of > beam_size)
      let validBeamBestOf = 0
      for (const beam of beamValues)
        for (const bestOf of bestOfValues)
          if (bestOf <= beam) validBeamBestOf++
      return (validBeamBestOf * otherCount * promptCount * modelCount) + repeatExtraFactor
    }
    if (strategy === 'smart') {
      // Smart běží v kolech (gate/search/confirm), takže odhad je ~2x kandidátní pool.
      return Math.max(modelCount, (maxTrials * modelCount * 2))
    }
    return (maxTrials * modelCount) + repeatExtraFactor
  }

  async function checkMicCalibration() {
    setCheckingCalibration(true)
    try {
      const res = await api.tuning.checkMicCalibration({
        rms_dbfs: calibrationRmsDbfs,
        clipping_rate_pct: calibrationClippingPct,
        noise_floor_dbfs: calibrationNoiseFloorDbfs,
      })
      setCalibrationResult(res)
      setCalibrationCheckedAt(new Date().toISOString())
      if (res.passed) {
        setMsg('Kalibrace PASS.')
      } else {
        const firstReason = res.reasons[0] ?? 'Kalibrace neprošla.'
        setMsg(`Kalibrace FAIL: ${firstReason}`)
      }
    } catch (e: any) {
      setCalibrationResult(null)
      setCalibrationCheckedAt(null)
      setMsg(`Kalibrace chyba: ${e.message}`)
    } finally {
      setCheckingCalibration(false)
    }
  }

  async function startTuning() {
    if (!selectedModels.length) { setMsg('Vyber alespoň jeden model.'); return }
    if (!selectedVideos.length) {
      if (inputMode === 'real_mic') {
        setMsg('Real mic tuning potřebuje alespoň jedno referenční video s titulky (kvůli WER/soft-WER). Pro čistý live přepis bez videa použij Benchmark -> Mikrofon.')
      } else {
        setMsg('Vyber alespoň jedno video.')
      }
      return
    }
    if (inputMode === 'real_mic') {
      if (!micChecklistConfirmed) {
        setMsg('Real mic: potvrď checklist protokolu před startem.')
        return
      }
      if (!calibrationResult?.passed) {
        setMsg('Real mic: kalibrace není PASS. Klikni na "Ověřit kalibraci".')
        return
      }
    }
    const noFallbackSet = paramValues['no_fallback'] ?? new Set<unknown>([true])
    const bestOfVals = [...(paramValues['best_of'] ?? new Set<unknown>([1]))].map(v => Number(v)).filter(v => Number.isFinite(v))
    const onlyNoFallbackTrue = noFallbackSet.has(true) && !noFallbackSet.has(false)
    const hasBestOfAboveOne = bestOfVals.some(v => v > 1)
    let preflightHintMsg = ''
    if (onlyNoFallbackTrue && hasBestOfAboveOne) {
      preflightHintMsg = 'Nápověda: no_fallback=true + best_of>1 je redundantní (best_of nemá efekt). Nastav best_of=1 nebo zapni no_fallback=false.'
    }
    let modelIdsForJob = [...selectedModels]
    let autoAdjustMsg = ''
    if (constraintsProfile === 'weak_cap' && modelIdsForJob.some(isLargeWhisperV3Model)) {
      const hasSmallModel = registry.some(m => m.model_id === 'whisper_cpp_small')
      if (!hasSmallModel) {
        setMsg('Kombinace large_v3 + weak_cap obvykle končí timeoutem. Nainstaluj a zvol whisper_cpp_small.')
        return
      }
      modelIdsForJob = Array.from(new Set(
        modelIdsForJob.map(mid => (isLargeWhisperV3Model(mid) ? 'whisper_cpp_small' : mid))
      ))
      setSelectedModels(modelIdsForJob)
      autoAdjustMsg = 'Auto-oprava: large_v3 pod weak_cap nahrazen za whisper_cpp_small (jinak validace typicky timeoutuje).'
    }
    setMsg('')
    const paramSpace = WHISPER_PARAM_DEFS
      .map(p => ({ name: p.name, values: [...(paramValues[p.name] ?? [p.default])] }))
    // Přidej initial_prompt jako parametr
    const prompts = allSelectedPrompts()
    if (prompts.length > 0) {
      paramSpace.push({ name: 'initial_prompt', values: prompts })
    }

    const baseline: Record<string, unknown> = {}
    for (const p of WHISPER_PARAM_DEFS) {
      baseline[p.name] = p.default
    }

    try {
      const safeRepeatTopK = Math.max(0, Math.floor(repeatTopK))
      const safeRepeatRuns = Math.max(1, Math.floor(repeatRuns))
      const hardwareProfilePayload = hardwareProfile !== 'auto' ? hardwareProfile : undefined
      const hardwareNotePayload = hardwareNote.trim() ? hardwareNote.trim() : undefined
      const resolvedConstraints = resolveConstraintsTargets()
      const resolvedLoad = resolveLoadTargets()
      const micDevicePayload = (() => {
        const raw = micDeviceId.trim()
        if (!raw || raw.toLowerCase() === 'default') return undefined
        if (/^-?\d+$/.test(raw)) return Number(raw)
        return raw
      })()
      const micChunkPayload = Math.max(0.05, Math.min(2.0, Number.isFinite(micChunkSeconds) ? micChunkSeconds : 0.2))
      const micPreparePayload = Math.max(0, Math.min(60, Number.isFinite(micPrepareSeconds) ? Math.floor(micPrepareSeconds) : 4))
      const job = await api.tuning.createJob({
        model_ids: modelIdsForJob,
        input_mode: inputMode,
        video_ids: selectedVideos,
        sample_seconds: sampleSeconds,
        clip_seed: clipStartSeconds != null ? undefined : clipSeed,
        random_seed: clipSeed,
        clip_start_seconds: clipStartSeconds ?? undefined,
        strategy,
        max_trials: maxTrials,
        param_space: paramSpace,
        baseline_params: baseline,
        repeat_top_k: safeRepeatTopK,
        repeat_runs: safeRepeatRuns,
        hardware_profile: hardwareProfilePayload,
        hardware_note: hardwareNotePayload,
        constraints_profile: resolvedConstraints.profile !== 'none' ? resolvedConstraints.profile : undefined,
        constraints_cpu_cores: resolvedConstraints.cores ?? undefined,
        constraints_ram_limit_mb: resolvedConstraints.ramMb ?? undefined,
        constraints_priority: resolvedConstraints.profile !== 'none' ? resolvedConstraints.priority : undefined,
        load_profile: resolvedLoad.profile !== 'none' ? resolvedLoad.profile : undefined,
        load_cpu_target_pct: resolvedLoad.cpu ?? undefined,
        load_ram_target_pct: resolvedLoad.ram ?? undefined,
        validate_beam_preflight: validateBeamPreflight,
        label: label || undefined,
        evaluation_mode: evaluationMode,
        mic_protocol: inputMode === 'real_mic' ? {
          distance_cm: Math.max(1, Math.min(300, micDistanceCm)),
          phone_volume_pct: Math.max(0, Math.min(100, micPhoneVolumePct)),
          input_gain_pct: Math.max(0, Math.min(100, micInputGainPct)),
          environment: micEnvironment,
          device_note: micDeviceNote.trim() || undefined,
        } : undefined,
        mic_calibration: inputMode === 'real_mic' ? {
          rms_dbfs: calibrationRmsDbfs,
          clipping_rate_pct: calibrationClippingPct,
          noise_floor_dbfs: calibrationNoiseFloorDbfs,
          passed: !!calibrationResult?.passed,
          checked_at: calibrationCheckedAt ?? new Date().toISOString(),
          reasons: calibrationResult?.reasons ?? [],
        } : undefined,
        mic_device: inputMode === 'real_mic' ? micDevicePayload : undefined,
        mic_chunk_seconds: inputMode === 'real_mic' ? micChunkPayload : undefined,
        mic_prepare_seconds: inputMode === 'real_mic' ? micPreparePayload : undefined,
      })
      setJobs(prev => [job, ...prev])
      setSelectedJob(job)
      startPolling(job.job_id)
      const postStartMsg = [autoAdjustMsg, preflightHintMsg].filter(Boolean).join(' ')
      if (postStartMsg) setMsg(postStartMsg)
    } catch (e: any) {
      setMsg(`Chyba: ${e.message}`)
    }
  }

  function applyQuickV3SmokePreset() {
    const czVideos = [...menuLibrary]
      .filter(v => v.subtitles_local && (v.language || '').toLowerCase() === 'cs')
      .sort((a, b) => (a.duration_seconds ?? Number.MAX_SAFE_INTEGER) - (b.duration_seconds ?? Number.MAX_SAFE_INTEGER))
      .slice(0, 2)
      .map(v => v.video_id)

    if (czVideos.length < 2) {
      setMsg('Rychly smoke potrebuje alespon 2 CZ videa s lokalnimi titulky.')
      return
    }

    const whisperModels = registry
      .map(m => m.model_id)
      .filter(id => id.startsWith('whisper_cpp_'))
    const smokeModel =
      whisperModels.find(id => id === 'whisper_cpp_small') ??
      whisperModels.find(id => id === 'whisper_cpp_large_v3_turbo') ??
      whisperModels[0]

    if (!smokeModel) {
      setMsg('Neni dostupny whisper_cpp model pro smoke test.')
      return
    }

    const smokePrompts = ['', 'Rozhovor v cestine:']
    setSelectedModels([smokeModel])
    setSelectedVideos(czVideos)
    setStrategy('grid')
    setSampleSeconds(60)
    setClipSeed(42)
    setClipStartSeconds(null)
    setRepeatTopK(0)
    setRepeatRuns(1)
    setEvaluationMode('heuristic')
    setValidateBeamPreflight(false)
    setLabel('v3_smoke_2cz_quick')
    setSelectedPrompts(new Set(smokePrompts))
    setCustomPrompt('')
    setParamValues(prev => ({
      ...prev,
      beam_size: new Set<unknown>([2]),
      best_of: new Set<unknown>([1]),
      threads: new Set<unknown>([4]),
      no_fallback: new Set<unknown>([true]),
      chunk_seconds: new Set<unknown>([15, 30]),
    }))

    setMsg(`Preset aplikovan: rychly v3 smoke (${smokeModel}). Zkontroluj konfiguraci a klikni "Spustit tuning".`)
  }

  function applyV3DecisionGridPreset() {
    const selectedCz = selectedVideos.filter(vid => {
      const item = menuLibrary.find(v => v.video_id === vid)
      return !!item && item.subtitles_local && (item.language || '').toLowerCase() === 'cs'
    })
    const fallbackCz = [...menuLibrary]
      .filter(v => v.subtitles_local && (v.language || '').toLowerCase() === 'cs')
      .sort((a, b) => (a.duration_seconds ?? Number.MAX_SAFE_INTEGER) - (b.duration_seconds ?? Number.MAX_SAFE_INTEGER))
      .map(v => v.video_id)
    const decisionVideos = (selectedCz.length >= 4 ? selectedCz : fallbackCz).slice(0, Math.max(4, Math.min(8, selectedCz.length || 4)))

    if (decisionVideos.length < 4) {
      setMsg('Plny v3 grid potrebuje alespon 4 CZ videa s lokalnimi titulky.')
      return
    }

    const rawDecisionModels = selectedModels.length > 0
      ? selectedModels
      : ['whisper_cpp_small']
    const decisionModels = constraintsProfile === 'weak_cap'
      ? Array.from(new Set(rawDecisionModels.map(mid => (isLargeWhisperV3Model(mid) ? 'whisper_cpp_small' : mid))))
      : rawDecisionModels

    const decisionPrompts = ['', 'Rozhovor v cestine:']

    setSelectedModels(decisionModels)
    setSelectedVideos(decisionVideos)
    setStrategy('grid')
    setSampleSeconds(60)
    setClipSeed(42)
    setClipStartSeconds(null)
    setRepeatTopK(4)
    setRepeatRuns(3)
    setEvaluationMode('heuristic')
    setValidateBeamPreflight(true)
    setSelectedPrompts(new Set(decisionPrompts))
    setCustomPrompt('')
    setParamValues(prev => ({
      ...prev,
      beam_size: new Set<unknown>([1, 2, 3, 5]),
      best_of: new Set<unknown>([1]),
      threads: new Set<unknown>([2, 4, 6, 8]),
      no_fallback: new Set<unknown>([true]),
      chunk_seconds: new Set<unknown>([15, 30]),
    }))

    setLabel(label.trim() || 'v3_decision_grid')
    setMsg('Preset aplikovan: plny v3 grid. Zkontroluj konfiguraci a klikni "Spustit tuning".')
  }

  function startPolling(jobId: string) {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      const fresh = await api.tuning.getJob(jobId)
      setSelectedJob(fresh)
      setJobs(prev => prev.map(j => j.job_id === jobId ? fresh : j))
      if (fresh.status === 'completed' || fresh.status === 'failed') {
        clearInterval(pollRef.current!); pollRef.current = null
      }
    }, 3000)
  }

  const whisperModels = registry.filter(m => m.adapter === 'whisper_cpp')
  const trialCount = countTrials()
  const isSlowModel = selectedModels.some(m => m.includes('large'))
  const noFallbackSet = paramValues['no_fallback'] ?? new Set<unknown>([true])
  const bestOfVals = [...(paramValues['best_of'] ?? new Set<unknown>([1]))].map(v => Number(v)).filter(v => Number.isFinite(v))
  const chunkVals = [...(paramValues['chunk_seconds'] ?? new Set<unknown>([30]))].map(v => Number(v)).filter(v => Number.isFinite(v))
  const hasNoFallbackBestOfRedundant = noFallbackSet.has(true) && bestOfVals.some(v => v > 1)
  const hasVerySmallChunk = chunkVals.some(v => v > 0 && v < 10)
  const hasSingleVideo = selectedVideos.length === 1
  const hasWeakRepeatPlan = repeatTopK > 0 && repeatRuns < 3
  const selectedPromptVariants = allSelectedPrompts()
  const selectedPromptCount = selectedPromptVariants.length
  const selectedPromptSummary = promptCollapsedSummary(selectedPromptVariants)

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Tuning — hledání nejlepšího nastavení</h1>
      <p className="text-sm text-gray-500">
        Automaticky prochází kombinace parametrů modelu a hledá nejlepší poměr WER ↔ RTF pro online mikrofonní přepis.
        Cíl je nízké zpoždění a stabilní běh i na průměrných starších kancelářských notebookách/PC.
        Výsledky zobrazí Pareto frontier — kombinace kde nelze zlepšit jedno bez zhoršení druhého.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Model */}
        <div className="bg-white rounded border border-gray-200 p-4 space-y-2">
          <button
            className="font-semibold text-sm text-gray-700 hover:text-blue-600 hover:underline text-left"
            title="Otevřít adresář s modely"
            onClick={() => api.openDir.modelStore().catch(() => {})}
          >Modely ↗</button>
          {whisperModels.map(m => (
            <label key={m.model_id} className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" value={m.model_id}
                checked={selectedModels.includes(m.model_id)}
                onChange={() => setSelectedModels(prev =>
                  prev.includes(m.model_id)
                    ? prev.filter(id => id !== m.model_id)
                    : [...prev, m.model_id]
                )} />
              <span>{m.label}</span>
            </label>
          ))}
          {isSlowModel && (
            <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5 mt-1">
              ⚠ large modely mají RTF &gt; 2.0 na CPU — pro live mikrofon nepoužitelné. Doporučujeme small nebo medium.
            </div>
          )}
          <p className="text-xs text-gray-400 pt-1">Tuning aktuálně podporuje whisper.cpp modely.</p>
        </div>

        {/* Videa */}
        <div className="bg-white rounded border border-gray-200 p-4 space-y-1">
          <div className="flex items-center justify-between mb-2">
            <h2 className="font-semibold text-sm text-gray-700">Evaluační video</h2>
            <div className="flex gap-1 text-xs">
              {TUNING_VIDEO_SORT_LABELS.map(({ key, label }) => {
                const idx = videoSortOrder.indexOf(key)
                const active = idx >= 0
                return (
                  <label key={key} className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border ${active ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-200 text-gray-500'}`}>
                    <input type="checkbox" checked={active} onChange={() => toggleVideoSortPriority(key)} />
                    <button type="button" onClick={() => toggleVideoSortDir(key)} className="hover:underline">
                      {label}{active ? ` ${idx + 1}.${videoSortDirMap[key] === 'asc' ? '▲' : '▼'}` : ''}
                    </button>
                  </label>
                )
              })}
            </div>
          </div>
          <div className="space-y-1 max-h-52 overflow-y-auto">
            {sortedLibrary.map(v => (
              <label key={v.video_id} className="flex items-center gap-2 text-xs cursor-pointer">
                <input type="checkbox" checked={selectedVideos.includes(v.video_id)}
                  onChange={() => setSelectedVideos(prev =>
                    prev.includes(v.video_id) ? prev.filter(x => x !== v.video_id) : [...prev, v.video_id]
                  )} />
                <span className={`shrink-0 px-1 rounded font-mono font-bold text-xs ${v.language === 'cs' ? 'bg-blue-100 text-blue-700' : 'bg-orange-100 text-orange-700'}`}
                  title={v.language === 'cs' ? 'Čeština' : v.language === 'en' ? 'Angličtina' : v.language}>
                  {v.language.toUpperCase()}
                </span>
                <span className="truncate" title={v.title}>{videoLabel(v.title, v.video_id)}</span>
                {v.upload_date && <span className="shrink-0 text-gray-400">{v.upload_date}</span>}
                {v.duration_seconds && <span className="shrink-0 text-gray-400">{Math.round(v.duration_seconds)}s</span>}
              </label>
            ))}
          </div>
          <p className="text-xs text-gray-400 pt-1">Jen videa s titulky (ground truth).</p>
        </div>

        {/* Nastavení jobu */}
        <div className="bg-white rounded border border-gray-200 p-4 space-y-3">
          <h2 className="font-semibold text-sm text-gray-700">Konfigurace</h2>
          <div className="space-y-1">
            <label className="text-xs text-gray-500">Strategie</label>
            <div className="space-y-1">
              {([
                ['ablation', 'Ablace — jeden param najednou'],
                ['grid', 'Grid — všechny kombinace'],
                ['random', 'Náhodné vzorkování'],
                ['smart', 'Smart — postupné vyřazování'],
              ] as [Strategy, string][]).map(([s, label]) => (
                <label key={s} className="flex items-center gap-2 text-xs cursor-pointer">
                  <input type="radio" name="strategy" value={s}
                    checked={strategy === s} onChange={() => setStrategy(s)} />
                  {label}
                </label>
              ))}
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-gray-500">Vstupní režim</label>
            <div className="flex rounded border border-gray-200 overflow-hidden text-xs w-fit">
              <button
                type="button"
                onClick={() => setInputMode('replay')}
                className={`px-3 py-1 transition-colors ${inputMode === 'replay' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              >
                Replay
              </button>
              <button
                type="button"
                onClick={() => setInputMode('real_mic')}
                className={`px-3 py-1 transition-colors border-l border-gray-200 ${inputMode === 'real_mic' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              >
                Real mic
              </button>
            </div>
            <p className="text-[11px] text-gray-400">
              Replay = tuning z videí. Real mic = vyžaduje protokol + kalibraci.
            </p>
            {inputMode === 'real_mic' && (
              <p className="text-[11px] text-amber-700">
                Real mic tuning stále potřebuje vybrané referenční video (pro WER/soft-WER vyhodnocení).
              </p>
            )}
          </div>
          {inputMode === 'real_mic' && (
            <div className="border border-blue-200 bg-blue-50 rounded p-3 space-y-3">
              <div className="text-xs font-medium text-blue-900">Real mic protokol</div>
              <div className="flex gap-2 flex-wrap text-xs">
                <div>
                  <label className="text-gray-600">Vzdálenost (cm)</label>
                  <input
                    type="number"
                    min={1}
                    max={300}
                    value={micDistanceCm}
                    onChange={e => setMicDistanceCm(Math.max(1, Math.min(300, +e.target.value || 1)))}
                    className="border rounded px-2 py-1 w-24 block mt-0.5"
                  />
                </div>
                <div>
                  <label className="text-gray-600">Hlasitost mobilu (%)</label>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={micPhoneVolumePct}
                    onChange={e => setMicPhoneVolumePct(Math.max(0, Math.min(100, +e.target.value || 0)))}
                    className="border rounded px-2 py-1 w-28 block mt-0.5"
                  />
                </div>
                <div>
                  <label className="text-gray-600">Input gain (%)</label>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={micInputGainPct}
                    onChange={e => setMicInputGainPct(Math.max(0, Math.min(100, +e.target.value || 0)))}
                    className="border rounded px-2 py-1 w-24 block mt-0.5"
                  />
                </div>
                <div>
                  <label className="text-gray-600">Prostředí</label>
                  <select
                    value={micEnvironment}
                    onChange={e => setMicEnvironment(e.target.value as 'quiet' | 'office_noise')}
                    className="border rounded px-2 py-1 w-32 block mt-0.5"
                  >
                    <option value="quiet">quiet</option>
                    <option value="office_noise">office_noise</option>
                  </select>
                </div>
                <div>
                  <label className="text-gray-600">Mic zařízení</label>
                  <select
                    value={micDeviceId}
                    onChange={e => setMicDeviceId(e.target.value)}
                    className="border rounded px-2 py-1 w-56 block mt-0.5"
                  >
                    <option value="">default</option>
                    {micDevices.map(d => (
                      <option key={d.index} value={String(d.index)}>
                        {d.index}: {d.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-gray-600">Mic chunk (s)</label>
                  <input
                    type="number"
                    step="0.05"
                    min={0.05}
                    max={2}
                    value={micChunkSeconds}
                    onChange={e => setMicChunkSeconds(Math.max(0.05, Math.min(2, +e.target.value || 0.2)))}
                    className="border rounded px-2 py-1 w-24 block mt-0.5"
                  />
                </div>
                <div>
                  <label className="text-gray-600">Příprava (s)</label>
                  <input
                    type="number"
                    min={0}
                    max={60}
                    value={micPrepareSeconds}
                    onChange={e => setMicPrepareSeconds(Math.max(0, Math.min(60, Math.floor(+e.target.value || 0))))}
                    className="border rounded px-2 py-1 w-24 block mt-0.5"
                  />
                </div>
              </div>
              <div>
                <label className="text-xs text-gray-600">Poznámka zařízení</label>
                <input
                  value={micDeviceNote}
                  onChange={e => setMicDeviceNote(e.target.value)}
                  placeholder="např. ntb mic + Samsung S23"
                  className="border rounded px-2 py-1 text-xs w-full mt-0.5"
                />
              </div>
              <div className="border-t border-blue-100 pt-2 space-y-2">
                <div className="text-xs font-medium text-blue-900">Kalibrace</div>
                <div className="flex gap-2 flex-wrap text-xs">
                  <div>
                    <label className="text-gray-600">RMS (dBFS)</label>
                    <input
                      type="number"
                      step="0.1"
                      value={calibrationRmsDbfs}
                      onChange={e => setCalibrationRmsDbfs(+e.target.value)}
                      className="border rounded px-2 py-1 w-24 block mt-0.5"
                    />
                  </div>
                  <div>
                    <label className="text-gray-600">Clipping (%)</label>
                    <input
                      type="number"
                      step="0.001"
                      min={0}
                      max={100}
                      value={calibrationClippingPct}
                      onChange={e => setCalibrationClippingPct(Math.max(0, Math.min(100, +e.target.value || 0)))}
                      className="border rounded px-2 py-1 w-24 block mt-0.5"
                    />
                  </div>
                  <div>
                    <label className="text-gray-600">Noise floor (dBFS)</label>
                    <input
                      type="number"
                      step="0.1"
                      value={calibrationNoiseFloorDbfs}
                      onChange={e => setCalibrationNoiseFloorDbfs(+e.target.value)}
                      className="border rounded px-2 py-1 w-28 block mt-0.5"
                    />
                  </div>
                  <div className="pt-5">
                    <button
                      type="button"
                      onClick={checkMicCalibration}
                      disabled={checkingCalibration}
                      className="px-3 py-1 rounded border border-blue-300 text-blue-700 hover:bg-blue-100 disabled:opacity-60"
                    >
                      {checkingCalibration ? 'Ověřuji...' : 'Ověřit kalibraci'}
                    </button>
                  </div>
                </div>
                {calibrationResult && (
                  <div className={`text-xs rounded px-2 py-1 ${calibrationResult.passed ? 'bg-emerald-100 text-emerald-800 border border-emerald-200' : 'bg-red-100 text-red-800 border border-red-200'}`}>
                    {calibrationResult.passed ? 'PASS' : 'FAIL'}
                    {!calibrationResult.passed && calibrationResult.reasons.length > 0 && (
                      <span className="ml-2">{calibrationResult.reasons[0]}</span>
                    )}
                    {calibrationCheckedAt && <span className="ml-2 text-[11px] text-gray-600">({formatClockHHMMSS(calibrationCheckedAt)})</span>}
                  </div>
                )}
                <label className="inline-flex items-center gap-2 text-xs text-gray-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={micChecklistConfirmed}
                    onChange={e => setMicChecklistConfirmed(e.target.checked)}
                  />
                  Potvrzuji stejnou vzdálenost, hlasitost a prostředí pro všechny trialy.
                </label>
              </div>
              <p className="text-[11px] text-blue-900">
                Real mic tuning běží přímo v workeru: před každým videem sleduj stavový řádek a spusť přehrání mobilu v momentu startu nahrávání.
              </p>
            </div>
          )}
          <div className="flex gap-3 flex-wrap">
            <div>
              <label className="text-xs text-gray-500">Délka klipu (s)</label>
              <input type="number" value={sampleSeconds} min={20} max={300}
                onChange={e => setSampleSeconds(+e.target.value)}
                className="border rounded px-2 py-1 text-sm w-20 block mt-0.5" />
            </div>
            <div>
              <label className="text-xs text-gray-500" title="Seed pro výběr pozice v klipu — stejný seed = stejný úsek. Ignorováno pokud je nastaven Pevný start.">
                Clip seed
              </label>
              <input type="number" value={clipSeed} min={0} max={9999}
                onChange={e => setClipSeed(+e.target.value)}
                disabled={clipStartSeconds != null}
                className="border rounded px-2 py-1 text-sm w-20 block mt-0.5 disabled:opacity-40" />
            </div>
            <div>
              <label className="text-xs text-gray-500" title="Pevný start klipu v sekundách od začátku videa. Přepíše clip seed. Prázdné = automaticky ze seedu.">
                Pevný start (s)
              </label>
              <input type="number"
                value={clipStartSeconds ?? ''}
                min={0}
                placeholder="auto"
                onChange={e => setClipStartSeconds(e.target.value === '' ? null : +e.target.value)}
                className="border rounded px-2 py-1 text-sm w-20 block mt-0.5" />
            </div>
            {(['random', 'smart'] as Strategy[]).includes(strategy) && (
              <div>
                <label className="text-xs text-gray-500">{strategy === 'smart' ? 'Max kandidátů/model' : 'Max triálů'}</label>
                <input type="number" value={maxTrials} min={4} max={50}
                  onChange={e => setMaxTrials(+e.target.value)}
                  className="border rounded px-2 py-1 text-sm w-20 block mt-0.5" />
              </div>
            )}
            <div>
              <label className="text-xs text-gray-500" title="Po základní vlně zopakuje nejlepších K kandidátů kvůli odhadu rozptylu/CI95.">
                Repeat top-K
              </label>
              <input type="number" value={repeatTopK} min={0} max={10}
                onChange={e => setRepeatTopK(Math.max(0, +e.target.value || 0))}
                className="border rounded px-2 py-1 text-sm w-20 block mt-0.5" />
            </div>
            <div>
              <label className="text-xs text-gray-500" title="Celkový počet běhů na kandidáta (včetně prvního běhu).">
                Repeat runs
              </label>
              <input type="number" value={repeatRuns} min={1} max={10}
                onChange={e => setRepeatRuns(Math.max(1, +e.target.value || 1))}
                className="border rounded px-2 py-1 text-sm w-20 block mt-0.5" />
            </div>
            <div>
              <label className="text-xs text-gray-500" title="Třída cílového HW. Auto = klasifikace podle logical cores + total RAM.">
                HW profil
              </label>
              <select
                value={hardwareProfile}
                onChange={e => setHardwareProfile(e.target.value)}
                className="border rounded px-2 py-1 text-sm w-44 block mt-0.5"
              >
                <option value="auto">auto (cores+RAM)</option>
                <option value="weak_office">weak (&lt;=4L / &lt;=8GB)</option>
                <option value="mid_office">mid (&lt;=8L / &lt;=16GB)</option>
                <option value="strong_office">strong (&gt;8L + &gt;16GB)</option>
                <option value="custom">custom</option>
              </select>
              <div className="text-[11px] text-gray-400 mt-1">
                Profil = třída stroje dle jader+RAM, ne podle aktuálního % CPU/RAM.
              </div>
            </div>
            <div>
              <label className="text-xs text-gray-500" title="Řízená zátěž během trialu. Simuluje obsazený stroj (target CPU/RAM %).">
                Zátěž simulace
              </label>
              <select
                value={loadProfile}
                onChange={e => setLoadProfile(e.target.value)}
                className="border rounded px-2 py-1 text-sm w-44 block mt-0.5"
              >
                <option value="none">vypnuto</option>
                <option value="light">light (CPU 25 / RAM 35)</option>
                <option value="medium">medium (CPU 45 / RAM 55)</option>
                <option value="heavy">heavy (CPU 65 / RAM 75)</option>
                <option value="custom">custom</option>
              </select>
              {loadProfile === 'custom' && (
                <div className="mt-1 flex items-center gap-1 text-xs">
                  <span className="text-gray-500">CPU</span>
                  <input
                    type="number"
                    min={0}
                    max={95}
                    value={loadCpuTargetPct}
                    onChange={e => setLoadCpuTargetPct(Math.max(0, Math.min(95, +e.target.value || 0)))}
                    className="border rounded px-1.5 py-0.5 w-14"
                  />
                  <span className="text-gray-400">%</span>
                  <span className="text-gray-500">RAM</span>
                  <input
                    type="number"
                    min={0}
                    max={95}
                    value={loadRamTargetPct}
                    onChange={e => setLoadRamTargetPct(Math.max(0, Math.min(95, +e.target.value || 0)))}
                    className="border rounded px-1.5 py-0.5 w-14"
                  />
                  <span className="text-gray-400">%</span>
                </div>
              )}
            </div>
            <div>
              <label className="text-xs text-gray-500" title="Procesní limity pro tuning worker/model (doporučeno pro simulaci slabšího HW).">
                Omezení výkonu
              </label>
              <select
                value={constraintsProfile}
                onChange={e => setConstraintsProfile(e.target.value)}
                className="border rounded px-2 py-1 text-sm w-44 block mt-0.5"
              >
                <option value="none">vypnuto</option>
                <option value="weak_cap">weak cap (2C / 4GB)</option>
                <option value="mid_cap">mid cap (4C / 8GB)</option>
                <option value="custom">custom</option>
              </select>
              {constraintsProfile === 'custom' && (
                <div className="mt-1 flex items-center gap-1 text-xs">
                  <span className="text-gray-500">Cores</span>
                  <input
                    type="number"
                    min={1}
                    max={128}
                    value={constraintsCpuCores}
                    onChange={e => setConstraintsCpuCores(Math.max(1, Math.min(128, +e.target.value || 1)))}
                    className="border rounded px-1.5 py-0.5 w-14"
                  />
                  <span className="text-gray-500">RAM</span>
                  <input
                    type="number"
                    min={256}
                    max={262144}
                    value={constraintsRamLimitMb}
                    onChange={e => setConstraintsRamLimitMb(Math.max(256, Math.min(262144, +e.target.value || 256)))}
                    className="border rounded px-1.5 py-0.5 w-16"
                  />
                  <span className="text-gray-400">MB</span>
                </div>
              )}
              {constraintsProfile !== 'none' && (
                <div className="mt-1 flex items-center gap-1 text-xs">
                  <span className="text-gray-500">Priorita</span>
                  <select
                    value={constraintsPriority}
                    onChange={e => setConstraintsPriority(e.target.value)}
                    className="border rounded px-1.5 py-0.5 text-xs"
                  >
                    <option value="idle">idle</option>
                    <option value="below_normal">below_normal</option>
                    <option value="normal">normal</option>
                  </select>
                </div>
              )}
            </div>
          </div>
          <div>
            <label className="text-xs text-gray-500">Popis</label>
            <input value={label} onChange={e => setLabel(e.target.value)}
              placeholder="volitelný popis..." className="border rounded px-2 py-1 text-sm w-full mt-0.5" />
          </div>
          <div>
            <label className="text-xs text-gray-500">HW poznámka (nepovinné)</label>
            <input value={hardwareNote} onChange={e => setHardwareNote(e.target.value)}
              placeholder="např. i5-8250U / 8GB / HDD" className="border rounded px-2 py-1 text-sm w-full mt-0.5" />
          </div>
        </div>
      </div>

      {/* Parametrický prostor */}
      <div className="bg-white rounded border border-gray-200 p-4 space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="font-semibold text-sm text-gray-700">Parametrický prostor</h2>
          <span className="text-xs text-gray-400">— klikni na hodnoty, které chceš testovat (modre = vybráno)</span>
          <div className="ml-auto flex gap-2">
            <button onClick={() => {
              setParamValues(Object.fromEntries(
                WHISPER_PARAM_DEFS.map(p => [p.name, new Set<unknown>([p.default])])
              ))
            }} className="px-2.5 py-1 text-xs rounded border border-gray-300 hover:bg-gray-50">
              Reset
            </button>
            <button onClick={() => {
              // Doporučený rozsah pro typický tuning run
              setParamValues({
                beam_size: new Set<unknown>([1, 3, 5]),
                best_of: new Set<unknown>([1, 3, 5]),
                threads: new Set<unknown>([2, 4]),
                no_fallback: new Set<unknown>([true, false]),
                chunk_seconds: new Set<unknown>([15, 30]),
              })
            }} className="px-2.5 py-1 text-xs rounded border border-blue-400 text-blue-700 hover:bg-blue-50">
              Doporuceny rozsah
            </button>
            <button onClick={() => {
              setParamValues(Object.fromEntries(
                WHISPER_PARAM_DEFS.map(p => [p.name, new Set<unknown>(p.values)])
              ))
            }} className="px-2.5 py-1 text-xs rounded border border-gray-300 hover:bg-gray-50">
              Vse
            </button>
          </div>
        </div>

        {/* Numerické / bool parametry */}
        <div className="space-y-3">
          {WHISPER_PARAM_DEFS.map(p => (
            <div key={p.name} className="flex items-start gap-4">
              <div className="w-36 shrink-0 pt-1 relative group/label">
                <span className="text-xs font-medium text-gray-600 cursor-help underline decoration-dotted decoration-gray-400">
                  {p.label}
                </span>
                <div className="pointer-events-none absolute left-0 top-5 z-20 hidden group-hover/label:block w-72 bg-gray-900 text-white text-xs rounded px-2.5 py-2 shadow-xl leading-relaxed">
                  {p.description}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(p.values as readonly unknown[]).map(v => {
                  const selected = paramValues[p.name]?.has(v) ?? false
                  const isDefault = v === p.default
                  const vStr = p.type === 'bool' ? (v ? 'true' : 'false') : String(v)
                  const vDesc = (p as { valueDescriptions?: Record<string, string> }).valueDescriptions?.[String(v)]
                  return (
                    <div key={vStr} className="relative group/val">
                      <button
                        onClick={() => toggleParamValue(p.name, v)}
                        className={`px-2.5 py-1 rounded text-xs font-mono border transition-colors ${
                          selected ? 'bg-blue-600 text-white border-blue-600'
                                 : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
                        }`}
                      >
                        {vStr}
                        {isDefault && <span className="ml-1 opacity-50">●</span>}
                      </button>
                      {vDesc && (
                        <div className="pointer-events-none absolute left-0 top-7 z-20 hidden group-hover/val:block whitespace-nowrap bg-gray-900 text-white text-xs rounded px-2 py-1 shadow-xl">
                          {vDesc}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
              <div className="text-xs text-gray-400 pt-1">
                {paramValues[p.name]?.size ?? 1} hodnot
              </div>
            </div>
          ))}
        </div>
        {(hasNoFallbackBestOfRedundant || hasVerySmallChunk || hasSingleVideo || hasWeakRepeatPlan) && (
          <div className="border border-amber-200 bg-amber-50 rounded px-3 py-2 text-xs text-amber-800 space-y-1">
            <div className="font-medium">Nápověda ke konfiguraci</div>
            {hasNoFallbackBestOfRedundant && (
              <div>• `no_fallback=true` + `best_of&gt;1` je redundantní. `best_of` se neuplatní.</div>
            )}
            {hasVerySmallChunk && (
              <div>• `chunk_seconds &lt; 10` může zvyšovat overhead/jitter. Pro stabilní srovnání používej hlavně 15/30.</div>
            )}
            {hasSingleVideo && (
              <div>• Jen 1 video = vysoké riziko overfitu. Pro tuning používej aspoň 2 videa.</div>
            )}
            {hasWeakRepeatPlan && (
              <div>• Pro reprodukovatelnost použij aspoň `repeat_runs=3` (CI95 je pak stabilnější).</div>
            )}
          </div>
        )}

        {/* Initial prompt */}
        <div className="border-t border-gray-100 pt-4 space-y-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPromptPanelCollapsed(prev => !prev)}
              className="inline-flex items-center gap-1 text-left"
            >
              <span className="text-[10px] text-gray-500">{promptPanelCollapsed ? '▶' : '▼'}</span>
              <span className="text-xs font-medium text-gray-700">Initial prompt</span>
            </button>
            <span className="text-xs text-gray-400">— kontext pro model (každý vybraný prompt = 1 varianta trialu)</span>
            <span className="text-xs text-gray-400 ml-auto">
              {promptVariantLabel(selectedPromptCount)}{promptPanelCollapsed ? ` ${selectedPromptSummary}` : ''}
            </span>
          </div>
          {!promptPanelCollapsed && (
            <>
              {PROMPT_LIBRARY.map(group => {
                const dynamicPrompts = group.id === 'topic_auto'
                  ? selectedVideos.slice(0, 3).map(vid => {
                      const item = library.find(l => l.video_id === vid)
                      if (!item) return null
                      return { label: item.title.slice(0, 30), text: autoPromptFromTitle(item.title) }
                    }).filter(Boolean) as { label: string; text: string }[]
                  : group.prompts
                if (group.id === 'topic_auto' && dynamicPrompts.length === 0) return null
                const colorMap: Record<string, string> = {
                  gray: 'border-gray-300 text-gray-600',
                  blue: 'border-blue-300 text-blue-700',
                  violet: 'border-violet-300 text-violet-700',
                  red: 'border-red-300 text-red-700',
                  indigo: 'border-indigo-300 text-indigo-700',
                  green: 'border-green-300 text-green-700',
                  orange: 'border-orange-300 text-orange-700',
                }
                const selectedColor: Record<string, string> = {
                  gray: 'bg-gray-600 text-white border-gray-600',
                  blue: 'bg-blue-600 text-white border-blue-600',
                  violet: 'bg-violet-600 text-white border-violet-600',
                  red: 'bg-red-600 text-white border-red-600',
                  indigo: 'bg-indigo-600 text-white border-indigo-600',
                  green: 'bg-green-600 text-white border-green-600',
                  orange: 'bg-orange-600 text-white border-orange-600',
                }
                return (
                  <div key={group.id}>
                    <div className="text-xs text-gray-400 mb-1">{group.label}</div>
                    <div className="flex flex-wrap gap-1.5">
                      {dynamicPrompts.map(p => {
                        const isSelected = selectedPrompts.has(p.text)
                        return (
                          <div key={p.text} className="relative group/pt">
                            <button
                              onClick={() => togglePrompt(p.text)}
                              className={`px-2.5 py-1 rounded text-xs border transition-colors ${
                                isSelected ? selectedColor[group.color] : `bg-white hover:bg-gray-50 ${colorMap[group.color]}`
                              }`}
                            >
                              {p.label}
                            </button>
                            {p.text && (
                              <div className="pointer-events-none absolute left-0 top-7 z-20 hidden group-hover/pt:block w-80 bg-gray-900 text-white text-xs rounded px-2.5 py-2 shadow-xl leading-relaxed font-mono break-all">
                                {p.text}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
              <div className="flex items-center gap-2">
                <input
                  value={customPrompt}
                  onChange={e => setCustomPrompt(e.target.value)}
                  placeholder="Vlastní prompt (ASCII)..."
                  className="border rounded px-2 py-1 text-xs flex-1"
                />
                {customPrompt.trim() && (
                  <span className={`text-xs px-2 py-1 rounded border ${selectedPromptVariants.includes(customPrompt.trim()) ? 'text-blue-600' : 'text-gray-400'}`}>
                    {selectedPromptVariants.includes(customPrompt.trim()) ? '✓ v trialu' : 'přidá se automaticky'}
                  </span>
                )}
              </div>
            </>
          )}
        </div>

        {/* Hodnocení */}
        <div className="border-t border-gray-100 pt-4">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-gray-700">Hodnocení chyb</span>
            <div className="flex rounded border border-gray-200 overflow-hidden text-xs ml-2">
              <button
                onClick={() => setEvaluationMode('heuristic')}
                className={`px-3 py-1 transition-colors ${evaluationMode === 'heuristic' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              >
                Heuristika
              </button>
              <button
                disabled
                title="Vyžaduje Ollamu (připraveno, zatím nedostupné)"
                className="px-3 py-1 bg-white text-gray-300 cursor-not-allowed border-l border-gray-200"
              >
                Heuristika + LLM
              </button>
            </div>
            <span className="text-xs text-gray-400">
              {evaluationMode === 'heuristic'
                ? '— znaková vzdálenost, práh 0.40'
                : '— heuristika + Ollama'}
            </span>
          </div>
        </div>

        <div className="border-t border-gray-100 pt-4">
          <label className="inline-flex items-center gap-2 text-xs font-medium text-gray-700 cursor-pointer">
            <input
              type="checkbox"
              checked={validateBeamPreflight}
              onChange={e => setValidateBeamPreflight(e.target.checked)}
              className="rounded border-gray-300"
            />
            Pre-flight validace beam_size (5s)
          </label>
          <p className="text-xs text-gray-500 mt-1">
            Když je vypnuto, trialy začnou hned bez úvodní validace.
          </p>
        </div>

        <div className="flex items-center gap-4 pt-2 border-t border-gray-100">
          <div className="text-sm text-gray-700">
            Vygeneruje <strong>{trialCount}</strong> kombinac{trialCount === 1 ? 'i' : trialCount < 5 ? 'e' : 'í'}.
            Odhadovaný čas: <strong>~{Math.round(trialCount * sampleSeconds * 0.6 / 60)} min</strong>
            <span className="text-gray-400 ml-1">(RTF≈0.5)</span>
          </div>
          <button
            onClick={applyQuickV3SmokePreset}
            className="ml-auto border border-emerald-300 text-emerald-700 hover:bg-emerald-50 px-3 py-1.5 rounded text-sm font-medium"
            title="Prednastavi kratky v3 smoke na 2 CZ videich (bez automatickeho startu)"
          >
            Nastavit: Rychly v3 smoke (2x CZ)
          </button>
          <button
            onClick={applyV3DecisionGridPreset}
            className="border border-indigo-300 text-indigo-700 hover:bg-indigo-50 px-3 py-1.5 rounded text-sm font-medium"
            title="Prednastavi plny v3 grid (beam 1/2/3/5, threads 2/4/6/8, prompt empty/CZ, chunk 15/30, repeat top4 x3)"
          >
            Nastavit: Plny v3 grid (4x CZ)
          </button>
          <button onClick={startTuning}
            className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-1.5 rounded text-sm font-medium">
            ▶ Spustit tuning
          </button>
          {msg && <span className="text-sm text-red-500">{msg}</span>}
        </div>
        <p className="text-xs text-gray-400">● = výchozí hodnota (baseline). Vyber více hodnot nebo promptů pro sweep.</p>
      </div>

      {/* Historie jobů */}
      {jobs.length > 0 && (
        <div className="bg-white rounded border border-gray-200">
          <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-3">
            <h2 className="font-semibold text-sm text-gray-700">Historie tuning jobů</h2>
            <span className="text-xs text-gray-400">{sortedHistoryJobs.length} jobů</span>
            {historyHiddenCount > 0 && (
              <button
                type="button"
                onClick={() => setShowAllHistoryJobs(v => !v)}
                className="ml-auto text-xs text-blue-700 hover:text-blue-900 border border-blue-200 rounded px-2 py-0.5"
              >
                {showAllHistoryJobs ? '▼ Skrýt starší joby' : `▶ Zobrazit dalších ${historyHiddenCount}`}
              </button>
            )}
          </div>
          <div className="px-4 py-2 border-b border-gray-100 text-[11px] uppercase tracking-wide text-gray-400 grid grid-cols-[3rem_10.5rem_minmax(11rem,1fr)_minmax(10rem,1fr)_7rem_8rem_8rem_8rem] gap-3">
            <span>#</span>
            <span>RokDatumHodina</span>
            <span>Model/Label</span>
            <span>Profily</span>
            <span>Triály</span>
            <span>Běží</span>
            <span>Zbývá</span>
            <span>Best WER</span>
          </div>
          <div className="divide-y divide-gray-100">
            {visibleHistoryJobs.map((j, idx) => {
              const elapsedS = jobElapsedSeconds(j, nowMs)
              const etaS = estimateRemainingSeconds(j, nowMs)
              return (
              <button key={j.job_id}
                onClick={() => { setSelectedJob(j); if (j.status === 'running') startPolling(j.job_id) }}
                className={`w-full text-left px-4 py-2.5 hover:bg-gray-50 text-sm ${selectedJob?.job_id === j.job_id ? 'bg-blue-50' : ''}`}
                title={j.job_id}
              >
                <div className="grid grid-cols-[3rem_10.5rem_minmax(11rem,1fr)_minmax(10rem,1fr)_7rem_8rem_8rem_8rem] gap-3 items-center">
                  <span className="flex items-center gap-2">
                    <span className="text-[11px] text-gray-500 font-mono">{idx + 1}</span>
                    <span className={`w-2 h-2 rounded-full shrink-0 ${
                      j.status === 'completed' ? 'bg-green-500' :
                      j.status === 'running' ? 'bg-blue-500 animate-pulse' :
                      j.status === 'failed' ? 'bg-red-500' : 'bg-gray-400'
                    }`} />
                  </span>
                  <span className="font-mono text-xs text-gray-500">
                    {formatJobHistoryName(j)}
                  </span>
                  <span className="text-gray-700 truncate">{j.label || j.model_id}</span>
                  <span className="flex items-center gap-1 flex-wrap">
                    {j.input_mode && (
                      <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${
                        j.input_mode === 'real_mic'
                          ? 'border-emerald-300 text-emerald-700'
                          : 'border-gray-300 text-gray-600'
                      }`}>
                        {j.input_mode}
                      </span>
                    )}
                    {j.strategy && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-violet-300 text-violet-700 font-mono">
                        {j.strategy}
                      </span>
                    )}
                    {j.hardware_profile && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-gray-300 text-gray-600 font-mono">
                        {j.hardware_profile}
                      </span>
                    )}
                    {j.load_profile && j.load_profile !== 'none' && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-amber-300 text-amber-700 font-mono">
                        load:{j.load_profile}
                      </span>
                    )}
                    {j.constraints_profile && j.constraints_profile !== 'none' && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-blue-300 text-blue-700 font-mono">
                        cap:{j.constraints_profile}
                      </span>
                    )}
                  </span>
                  <span className="text-gray-500 text-xs font-mono">{j.completed_trials}/{j.total_trials}</span>
                  <span className="text-gray-500 text-xs font-mono">
                    {elapsedS != null ? `⏱ ${formatElapsedShort(elapsedS)}` : '–'}
                  </span>
                  <span className="text-gray-500 text-xs font-mono">
                    {etaS != null ? `~${formatElapsedShort(etaS)}` : '–'}
                  </span>
                  {(() => {
                    const bestRow = j.best_trial_idx != null
                      ? j.results.find(r => r.trial_idx === j.best_trial_idx)
                      : null
                    return bestRow ? (
                      <span className="text-xs">🏆 {((bestRow.wer ?? 0) * 100).toFixed(1)}%</span>
                    ) : <span className="text-xs text-gray-300">–</span>
                  })()}
                </div>
              </button>
            )})}
          </div>
        </div>
      )}

      {/* Detail vybraného jobu */}
      {selectedJob && <TuningJobDetail job={selectedJob} library={library} nowMs={nowMs} onCancel={async () => {
        await api.tuning.cancelJob(selectedJob.job_id)
        const cancelled = { ...selectedJob, status: 'cancelled' as const }
        setSelectedJob(cancelled)
        setJobs(prev => prev.map((j: TuningJobStatus) => j.job_id === selectedJob.job_id ? cancelled : j))
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
      }} />}
    </div>
  )
}

type SortCol = 'wer' | 'cer' | 'wer_normalized' | 'wer_soft' | 'rtf' | 'rtf_viable' | 'perceived_delay_s' | 'latency_ms' | 'trial_finished_at' | 'is_pareto'

const SORT_COLUMNS: [SortCol, string][] = [
  ['wer', 'WER'],
  ['wer_soft', 'WER soft'],
  ['cer', 'CER'],
  ['wer_normalized', 'WER norm.'],
  ['rtf', 'RTF'],
  ['rtf_viable', 'Live mic'],
  ['perceived_delay_s', 'Zpoždění'],
  ['latency_ms', 'Latence'],
  ['trial_finished_at', 'Konec'],
  ['is_pareto', 'Pareto'],
]

const SORT_DEFAULT_DIR: Record<SortCol, 1 | -1> = {
  wer: 1,
  wer_soft: 1,
  cer: 1,
  wer_normalized: 1,
  rtf: 1,
  rtf_viable: -1,
  perceived_delay_s: 1,
  latency_ms: 1,
  trial_finished_at: -1,
  is_pareto: -1,
}

// Přibližná RAM náročnost modelů (MB) — pro filtr HW limitů
const MODEL_RAM_MB: Record<string, number> = {
  'whisper_cpp_tiny':           300,
  'whisper_cpp_base':           350,
  'whisper_cpp_small':          600,
  'whisper_cpp_medium':        1500,
  'whisper_cpp_large_v3_turbo':1600,
  'whisper_cpp_large_v3':      3100,
}

function TuningJobDetail({ job, library, nowMs, onCancel }: { job: TuningJobStatus; library: LibraryItem[]; nowMs: number; onCancel: () => void }) {
  const [expandedTrial, setExpandedTrial] = useState<number | null>(null)
  const [showFullTextByTrial, setShowFullTextByTrial] = useState<Record<number, boolean>>({})
  const [msgAge, setMsgAge] = useState<number>(0)
  const [decision, setDecision] = useState<TuningDecisionReport | null>(null)
  const [decisionLoading, setDecisionLoading] = useState(false)
  const [decisionError, setDecisionError] = useState('')
  const [sortPriorities, setSortPriorities] = useState<SortCol[]>(['wer'])
  const [ram, setRam] = useState<{ used: number; total: number; pct: number } | null>(null)
  const [filterThreads, setFilterThreads] = useState<number | null>(null)
  const [filterViableOnly, setFilterViableOnly] = useState(false)
  const [filterRamMb, setFilterRamMb] = useState<string>('')
  const [eventCursor, setEventCursor] = useState<number>(0)
  const [eventLog, setEventLog] = useState<TuningEventItem[]>([])
  const [eventStats, setEventStats] = useState<TuningEventStats | null>(null)
  const [eventValidation, setEventValidation] = useState<TuningEventValidation | null>(null)
  const [eventError, setEventError] = useState<string>('')
  const eventCursorRef = useRef<number>(0)
  const hasNonDefaultSort = !(sortPriorities.length === 1 && sortPriorities[0] === 'wer')
  const isValidationPhase = (job.progress_message ?? '').startsWith('Validace ')
  const staleWarnSec = isValidationPhase ? 120 : 90
  const elapsedJobS = jobElapsedSeconds(job, nowMs)
  const etaJobS = estimateRemainingSeconds(job, nowMs)

  useEffect(() => {
    if (job.status !== 'running') { setMsgAge(0); return }
    const tick = () => {
      const ts = job.updated_ts
      if (ts) setMsgAge(Math.floor((Date.now() - new Date(ts).getTime()) / 1000))
    }
    tick()
    const iv = setInterval(tick, 1000)
    return () => clearInterval(iv)
  }, [job.status, job.updated_ts])

  async function loadDecisionReport() {
    setDecisionLoading(true)
    setDecisionError('')
    try {
      const report = await api.tuning.decisionReport(job.job_id, {
        min_success_rate: 0.95,
        max_rtf: 1.0,
        require_repro_n: 3,
        top: 5,
      })
      setDecision(report)
    } catch (e: any) {
      setDecisionError(e.message ?? 'Nacteni decision reportu selhalo')
    } finally {
      setDecisionLoading(false)
    }
  }

  useEffect(() => {
    setDecision(null)
    setDecisionError('')
    if (job.status === 'completed') {
      void loadDecisionReport()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.job_id, job.status])

  useEffect(() => {
    if (job.status !== 'running') return
    const fetchRam = () => fetch('/api/health').then(r => r.json()).then(d => {
      if (d.ram_total_mb) setRam({ used: d.ram_used_mb, total: d.ram_total_mb, pct: d.ram_percent })
    }).catch(() => {})
    fetchRam()
    const iv = setInterval(fetchRam, 3000)
    return () => clearInterval(iv)
  }, [job.status])

  useEffect(() => {
    let cancelled = false
    eventCursorRef.current = 0
    setEventCursor(0)
    setEventLog([])
    setEventStats(null)
    setEventValidation(null)
    setEventError('')

    const loadInitialEvents = async () => {
      try {
        const payload = await api.tuning.events(job.job_id, { after_seq: 0, limit: 80 })
        if (cancelled) return
        const nextCursor = Math.max(0, payload.next_after_seq ?? 0)
        eventCursorRef.current = nextCursor
        setEventCursor(nextCursor)
        setEventLog((payload.events ?? []).slice(-40))
        setEventStats(payload.stats ?? null)
        setEventValidation(payload.validation ?? null)
      } catch (e: any) {
        if (!cancelled) setEventError(e?.message ?? 'Nacteni event streamu selhalo')
      }
    }

    void loadInitialEvents()
    return () => { cancelled = true }
  }, [job.job_id])

  useEffect(() => {
    if (job.status !== 'running' && job.status !== 'pending') return
    let cancelled = false
    const pollEvents = async () => {
      try {
        const payload = await api.tuning.events(job.job_id, {
          after_seq: eventCursorRef.current,
          limit: 120,
        })
        if (cancelled) return
        const incoming = payload.events ?? []
        if (incoming.length > 0) {
          setEventLog(prev => [...prev, ...incoming].slice(-40))
        }
        const nextCursor = Math.max(eventCursorRef.current, payload.next_after_seq ?? eventCursorRef.current)
        eventCursorRef.current = nextCursor
        setEventCursor(nextCursor)
        setEventStats(payload.stats ?? null)
        setEventValidation(payload.validation ?? null)
        setEventError('')
      } catch (e: any) {
        if (!cancelled) setEventError(e?.message ?? 'Event stream polling selhal')
      }
    }

    void pollEvents()
    const iv = setInterval(() => { void pollEvents() }, 1500)
    return () => {
      cancelled = true
      clearInterval(iv)
    }
  }, [job.job_id, job.status])

  function toggleSortPriority(col: SortCol, checked: boolean) {
    setSortPriorities(prev => {
      const exists = prev.includes(col)
      if (checked) {
        if (exists) return prev
        return [...prev, col]
      }
      return prev.filter(c => c !== col)
    })
  }

  function toggleFullText(trialIdx: number) {
    setShowFullTextByTrial(prev => ({ ...prev, [trialIdx]: !prev[trialIdx] }))
  }

  const best = job.best_trial_idx != null
    ? (job.results.find(r => r.trial_idx === job.best_trial_idx) ?? null)
    : null
  const bestLane = best
    ? normalizeLatencyLane(best.latency_lane, best.latency_quality, best.perceived_delay_quality)
    : null
  const nonProxyCandidates = job.results.filter(r =>
    !r.is_repeat &&
    r.wer != null &&
    !r.error &&
    r.rtf != null &&
    r.rtf <= 1.2 &&
    normalizeLatencyLane(r.latency_lane, r.latency_quality, r.perceived_delay_quality) === 'strict_live'
  )
  const recommendedBest = nonProxyCandidates.length > 0
    ? nonProxyCandidates.reduce((a, b) => ((a.wer ?? Infinity) <= (b.wer ?? Infinity) ? a : b))
    : best
  const bestIsProxyOnly = bestLane === 'batch_proxy'
  const recommendationDiffers = !!best && !!recommendedBest && best.trial_idx !== recommendedBest.trial_idx
  const highlightedBestTrialIdx = recommendedBest?.trial_idx ?? best?.trial_idx ?? null
  const detailColSpan = 12 + (job.model_ids?.length > 1 ? 1 : 0)
  const ramLimitMb = filterRamMb !== '' ? parseInt(filterRamMb) : null
  const sorted = [...job.results]
    .filter(r => r.wer != null)
    .filter(r => filterThreads == null || (r.params as any).threads === filterThreads)
    .filter(r => !filterViableOnly || r.rtf_viable)
    .filter(r => ramLimitMb == null || (MODEL_RAM_MB[r.model_id ?? ''] ?? 0) <= ramLimitMb)
    .sort((a, b) => {
      for (const col of sortPriorities) {
        const dir = SORT_DEFAULT_DIR[col]
        let an: number | null = null
        let bn: number | null = null
        if (col === 'trial_finished_at') {
          an = parseIsoToMs(a.trial_finished_at ?? null)
          bn = parseIsoToMs(b.trial_finished_at ?? null)
        } else {
          const av = (a as any)[col]
          const bv = (b as any)[col]
          if (typeof av === 'boolean' || typeof bv === 'boolean') {
            an = av === true ? 1 : av === false ? 0 : null
            bn = bv === true ? 1 : bv === false ? 0 : null
          } else {
            an = typeof av === 'number' ? av : null
            bn = typeof bv === 'number' ? bv : null
          }
        }
        if (an == null && bn == null) continue
        if (an == null) return 1
        if (bn == null) return -1
        if (an !== bn) return (an < bn ? -1 : 1) * dir
      }
      return a.trial_idx - b.trial_idx
    })

  const scatterData = job.results
    .filter(r => !r.is_repeat && r.wer != null && r.rtf != null)
    .map(r => ({
      x: r.rtf!,
      y: +(r.wer! * 100).toFixed(2),
      label: Object.entries(r.params)
        .filter(([k]) => k !== 'chunk_seconds')
        .map(([k, v]) => `${k}=${v}`)
        .join(', ') + ` chunk=${r.chunk_seconds}s`,
      isPareto: r.is_pareto,
      isBest: r.trial_idx === highlightedBestTrialIdx,
    }))

  return (
    <div className="space-y-4">
      {/* Stav + nejlepší konfig */}
      <div className="bg-white rounded border border-gray-200 p-4">
        <div className="flex items-center gap-4 flex-wrap">
          <span className="font-mono text-xs text-gray-400">{job.job_id}</span>
          <span className={`text-xs px-2 py-0.5 rounded font-medium ${
            job.status === 'completed' ? 'bg-green-100 text-green-800' :
            job.status === 'running' ? 'bg-blue-100 text-blue-800' :
            job.status === 'failed' ? 'bg-red-100 text-red-800' : 'bg-gray-100 text-gray-600'
          }`}>{job.status}</span>
          <span className="text-sm text-gray-600">{job.completed_trials} / {job.total_trials} triálů</span>
          {elapsedJobS != null && (
            <span className="text-xs text-gray-600 font-mono">
              ⏱ {job.status === 'running' || job.status === 'pending' ? 'Běží' : 'Trvalo'}: {formatElapsedShort(elapsedJobS)}
            </span>
          )}
          {(job.status === 'running' || job.status === 'pending') && etaJobS != null && (
            <span className="text-xs text-gray-600 font-mono">
              ⌛ Odhad zbývá: ~{formatElapsedShort(etaJobS)}
            </span>
          )}
          {job.input_mode && (
            <span className={`text-xs px-2 py-0.5 rounded border font-mono ${
              job.input_mode === 'real_mic'
                ? 'border-emerald-300 text-emerald-700'
                : 'border-gray-300 text-gray-700'
            }`}>
              {job.input_mode}
            </span>
          )}
          {job.strategy && (
            <span className="text-xs px-2 py-0.5 rounded border border-violet-300 text-violet-700 font-mono">
              {job.strategy}
            </span>
          )}
          {job.hardware_profile && (
            <span className="text-xs px-2 py-0.5 rounded border border-gray-300 text-gray-700 font-mono" title={job.hardware_note ?? undefined}>
              {job.hardware_profile}
            </span>
          )}
          {job.load_profile && job.load_profile !== 'none' && (
            <span className="text-xs px-2 py-0.5 rounded border border-amber-300 text-amber-700 font-mono"
              title={`CPU ${job.load_cpu_target_pct ?? '-'}% / RAM ${job.load_ram_target_pct ?? '-'}%`}>
              load {job.load_profile}
            </span>
          )}
          {job.constraints_profile && job.constraints_profile !== 'none' && (
            <span className={`text-xs px-2 py-0.5 rounded border font-mono ${
              job.constraints_applied === false ? 'border-red-300 text-red-700' : 'border-blue-300 text-blue-700'
            }`}
              title={`cores ${job.constraints_cpu_cores ?? '-'} (ok=${job.constraints_cpu_applied ?? '-'}) / RAM ${job.constraints_ram_limit_mb ?? '-'} MB (hard_ok=${job.constraints_ram_hard_cap_applied ?? '-'}; ${job.constraints_ram_hard_cap_error ?? 'ok'}) / prio ${job.constraints_priority ?? '-'} (ok=${job.constraints_priority_applied ?? '-'}) / ram_mode ${job.constraints_ram_mode ?? 'none'}`}>
              cap {job.constraints_profile}
            </span>
          )}
          {job.hardware_info?.hostname && (
            <span className="text-xs text-gray-400" title={job.hardware_info.cpu_model ?? undefined}>
              host: {job.hardware_info.hostname}
            </span>
          )}
          {job.status === 'running' && (
            <div className="w-32 h-2 bg-gray-200 rounded overflow-hidden">
              <div className="h-full bg-blue-500 transition-all"
                style={{ width: `${Math.round(job.completed_trials / job.total_trials * 100)}%` }} />
            </div>
          )}
          {(job.status === 'running' || job.status === 'pending') && (
            <button onClick={onCancel}
              className="text-xs text-red-600 hover:text-red-800 border border-red-200 hover:border-red-400 rounded px-2 py-0.5">
              ⏹ Zastavit
            </button>
          )}
          {job.status === 'running' && ram && (
            <span className={`text-xs font-mono px-2 py-0.5 rounded border ${ram.pct > 90 ? 'text-red-700 bg-red-50 border-red-200' : ram.pct > 75 ? 'text-amber-700 bg-amber-50 border-amber-200' : 'text-gray-600 bg-gray-50 border-gray-200'}`}
              title="RAM systému">
              RAM {ram.used.toLocaleString()} / {ram.total.toLocaleString()} MB ({ram.pct}%)
            </span>
          )}
          <button onClick={() => api.tuning.openJobDir(job.job_id)}
            className="ml-auto text-xs text-gray-500 hover:text-gray-800 border border-gray-200 rounded px-2 py-0.5">
            📁 Otevřít složku
          </button>
          <button
            onClick={() => api.openDir.subtitles()}
            className="text-xs text-gray-500 hover:text-gray-800 border border-gray-200 rounded px-2 py-0.5"
            title="Otevře runtime/library/subtitles (zdrojové VTT texty)"
          >
            📝 Zdrojové texty
          </button>
          <button
            onClick={() => void loadDecisionReport()}
            disabled={decisionLoading || job.status !== 'completed'}
            className="text-xs text-indigo-600 hover:text-indigo-800 border border-indigo-200 hover:border-indigo-400 rounded px-2 py-0.5 disabled:opacity-40 disabled:cursor-not-allowed"
            title="Prepocte finalni decision report z metrik jobu"
          >
            {decisionLoading ? '… Decision' : 'Decision'}
          </button>
        </div>
        {job.audio_ready?.length > 0 && (
          <div className="mt-2 text-xs px-3 py-1.5 bg-green-50 text-green-800 rounded flex gap-3 flex-wrap">
            {job.audio_ready.map(vid => (
              <span key={vid}>✓ Audio {vid} staženo</span>
            ))}
          </div>
        )}
        {job.progress_message && (
          <div className={`mt-2 text-xs font-mono px-3 py-2 rounded ${
            job.status === 'running' ? 'bg-blue-50 text-blue-800' : 'bg-gray-50 text-gray-600'
          }`}>
            {job.status === 'running' && <span className="animate-pulse mr-2">⌛</span>}
            {job.progress_message}
            {job.status === 'running' && msgAge > 0 && (
              <span className={`ml-3 ${msgAge > staleWarnSec ? 'text-red-600 font-bold' : msgAge > 30 ? 'text-amber-600' : 'text-blue-400'}`}>
                {msgAge > staleWarnSec ? '⚠ zaseknuté?' : `+${msgAge}s`}
              </span>
            )}
          </div>
        )}
        {(eventLog.length > 0 || eventStats || eventError) && (
          <div className="mt-2 text-xs px-3 py-2 rounded border border-gray-200 bg-gray-50">
            <div className="flex flex-wrap items-center gap-2 text-gray-700 mb-1">
              <span className="font-semibold">Event stream</span>
              <span className="font-mono text-[11px]">cursor {eventCursor}</span>
              {eventStats && (
                <span className="font-mono text-[11px]">
                  total {eventStats.count} · max {eventStats.max_seq}
                </span>
              )}
              {eventValidation && (
                <span className={`font-mono text-[11px] ${eventValidation.ok ? 'text-green-700' : 'text-red-700'}`}>
                  validate {eventValidation.ok ? 'ok' : 'fail'}
                </span>
              )}
              {eventError && <span className="text-red-700">{eventError}</span>}
            </div>
            {eventLog.length > 0 && (
              <div className="max-h-28 overflow-y-auto space-y-0.5 font-mono text-[11px] text-gray-600">
                {eventLog.slice(-8).map((ev) => (
                  <div key={`${ev.seq}-${ev.event_type}`} className="flex items-start gap-2">
                    <span className="text-gray-400 shrink-0">#{ev.seq}</span>
                    <span className="text-gray-500 shrink-0">{formatClockHHMMSS(ev.ts_utc)}</span>
                    <span className="text-gray-700 shrink-0">{ev.event_type}</span>
                    <span className="truncate">{formatEventPayloadShort(ev.payload)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {job.status === 'running' && isValidationPhase && (
          <div className="mt-2 text-[11px] text-gray-500">
            Validace může u `large_v3` pod cap profilem trvat déle; varování se zobrazí až po 120s bez heartbeat.
          </div>
        )}
        {job.constraints_warnings && job.constraints_warnings.length > 0 && (
          <div className="mt-2 text-xs px-3 py-2 rounded border border-amber-300 bg-amber-50 text-amber-900">
            {job.constraints_warnings.join(' | ')}
          </div>
        )}
        {(job.status === 'completed' || (job.repro_validation?.checked_top_k ?? 0) > 0) && job.repro_validation && (
          <div className={`mt-2 text-xs px-3 py-2 rounded border ${job.repro_validation.passed ? 'border-green-300 bg-green-50 text-green-800' : 'border-amber-300 bg-amber-50 text-amber-900'}`}>
            Repro n≥{job.repro_validation.required_n ?? 3}:{' '}
            {job.repro_validation.passed
              ? 'OK'
              : (job.repro_validation.checked_top_k ?? 0) === 0
                ? (job.status === 'completed'
                  ? 'nevztahuje se (žádný validní seed trial)'
                  : `čeká na seed trialy (${job.completed_trials}/${job.total_trials})`)
                : (job.repro_validation.missing_trial_idxs ?? []).length > 0
                  ? `MISSING trial #${(job.repro_validation.missing_trial_idxs ?? []).join(', ')}`
                  : 'chybí repeat běhy'}
          </div>
        )}

        {bestIsProxyOnly && (
          <div className="mt-3 text-xs px-3 py-2 rounded border border-amber-300 bg-amber-50 text-amber-900">
            Tvrdé varování: nejlepší trial má lane `batch_proxy` (proxy latence). Neber jako finální live rozhodnutí.
          </div>
        )}

        {decisionError && (
          <div className="mt-3 text-xs px-3 py-2 rounded border border-red-300 bg-red-50 text-red-800">
            Decision report: {decisionError}
          </div>
        )}

        {decision?.best && (
          <div className="mt-3 p-3 bg-indigo-50 border border-indigo-200 rounded text-xs">
            <div className="flex items-center gap-2 mb-2">
              <span className="font-semibold text-indigo-800">
                Decision report ({decisionPoolLabel(decision.selected_pool)})
              </span>
              <span className="font-mono text-indigo-700">
                trial #{decision.best.trial_idx} · score {decision.best.score.toFixed(4)}
              </span>
            </div>
            <div className="flex flex-wrap gap-3 text-indigo-900">
              <span>WER <strong>{(decision.best.wer * 100).toFixed(2)}%</strong></span>
              <span>RTF <strong>{decision.best.rtf.toFixed(3)}</strong></span>
              <span>Delay <strong>{decision.best.perceived_delay_s != null ? `${decision.best.perceived_delay_s.toFixed(2)}s` : '-'}</strong></span>
              <span>Success <strong>{decision.best.success_rate != null ? `${(decision.best.success_rate * 100).toFixed(1)}%` : '-'}</strong></span>
              <span>Lane <strong>{decision.best.latency_lane ?? decision.selected_lane ?? '-'}</strong></span>
              <span>LatencyQ <strong>{decision.best.latency_quality ?? '-'}</strong></span>
              <span>Lane mix <strong>{formatLaneCounts(decision.lane_counts)}</strong></span>
              {decision.load_profile && decision.load_profile !== 'none' && (
                <span>
                  Load <strong>{decision.load_profile}</strong> (CPU {decision.load_cpu_target_pct ?? '-'}% / RAM {decision.load_ram_target_pct ?? '-'}%)
                </span>
              )}
              {decision.constraints_profile && decision.constraints_profile !== 'none' && (
                <span>
                  Cap <strong>{decision.constraints_profile}</strong> ({decision.constraints_cpu_cores ?? '-'}C / {decision.constraints_ram_limit_mb ?? '-'} MB)
                </span>
              )}
              {decision.repro_validation && (
                <span>
                  Repro n≥{decision.require_repro_n ?? 3}:{' '}
                  <strong>
                    {decision.repro_validation.passed
                      ? 'OK'
                      : (decision.repro_validation.checked_top_k ?? 0) === 0
                        ? 'nevztahuje se (žádný validní seed trial)'
                        : (decision.repro_validation.missing_trial_idxs ?? []).length > 0
                          ? `MISSING trial #${(decision.repro_validation.missing_trial_idxs ?? []).join(', ')}`
                          : 'chybí repeat běhy'}
                  </strong>
                </span>
              )}
            </div>
            {decision.top && decision.top.length > 1 && (
              <div className="mt-2 text-indigo-800">
                <div className="font-medium mb-1">Top kandidáti:</div>
                <div className="space-y-0.5 font-mono">
                  {decision.top.slice(0, 3).map((c, i) => (
                    <div key={c.trial_idx}>
                      {i + 1}. #{c.trial_idx} WER {(c.wer * 100).toFixed(2)}% · RTF {c.rtf.toFixed(3)} · lane {c.latency_lane ?? '-'} · score {c.score.toFixed(4)}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {recommendedBest && (
          <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-semibold text-green-800">
                🏆 {recommendationDiffers ? 'Doporučená konfigurace (ne-proxy preferovaná)' : 'Nejlepší konfigurace (nejnižší WER, RTF ≤ 1.2)'}
              </span>
              <button
                onClick={() => {
                  localStorage.setItem('tuning_recommendation_v1', JSON.stringify({
                    model_id: recommendedBest.model_id ?? job.model_id,
                    params: recommendedBest.params,
                    wer: recommendedBest.wer,
                    rtf: recommendedBest.rtf,
                  }))
                  alert(`Nastavení uloženo.\nModel: ${recommendedBest.model_id ?? job.model_id}\nParams: ${JSON.stringify(recommendedBest.params)}`)
                }}
                className="ml-auto text-xs bg-green-700 hover:bg-green-800 text-white px-3 py-1 rounded">
                Použít toto nastavení
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(recommendedBest.params).map(([k, v]) => (
                <span key={k} className="bg-white border border-green-200 rounded px-2 py-0.5 text-xs font-mono">
                  <span className="text-gray-500">{k}=</span><strong>{String(v)}</strong>
                </span>
              ))}
            </div>
            <div className="flex gap-4 mt-2 text-xs">
              <WerBadge value={recommendedBest.wer} />
              {recommendedBest.cer != null && <span className="text-gray-600">CER {(recommendedBest.cer * 100).toFixed(1)} %</span>}
              {recommendedBest.rtf != null && <span className={recommendedBest.rtf > 1 ? 'text-red-500' : 'text-green-600'}>RTF {recommendedBest.rtf.toFixed(2)}</span>}
              {recommendedBest.perceived_delay_s != null && <span className="text-gray-500" title="Čas od promluvení do zobrazení přepisu">⏱ {recommendedBest.perceived_delay_s.toFixed(1)}s zpoždění</span>}
              {recommendedBest.latency_ms != null && <span className="text-gray-500">{recommendedBest.latency_ms.toFixed(0)} ms latence</span>}
            </div>
          </div>
        )}
      </div>

      {job.reproducibility && job.reproducibility.length > 0 && (
        <div className="bg-white rounded border border-gray-200 p-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-2">Reproducibility (top kandidáti)</h3>
          <p className="text-xs text-gray-400 mb-3">
            Agregace opakovaných běhů: mean, std a 95% interval spolehlivosti (CI95).
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 text-gray-500 uppercase">
                <tr>
                  <th className="px-2 py-1.5 text-left">Seed trial</th>
                  <th className="px-2 py-1.5 text-center">Běhy</th>
                  <th className="px-2 py-1.5 text-center">WER mean ± std</th>
                  <th className="px-2 py-1.5 text-center">WER CI95</th>
                  <th className="px-2 py-1.5 text-center">RTF mean ± std</th>
                  <th className="px-2 py-1.5 text-center">RTF CI95</th>
                  <th className="px-2 py-1.5 text-center">Live rate</th>
                </tr>
              </thead>
              <tbody>
                {job.reproducibility.map((rep) => (
                  <tr key={rep.seed_trial_idx} className="border-t border-gray-100">
                    <td className="px-2 py-1.5 font-mono">#{rep.seed_trial_idx}</td>
                    <td className="px-2 py-1.5 text-center font-mono">{rep.runs_ok}/{rep.runs_total}</td>
                    <td className="px-2 py-1.5 text-center font-mono">
                      {rep.wer ? `${(rep.wer.mean * 100).toFixed(2)}% ± ${(rep.wer.std * 100).toFixed(2)}%` : '–'}
                    </td>
                    <td className="px-2 py-1.5 text-center font-mono">
                      {rep.wer ? `${(rep.wer.ci95_low * 100).toFixed(2)}–${(rep.wer.ci95_high * 100).toFixed(2)}%` : '–'}
                    </td>
                    <td className="px-2 py-1.5 text-center font-mono">
                      {rep.rtf ? `${rep.rtf.mean.toFixed(3)} ± ${rep.rtf.std.toFixed(3)}` : '–'}
                    </td>
                    <td className="px-2 py-1.5 text-center font-mono">
                      {rep.rtf ? `${rep.rtf.ci95_low.toFixed(3)}–${rep.rtf.ci95_high.toFixed(3)}` : '–'}
                    </td>
                    <td className="px-2 py-1.5 text-center font-mono">
                      {rep.rtf_viable_rate != null ? `${(rep.rtf_viable_rate * 100).toFixed(0)}%` : '–'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Pareto scatter */}
      {scatterData.length > 0 && (
        <div className="bg-white rounded border border-gray-200 p-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-1">Pareto frontier — WER vs RTF</h3>
          <p className="text-xs text-gray-400 mb-3">
            Zlaté body = Pareto optimální (nelze zlepšit WER bez zhoršení RTF a naopak).
            RTF &lt; 1.0 = stíhá živý přepis.
          </p>
          <ResponsiveContainer width="100%" height={280}>
            <ScatterChart margin={{ top: 24, right: 20, bottom: 20, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="x" name="RTF" unit="" label={{ value: 'RTF', position: 'insideBottom', offset: -10 }} type="number" />
              <YAxis dataKey="y" name="WER" unit="%" label={{ value: 'WER %', angle: -90, position: 'insideLeft' }} />
              <ReferenceLine x={1.0} stroke="#ef4444" strokeDasharray="4 4" label={{ value: 'RTF=1', position: 'insideTopRight', fontSize: 10, fill: '#ef4444' }} />
              <Tooltip
                content={({ payload }) => {
                  if (!payload?.length) return null
                  const d = payload[0].payload
                  return (
                    <div className="bg-white border border-gray-200 rounded p-2 text-xs shadow max-w-xs">
                      <div className="font-mono text-gray-500 mb-1 break-all">{d.label}</div>
                      <div>WER: <strong>{d.y} %</strong></div>
                      <div>RTF: <strong>{d.x.toFixed(3)}</strong></div>
                      {d.isPareto && <div className="text-yellow-600 font-semibold mt-1">★ Pareto optimální</div>}
                      {d.isBest && <div className="text-green-600 font-semibold">🏆 Nejlepší</div>}
                    </div>
                  )
                }}
              />
              <Scatter data={scatterData} name="triály">
                {scatterData.map((entry, i) => (
                  <Cell key={i}
                    fill={entry.isBest ? '#16a34a' : entry.isPareto ? '#ca8a04' : '#94a3b8'}
                    stroke={entry.isBest ? '#15803d' : entry.isPareto ? '#a16207' : '#64748b'}
                    strokeWidth={entry.isPareto || entry.isBest ? 2 : 1}
                    r={entry.isBest ? 8 : entry.isPareto ? 6 : 4}
                  />
                ))}
              </Scatter>
            </ScatterChart>
          </ResponsiveContainer>
          <div className="flex gap-4 text-xs text-gray-500 mt-1 justify-center">
            <span><span className="inline-block w-3 h-3 rounded-full bg-green-600 mr-1" />Nejlepší</span>
            <span><span className="inline-block w-3 h-3 rounded-full bg-yellow-600 mr-1" />Pareto optimální</span>
            <span><span className="inline-block w-3 h-3 rounded-full bg-slate-400 mr-1" />Ostatní</span>
          </div>
        </div>
      )}

      {/* Triály s chybou nebo null WER */}
      {job.results.filter(r => r.wer == null).length > 0 && (
        <div className="bg-orange-50 rounded border border-orange-300 p-4">
          <h3 className="text-sm font-semibold text-orange-800 mb-2">
            ⚠ Triály bez výsledku ({job.results.filter(r => r.wer == null).length}×)
          </h3>
          <div className="space-y-1.5">
            {job.results.filter(r => r.wer == null).map(r => (
              <div key={r.trial_idx} className="text-xs font-mono bg-white border border-orange-200 rounded px-2 py-1.5 text-orange-900">
                <span className="text-orange-500 font-bold">Trial {r.trial_idx}:</span>{' '}
                {r.error ?? 'WER = null — prázdný přepis nebo chybí titulky'}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Filtry HW */}
      {job.results.some(r => r.wer != null) && (() => {
        const threadOptions = [...new Set(
          job.results.filter(r => r.wer != null).map(r => (r.params as any).threads).filter(Boolean)
        )].sort((a, b) => a - b) as number[]
        const modelRams = [...new Set(job.results.map(r => r.model_id).filter(Boolean))]
          .map(mid => ({ mid: mid!, ram: MODEL_RAM_MB[mid!] }))
          .filter(x => x.ram)
        return (
          <div className="bg-white rounded border border-gray-200 p-3 flex flex-wrap gap-4 items-center text-xs">
            {/* Vlákna CPU */}
            {threadOptions.length > 1 && (
              <div className="flex items-center gap-1.5">
                <span className="text-gray-500 whitespace-nowrap">Vlákna CPU:</span>
                <button onClick={() => setFilterThreads(null)}
                  className={`px-2 py-0.5 rounded border text-xs ${filterThreads == null ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
                  Vše
                </button>
                {threadOptions.map(t => (
                  <button key={t} onClick={() => setFilterThreads(filterThreads === t ? null : t)}
                    className={`px-2 py-0.5 rounded border text-xs ${filterThreads === t ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'}`}>
                    {t}
                  </button>
                ))}
              </div>
            )}
            {/* Live mic only */}
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={filterViableOnly} onChange={e => setFilterViableOnly(e.target.checked)}
                className="accent-green-600" />
              <span className="text-gray-600">Pouze live mic (RTF &lt; 1.0)</span>
            </label>
            {/* RAM limit */}
            <div className="flex items-center gap-1.5">
              <span className="text-gray-500 whitespace-nowrap">Max RAM:</span>
              <input type="number" value={filterRamMb} onChange={e => setFilterRamMb(e.target.value)}
                placeholder="bez limitu"
                className="w-28 border border-gray-300 rounded px-2 py-0.5 text-xs text-gray-700 placeholder-gray-400"
              />
              <span className="text-gray-400">MB</span>
              {modelRams.length > 0 && (
                <span className="text-gray-400 ml-1">
                  ({modelRams.map(x => `${x.mid!.replace('whisper_cpp_','')} ~${x.ram} MB`).join(', ')})
                </span>
              )}
            </div>
            {/* Reset */}
            {(filterThreads != null || filterViableOnly || filterRamMb !== '' || hasNonDefaultSort) && (
              <button onClick={() => { setFilterThreads(null); setFilterViableOnly(false); setFilterRamMb(''); setSortPriorities(['wer']) }}
                className="text-gray-400 hover:text-gray-700 text-xs underline">
                reset filtrů/řazení
              </button>
            )}
            <span className="ml-auto text-gray-400">{sorted.length} / {job.results.filter(r => r.wer != null).length} triálů</span>
          </div>
        )
      })()}

      {/* Tabulka výsledků */}
      {sorted.length > 0 && (
        <div className="bg-white rounded border border-gray-200 overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-gray-500 uppercase">
              <tr>
                <th className="px-3 py-2 text-center">#</th>
                {job.model_ids?.length > 1 && <th className="px-3 py-2 text-left">Model</th>}
                <th className="px-3 py-2 text-left">Parametry</th>
                {SORT_COLUMNS.map(([col, label], i) => {
                  const idx = sortPriorities.indexOf(col)
                  return (
                    <th key={i}
                      className="px-3 py-2 text-center select-none whitespace-nowrap"
                      title={col === 'perceived_delay_s' ? 'Perceived delay: preferuje měřenou first-word latenci (live), jinak fallback proxy.' : undefined}>
                      <label className="inline-flex items-center gap-1 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={idx >= 0}
                          onChange={e => toggleSortPriority(col, e.target.checked)}
                          className="accent-blue-600"
                        />
                        <span>{label}</span>
                        {idx >= 0 && (
                          <span className="text-[10px] px-1 rounded bg-blue-100 text-blue-700 font-mono">{idx + 1}</span>
                        )}
                      </label>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, rank) => (
                <>
                  <tr key={r.trial_idx}
                    onClick={() => setExpandedTrial(expandedTrial === r.trial_idx ? null : r.trial_idx)}
                    className={`border-t border-gray-100 cursor-pointer ${r.trial_idx === highlightedBestTrialIdx ? 'bg-green-50 hover:bg-green-100' : 'hover:bg-gray-50'}`}>
                    <td className="px-3 py-2 text-center text-gray-400 font-mono">
                      {rank === 0 ? '🏆' : rank + 1}
                    </td>
                    {job.model_ids?.length > 1 && (
                      <td className="px-3 py-2 text-xs font-mono text-blue-700 whitespace-nowrap">
                        {r.model_id?.replace('whisper_cpp_', '') ?? '—'}
                      </td>
                    )}
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1 items-center">
                        <span className="text-blue-500 mr-1 text-xs font-bold">{expandedTrial === r.trial_idx ? '▼' : '▶'}</span>
                        {r.is_repeat && (
                          <span className="bg-indigo-100 text-indigo-700 rounded px-1.5 py-0.5 font-mono">
                            repeat {r.repeat_no ?? '?'} of #{r.repeat_of_trial_idx ?? '?'}
                          </span>
                        )}
                        {Object.entries(r.params).map(([k, v]) => (
                          <span key={k} className="bg-gray-100 rounded px-1.5 py-0.5 font-mono text-gray-700">
                            {k}=<strong>{String(v)}</strong>
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <WerBadge value={r.wer} label="" />
                      {r.error && r.wer != null && (
                        <span className="ml-1 text-orange-500 text-xs" title={`Částečný výsledek — trial selhal: ${r.error}`}>⚠</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-center" title="WER ignorující drobné záměny (znaková vzdálenost &lt; 0.40)">
                      <WerBadge value={r.wer_soft} label="" />
                    </td>
                    <td className="px-3 py-2 text-center"><WerBadge value={r.cer} label="" /></td>
                    <td className="px-3 py-2 text-center"><WerBadge value={r.wer_normalized} label="" /></td>
                    <td className="px-3 py-2 text-center font-mono">
                      {r.rtf != null
                        ? <span className={r.rtf > 1 ? 'bg-red-100 text-red-700 font-bold px-1 rounded' : 'text-green-700 font-semibold'}>{r.rtf.toFixed(3)}</span>
                        : '–'}
                    </td>
                    <td className="px-3 py-2 text-center" title="RTF < 1.0 = model stíhá live přepis mikrofonu">
                      {r.rtf != null
                        ? r.rtf_viable
                          ? <span className="text-green-700 font-bold" title="✓ Stíhá live přepis (RTF &lt; 1.0)">✓ OK</span>
                          : <span className="text-red-600 font-bold" title="✗ Nestíhá live přepis (RTF &gt; 1.0)">✗ Pomalý</span>
                        : '–'}
                    </td>
                    <td
                      className="px-3 py-2 text-center font-mono text-gray-600"
                      title={`Čas od promluvení do zobrazení přepisu (${r.perceived_delay_method ?? 'n/a'})`}
                    >
                      {r.perceived_delay_s != null
                        ? `${r.perceived_delay_quality === 'low' ? '~' : ''}${r.perceived_delay_s.toFixed(1)}s`
                        : '–'}
                    </td>
                    <td className="px-3 py-2 text-center font-mono text-gray-600">
                      {r.latency_ms != null
                        ? `${isApproxLatencyLane(normalizeLatencyLane(r.latency_lane, r.latency_quality, r.perceived_delay_quality)) ? '~' : ''}${r.latency_ms.toFixed(0)}ms`
                        : '–'}
                    </td>
                    <td className="px-3 py-2 text-center font-mono text-gray-600">
                      {formatClockHHMMSS(r.trial_finished_at)}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {r.is_pareto ? <span className="text-yellow-600 font-bold">★</span> : ''}
                    </td>
                  </tr>
                  {expandedTrial === r.trial_idx && (
                    <tr key={`${r.trial_idx}-detail`} className={r.trial_idx === highlightedBestTrialIdx ? 'bg-green-50' : 'bg-gray-50'}>
                      <td colSpan={detailColSpan} className="px-4 py-3 border-t border-gray-100">
                        <div className="space-y-4 text-xs">
                          {r.error && (
                            <div className="bg-red-50 border border-red-200 rounded p-2 text-red-700 font-mono">{r.error}</div>
                          )}

                          {/* Statistiky */}
                          <div className="flex flex-wrap gap-4 text-gray-600 bg-white border border-gray-200 rounded px-3 py-2">
                            <span>WER: <strong>{r.wer != null ? (r.wer*100).toFixed(1)+'%' : '–'}</strong></span>
                            <span title="WER ignorující drobné záměny (znaková vzdálenost &lt; 0.40)">
                              WER soft: <strong className={r.wer_soft != null && r.wer != null && r.wer_soft < r.wer ? 'text-green-700' : ''}>
                                {r.wer_soft != null ? (r.wer_soft*100).toFixed(1)+'%' : '–'}
                              </strong>
                              {r.wer != null && r.wer_soft != null && r.wer_soft < r.wer && (
                                <span className="text-xs text-gray-400 ml-1">
                                  ({((r.wer - r.wer_soft)*100).toFixed(1)} % drobných)
                                </span>
                              )}
                            </span>
                            <span>CER: <strong>{r.cer != null ? (r.cer*100).toFixed(1)+'%' : '–'}</strong></span>
                            <span>WER norm: <strong>{r.wer_normalized != null ? (r.wer_normalized*100).toFixed(1)+'%' : '–'}</strong></span>
                            <span>MER: <strong>{r.mer != null ? (r.mer*100).toFixed(1)+'%' : '–'}</strong></span>
                            <span>WIL: <strong>{r.wil != null ? (r.wil*100).toFixed(1)+'%' : '–'}</strong></span>
                            <span className={r.rtf != null && r.rtf > 1 ? 'text-red-700 font-bold' : 'text-green-700 font-semibold'}>
                              RTF: <strong>{r.rtf?.toFixed(3) ?? '–'}</strong>
                            </span>
                            <span>Latence: <strong>{r.latency_ms != null ? r.latency_ms.toFixed(0)+'ms' : '–'}</strong></span>
                            {r.ram_mb != null && (
                              <span title="Průměrná RAM model procesu přes videa v trialu">
                                RAM avg: <strong>{r.ram_mb.toFixed(0)} MB</strong>
                              </span>
                            )}
                            {r.ram_peak_mb != null && (
                              <span title="Nejvyšší RAM model procesu přes videa v trialu">
                                RAM peak: <strong>{r.ram_peak_mb.toFixed(0)} MB</strong>
                              </span>
                            )}
                            {r.worker_rss_peak_mb != null && (
                              <span title="Peak RSS tuning worker procesu během trialu">
                                Worker RSS peak: <strong>{r.worker_rss_peak_mb.toFixed(0)} MB</strong>
                              </span>
                            )}
                            {(r.load_cpu_target_pct != null || r.load_ram_target_pct != null) && (
                              <span title={`Profil: ${r.load_profile ?? 'custom'}`}>
                                Load cíl: <strong>CPU {r.load_cpu_target_pct != null ? `${r.load_cpu_target_pct.toFixed(0)}%` : '–'}</strong>
                                {' / '}
                                <strong>RAM {r.load_ram_target_pct != null ? `${r.load_ram_target_pct.toFixed(0)}%` : '–'}</strong>
                              </span>
                            )}
                            {(r.load_cpu_actual_avg_pct != null || r.load_ram_actual_avg_pct != null) && (
                              <span>
                                Load skutečnost: <strong>CPU {r.load_cpu_actual_avg_pct != null ? `${r.load_cpu_actual_avg_pct.toFixed(0)}%` : '–'}</strong>
                                {' / '}
                                <strong>RAM {r.load_ram_actual_avg_pct != null ? `${r.load_ram_actual_avg_pct.toFixed(0)}%` : '–'}</strong>
                                {r.load_samples != null && <span className="text-gray-400 ml-1">({r.load_samples} vzorků)</span>}
                              </span>
                            )}
                            {r.load_control_ok === false && (
                              <span className="text-amber-700">
                                Load control: mimo toleranci
                              </span>
                            )}
                            {(r.constraints_cpu_cores != null || r.constraints_ram_limit_mb != null) && (
                              <span>
                                Cap: <strong>{r.constraints_cpu_cores != null ? `${r.constraints_cpu_cores}C` : '—'}</strong>
                                {' / '}
                                <strong>{r.constraints_ram_limit_mb != null ? `${r.constraints_ram_limit_mb} MB` : '—'}</strong>
                                {' / '}
                                <strong>{r.constraints_priority ?? 'below_normal'}</strong>
                                {' / '}
                                <strong>{r.constraints_ram_mode ?? 'none'}</strong>
                              </span>
                            )}
                            {r.constraints_applied === false && (
                              <span className="text-amber-700">Cap: neaplikováno</span>
                            )}
                            {r.perceived_delay_s != null && (
                              <span title={`Perceived delay method: ${r.perceived_delay_method ?? 'n/a'}`}>
                                Zpoždění: <strong>{r.perceived_delay_s.toFixed(1)}s</strong>
                                {r.perceived_delay_quality === 'low' && <span className="ml-1 text-amber-700">(proxy)</span>}
                              </span>
                            )}
                            {(r.latency_lane || r.latency_quality) && (
                              <span title="Lane + kvalita latence podle způsobu měření">
                                Latence lane: <strong>{normalizeLatencyLane(r.latency_lane, r.latency_quality, r.perceived_delay_quality)}</strong>
                                {r.latency_quality && (
                                  <>
                                    {' · '}kvalita: <strong>{r.latency_quality}</strong>
                                  </>
                                )}
                              </span>
                            )}
                            {r.first_token_ms_p95 != null && (
                              <span>First token p95: <strong>{r.first_token_ms_p95.toFixed(0)}ms</strong></span>
                            )}
                            {r.segment_finalize_ms_p95 != null && (
                              <span>Finalize p95: <strong>{r.segment_finalize_ms_p95.toFixed(0)}ms</strong></span>
                            )}
                            {r.processing_ms_p95 != null && (
                              <span>Chunk proc p95: <strong>{r.processing_ms_p95.toFixed(0)}ms</strong></span>
                            )}
                            {r.capture_jitter_ms_p95 != null && (
                              <span>Capture jitter p95: <strong>{r.capture_jitter_ms_p95.toFixed(0)}ms</strong></span>
                            )}
                            {r.capture_lag_ms_p95 != null && (
                              <span>Capture lag p95: <strong>{r.capture_lag_ms_p95.toFixed(0)}ms</strong></span>
                            )}
                            {r.queue_depth_peak_s != null && (
                              <span>Queue debt peak: <strong>{(r.queue_depth_peak_s * 1000).toFixed(0)}ms</strong></span>
                            )}
                            {r.backpressure_events != null && (
                              <span>Backpressure: <strong>{r.backpressure_events}</strong></span>
                            )}
                            {r.drop_rate != null && (
                              <span>Drop: <strong>{(r.drop_rate * 100).toFixed(2)}%</strong></span>
                            )}
                            {r.reason_code && (
                              <span>Reason: <strong>{r.reason_code}</strong></span>
                            )}
                            {r.rtf_p95 != null && <span>RTF p95: <strong>{r.rtf_p95.toFixed(3)}</strong></span>}
                            {r.latency_p95_ms != null && <span>Latence p95: <strong>{r.latency_p95_ms.toFixed(0)}ms</strong></span>}
                            {r.ram_p95_mb != null && <span>RAM p95: <strong>{r.ram_p95_mb.toFixed(0)} MB</strong></span>}
                            {r.success_rate != null && <span>Stabilita: <strong>{(r.success_rate * 100).toFixed(0)}%</strong></span>}
                            {r.resource_metrics_available === false && (
                              <span className="text-amber-700">RAM/RSS: nedostupné (chybí psutil)</span>
                            )}
                            {r.elapsed_s != null && <span>Engine: <strong>{r.elapsed_s.toFixed(1)}s</strong></span>}
                            {r.total_audio_s != null && <span>Audio: <strong>{r.total_audio_s.toFixed(1)}s</strong></span>}
                            {r.word_count != null && <span>Slov: <strong>{r.word_count}</strong></span>}
                          </div>

                          {/* Per-video breakdown */}
                          {r.source_metrics && r.source_metrics.length > 0 && (
                            <div>
                              <div className="font-semibold text-gray-600 mb-1">Výsledky per video (průměr v tabulce):</div>
                              <div className="flex flex-wrap gap-2">
                                {r.source_metrics.map(sm => (
                                  <div key={sm.video_id} className={`border rounded px-2 py-1 font-mono text-center ${sm.error ? 'border-red-200 bg-red-50' : 'border-gray-200 bg-white'}`}>
                                    <div className="flex items-center justify-center gap-1">
                                      <div className="text-gray-500 text-xs truncate max-w-28" title={sm.video_id}>
                                        {videoLabel(library.find(v => v.video_id === sm.video_id)?.title ?? sm.video_id, sm.video_id)}
                                      </div>
                                      <button
                                        type="button"
                                        onClick={() => api.openDir.subtitlesVideo(sm.video_id)}
                                        className="text-[11px] text-gray-400 hover:text-gray-700"
                                        title={`Otevřít titulky videa ${sm.video_id}`}
                                      >
                                        📁
                                      </button>
                                    </div>
                                    {sm.error
                                      ? <div className="text-red-600 text-xs">chyba</div>
                                      : <>
                                          <div>WER <strong>{sm.wer != null ? (sm.wer*100).toFixed(1)+'%' : '–'}</strong></div>
                                          <div className={sm.rtf != null && sm.rtf > 1 ? 'text-red-600' : 'text-green-700'}>
                                            RTF <strong>{sm.rtf?.toFixed(2) ?? '–'}</strong>
                                          </div>
                                        </>}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Plné texty */}
                          {(r.transcript || r.reference_text) && (
                            <div>
                              <div className="flex items-center gap-2 mb-1">
                                <div className="font-semibold text-gray-600">Srovnání textů (plné znění):</div>
                                <button
                                  type="button"
                                  onClick={() => toggleFullText(r.trial_idx)}
                                  className="text-[11px] text-blue-700 hover:text-blue-900 border border-blue-200 rounded px-2 py-0.5"
                                >
                                  {showFullTextByTrial[r.trial_idx] ? '▼ Skrýt texty' : '▶ Zobrazit texty'}
                                </button>
                              </div>
                              <div className="mt-1 text-[11px] text-gray-500">
                                Přepsaný: {textWordCount(r.transcript).toLocaleString()} slov · {textCharCount(r.transcript).toLocaleString()} znaků
                                {' '}|{' '}
                                Referenční: {textWordCount(r.reference_text).toLocaleString()} slov · {textCharCount(r.reference_text).toLocaleString()} znaků
                                {' '}|{' '}
                                Δ slov {Math.abs(textWordCount(r.transcript) - textWordCount(r.reference_text)).toLocaleString()}
                                {' '}·{' '}
                                Δ znaků {Math.abs(textCharCount(r.transcript) - textCharCount(r.reference_text)).toLocaleString()}
                              </div>
                              {showFullTextByTrial[r.trial_idx] && (
                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 mt-2">
                                  <div className="bg-white border border-gray-200 rounded p-2">
                                    <div className="text-[11px] text-gray-500">
                                      Přepsaný text
                                    </div>
                                    <div className="mt-1 text-xs leading-5 whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                                      {r.transcript || '—'}
                                    </div>
                                  </div>
                                  <div className="bg-white border border-gray-200 rounded p-2">
                                    <div className="text-[11px] text-gray-500">
                                      Referenční text
                                    </div>
                                    <div className="mt-1 text-xs leading-5 italic whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                                      {r.reference_text || '—'}
                                    </div>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}

                          {/* Word diff */}
                          {r.word_diff && r.word_diff.length > 0 && (
                            <div>
                              <div className="font-semibold text-gray-600 mb-1">Word diff (přepis vs reference):</div>
                              <div className="bg-white border border-gray-200 rounded p-2 max-h-48 overflow-auto">
                                <div className="inline-block max-w-full align-top">
                                  <div className="inline-flex gap-1 font-mono text-xs leading-6 whitespace-nowrap">
                                    {buildWordDiffAlignedCells(r.word_diff as WordDiffItem[]).map((cell) => (
                                      <div
                                        key={cell.key}
                                        className="shrink-0"
                                        style={{ width: `${cell.widthCh}ch` }}
                                        title={cell.title}
                                      >
                                        <div className={`${cell.topClass} block`}>{cell.top || '\u00A0'}</div>
                                        <div className={`${cell.bottomClass} block mt-0.5 text-xs not-italic`}>{cell.bottom || '\u00A0'}</div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              </div>
                              <div className="flex flex-wrap gap-3 mt-1 text-xs text-gray-400">
                                <span><span className="bg-sky-100 text-sky-700 rounded px-1 font-semibold">slovo</span> záměna</span>
                                <span><span className="bg-green-100 text-green-700 rounded px-1">slovo</span> záměna (drobná)</span>
                                <span><span className="bg-red-200 text-red-800 rounded px-1 font-semibold">[slovo]</span> chybí</span>
                                <span><span className="bg-gray-100 text-gray-500 rounded px-1 line-through">slovo</span> přebývá</span>
                              </div>
                            </div>
                          )}

                          {/* Chunk metriky */}
                          {r.chunk_metrics && r.chunk_metrics.length > 0 && (
                            <div>
                              <div className="font-semibold text-gray-600 mb-1">Chunk metriky (RTF per chunk):</div>
                              <div className="flex flex-wrap gap-1">
                                {r.chunk_metrics.map((c, i) => (
                                  <div key={i} className={`px-2 py-1 rounded font-mono text-center border ${
                                    c.rtf > 1 ? 'bg-red-50 border-red-200 text-red-700' :
                                    c.rtf > 0.7 ? 'bg-yellow-50 border-yellow-200 text-yellow-700' :
                                    'bg-green-50 border-green-200 text-green-700'
                                  }`}>
                                    <div className="text-xs">{c.chunk_start_s.toFixed(0)}s</div>
                                    <div className="font-bold">{c.rtf.toFixed(2)}</div>
                                  </div>
                                ))}
                              </div>
                              <div className="text-gray-400 mt-1">RTF &lt;0.7 zelená · 0.7–1.0 žlutá · &gt;1.0 červená (nestíhá živý přepis)</div>
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Čekání na první výsledky */}
      {job.status === 'running' && job.results.length === 0 && (
        <div className="bg-white rounded border border-blue-200 p-4 text-sm text-blue-800">
          <span className="animate-pulse mr-2">⌛</span>
          Probíhá první trial — {job.progress_message || 'inicializace...'}
        </div>
      )}

      {job.status === 'failed' && (
        <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">
          <strong>Tuning selhal:</strong> {job.error}
        </div>
      )}
    </div>
  )
}
