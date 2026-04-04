/**
 * TranscribeJobPanel — levý panel stránky Přepis.
 *
 * Sekce:
 * 1. Zdroj audia: Knihovna | Upload souboru
 * 2. Rozsah přepisu: celý soubor / vlastní úsek (od–do)
 * 3. Výběr modelu + parametry (ModelParamsForm)
 * 4. Spustit → progress bar
 * 5. Live přepis: průběžné segmenty jak přicházejí
 * 6. Diagnostika v reálném čase (RTF, CPU, RAM, latence, progress)
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../../api/client'
import { ModelParamsForm } from '../ModelParamsForm'
import { loadSettings, saveSettings } from './useTranscribeStorage'
import type { LibraryItem, ModelDescriptor, BenchmarkJobStatus, LiveJobProgress, SegmentBundle } from '../../types'
import { formatClockHms, formatDateTimeDayMonthHm, formatDateTimeShort } from '../../lib/time'

/** Seřadí položky knihovny stejně jako LibraryPage (přečte uložené nastavení z localStorage). */
function sortLibraryItems(items: LibraryItem[]): LibraryItem[] {
  let sortOrder: string[]
  let sortDirMap: Record<string, 'asc' | 'desc'>
  try {
    const s = JSON.parse(localStorage.getItem('astt_library_settings_v1') ?? '{}')
    sortOrder = s.sortOrder ?? ['added_at']
    sortDirMap = s.sortDirMap ?? {}
  } catch {
    sortOrder = ['added_at']
    sortDirMap = {}
  }
  const defaultDir: Record<string, 'asc' | 'desc'> = {
    title: 'asc', language: 'asc', duration: 'asc', genre: 'asc',
    view_count: 'desc', added_at: 'desc', wer: 'asc',
    subtitles: 'desc', audio: 'desc', visible_in_menus: 'desc',
  }
  const dir = (k: string): 'asc' | 'desc' => sortDirMap[k] ?? defaultDir[k] ?? 'asc'
  return [...items].sort((a, b) => {
    for (const key of (sortOrder.length ? sortOrder : ['added_at'])) {
      let va: string | number = ''
      let vb: string | number = ''
      if (key === 'title') { va = (a.title || '').toLowerCase(); vb = (b.title || '').toLowerCase() }
      else if (key === 'language') { va = a.language || ''; vb = b.language || '' }
      else if (key === 'duration') { va = a.duration_seconds ?? -1; vb = b.duration_seconds ?? -1 }
      else if (key === 'genre') { va = (a.genre || '').toLowerCase(); vb = (b.genre || '').toLowerCase() }
      else if (key === 'view_count') { va = a.view_count ?? -1; vb = b.view_count ?? -1 }
      else if (key === 'added_at') { va = a.upload_date ?? a.added_at ?? ''; vb = b.upload_date ?? b.added_at ?? '' }
      else if (key === 'subtitles') { va = a.subtitles_local ? 1 : 0; vb = b.subtitles_local ? 1 : 0 }
      else if (key === 'audio') { va = a.audio_cached ? 1 : 0; vb = b.audio_cached ? 1 : 0 }
      const cmp = va < vb ? -1 : va > vb ? 1 : 0
      if (cmp !== 0) return dir(key) === 'asc' ? cmp : -cmp
    }
    return 0
  })
}

interface UploadedSource {
  source_id: string
  filename: string
  original_name: string
  url: string
  source_path: string
  size_bytes: number
}

interface TranscriptSegment {
  text: string
  timestamp: string
  isNew: boolean
}

interface Props {
  onAudioReady: (audioUrl: string, sourcePath?: string) => void
  onTranscriptUpdate: (transcript: string, audioSecs?: number) => void
  onJobStop?: () => void
  audioDuration?: number
  onModelChange?: (modelId: string) => void
  onSourceLabelChange?: (label: string) => void
  onVideoIdChange?: (videoId: string) => void
}

// Formátování sekund na MM:SS nebo H:MM:SS
function fmtTime(s: number): string {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

// Parsování MM:SS nebo H:MM:SS → sekundy
function parseTime(s: string): number | null {
  const parts = s.trim().split(':').map(Number)
  if (parts.some(isNaN)) return null
  if (parts.length === 2) return parts[0] * 60 + parts[1]
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2]
  return null
}

type SuitabilityGrade = 'green' | 'amber' | 'red'

function toneTextClass(level: SuitabilityGrade): string {
  if (level === 'green') return 'text-emerald-300'
  if (level === 'amber') return 'text-amber-300'
  return 'text-red-300'
}

const ACTIVE_JOB_KEY = 'astt_active_transcribe_job'
const PRESTART_DELAY_MS = 2000
const TRANSCRIPT_BLOCK_SEPARATOR = '\n\n\n'

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function fmtParamValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value == null) return 'null'
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function formatElapsedMmSs(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds))
  const m = Math.floor(safe / 60)
  const s = safe % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export function TranscribeJobPanel({ onAudioReady, onTranscriptUpdate, onJobStop, audioDuration, onModelChange, onSourceLabelChange, onVideoIdChange }: Props) {
  // Načti uložená nastavení
  const _saved = loadSettings()

  // --- Zdroj ---
  const [sourceTab, setSourceTab] = useState<'library' | 'upload'>((_saved.source_tab as 'library' | 'upload') ?? 'library')
  const [library, setLibrary] = useState<LibraryItem[]>([])
  const [selectedVideoId, setSelectedVideoId] = useState<string>(_saved.selected_video_id ?? '')
  const [uploadedSource, setUploadedSource] = useState<UploadedSource | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [dragOver, setDragOver] = useState(false)

  // --- Rozsah přepisu ---
  const [rangeMode, setRangeMode] = useState<'full' | 'segment'>((_saved.range_mode as 'full' | 'segment') ?? 'full')
  const [rangeFrom, setRangeFrom] = useState(_saved.range_from ?? '00:00')
  const [rangeTo, setRangeTo] = useState(_saved.range_to ?? '')
  const [rangeError, setRangeError] = useState('')
  const [segmentBundle, setSegmentBundle] = useState<SegmentBundle | null>(null)
  const [segmentBundleLoading, setSegmentBundleLoading] = useState(false)
  const [segmentBundleEnabled, setSegmentBundleEnabled] = useState<boolean>(_saved.segment_bundle_enabled ?? true)
  const [tsEnabled, setTsEnabled] = useState(_saved.ts_enabled ?? true)
  const initialTsInterval = Math.max(5, Number(_saved.ts_interval_s ?? 60) || 60)
  const [tsIntervalS, setTsIntervalS] = useState(initialTsInterval)
  const [tsIntervalDraft, setTsIntervalDraft] = useState(String(initialTsInterval))

  // --- Model ---
  const [registry, setRegistry] = useState<ModelDescriptor[]>([])
  const [selectedModel, setSelectedModel] = useState<string>(_saved.model ?? '')
  const [modelParams, setModelParams] = useState<Record<string, unknown>>(
    (_saved.model_params ?? {})[_saved.model ?? ''] ?? {}
  )

  // --- Job ---
  const _savedJob = (() => { try { return JSON.parse(localStorage.getItem(ACTIVE_JOB_KEY) ?? 'null') } catch { return null } })()
  const [job, setJob] = useState<BenchmarkJobStatus | null>(_savedJob)
  const [live, setLive] = useState<LiveJobProgress | null>(null)
  const [running, setRunning] = useState<boolean>(_savedJob?.status === 'running' || _savedJob?.status === 'pending')
  const [msg, setMsg] = useState('')
  const [copyMsg, setCopyMsg] = useState('')
  const [finalMetrics, setFinalMetrics] = useState<{
    rtf: number | null; latency_ms: number | null; ram_mb: number | null
    cpu_percent: number | null; wer: number | null
    chunk_p50_ms: number | null; chunk_p95_ms: number | null
  } | null>(null)

  // --- Live segmenty ---
  const [segments, setSegments] = useState<TranscriptSegment[]>([])
  const [lastTranscript, setLastTranscript] = useState('')
  const [lastCoreTranscript, setLastCoreTranscript] = useState('')
  const [segmentedRunActive, setSegmentedRunActive] = useState(false)
  const [segmentProgressLabel, setSegmentProgressLabel] = useState('')
  const segmentsEndRef = useRef<HTMLDivElement>(null)
  const runStartLineRef = useRef('')
  const runEndLineRef = useRef('')
  const runRangeRef = useRef<{ startS: number; endS: number | null }>({ startS: 0, endS: null })
  const runWallStartedAtMsRef = useRef<number | null>(null)
  const syntheticTsLinesRef = useRef<string[]>([])
  const lastSyntheticBoundaryRef = useRef<number>(-1)

  // --- Diagnostika ---
  const [showDiag, setShowDiag] = useState(true)
  const [elapsedSec, setElapsedSec] = useState(0)

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const cancelRequestedRef = useRef(false)

  // Sekundový čítač elapsed pro zobrazení při stagnaci progressu
  useEffect(() => {
    if (running && job?.started_at) {
      elapsedRef.current = setInterval(() => {
        setElapsedSec(Math.floor((Date.now() - new Date(job.started_at!).getTime()) / 1000))
      }, 1000)
    } else {
      if (elapsedRef.current) clearInterval(elapsedRef.current)
      setElapsedSec(0)
    }
    return () => { if (elapsedRef.current) clearInterval(elapsedRef.current) }
  }, [running, job?.started_at])

  // Obnov poll po reloadu pokud byl job running
  useEffect(() => {
    if (_savedJob?.job_id && (_savedJob.status === 'running' || _savedJob.status === 'pending')) {
      startPoll(_savedJob.job_id)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    api.library.list().then(items => {
      const visible = sortLibraryItems(items.filter(i => i.visible_in_menus !== false))
      setLibrary(visible)
      const savedVid = loadSettings().selected_video_id
      if (savedVid && visible.find(v => v.video_id === savedVid)) {
        setSelectedVideoId(savedVid)
      } else if (visible.length > 0) {
        setSelectedVideoId(visible[0].video_id)
      }
    }).catch(() => {})
    api.models.registry().then(reg => {
      setRegistry(reg)
      // Přednostně použij uloženou volbu; fallback na první model
      const saved = loadSettings().model
      const chosen = (saved && reg.find(m => m.model_id === saved))
        ? saved
        : reg.length > 0 ? reg[0].model_id : ''
      if (chosen) {
        setSelectedModel(chosen)
        saveSettings({ model: chosen })
      }
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (sourceTab !== 'library' || !selectedVideoId) {
      setSegmentBundle(null)
      setSegmentBundleLoading(false)
      return
    }
    let cancelled = false
    setSegmentBundleLoading(true)
    api.library.getSegmentBundle(selectedVideoId).then(bundle => {
      if (cancelled) return
      setSegmentBundle(bundle)
    }).catch(() => {
      if (cancelled) return
      setSegmentBundle(null)
    }).finally(() => {
      if (!cancelled) setSegmentBundleLoading(false)
    })
    return () => { cancelled = true }
  }, [sourceTab, selectedVideoId])

  useEffect(() => {
    setTsIntervalDraft(String(tsIntervalS))
  }, [tsIntervalS])

  const commitTsIntervalDraft = useCallback(() => {
    const parsed = Number(tsIntervalDraft)
    const normalized = Number.isFinite(parsed)
      ? Math.max(5, Math.min(3600, Math.floor(parsed)))
      : 5
    setTsIntervalS(normalized)
    setTsIntervalDraft(String(normalized))
    saveSettings({ ts_interval_s: normalized })
  }, [tsIntervalDraft])

  // Při změně audioDuration nastavíme výchozí rangeTo
  useEffect(() => {
    if (audioDuration && audioDuration > 0 && !rangeTo) {
      setRangeTo(fmtTime(audioDuration))
    }
  }, [audioDuration, rangeTo])

  const selectedLibraryItem = library.find(v => v.video_id === selectedVideoId)
  const sourceDisplayName = sourceTab === 'library'
    ? (selectedLibraryItem?.title || '')
    : (uploadedSource?.original_name || '')
  const selectedModelDesc = registry.find(m => m.model_id === selectedModel)
  const selectedModelLabel = selectedModelDesc?.label || selectedModel
  const streamSuitability: SuitabilityGrade = selectedModelDesc?.stream_suitability ?? 'red'
  const transcriptSuitability: SuitabilityGrade = selectedModelDesc?.transcript_suitability ?? 'red'
  const modelPreviewWords = (selectedModelLabel || '')
    .split(/[^A-Za-z0-9-]+/)
    .map(w => w.trim())
    .filter(Boolean)
  const modelWordsForPreview = modelPreviewWords.length > 0 ? modelPreviewWords : [selectedModelLabel]
  const segmentBundleActive = sourceTab === 'library'
    && !!segmentBundle
    && segmentBundleEnabled
    && segmentBundle.segments.length > 0

  const composeTranscript = useCallback((coreText: string): string => {
    const parts: string[] = []
    const startLine = runStartLineRef.current.trim()
    const body = coreText.trim()
    const endLine = runEndLineRef.current.trim()
    const syntheticTsBlock = syntheticTsLinesRef.current.join('\n').trim()
    if (startLine) parts.push(startLine)
    if (body) {
      const bodyLines = body
        .split('\n')
        .map(line => line.trim())
        .filter(Boolean)
      if (bodyLines.length > 1) {
        parts.push(bodyLines[0])
        parts.push('α START')
        if (syntheticTsBlock) parts.push(syntheticTsBlock)
        parts.push(bodyLines.slice(1).join('\n'))
      } else {
        parts.push('α START')
        if (syntheticTsBlock) parts.push(syntheticTsBlock)
        parts.push(bodyLines[0])
      }
    } else if (startLine && syntheticTsBlock) {
      parts.push('α START')
      parts.push(syntheticTsBlock)
    }
    if (endLine) parts.push(endLine)
    return parts.join(TRANSCRIPT_BLOCK_SEPARATOR).trim()
  }, [])

  const buildSettingsSummary = useCallback((desc: ModelDescriptor | undefined, overrides: Record<string, unknown>): string => {
    if (!desc) return '-'
    const byName = new Map(Object.entries(overrides))
    const entries = desc.params.map(p => ({
      name: p.name,
      value: byName.has(p.name) ? byName.get(p.name) : p.default,
    }))
    for (const [name, value] of byName.entries()) {
      if (!entries.some(e => e.name === name)) entries.push({ name, value })
    }
    if (entries.length === 0) return '-'
    return entries.map(e => `${e.name}=${fmtParamValue(e.value)}`).join(', ')
  }, [])

  const buildRangeLabel = useCallback((startS: number, endS: number | null): string => {
    const start = Math.max(0, Math.floor(startS))
    const end = endS == null ? '?' : fmtTime(Math.max(start, Math.floor(endS)))
    return `${fmtTime(start)}–${end}`
  }, [])

  const appendOmegaLine = useCallback(() => {
    const finished = formatDateTimeDayMonthHm(Date.now())
    const range = buildRangeLabel(runRangeRef.current.startS, runRangeRef.current.endS)
    const wallStarted = runWallStartedAtMsRef.current
    const elapsed = wallStarted != null
      ? formatElapsedMmSs((Date.now() - wallStarted) / 1000)
      : '--:--'
    runEndLineRef.current = `Ω END | Ukončeno: ${finished} | Rozsah zdroje: ${range} | Přepis celkem trval ${elapsed}`
  }, [buildRangeLabel])

  const copyText = useCallback(async (value: string, label: string) => {
    const text = value.trim()
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopyMsg(`${label} zkopírován.`)
    } catch {
      try {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.focus()
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
        setCopyMsg(`${label} zkopírován.`)
      } catch {
        setCopyMsg(`Kopírování selhalo: ${label}.`)
      }
    }
    window.setTimeout(() => setCopyMsg(''), 2200)
  }, [])

  const publishTranscript = useCallback((coreText: string, audioSecs?: number) => {
    const normalizedCore = coreText.trim()
    const hasInlineTimestamps = /\[\d{2}:\d{2}\]/.test(normalizedCore)
    if (!hasInlineTimestamps && tsEnabled && audioSecs != null && audioSecs > 0) {
      const boundary = Math.floor(audioSecs / tsIntervalS) * tsIntervalS
      if (boundary > lastSyntheticBoundaryRef.current && boundary > 0) {
        const m = Math.floor(boundary / 60)
        const s = boundary % 60
        syntheticTsLinesRef.current.push(`[${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}]`)
        lastSyntheticBoundaryRef.current = boundary
      }
    } else if (hasInlineTimestamps) {
      syntheticTsLinesRef.current = []
      lastSyntheticBoundaryRef.current = -1
    }
    setLastCoreTranscript(normalizedCore)
    const normalized = composeTranscript(normalizedCore)
    setLastTranscript(normalized)
    const lines = normalized
      .split(/\n+/)
      .map(s => s.trim())
      .filter(Boolean)
    setSegments(lines.map((line, i) => ({
      text: line,
      timestamp: formatClockHms(Date.now()),
      isNew: i === lines.length - 1,
    })))
    onTranscriptUpdate(normalized, audioSecs)
    setTimeout(() => segmentsEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
  }, [composeTranscript, onTranscriptUpdate, tsEnabled, tsIntervalS])

  // Přidání nových segmentů z live transcriptu
  useEffect(() => {
    if (segmentedRunActive) return
    const streamedText = (live?.transcript_ts && live.transcript_ts.trim())
      ? live.transcript_ts
      : live?.transcript
    if (!streamedText || streamedText === lastCoreTranscript) return
    const newText = streamedText

    // Odvoď audio čas pro timestamp:
    // 1) z progress message "Xs zprac." (benchmark mode)
    // 2) z live.percent (10–90 = replay fáze) × délka audia
    const audioMatch = live?.message?.match(/(\d+(?:\.\d+)?)s\s+zprac/)
    let audioSecs: number | undefined
    const runStart = runRangeRef.current.startS
    const runEnd = runRangeRef.current.endS
    const runSpan = runEnd != null ? Math.max(1, runEnd - runStart) : null
    if (audioMatch) {
      const processedS = parseFloat(audioMatch[1])
      if (!Number.isNaN(processedS)) {
        audioSecs = runSpan != null
          ? Math.min(runStart + processedS, runEnd ?? (runStart + processedS))
          : runStart + processedS
      }
    }
    if (audioSecs == null && live?.percent != null && live.percent >= 10) {
      if (runSpan != null) {
        audioSecs = runStart + ((live.percent - 10) / 80) * runSpan
      } else if (audioDuration && audioDuration > 0) {
        audioSecs = ((live.percent - 10) / 80) * audioDuration
      }
    }
    publishTranscript(newText, audioSecs)
  }, [live?.transcript, live?.transcript_ts, live?.message, live?.percent, lastCoreTranscript, audioDuration, segmentedRunActive, publishTranscript])

  const handleUpload = useCallback(async (file: File) => {
    setUploading(true)
    setUploadError('')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await fetch('/api/transcribe/upload', { method: 'POST', body: fd })
      if (!r.ok) throw new Error(await r.text())
      const data: UploadedSource = await r.json()
      setUploadedSource(data)
      onAudioReady(data.url, data.source_path)
    } catch (e: unknown) {
      setUploadError(e instanceof Error ? e.message : 'Upload selhal')
    } finally {
      setUploading(false)
    }
  }, [onAudioReady])

  const loadRunMetrics = useCallback((runId: string) => {
    api.runs.get(runId).then(run => {
      const res = run.results?.[0]
      if (!res) return
      const agg = res.aggregate
      const chunks = res.source_metrics?.[0]?.chunk_metrics ?? []
      const rtfs = chunks.map(c => c.rtf).filter((v): v is number => v != null).sort((a, b) => a - b)
      const p50idx = Math.floor(rtfs.length * 0.5)
      const p95idx = Math.floor(rtfs.length * 0.95)
      setFinalMetrics({
        rtf: agg.rtf,
        latency_ms: agg.latency_ms,
        ram_mb: agg.ram_mb,
        cpu_percent: agg.cpu_percent,
        wer: agg.wer,
        chunk_p50_ms: rtfs.length > 0 ? (rtfs[p50idx] ?? null) : null,
        chunk_p95_ms: rtfs.length > 0 ? (rtfs[p95idx] ?? null) : null,
      })
    }).catch(() => {})
  }, [])

  const startPoll = useCallback((jobId: string) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const [jobStatus, liveData] = await Promise.all([
          api.benchmark.getJob(jobId),
          api.benchmark.getLive(jobId).catch(() => null),
        ])
        setJob(jobStatus)
        localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(jobStatus))
        if (liveData) setLive(liveData)
        if (['completed', 'failed', 'cancelled'].includes(jobStatus.status)) {
          clearInterval(pollRef.current!)
          if (jobStatus.status === 'completed') {
            appendOmegaLine()
            publishTranscript(lastCoreTranscript, runRangeRef.current.endS ?? undefined)
          }
          setRunning(false)
          localStorage.removeItem(ACTIVE_JOB_KEY)
          onJobStop?.()
          // Po dokončení načti finální metriky z run výsledku
          if (jobStatus.status === 'completed' && jobStatus.run_id) {
            loadRunMetrics(jobStatus.run_id)
          }
        }
      } catch {}
    }, 1200)
  }, [onJobStop, loadRunMetrics, appendOmegaLine, publishTranscript, lastCoreTranscript])

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const validateRange = (): { startS: number; durationS: number } | null => {
    if (rangeMode === 'full') return { startS: 0, durationS: 99999 }
    const from = parseTime(rangeFrom)
    const to = parseTime(rangeTo)
    if (from === null) { setRangeError('Neplatný formát "od" (MM:SS nebo H:MM:SS)'); return null }
    if (to === null) { setRangeError('Neplatný formát "do" (MM:SS nebo H:MM:SS)'); return null }
    if (to <= from) { setRangeError('"Do" musí být větší než "Od"'); return null }
    setRangeError('')
    return { startS: from, durationS: to - from }
  }

  const runSegmentBundle = useCallback(async (opts: {
    bundle: SegmentBundle
    videoId: string
    sourceLabel: string
  }) => {
    const { bundle, videoId, sourceLabel } = opts
    const total = bundle.segments.length
    if (total === 0) throw new Error('Segment bundle neobsahuje žádné segmenty.')

    cancelRequestedRef.current = false
    setSegmentedRunActive(true)
    setSegmentProgressLabel('')
    const completedTexts: string[] = []

    try {
      for (let idx = 0; idx < total; idx += 1) {
        if (cancelRequestedRef.current) throw new Error('cancelled_by_user')
        const seg = bundle.segments[idx]
        const segStart = Math.max(0, Math.floor(seg.start_s))
        const segDuration = Math.max(1, Math.ceil(seg.duration_s))
        setSegmentProgressLabel(`Segment ${idx + 1}/${total} (${fmtTime(seg.start_s)}–${fmtTime(seg.end_s)})`)

        const jobStatus = await api.benchmark.createJob({
          video_ids: [videoId],
          model_ids: [selectedModel],
          setting_ids: ['balanced'],
          evaluation_mode: 'streaming',
          sample_seconds: segDuration,
          segment_start_seconds: segStart,
          model_params: Object.keys(modelParams).length > 0 ? { [selectedModel]: modelParams } : undefined,
          label: `Přepis segment ${idx + 1}/${total}: ${selectedModelDesc?.label || selectedModel}`,
        })
        setJob(jobStatus)
        setLive(null)
        localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(jobStatus))

        let finalSegmentText = ''
        while (true) {
          if (cancelRequestedRef.current) {
            await api.benchmark.cancelJob(jobStatus.job_id).catch(() => {})
            throw new Error('cancelled_by_user')
          }
          const [currentJob, liveData] = await Promise.all([
            api.benchmark.getJob(jobStatus.job_id),
            api.benchmark.getLive(jobStatus.job_id).catch(() => null),
          ])
          setJob(currentJob)
          localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(currentJob))
          if (liveData) {
            setLive(liveData)
            const partial = ((liveData.transcript_ts && liveData.transcript_ts.trim())
              ? liveData.transcript_ts
              : liveData.transcript || '').trim()
            if (partial) {
              finalSegmentText = partial
              const combined = [...completedTexts, partial].filter(Boolean).join(TRANSCRIPT_BLOCK_SEPARATOR)
              const pct = Math.max(0, Math.min(100, liveData.percent || 0)) / 100
              publishTranscript(combined, seg.start_s + seg.duration_s * pct)
            }
          }

          if (['completed', 'failed', 'cancelled'].includes(currentJob.status)) {
            if (currentJob.status === 'completed') {
              if (currentJob.run_id) loadRunMetrics(currentJob.run_id)
              if (!finalSegmentText && liveData?.transcript) {
                finalSegmentText = ((liveData.transcript_ts && liveData.transcript_ts.trim())
                  ? liveData.transcript_ts
                  : liveData.transcript).trim()
              }
              completedTexts.push(finalSegmentText)
              const combinedFinal = completedTexts.filter(Boolean).join(TRANSCRIPT_BLOCK_SEPARATOR)
              if (combinedFinal) publishTranscript(combinedFinal, seg.end_s)
            } else if (currentJob.status === 'cancelled') {
              throw new Error('cancelled_by_user')
            } else {
              throw new Error(currentJob.error || `Segment ${idx + 1} selhal.`)
            }
            break
          }
          await sleep(1200)
        }
      }
      appendOmegaLine()
      publishTranscript(completedTexts.filter(Boolean).join(TRANSCRIPT_BLOCK_SEPARATOR), runRangeRef.current.endS ?? undefined)
      setMsg(`Segment bundle dokončen: ${total} segmentů (${sourceLabel}).`)
      setSegmentProgressLabel(`Hotovo ${total}/${total}`)
    } finally {
      setSegmentedRunActive(false)
      localStorage.removeItem(ACTIVE_JOB_KEY)
    }
  }, [
    selectedModel,
    modelParams,
    selectedModelDesc,
    publishTranscript,
    loadRunMetrics,
    appendOmegaLine,
  ])

  const handleStart = useCallback(async () => {
    if (!selectedModel) { setMsg('Vyberte model'); return }
    setMsg('')
    setRunning(true)
    setSegments([])
    setLastTranscript('')
    setLastCoreTranscript('')
    setFinalMetrics(null)
    cancelRequestedRef.current = false
    runStartLineRef.current = ''
    runEndLineRef.current = ''
    runRangeRef.current = { startS: 0, endS: null }
    runWallStartedAtMsRef.current = null
    syntheticTsLinesRef.current = []
    lastSyntheticBoundaryRef.current = -1

    let sources: string[] | undefined
    let videoIds: string[] | undefined
    let sourceLabel = ''
    let resolvedSourceDurationS: number | null = null

    if (sourceTab === 'library') {
      if (!selectedVideoId) { setMsg('Vyberte video'); setRunning(false); return }
      videoIds = [selectedVideoId]
      onAudioReady(`/api/transcribe/library-audio/${selectedVideoId}`, '')
      const vid = library.find(v => v.video_id === selectedVideoId)
      sourceLabel = vid?.title || ''
      resolvedSourceDurationS = typeof vid?.duration_seconds === 'number' ? vid.duration_seconds : null
      onSourceLabelChange?.(sourceLabel)
      onVideoIdChange?.(selectedVideoId)
    } else {
      if (!uploadedSource) { setMsg('Nahrajte soubor'); setRunning(false); return }
      sources = [uploadedSource.source_path]
      sourceLabel = uploadedSource.original_name
      resolvedSourceDurationS = typeof audioDuration === 'number' && audioDuration > 0 ? audioDuration : null
      onSourceLabelChange?.(sourceLabel)
      onVideoIdChange?.('')
    }
    onModelChange?.(selectedModel)

    if (segmentBundleActive && segmentBundle) {
      runRangeRef.current = {
        startS: segmentBundle.segments[0]?.start_s ?? 0,
        endS: segmentBundle.segments[segmentBundle.segments.length - 1]?.end_s ?? null,
      }
    } else if (rangeMode === 'segment') {
      const parsedStart = parseTime(rangeFrom)
      const parsedEnd = parseTime(rangeTo)
      runRangeRef.current = {
        startS: parsedStart ?? 0,
        endS: parsedEnd ?? null,
      }
    } else {
      runRangeRef.current = {
        startS: 0,
        endS: resolvedSourceDurationS,
      }
    }

    const settingsSummary = buildSettingsSummary(selectedModelDesc, modelParams)
    runStartLineRef.current = `α START | Zdroj: ${sourceLabel || '-'} | Model: ${selectedModelLabel || selectedModel} | Nastavení: ${settingsSummary}`

    const validatedRange = segmentBundleActive ? null : validateRange()
    if (!segmentBundleActive && !validatedRange) {
      setRunning(false)
      return
    }

    publishTranscript('', runRangeRef.current.startS)
    setMsg('Příprava přepisu (start za 2s)...')
    await sleep(PRESTART_DELAY_MS)
    if (cancelRequestedRef.current) {
      setMsg('Přepis zastaven před spuštěním.')
      setRunning(false)
      return
    }
    runWallStartedAtMsRef.current = Date.now()
    setMsg('')

    if (segmentBundleActive && selectedVideoId && segmentBundle) {
      try {
        await runSegmentBundle({
          bundle: segmentBundle,
          videoId: selectedVideoId,
          sourceLabel: sourceLabel || selectedVideoId,
        })
      } catch (e: unknown) {
        const text = e instanceof Error ? e.message : String(e)
        if (text.includes('cancelled_by_user')) setMsg('Přepis zastaven uživatelem.')
        else setMsg(text || 'Segmentovaný přepis selhal.')
      } finally {
        setSegmentProgressLabel('')
        setRunning(false)
        onJobStop?.()
      }
      return
    }

    try {
      const jobStatus = await api.benchmark.createJob({
        video_ids: videoIds,
        sources,
        model_ids: [selectedModel],
        setting_ids: ['balanced'],
        evaluation_mode: 'streaming',
        sample_seconds: rangeMode === 'segment' ? (validatedRange?.durationS ?? 99999) : 99999,
        segment_start_seconds: rangeMode === 'segment' && (validatedRange?.startS ?? 0) > 0 ? validatedRange?.startS : undefined,
        model_params: Object.keys(modelParams).length > 0 ? { [selectedModel]: modelParams } : undefined,
        label: `Přepis: ${selectedModelDesc?.label || selectedModel}${rangeMode === 'segment' ? ` (${rangeFrom}-${rangeTo})` : ''}`,
      })
      setJob(jobStatus)
      localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(jobStatus))
      startPoll(jobStatus.job_id)
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : 'Chyba při spuštění')
      setRunning(false)
    }
  }, [
    selectedModel,
    sourceTab,
    selectedVideoId,
    uploadedSource,
    modelParams,
    selectedModelDesc,
    rangeMode,
    rangeFrom,
    rangeTo,
    onAudioReady,
    onSourceLabelChange,
    onVideoIdChange,
    onModelChange,
    startPoll,
    validateRange,
    segmentBundleEnabled,
    segmentBundle,
    segmentBundleActive,
    runSegmentBundle,
    library,
    audioDuration,
    publishTranscript,
    buildSettingsSummary,
    selectedModelLabel,
    onJobStop,
  ])

  const handleCancel = useCallback(async () => {
    cancelRequestedRef.current = true
    if (!job) {
      setRunning(false)
      return
    }
    await api.benchmark.cancelJob(job.job_id).catch(() => {})
    if (segmentedRunActive) {
      setMsg('Ruším běžící segment...')
      return
    }
    setRunning(false)
    localStorage.removeItem(ACTIVE_JOB_KEY)
    onJobStop?.()
  }, [job, onJobStop, segmentedRunActive])

  const statusColor = (s?: string) => {
    if (s === 'completed') return 'text-green-400'
    if (s === 'failed' || s === 'cancelled') return 'text-red-400'
    if (s === 'running') return 'text-blue-400'
    return 'text-gray-400'
  }

  const lastHw = live?.hw_series?.length ? live.hw_series[live.hw_series.length - 1] : null

  // ETA výpočet (aktualizuje se každý poll, ale zobrazíme zaokrouhleně)
  const eta = (() => {
    if (!job?.started_at || !job.progress_percent || job.progress_percent <= 0) return null
    const elapsedMs = Date.now() - new Date(job.started_at).getTime()
    const totalEstMs = (elapsedMs / job.progress_percent) * 100
    const remainingMs = totalEstMs - elapsedMs
    if (remainingMs < 0) return null
    const remainSec = Math.round(remainingMs / 1000)
    if (remainSec < 60) return `~${remainSec}s`
    const m = Math.round(remainSec / 60)
    return `~${m}min`
  })()

  // Odhadovaný RTF z progress message: "Xs zprac. / ~Ys audia" + elapsed wall time
  const estimatedRtf = (() => {
    if (!job?.started_at || !live?.message) return null
    // Parsuj "115s zprac." → 115 sekund audia zpracováno
    const match = live.message.match(/(\d+(?:\.\d+)?)s\s+zprac/)
    if (!match) return null
    const audioProcessedS = parseFloat(match[1])
    if (audioProcessedS <= 0) return null
    const elapsedS = (Date.now() - new Date(job.started_at).getTime()) / 1000
    const rtf = elapsedS / audioProcessedS
    return rtf.toFixed(2)
  })()

  return (
    <div className="flex flex-col gap-3 p-3 bg-gray-800 text-gray-100 h-full overflow-y-auto text-sm">

      {/* ── 1. Zdroj audia ── */}
      <section className="bg-gray-750 rounded border border-gray-700 p-3">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2 select-text">Zdroj audia</h3>
        <div className="flex gap-1 mb-2">
          {(['library', 'upload'] as const).map(t => (
            <button key={t} onClick={() => { setSourceTab(t); saveSettings({ source_tab: t }) }}
              className={`px-2.5 py-1 text-xs rounded ${sourceTab === t ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}>
              {t === 'library' ? 'Knihovna' : 'Upload'}
            </button>
          ))}
        </div>

        {sourceTab === 'library' ? (
          <select value={selectedVideoId} onChange={e => { setSelectedVideoId(e.target.value); saveSettings({ selected_video_id: e.target.value }) }}
            className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-gray-100">
            {library.length === 0 && <option value="">Načítám...</option>}
            {library.map(v => (
              <option key={v.video_id} value={v.video_id}>
                {v.title}{v.duration_seconds ? ` (${Math.round(v.duration_seconds / 60)}min)` : ''}{!v.audio_cached ? ' ⚠' : ''}
              </option>
            ))}
          </select>
        ) : (
          <div>
            <div
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={e => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) handleUpload(f) }}
              onClick={() => document.getElementById('transcribe-file-input')?.click()}
              className={`border-2 border-dashed rounded p-3 text-center text-xs cursor-pointer transition-colors
                ${dragOver ? 'border-blue-400 bg-blue-900/20' : 'border-gray-600 hover:border-gray-400'}`}
            >
              <input id="transcribe-file-input" type="file"
                accept=".mp3,.wav,.mp4,.m4a,.ogg,.flac,.webm,.mkv,.avi,.mov"
                className="hidden" onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(f) }} />
              {uploading ? <span className="text-blue-400">Nahrávám...</span>
                : uploadedSource ? (
                  <div>
                    <div className="text-green-400 font-medium truncate">{uploadedSource.original_name}</div>
                    <div className="text-gray-400">{(uploadedSource.size_bytes / 1024 / 1024).toFixed(1)} MB · klik pro změnu</div>
                  </div>
                ) : <span className="text-gray-400">Přetáhni soubor nebo klikni<br /><span className="text-gray-500">MP3 WAV MP4 M4A OGG FLAC...</span></span>}
            </div>
            {uploadError && <div className="text-red-400 text-xs mt-1">{uploadError}</div>}
          </div>
        )}
        {sourceDisplayName && (
          <div className="mt-2 flex items-center gap-2 text-xs">
            <span className="text-gray-400 whitespace-nowrap">Kopírovatelné:</span>
            <input
              value={sourceDisplayName}
              readOnly
              onFocus={(e) => e.currentTarget.select()}
              onClick={(e) => e.currentTarget.select()}
              className="flex-1 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-xs text-gray-100 font-mono select-text"
              title={sourceDisplayName}
            />
            <button
              type="button"
              onClick={() => { void copyText(sourceDisplayName, 'Název audia') }}
              className="ml-auto px-2 py-0.5 rounded border border-gray-600 text-gray-200 hover:bg-gray-700"
            >
              Kopírovat
            </button>
          </div>
        )}
      </section>

      {/* ── 2. Rozsah přepisu ── */}
      <section className="bg-gray-750 rounded border border-gray-700 p-3">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Rozsah přepisu</h3>
        <div className="flex gap-1 mb-2">
          <button onClick={() => { setRangeMode('full'); saveSettings({ range_mode: 'full' }) }}
            className={`px-2.5 py-1 text-xs rounded ${rangeMode === 'full' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}>
            Celý soubor
          </button>
          <button onClick={() => { setRangeMode('segment'); saveSettings({ range_mode: 'segment' }) }}
            className={`px-2.5 py-1 text-xs rounded ${rangeMode === 'segment' ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'}`}>
            Vlastní úsek
          </button>
        </div>

        {rangeMode === 'segment' && !segmentBundleActive && (
          <div className="flex items-center gap-2">
            <div className="flex flex-col gap-0.5">
              <label className="text-xs text-gray-400">Od</label>
              <input value={rangeFrom} onChange={e => { setRangeFrom(e.target.value); saveSettings({ range_from: e.target.value }) }}
                placeholder="00:00"
                className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-gray-100 w-24 font-mono" />
            </div>
            <span className="text-gray-500 mt-4">–</span>
            <div className="flex flex-col gap-0.5">
              <label className="text-xs text-gray-400">Do</label>
              <input value={rangeTo} onChange={e => { setRangeTo(e.target.value); saveSettings({ range_to: e.target.value }) }}
                placeholder={audioDuration ? fmtTime(audioDuration) : 'MM:SS'}
                className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-gray-100 w-24 font-mono" />
            </div>
            {rangeFrom && rangeTo && (() => {
              const f = parseTime(rangeFrom), t = parseTime(rangeTo)
              if (f !== null && t !== null && t > f) {
                return <span className="text-gray-400 text-xs mt-4">{fmtTime(t - f)}</span>
              }
              return null
            })()}
          </div>
        )}
        {rangeError && <div className="text-red-400 text-xs mt-1">{rangeError}</div>}
        {rangeMode === 'segment' && !segmentBundleActive && (
          <div className="text-xs text-gray-500 mt-1">Formát: MM:SS nebo H:MM:SS</div>
        )}

        {sourceTab === 'library' && (
          <div className="mt-2 pt-2 border-t border-gray-700 space-y-1">
            <label className="flex items-center gap-1.5 cursor-pointer text-xs text-gray-300">
              <input
                type="checkbox"
                checked={segmentBundleEnabled}
                disabled={segmentBundleLoading || !segmentBundle}
                onChange={e => {
                  setSegmentBundleEnabled(e.target.checked)
                  saveSettings({ segment_bundle_enabled: e.target.checked })
                }}
                className="accent-cyan-500 w-3.5 h-3.5"
              />
              Použít uložený segment bundle z Library
            </label>
            {segmentBundleLoading && (
              <div className="text-xs text-gray-400">Načítám segment bundle...</div>
            )}
            {!segmentBundleLoading && !segmentBundle && (
              <div className="text-xs text-gray-500">Pro vybrané video není uložený bundle. Nastavte ho v Library.</div>
            )}
            {segmentBundle && (
              <div className="text-xs text-gray-400">
                {segmentBundle.segments.length} segmentů
                {' | '}
                režim {segmentBundle.mode}
                {' | '}
                tolerance ±{segmentBundle.tolerance_seconds.toFixed(1)}s
                {' | '}
                aktualizace {formatDateTimeShort(segmentBundle.updated_at)}
              </div>
            )}
            {segmentBundleActive && (
              <div className="text-xs text-cyan-300">
                Aktivní segment bundle: při startu poběží segmenty postupně jako jeden logický přepis.
              </div>
            )}
            {segmentProgressLabel && (
              <div className="text-xs text-cyan-300">{segmentProgressLabel}</div>
            )}
          </div>
        )}

        {/* Časové značky */}
        <div className="flex items-center gap-3 mt-2 pt-2 border-t border-gray-700">
          <label className="flex items-center gap-1.5 cursor-pointer text-xs text-gray-300">
            <input type="checkbox" checked={tsEnabled}
              onChange={e => { setTsEnabled(e.target.checked); saveSettings({ ts_enabled: e.target.checked }) }}
              className="accent-blue-500 w-3.5 h-3.5" />
            Časové značky
          </label>
          {tsEnabled && (
            <div className="flex items-center gap-1.5 text-xs text-gray-400">
              <span>každých</span>
              <input
                type="number" min={5} max={3600} step={1}
                value={tsIntervalDraft}
                onChange={e => setTsIntervalDraft(e.target.value)}
                onBlur={commitTsIntervalDraft}
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    commitTsIntervalDraft()
                    ;(e.currentTarget as HTMLInputElement).blur()
                  }
                }}
                className="bg-gray-700 border border-gray-600 rounded px-1.5 py-0.5 text-xs text-gray-100 w-16 text-center"
              />
              <span>s</span>
            </div>
          )}
        </div>
      </section>

      {/* ── 3. Model ── */}
      <section className="bg-gray-750 rounded border border-gray-700 p-3">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2 select-text">Model STT</h3>
        <select value={selectedModel} onChange={e => {
          const mid = e.target.value
          setSelectedModel(mid)
          setModelParams((loadSettings().model_params ?? {})[mid] ?? {})
          onModelChange?.(mid)
          saveSettings({ model: mid })
        }}
          className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-gray-100 mb-2">
          {registry.map(m => <option key={m.model_id} value={m.model_id}>{m.label}</option>)}
        </select>
        {selectedModel && (
          <div className="mb-2 space-y-1">
            <div className="flex items-center gap-2 text-xs">
              <span className="text-gray-400 whitespace-nowrap">Kopírovatelné:</span>
              <input
                value={selectedModelLabel}
                readOnly
                onFocus={(e) => e.currentTarget.select()}
                onClick={(e) => e.currentTarget.select()}
                className="flex-1 bg-gray-700 border border-gray-600 rounded px-2 py-1 text-xs text-gray-100 font-mono select-text"
                title={selectedModelLabel}
              />
              <button
                type="button"
                onClick={() => { void copyText(selectedModelLabel, 'Model STT') }}
                className="ml-auto px-2 py-0.5 rounded border border-gray-600 text-gray-200 hover:bg-gray-700"
              >
                Kopírovat
              </button>
            </div>
            <div className="text-sm font-mono select-text" title={selectedModelLabel}>
              {modelWordsForPreview.map((word, idx) => (
                <span
                  key={`${word}-${idx}`}
                  className={idx === 0
                    ? toneTextClass(streamSuitability)
                    : idx === 1
                      ? toneTextClass(transcriptSuitability)
                      : 'text-gray-200'}
                >
                  {idx > 0 ? ' ' : ''}{word}
                </span>
              ))}
            </div>
            <div className="text-[11px] text-gray-500">
              1. slovo = vhodnost pro live stream, 2. slovo = vhodnost pro přepis.
            </div>
          </div>
        )}
        {selectedModelDesc && selectedModelDesc.params.length > 0 && (
          <ModelParamsForm modelId={selectedModel} params={selectedModelDesc.params}
            values={modelParams} onChange={p => {
              setModelParams(p)
              const allParams = { ...(loadSettings().model_params ?? {}), [selectedModel]: p }
              saveSettings({ model_params: allParams })
            }} compact />
        )}
      </section>

      {/* ── 4. Spustit / Zastavit ── */}
      <div className="flex gap-2">
        <button onClick={handleStart} disabled={running}
          className="flex-1 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-600 text-white rounded text-sm font-medium">
          {running ? '⏳ Přepisuji...' : '▶ Spustit přepis'}
        </button>
        <button
          onClick={handleCancel}
          disabled={!running || !job}
          title="Zastavit / zrušit přepis"
          className={`px-3 py-2 rounded text-sm font-medium transition-colors ${
            running && job
              ? 'bg-red-700 hover:bg-red-600 text-white'
              : 'bg-gray-700 text-gray-500 cursor-not-allowed'
          }`}
        >
          ⏹ Zastavit
        </button>
      </div>
      {copyMsg && <div className="text-emerald-300 text-xs">{copyMsg}</div>}
      {msg && <div className="text-red-400 text-xs">{msg}</div>}

      {/* ── Progress bar ── */}
      {job && (
        <div>
          <div className="flex justify-between text-xs mb-1">
            <span className={statusColor(job.status)}>{job.status}</span>
            <span className="text-gray-400">{job.progress_percent}%</span>
          </div>
          <div className="h-1.5 bg-gray-700 rounded overflow-hidden">
            {running && job.progress_percent != null && job.progress_percent < 20 && elapsedSec > 10
              ? <div className="h-full bg-blue-500 animate-pulse" style={{ width: '15%' }} />
              : <div className="h-full bg-blue-500 transition-all duration-300" style={{ width: `${job.progress_percent}%` }} />
            }
          </div>
          {job.progress_message && (
            <div className="text-xs text-gray-500 mt-0.5 truncate">{job.progress_message}</div>
          )}
          {running && elapsedSec > 10 && (job.progress_percent ?? 0) < 20 && (
            <div className="text-xs text-yellow-500 mt-0.5 animate-pulse">
              ⏳ Zpracovávám audio… {Math.floor(elapsedSec / 60) > 0 ? `${Math.floor(elapsedSec / 60)}min ` : ''}{elapsedSec % 60}s
            </div>
          )}
        </div>
      )}

      {/* ── 5. Diagnostika ── */}
      {live && (
        <section className="bg-gray-900 rounded border border-gray-700 p-2">
          <button onClick={() => setShowDiag(v => !v)}
            className="flex items-center justify-between w-full text-xs text-gray-400 hover:text-gray-200 mb-1">
            <span className="font-semibold uppercase tracking-wide">Diagnostika</span>
            <span>{showDiag ? '▲' : '▼'}</span>
          </button>
          {showDiag && (
            <div className="space-y-2 text-xs font-mono">
              {/* ETA */}
              {eta && running && (
                <div className="bg-blue-900/30 rounded px-2 py-1 text-blue-300 flex justify-between">
                  <span>Odhad dokončení: <span className="text-blue-500 text-xs">(přepočítáván každé 3 min.)</span></span>
                  <span className="font-bold">{eta}</span>
                </div>
              )}

              {/* STT metriky */}
              <div className="text-gray-500 text-xs mb-0.5">
                STT metriky {finalMetrics ? '(finální)' : estimatedRtf ? '(odhad za běhu)' : '(dostupné po dokončení)'}
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                <DiagRow
                  label="RTF"
                  value={finalMetrics?.rtf != null ? finalMetrics.rtf.toFixed(3) : estimatedRtf ? `~${estimatedRtf}` : '–'}
                  warn={(finalMetrics?.rtf ?? parseFloat(estimatedRtf ?? '0')) > 1.0}
                />
                <DiagRow label="Status" value={live.status} />
                <DiagRow
                  label="Latence ms"
                  value={finalMetrics?.latency_ms != null ? finalMetrics.latency_ms.toFixed(0) : '–'}
                  warn={finalMetrics?.latency_ms != null && finalMetrics.latency_ms > 1500}
                />
                <DiagRow
                  label="P50 RTF"
                  value={finalMetrics?.chunk_p50_ms != null ? finalMetrics.chunk_p50_ms.toFixed(3) : '–'}
                />
                <DiagRow
                  label="P95 RTF"
                  value={finalMetrics?.chunk_p95_ms != null ? finalMetrics.chunk_p95_ms.toFixed(3) : '–'}
                  warn={finalMetrics?.chunk_p95_ms != null && finalMetrics.chunk_p95_ms > 1.0}
                />
                <DiagRow
                  label="WER"
                  value={finalMetrics?.wer != null ? `${(finalMetrics.wer * 100).toFixed(1)}%` : '–'}
                />
                <DiagRow
                  label="RAM MB"
                  value={finalMetrics?.ram_mb != null ? finalMetrics.ram_mb.toFixed(0) : lastHw?.ram_mb != null ? lastHw.ram_mb.toFixed(0) : '–'}
                  warn={(finalMetrics?.ram_mb ?? lastHw?.ram_mb ?? 0) > 8000}
                />
                <DiagRow
                  label="CPU %"
                  value={finalMetrics?.cpu_percent != null ? finalMetrics.cpu_percent.toFixed(0) : lastHw?.cpu != null ? lastHw.cpu.toFixed(0) : '–'}
                  warn={(finalMetrics?.cpu_percent ?? lastHw?.cpu ?? 0) > 80}
                />
              </div>

              {/* HW podmínky */}
              <div className="text-gray-500 text-xs mt-1 mb-0.5">Hardware podmínky</div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                <DiagRow label="Progress" value={`${live.percent ?? 0}%`} />
                {job?.conditions_clean != null && (
                  <DiagRow label="Podmínky" value={job.conditions_clean ? '✓ čisté' : '⚠ rušné'} warn={!job.conditions_clean} />
                )}
                {job?.pre_cpu != null && <DiagRow label="CPU před" value={`${job.pre_cpu.toFixed(0)}%`} warn={job.pre_cpu > 40} />}
                {job?.pre_ram_mb != null && <DiagRow label="RAM před" value={`${(job.pre_ram_mb / 1024).toFixed(1)} GB`} />}
              </div>

              {/* Poslední log zprávy */}
              {(live.message_log?.length ?? 0) > 0 && (
                <div className="mt-1">
                  <div className="text-gray-500 text-xs mb-0.5">Log (posledních 5)</div>
                  <div className="space-y-0.5">
                    {live.message_log.slice(-5).map((m, i) => (
                      <div key={i} className="text-gray-400 truncate text-xs">{m}</div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
          {/* CPU mini-chart */}
          {showDiag && live.hw_series && live.hw_series.length > 1 && (
            <div className="mt-2">
              <div className="text-xs text-gray-500 mb-1">CPU/RAM v čase:</div>
              <MiniChart series={live.hw_series} />
            </div>
          )}
        </section>
      )}

      {/* ── 6. Live přepis ── */}
      {segments.length > 0 && (
        <section className="bg-gray-900 rounded border border-gray-700 p-2">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
            Live přepis ({segments.length} seg.)
          </h3>
          <div className="max-h-48 overflow-y-auto space-y-1">
            {segments.map((seg, i) => (
              <div key={i}
                className={`text-xs rounded px-2 py-1 transition-colors ${seg.isNew ? 'bg-blue-900/40 text-blue-200' : 'bg-gray-800 text-gray-300'}`}>
                {seg.text}
              </div>
            ))}
            <div ref={segmentsEndRef} />
          </div>
        </section>
      )}
    </div>
  )
}

// Pomocná komponenta pro jeden diagnostický řádek
function DiagRow({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <>
      <span className="text-gray-500">{label}</span>
      <span className={warn ? 'text-orange-400' : 'text-gray-200'}>{value}</span>
    </>
  )
}

// Mini SVG chart pro CPU a RAM
function MiniChart({ series }: { series: { cpu: number | null; ram_mb: number | null }[] }) {
  const W = 200; const H = 40
  const cpuMax = 100
  const ramMax = Math.max(...series.map(s => s.ram_mb ?? 0), 1000)
  const n = series.length

  const cpuPts = series.map((s, i) => {
    const x = (i / (n - 1)) * W
    const y = H - ((s.cpu ?? 0) / cpuMax) * H
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  const ramPts = series.map((s, i) => {
    const x = (i / (n - 1)) * W
    const y = H - ((s.ram_mb ?? 0) / ramMax) * H
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-10" preserveAspectRatio="none">
      <polyline points={cpuPts} fill="none" stroke="#60a5fa" strokeWidth="1.5" />
      <polyline points={ramPts} fill="none" stroke="#34d399" strokeWidth="1.5" />
      <text x="2" y="8" fontSize="7" fill="#60a5fa">CPU</text>
      <text x="2" y="16" fontSize="7" fill="#34d399">RAM</text>
    </svg>
  )
}
