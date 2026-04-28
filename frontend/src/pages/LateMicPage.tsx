import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { LateMicSegmentResponse, LibraryItem, ModelDescriptor } from '../types'
import { ModelParamsForm } from '../components/ModelParamsForm'
import { ActionButton, FieldHintLabel } from '../components/UiPrimitives'
import { videoLabel } from '../utils'
import { sortLibraryItemsLikeLibraryPage, useLibrarySortRevision, usesLibraryWerSort } from '../lib/librarySort'
import { api } from '../api/client'

type AudioSourceMode = 'free_mic' | 'reference_audio'
type SegmentMode = 'recommended' | 'manual'
type DeletePolicy = 'after_transcript' | 'after_run' | 'keep_failed' | 'keep_all'

interface Props {
  models: ModelDescriptor[]
  library: LibraryItem[]
}

interface MicProof {
  status: 'idle' | 'starting' | 'running' | 'error'
  message: string
  rmsDbfs: number | null
  peakDbfs: number | null
  vad: 'rec' | 'silence' | 'unknown'
  clippingPct: number
  silenceMs: number
  chunks: number
  bytes: number
  sampleRate: number | null
}

interface SegmentConfig {
  lagBudgetS: number
  targetSegmentS: number
  maxPauseWaitS: number
  overlapS: number
}

interface CapturedPcmSegment {
  pcm16Base64: string
  sampleRate: number
  audioDurationS: number
  bytes: number
  rmsDbfs: number
  peakDbfs: number
  clippingPct: number
  silenceMs: number
  endedBy: 'pause' | 'hard_cap'
  captureStartedAt: string
  captureFinishedAt: string
}

interface LateMicRunResult extends LateMicSegmentResponse {
  run_id: string
  run_index: number
  run_total: number
  repeat_index: number
  repeat_total: number
  lag_budget_s: number
  target_segment_s: number
  max_pause_wait_s: number
  capture_audio_s: number
  capture_bytes: number
  capture_rms_dbfs: number
  capture_peak_dbfs: number
  capture_clipping_pct: number
  capture_silence_ms: number
  capture_ended_by: 'pause' | 'hard_cap'
  cleanup_status?: 'not_needed' | 'pending' | 'deleted' | 'failed'
}

interface LateMicProgress {
  capturesDone: number
  capturesTotal: number
  transcriptionsDone: number
  transcriptionsTotal: number
  current: string
}

const RECOMMENDED_SEGMENTS: SegmentConfig[] = [
  { lagBudgetS: 5, targetSegmentS: 3, maxPauseWaitS: 1, overlapS: 0.5 },
  { lagBudgetS: 10, targetSegmentS: 5, maxPauseWaitS: 2, overlapS: 1 },
  { lagBudgetS: 15, targetSegmentS: 8, maxPauseWaitS: 3, overlapS: 1.5 },
  { lagBudgetS: 20, targetSegmentS: 10, maxPauseWaitS: 3, overlapS: 2 },
  { lagBudgetS: 25, targetSegmentS: 12, maxPauseWaitS: 4, overlapS: 2 },
  { lagBudgetS: 30, targetSegmentS: 15, maxPauseWaitS: 5, overlapS: 2 },
  { lagBudgetS: 35, targetSegmentS: 18, maxPauseWaitS: 5, overlapS: 2 },
  { lagBudgetS: 40, targetSegmentS: 20, maxPauseWaitS: 5, overlapS: 3 },
  { lagBudgetS: 45, targetSegmentS: 22, maxPauseWaitS: 5, overlapS: 3 },
  { lagBudgetS: 50, targetSegmentS: 25, maxPauseWaitS: 5, overlapS: 3 },
  { lagBudgetS: 55, targetSegmentS: 28, maxPauseWaitS: 5, overlapS: 3 },
  { lagBudgetS: 60, targetSegmentS: 30, maxPauseWaitS: 5, overlapS: 3 },
]

const DEFAULT_PROOF: MicProof = {
  status: 'idle',
  message: 'Mikrofon zatím neměřen.',
  rmsDbfs: null,
  peakDbfs: null,
  vad: 'unknown',
  clippingPct: 0,
  silenceMs: 0,
  chunks: 0,
  bytes: 0,
  sampleRate: null,
}

export function LateMicPage({ models, library }: Props) {
  const [audioSourceMode, setAudioSourceMode] = useState<AudioSourceMode>('reference_audio')
  const [audioDevices, setAudioDevices] = useState<MediaDeviceInfo[]>([])
  const [selectedDeviceId, setSelectedDeviceId] = useState('')
  const [proof, setProof] = useState<MicProof>(DEFAULT_PROOF)
  const [silenceThresholdDbfs, setSilenceThresholdDbfs] = useState(-50)

  const [selectedVideoId, setSelectedVideoId] = useState('')
  const [libraryWerByVideoId, setLibraryWerByVideoId] = useState<Record<string, number | null>>({})
  const [clipFromS, setClipFromS] = useState(0)
  const [clipToS, setClipToS] = useState(60)

  const [includeFiveSeconds, setIncludeFiveSeconds] = useState(true)
  const [lagFromS, setLagFromS] = useState(10)
  const [lagToS, setLagToS] = useState(60)
  const [lagStepS, setLagStepS] = useState(5)
  const [segmentMode, setSegmentMode] = useState<SegmentMode>('recommended')
  const [manualSegmentS, setManualSegmentS] = useState(10)
  const [manualPauseWaitS, setManualPauseWaitS] = useState(3)
  const [manualOverlapS, setManualOverlapS] = useState(2)
  const [minPauseMs, setMinPauseMs] = useState(700)
  const [queueReserveS, setQueueReserveS] = useState(0)
  const [deletePolicy, setDeletePolicy] = useState<DeletePolicy>('after_transcript')

  const [selectedModelIds, setSelectedModelIds] = useState<string[]>([])
  const [activeParamModelId, setActiveParamModelId] = useState('')
  const [perModelParams, setPerModelParams] = useState<Record<string, Record<string, unknown>>>({})
  const [repeatCount, setRepeatCount] = useState(1)
  const [copyMessage, setCopyMessage] = useState('')
  const [lateMicRunning, setLateMicRunning] = useState(false)
  const [lateMicStatus, setLateMicStatus] = useState('')
  const [lateMicResults, setLateMicResults] = useState<LateMicRunResult[]>([])
  const [lateMicErrors, setLateMicErrors] = useState<string[]>([])
  const [lateMicProgress, setLateMicProgress] = useState<LateMicProgress | null>(null)

  const streamRef = useRef<MediaStream | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const processorRef = useRef<ScriptProcessorNode | null>(null)
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const silenceMsRef = useRef(0)
  const initializedModelsRef = useRef(false)
  const lateMicStopRequestedRef = useRef(false)
  const librarySortRevision = useLibrarySortRevision()

  const referenceLibrary = useMemo(
    () => sortLibraryItemsLikeLibraryPage(
      library.filter(item => item.visible_in_menus !== false),
      libraryWerByVideoId,
    ),
    [library, librarySortRevision, libraryWerByVideoId],
  )

  const selectedVideo = useMemo(
    () => referenceLibrary.find(item => item.video_id === selectedVideoId) ?? null,
    [selectedVideoId, referenceLibrary],
  )

  const eligibleModels = useMemo(() => {
    const rows = models.filter(model => (
      model.transcript_suitability !== 'red' ||
      model.supports_streaming ||
      model.supports_microphone
    ))
    return rows.length > 0 ? rows : models
  }, [models])

  const activeParamModel = useMemo(
    () => eligibleModels.find(model => model.model_id === activeParamModelId) ?? null,
    [activeParamModelId, eligibleModels],
  )

  const lagBudgets = useMemo(() => {
    const values = new Set<number>()
    if (includeFiveSeconds) values.add(5)
    const from = clampNumber(lagFromS, 5, 60)
    const to = clampNumber(lagToS, from, 60)
    const step = clampNumber(lagStepS, 1, 30)
    for (let value = from; value <= to; value += step) values.add(value)
    return [...values].sort((a, b) => a - b)
  }, [includeFiveSeconds, lagFromS, lagStepS, lagToS])

  const segmentPlan = useMemo(() => (
    lagBudgets.map(lagBudgetS => {
      const recommended = recommendedSegmentForLag(lagBudgetS)
      const targetSegmentS = segmentMode === 'recommended' ? recommended.targetSegmentS : manualSegmentS
      const maxPauseWaitS = segmentMode === 'recommended' ? recommended.maxPauseWaitS : manualPauseWaitS
      const overlapS = segmentMode === 'recommended' ? recommended.overlapS : manualOverlapS
      const decodeWindowS = Math.max(0, lagBudgetS - targetSegmentS - queueReserveS)
      const requiredRtf = targetSegmentS > 0 ? decodeWindowS / targetSegmentS : 0
      return {
        lagBudgetS,
        targetSegmentS,
        maxPauseWaitS,
        overlapS,
        decodeWindowS,
        requiredRtf,
        feasible: requiredRtf > 0,
      }
    })
  ), [lagBudgets, manualOverlapS, manualPauseWaitS, manualSegmentS, queueReserveS, segmentMode])

  const selectedModels = useMemo(
    () => selectedModelIds
      .map(modelId => eligibleModels.find(model => model.model_id === modelId))
      .filter((model): model is ModelDescriptor => Boolean(model)),
    [eligibleModels, selectedModelIds],
  )

  const totalCaptures = segmentPlan.length * repeatCount
  const totalTrials = selectedModels.length * totalCaptures
  const clipDurationS = Math.max(0, clipToS - clipFromS)
  const estimatedSegmentCount = totalCaptures
  const estimatedAudioMinutes = repeatCount * segmentPlan.reduce((sum, row) => sum + row.targetSegmentS + row.maxPauseWaitS, 0) / 60
  const lateMicSummary = useMemo(() => summarizeLateMicResults(lateMicResults), [lateMicResults])

  useEffect(() => {
    refreshAudioDevices().catch(() => {
      setAudioDevices([])
    })
  }, [])

  useEffect(() => {
    if (!selectedVideoId && referenceLibrary.length > 0) {
      const first = referenceLibrary[0]
      setSelectedVideoId(first.video_id)
      const duration = first.duration_seconds ?? first.audio_duration_seconds ?? 60
      setClipToS(Math.min(60, Math.max(5, Math.floor(duration))))
    }
  }, [selectedVideoId, referenceLibrary])

  useEffect(() => {
    const visible = library.filter(item => !!item.video_id && item.visible_in_menus !== false)
    if (!usesLibraryWerSort() || visible.length === 0) {
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
  }, [library, librarySortRevision])

  useEffect(() => {
    if (selectedVideo) {
      const maxDuration = Math.floor(selectedVideo.duration_seconds ?? selectedVideo.audio_duration_seconds ?? clipToS)
      setClipFromS(prev => clampNumber(prev, 0, Math.max(0, maxDuration - 1)))
      setClipToS(prev => clampNumber(prev, 1, Math.max(1, maxDuration)))
    }
  }, [clipToS, selectedVideo])

  useEffect(() => {
    if (initializedModelsRef.current || eligibleModels.length === 0) return
    const preferred = ['vosk_small_cs_0_4', 'whisper_cpp_base', 'whisper_cpp_large_v3_turbo']
      .filter(modelId => eligibleModels.some(model => model.model_id === modelId))
    const initial = preferred.length > 0 ? preferred : eligibleModels.slice(0, 2).map(model => model.model_id)
    setSelectedModelIds(initial)
    setActiveParamModelId(initial[0] ?? '')
    initializedModelsRef.current = true
  }, [eligibleModels])

  useEffect(() => {
    if (selectedModelIds.length === 0) {
      setActiveParamModelId('')
      return
    }
    if (!selectedModelIds.includes(activeParamModelId)) {
      setActiveParamModelId(selectedModelIds[0])
    }
  }, [activeParamModelId, selectedModelIds])

  async function refreshAudioDevices() {
    if (!navigator.mediaDevices?.enumerateDevices) return
    const devices = await navigator.mediaDevices.enumerateDevices()
    setAudioDevices(devices.filter(device => device.kind === 'audioinput'))
  }

  async function startMicProof() {
    stopMicProof()
    setProof(prev => ({ ...prev, status: 'starting', message: 'Žádám o přístup k mikrofonu...' }))
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('Prohlížeč nepodporuje getUserMedia.')
      }
      const constraints: MediaStreamConstraints = {
        audio: selectedDeviceId
          ? {
              deviceId: { exact: selectedDeviceId },
              channelCount: 1,
              echoCancellation: false,
              noiseSuppression: false,
              autoGainControl: false,
            }
          : {
              channelCount: 1,
              echoCancellation: false,
              noiseSuppression: false,
              autoGainControl: false,
            },
      }
      const stream = await navigator.mediaDevices.getUserMedia(constraints)
      const AudioContextCtor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!AudioContextCtor) throw new Error('Prohlížeč nepodporuje AudioContext.')
      const audioContext = new AudioContextCtor()
      const source = audioContext.createMediaStreamSource(stream)
      const processor = audioContext.createScriptProcessor(4096, 1, 1)
      silenceMsRef.current = 0

      processor.onaudioprocess = event => {
        const input = event.inputBuffer.getChannelData(0)
        let sumSq = 0
        let peak = 0
        let clipping = 0
        for (let i = 0; i < input.length; i += 1) {
          const abs = Math.abs(input[i])
          sumSq += input[i] * input[i]
          if (abs > peak) peak = abs
          if (abs >= 0.98) clipping += 1
        }
        const output = event.outputBuffer.getChannelData(0)
        output.fill(0)
        const rms = Math.sqrt(sumSq / Math.max(1, input.length))
        const rmsDbfs = toDbfs(rms)
        const peakDbfs = toDbfs(peak)
        const chunkMs = input.length / audioContext.sampleRate * 1000
        const vad = rmsDbfs > silenceThresholdDbfs ? 'rec' : 'silence'
        silenceMsRef.current = vad === 'silence' ? silenceMsRef.current + chunkMs : 0
        const bytes = Math.round(input.length * 2)
        const clippingPct = input.length > 0 ? clipping / input.length * 100 : 0

        setProof(prev => ({
          status: 'running',
          message: 'Mic důkaz běží z browser PCM vstupu.',
          rmsDbfs,
          peakDbfs,
          vad,
          clippingPct,
          silenceMs: Math.round(silenceMsRef.current),
          chunks: prev.chunks + 1,
          bytes: prev.bytes + bytes,
          sampleRate: audioContext.sampleRate,
        }))
      }

      source.connect(processor)
      processor.connect(audioContext.destination)
      streamRef.current = stream
      audioContextRef.current = audioContext
      processorRef.current = processor
      sourceRef.current = source
      await refreshAudioDevices()
    } catch (error) {
      stopMicProof()
      setProof({
        ...DEFAULT_PROOF,
        status: 'error',
        message: error instanceof Error ? error.message : 'Mikrofon se nepodařilo spustit.',
      })
    }
  }

  function stopMicProof() {
    processorRef.current?.disconnect()
    sourceRef.current?.disconnect()
    streamRef.current?.getTracks().forEach(track => track.stop())
    audioContextRef.current?.close().catch(() => {})
    processorRef.current = null
    sourceRef.current = null
    streamRef.current = null
    audioContextRef.current = null
    silenceMsRef.current = 0
    setProof(prev => prev.status === 'running' || prev.status === 'starting'
      ? { ...prev, status: 'idle', message: 'Mic důkaz zastaven.' }
      : prev)
  }

  useEffect(() => () => stopMicProof(), [])

  function toggleModel(modelId: string) {
    setSelectedModelIds(prev => (
      prev.includes(modelId)
        ? prev.filter(id => id !== modelId)
        : [...prev, modelId]
    ))
  }

  async function copyPlan() {
    const payload = {
      mode: 'late_mic',
      source: {
        type: audioSourceMode,
        video_id: audioSourceMode === 'reference_audio' ? selectedVideoId : null,
        clip_from_s: clipFromS,
        clip_to_s: clipToS,
      },
      mic_proof_expected: ['rms_dbfs', 'peak_dbfs', 'vad', 'clipping_pct', 'silence_ms', 'sample_rate'],
      segmentation: {
        lag_budgets_s: lagBudgets,
        segment_mode: segmentMode,
        min_pause_ms: minPauseMs,
        queue_reserve_s: queueReserveS,
        delete_policy: deletePolicy,
        segment_plan: segmentPlan,
      },
      models: selectedModels.map(model => ({
        model_id: model.model_id,
        label: model.label,
        params: perModelParams[model.model_id] ?? {},
      })),
      repeats: repeatCount,
      estimated: {
        captures: totalCaptures,
        transcriptions: totalTrials,
        source_audio_minutes: round1(estimatedAudioMinutes),
        segments: estimatedSegmentCount,
      },
    }
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2))
      setCopyMessage('Plán zkopírován do schránky.')
    } catch {
      setCopyMessage('Schránka není dostupná; plán zatím nelze zkopírovat.')
    }
  }

  async function startLateMicRun() {
    if (selectedModels.length === 0) {
      setLateMicStatus('Vyber alespoň jeden model.')
      return
    }
    if (segmentPlan.length === 0 || totalTrials === 0) {
      setLateMicStatus('Plán je prázdný.')
      return
    }
    const runId = `latemic_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`
    lateMicStopRequestedRef.current = false
    setLateMicRunning(true)
    setLateMicResults([])
    setLateMicErrors([])
    setLateMicProgress({
      capturesDone: 0,
      capturesTotal: totalCaptures,
      transcriptionsDone: 0,
      transcriptionsTotal: totalTrials,
      current: 'Příprava LateMic runu...',
    })
    setCopyMessage('')
    let captureIndex = 0
    let transcriptionIndex = 0
    const pendingCleanupIds: string[] = []
    try {
      for (let repeatIndex = 1; repeatIndex <= repeatCount; repeatIndex += 1) {
        for (const row of segmentPlan) {
          if (lateMicStopRequestedRef.current) break
          captureIndex += 1
          const targetS = Math.max(1, Math.min(60, row.targetSegmentS))
          const maxPauseWaitS = Math.max(0, Math.min(60, row.maxPauseWaitS))
          const captureLabel = `Nahrávám ${captureIndex}/${totalCaptures}: lag ${row.lagBudgetS}s, opak. ${repeatIndex}/${repeatCount}, segment ${targetS}s...`
          setLateMicStatus(captureLabel)
          setLateMicProgress(prev => prev ? { ...prev, current: captureLabel } : prev)
          const captured = await capturePcm16Segment({
            minDurationS: targetS,
            maxPauseWaitS,
            deviceId: selectedDeviceId,
            silenceThresholdDbfs,
            minPauseMs,
            shouldCancel: () => lateMicStopRequestedRef.current,
          })
          const captureFinishedPerf = performance.now()
          setLateMicProgress(prev => prev ? { ...prev, capturesDone: captureIndex } : prev)

          for (const [modelIndex, model] of selectedModels.entries()) {
            if (lateMicStopRequestedRef.current) break
            transcriptionIndex += 1
            const queueWaitS = Math.max(0, (performance.now() - captureFinishedPerf) / 1000)
            const current = `Přepisuji ${transcriptionIndex}/${totalTrials}: ${model.label}, lag ${row.lagBudgetS}s, opak. ${repeatIndex}/${repeatCount}...`
            setLateMicStatus(current)
            setLateMicProgress(prev => prev ? { ...prev, current } : prev)
            try {
              const response = await api.latemic.createSegment({
                model_id: model.model_id,
                model_params: perModelParams[model.model_id] ?? {},
                pcm16_base64: captured.pcm16Base64,
                sample_rate: captured.sampleRate,
                lag_budget_s: row.lagBudgetS,
                target_segment_s: targetS,
                queue_wait_s: queueWaitS,
                segment_index: captureIndex - 1,
                delete_policy: deletePolicy,
                client_meta: {
                  mode: 'late_mic_full_run',
                  run_id: runId,
                  capture_index: captureIndex,
                  capture_total: totalCaptures,
                  transcription_index: transcriptionIndex,
                  transcription_total: totalTrials,
                  repeat_index: repeatIndex,
                  repeat_total: repeatCount,
                  model_index: modelIndex + 1,
                  model_total: selectedModels.length,
                  lag_budget_s: row.lagBudgetS,
                  max_pause_wait_s: maxPauseWaitS,
                  min_pause_ms: minPauseMs,
                  silence_threshold_dbfs: silenceThresholdDbfs,
                  capture_audio_s: captured.audioDurationS,
                  capture_ended_by: captured.endedBy,
                  capture_started_at: captured.captureStartedAt,
                  capture_finished_at: captured.captureFinishedAt,
                  audio_source_mode: audioSourceMode,
                  reference_video_id: audioSourceMode === 'reference_audio' ? selectedVideoId : null,
                  clip_from_s: clipFromS,
                  clip_to_s: clipToS,
                },
              })
              const enriched: LateMicRunResult = {
                ...response,
                run_id: runId,
                run_index: transcriptionIndex,
                run_total: totalTrials,
                repeat_index: repeatIndex,
                repeat_total: repeatCount,
                lag_budget_s: row.lagBudgetS,
                target_segment_s: targetS,
                max_pause_wait_s: maxPauseWaitS,
                capture_audio_s: captured.audioDurationS,
                capture_bytes: captured.bytes,
                capture_rms_dbfs: captured.rmsDbfs,
                capture_peak_dbfs: captured.peakDbfs,
                capture_clipping_pct: captured.clippingPct,
                capture_silence_ms: captured.silenceMs,
                capture_ended_by: captured.endedBy,
                cleanup_status: deletePolicy === 'after_run' && response.wav_retained ? 'pending' : 'not_needed',
              }
              if (deletePolicy === 'after_run' && response.wav_retained) {
                pendingCleanupIds.push(response.segment_id)
              }
              setLateMicResults(prev => [...prev, enriched])
            } catch (error) {
              setLateMicErrors(prev => [...prev, `${model.model_id} lag ${row.lagBudgetS}s opak. ${repeatIndex}: ${error instanceof Error ? error.message : String(error)}`])
            } finally {
              setLateMicProgress(prev => prev ? { ...prev, transcriptionsDone: transcriptionIndex } : prev)
            }
          }
        }
        if (lateMicStopRequestedRef.current) break
      }

      if (deletePolicy === 'after_run' && pendingCleanupIds.length > 0) {
        setLateMicStatus(`Mažu WAV po runu (${pendingCleanupIds.length})...`)
        for (const segmentId of pendingCleanupIds) {
          try {
            const deleted = await api.latemic.deleteSegmentWav(segmentId)
            setLateMicResults(prev => prev.map(row => row.segment_id === segmentId
              ? {
                  ...row,
                  wav_deleted: deleted.wav_deleted,
                  wav_retained: deleted.wav_retained,
                  cleanup_status: deleted.wav_deleted ? 'deleted' : 'failed',
                }
              : row))
          } catch {
            setLateMicResults(prev => prev.map(row => row.segment_id === segmentId ? { ...row, cleanup_status: 'failed' } : row))
          }
        }
      }

      setLateMicStatus(lateMicStopRequestedRef.current ? 'LateMic run zastaven uživatelem.' : 'LateMic run dokončen.')
    } catch (error) {
      setLateMicStatus(error instanceof Error ? error.message : String(error))
    } finally {
      setLateMicRunning(false)
      lateMicStopRequestedRef.current = false
    }
  }

  function stopLateMicRun() {
    lateMicStopRequestedRef.current = true
    setLateMicStatus('Zastavuji LateMic run po aktuálním kroku...')
  }

  return (
    <div className="bg-slate-800 text-slate-100 rounded border border-slate-700 p-4 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">Little late Mic</h2>
          <p className="text-xs text-slate-400">
            Segmentový mikrofonní test: hledá nejvyšší kvalitu při nejmenším praktickém zpoždění.
          </p>
        </div>
        <span className="rounded border border-amber-500/60 bg-amber-500/10 px-2 py-1 text-xs text-amber-200">
          delayed mic, ne live stream
        </span>
      </div>

      <Section step="1" title="Mikrofon a důkaz vstupu" subtitle="Nejdřív ověř, že stránka skutečně vidí vstup z mikrofonu.">
        <div className="grid grid-cols-1 lg:grid-cols-[1.1fr_1fr] gap-4">
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setAudioSourceMode('free_mic')}
                className={modeButtonClass(audioSourceMode === 'free_mic')}
              >
                Volný mikrofon
              </button>
              <button
                type="button"
                onClick={() => setAudioSourceMode('reference_audio')}
                className={modeButtonClass(audioSourceMode === 'reference_audio')}
              >
                Referenční audio přes mikrofon
              </button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_auto] gap-2 items-end">
              <label className="space-y-1">
                <span className="text-xs text-slate-400">Mikrofon</span>
                <select
                  value={selectedDeviceId}
                  onChange={event => setSelectedDeviceId(event.target.value)}
                  className="w-full rounded border border-slate-600 bg-slate-700 px-2 py-2 text-sm text-white"
                >
                  <option value="">výchozí</option>
                  {audioDevices.map((device, index) => (
                    <option key={device.deviceId || index} value={device.deviceId}>
                      {device.label || `Mikrofon ${index + 1}`}
                    </option>
                  ))}
                </select>
              </label>
              <ActionButton
                type="button"
                onClick={proof.status === 'running' || proof.status === 'starting' ? stopMicProof : startMicProof}
                variant={proof.status === 'running' || proof.status === 'starting' ? 'stop' : 'start'}
                title="Spustí nebo zastaví kontrolu, že prohlížeč opravdu přijímá audio z vybraného mikrofonu."
              >
                {proof.status === 'running' || proof.status === 'starting' ? 'Zastavit důkaz' : 'Spustit důkaz'}
              </ActionButton>
              <ActionButton
                type="button"
                onClick={() => refreshAudioDevices().catch(() => {})}
                variant="neutral"
                title="Znovu načte seznam mikrofonů z prohlížeče."
              >
                ↻ Obnovit
              </ActionButton>
            </div>
            <label className="inline-flex items-center gap-2 text-xs text-slate-300" title="RMS nad tímto prahem se bere jako řeč/VAD. Slouží jen pro důkazní panel a návrh ukončení segmentu v pauze.">
              VAD práh dBFS
              <input
                type="number"
                value={silenceThresholdDbfs}
                onChange={event => setSilenceThresholdDbfs(Number(event.target.value))}
                className="w-20 rounded border border-slate-600 bg-slate-700 px-2 py-1 text-white"
                min={-90}
                max={-20}
              />
            </label>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <Metric label="RMS" value={formatDb(proof.rmsDbfs)} tone={proof.rmsDbfs != null && proof.rmsDbfs < -65 ? 'warn' : 'normal'} />
            <Metric label="Peak" value={formatDb(proof.peakDbfs)} />
            <Metric label="VAD" value={proof.vad === 'rec' ? 'řeč' : proof.vad === 'silence' ? 'ticho' : '—'} tone={proof.vad === 'silence' ? 'warn' : 'normal'} />
            <Metric label="Clipping" value={`${proof.clippingPct.toFixed(3)} %`} tone={proof.clippingPct > 0.1 ? 'bad' : 'normal'} />
            <Metric label="Ticho" value={`${proof.silenceMs} ms`} />
            <Metric label="Chunky" value={String(proof.chunks)} />
            <Metric label="Audio" value={formatBytes(proof.bytes)} />
            <Metric label="SR" value={proof.sampleRate ? `${proof.sampleRate} Hz` : '—'} />
          </div>
        </div>
        <p className={`text-xs ${proof.status === 'error' ? 'text-red-300' : 'text-slate-400'}`}>{proof.message}</p>
      </Section>

      <Section step="2" title="Segmentace a zpoždění" subtitle="Tady se definuje, jak dlouhé úseky audia se budou ukončovat a jaké zpoždění se testuje.">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.1fr] gap-4">
          <div className="space-y-3">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <NumberField label="Od zpoždění (s)" hint="Nejmenší povolené zpoždění přepisu. Nižší hodnota znamená rychlejší, ale náročnější test." value={lagFromS} min={5} max={60} onChange={setLagFromS} />
              <NumberField label="Do zpoždění (s)" hint="Nejvyšší testované zpoždění. Vyšší hodnota dá pomalejším modelům víc času na kvalitní výsledek." value={lagToS} min={5} max={60} onChange={setLagToS} />
              <NumberField label="Krok (s)" hint="Rozestup mezi testovanými zpožděními. Například 5 znamená 10, 15, 20..." value={lagStepS} min={1} max={30} onChange={setLagStepS} />
              <NumberField label="Opakování" hint="Kolikrát se každá varianta zopakuje. Vyšší číslo lépe odhalí náhodné výkyvy." value={repeatCount} min={1} max={10} onChange={setRepeatCount} />
            </div>
            <label className="inline-flex items-center gap-2 text-sm text-slate-200">
              <input
                type="checkbox"
                checked={includeFiveSeconds}
                onChange={event => setIncludeFiveSeconds(event.target.checked)}
                className="accent-emerald-500"
              />
              Zahrnout 5s variantu pro velmi rychlé modely
            </label>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => setSegmentMode('recommended')} className={modeButtonClass(segmentMode === 'recommended')}>
                Doporučené délky
              </button>
              <button type="button" onClick={() => setSegmentMode('manual')} className={modeButtonClass(segmentMode === 'manual')}>
                Ruční délka pro všechny
              </button>
            </div>
            {segmentMode === 'manual' && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                <NumberField label="Délka segmentu (s)" hint="Jak dlouhý kus reálného mic audia se uloží a pošle modelu. Delší segment obvykle zlepší kontext, ale zvyšuje zpoždění." value={manualSegmentS} min={1} max={60} onChange={setManualSegmentS} />
                <NumberField label="Čekání na pauzu (s)" hint="Kolik sekund se po minimální délce čeká na přirozenou pauzu řeči. Vyšší hodnota lépe ukončí segment v tichu, ale prodlužuje lag." value={manualPauseWaitS} min={0} max={20} onChange={setManualPauseWaitS} />
                <NumberField label="Překryv (s)" hint="Kolik sekund se ponechá jako kontext mezi segmenty. Vyšší hodnota může zlepšit návaznost, ale zvyšuje množství zpracovaného audia." value={manualOverlapS} min={0} max={10} step={0.5} onChange={setManualOverlapS} />
              </div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              <NumberField label="Min. pauza (ms)" hint="Jak dlouho musí trvat ticho, aby se segment mohl ukončit v pauze. Nižší hodnota reaguje rychleji, vyšší je stabilnější." value={minPauseMs} min={200} max={2000} step={50} onChange={setMinPauseMs} />
              <NumberField label="Rezerva fronty (s)" hint="Bezpečnostní rezerva pro čekání ve frontě před přepisem. Zvyšuje realističnost plánu, ale snižuje prostor pro samotný model." value={queueReserveS} min={0} max={20} step={0.5} onChange={setQueueReserveS} />
              <label className="space-y-1">
                <span className="text-xs text-slate-400">Mazání dočasného audia</span>
                <select
                  value={deletePolicy}
                  onChange={event => setDeletePolicy(event.target.value as DeletePolicy)}
                  className="w-full rounded border border-slate-600 bg-slate-700 px-2 py-2 text-sm text-white"
                >
                  <option value="after_transcript">po přepisu segmentu</option>
                  <option value="after_run">po celém runu</option>
                  <option value="keep_failed">ponechat jen chybové</option>
                  <option value="keep_all">ponechat vše pro audit</option>
                </select>
              </label>
            </div>
          </div>
          <div className="overflow-x-auto rounded border border-slate-700">
            <table className="w-full text-xs">
              <thead className="bg-slate-900/70 text-slate-400">
                <tr>
                  <th className="px-2 py-2 text-left">Lag</th>
                  <th className="px-2 py-2 text-left">Segment</th>
                  <th className="px-2 py-2 text-left">Pauza max</th>
                  <th className="px-2 py-2 text-left">Překryv</th>
                  <th className="px-2 py-2 text-left" title="Maximální RTF, které se ještě vejde do zpoždění: (lag - segment - rezerva) / segment.">
                    Potř. RTF ≤
                  </th>
                </tr>
              </thead>
              <tbody>
                {segmentPlan.map(row => (
                  <tr key={row.lagBudgetS} className="border-t border-slate-700">
                    <td className="px-2 py-1 font-mono text-white">{row.lagBudgetS}s</td>
                    <td className="px-2 py-1">{row.targetSegmentS}s</td>
                    <td className="px-2 py-1">{row.maxPauseWaitS}s</td>
                    <td className="px-2 py-1">{row.overlapS}s</td>
                    <td className={`px-2 py-1 font-mono ${row.feasible ? rtfTone(row.requiredRtf) : 'text-red-300'}`}>
                      {row.feasible ? row.requiredRtf.toFixed(2) : 'nemožné'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Section>

      <Section step="3" title="Modely a parametry" subtitle="LateMic může testovat modely vhodné pro segmentový WAV přepis, nejen čistý live stream.">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1fr] gap-4">
          <div className="space-y-2">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
              {eligibleModels.map(model => (
                <label key={model.model_id} className={`flex items-center gap-2 rounded border px-2 py-1.5 text-sm ${selectedModelIds.includes(model.model_id) ? 'border-blue-500 bg-blue-950/40' : 'border-slate-700 bg-slate-900/30'}`}>
                  <input
                    type="checkbox"
                    checked={selectedModelIds.includes(model.model_id)}
                    onChange={() => toggleModel(model.model_id)}
                    className="accent-blue-500"
                  />
                  <span className="truncate" title={`${model.model_id} | ${model.notes}`}>{model.label}</span>
                  <span className={`ml-auto rounded px-1.5 py-0.5 text-[10px] ${suitabilityClass(model.transcript_suitability)}`}>
                    {model.transcript_suitability}
                  </span>
                </label>
              ))}
            </div>
            {eligibleModels.length === 0 && <p className="text-sm text-slate-400">Žádný model není v registru dostupný.</p>}
          </div>
          <div className="space-y-2">
            <label className="space-y-1 block">
              <FieldHintLabel tone="dark" className="text-xs" hint="Vyber model, jehož konkrétní parametry chceš upravit. LateMic uloží nastavení po modelech.">
                Parametry modelu
              </FieldHintLabel>
              <select
                value={activeParamModelId}
                onChange={event => setActiveParamModelId(event.target.value)}
                title="Model, jehož parametry se právě zobrazují."
                className="w-full rounded border border-slate-600 bg-slate-700 px-2 py-2 text-sm text-white"
              >
                {selectedModels.map(model => (
                  <option key={model.model_id} value={model.model_id}>{model.label}</option>
                ))}
              </select>
            </label>
            {activeParamModel?.params.length ? (
              <div className="rounded border border-slate-700 bg-slate-900/40 p-3 max-h-80 overflow-y-auto">
                <ModelParamsForm
                  modelId={activeParamModel.model_id}
                  params={activeParamModel.params}
                  values={perModelParams[activeParamModel.model_id] ?? {}}
                  onChange={values => setPerModelParams(prev => ({ ...prev, [activeParamModel.model_id]: values }))}
                  compact
                />
              </div>
            ) : (
              <p className="rounded border border-slate-700 bg-slate-900/40 p-3 text-sm text-slate-400">
                Vybraný model nemá publikované nastavitelné parametry.
              </p>
            )}
          </div>
        </div>
      </Section>

      <Section step="4" title="Testovací plán" subtitle="Plán je kombinace model × zpoždění × opakování. Čísla níže jsou plán, ne naměřený výsledek.">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Metric label="Modelů" value={String(selectedModels.length)} />
          <Metric label="Lag variant" value={String(segmentPlan.length)} />
          <Metric label="Trialů" value={String(totalTrials)} tone={totalTrials > 120 ? 'warn' : 'normal'} />
          <Metric label="Audio v plánu" value={`${round1(estimatedAudioMinutes)} min`} />
        </div>

        {audioSourceMode === 'reference_audio' && (
          <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto_auto] gap-2 items-end">
            <label className="space-y-1">
              <FieldHintLabel tone="dark" className="text-xs" hint="Referenční video/audio vybrané z knihovny. Pořadí odpovídá aktuálnímu řazení stránky Knihovna.">
                Referenční video/audio z knihovny
              </FieldHintLabel>
              <select
                value={selectedVideoId}
                onChange={event => setSelectedVideoId(event.target.value)}
                title="Vyber referenční audio z knihovny pro testovací plán."
                className="w-full rounded border border-slate-600 bg-slate-700 px-2 py-2 text-sm text-white"
              >
                {referenceLibrary.map(item => (
                  <option key={item.video_id} value={item.video_id}>
                    {videoLabel(item.title, item.video_id)}
                  </option>
                ))}
              </select>
            </label>
            <NumberField label="Od (s)" value={clipFromS} min={0} max={Math.max(0, clipToS - 1)} onChange={setClipFromS} />
            <NumberField label="Do (s)" value={clipToS} min={1} max={Math.max(1, selectedVideo?.duration_seconds ?? 3600)} onChange={setClipToS} />
          </div>
        )}

        <div className="overflow-x-auto rounded border border-slate-700">
          <table className="w-full text-xs">
            <thead className="bg-slate-900/70 text-slate-400">
              <tr>
                <th className="px-2 py-2 text-left">Model</th>
                <th className="px-2 py-2 text-left">Lag</th>
                <th className="px-2 py-2 text-left">Segment</th>
                <th className="px-2 py-2 text-left">Potř. RTF</th>
                <th className="px-2 py-2 text-left">Opakování</th>
              </tr>
            </thead>
            <tbody>
              {selectedModels.flatMap(model => segmentPlan.map(row => (
                <tr key={`${model.model_id}:${row.lagBudgetS}`} className="border-t border-slate-700">
                  <td className="px-2 py-1 font-mono text-white">{model.model_id}</td>
                  <td className="px-2 py-1">{row.lagBudgetS}s</td>
                  <td className="px-2 py-1">{row.targetSegmentS}s + pauza max {row.maxPauseWaitS}s</td>
                  <td className={`px-2 py-1 font-mono ${row.feasible ? rtfTone(row.requiredRtf) : 'text-red-300'}`}>
                    {row.feasible ? row.requiredRtf.toFixed(2) : 'nemožné'}
                  </td>
                  <td className="px-2 py-1">{repeatCount}×</td>
                </tr>
              )))}
              {selectedModels.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-2 py-4 text-center text-slate-400">Vyber alespoň jeden model.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {estimatedSegmentCount > 0 && (
          <p className="text-xs text-slate-400">
            Odhad segmentů: {estimatedSegmentCount}. Primární limit bude `max_visible_lag_s = segment_audio_s + queue_wait_s + decode_s`.
          </p>
        )}
      </Section>

      <Section step="5" title="Start a výsledky" subtitle="Běh nahraje autoritativní mic segmenty podle plánu a každý segment přepíše vybranými modely.">
        <div className="flex flex-wrap items-center gap-2">
          <ActionButton
            type="button"
            onClick={startLateMicRun}
            disabled={lateMicRunning || selectedModels.length === 0}
            title="Spustí celý plán: opakování × lag varianty × vybrané modely. Každý přepis vzniká jen z uloženého mic segmentu."
            variant="start"
          >
            {lateMicRunning ? 'LateMic běží' : 'Start LateMic run'}
          </ActionButton>
          {lateMicRunning && (
            <ActionButton
              type="button"
              onClick={stopLateMicRun}
              variant="stop"
              title="Neukončí rozpracovaný krok násilně, ale zastaví běh před dalším plánovaným krokem."
            >
              Zastavit po kroku
            </ActionButton>
          )}
          <ActionButton
            type="button"
            onClick={copyPlan}
            variant="secondary"
            title="Zkopíruje aktuální plán testu jako JSON pro kontrolu nebo dokumentaci."
          >
            ⧉ Kopírovat plán
          </ActionButton>
          {copyMessage && <span className="text-xs text-slate-300">{copyMessage}</span>}
          {lateMicStatus && <span className="text-xs text-slate-300">{lateMicStatus}</span>}
        </div>
        {lateMicProgress && (
          <div className="rounded border border-slate-700 bg-slate-950/30 p-2 space-y-2">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <Metric label="Nahrávky" value={`${lateMicProgress.capturesDone}/${lateMicProgress.capturesTotal}`} />
              <Metric label="Přepisy" value={`${lateMicProgress.transcriptionsDone}/${lateMicProgress.transcriptionsTotal}`} />
              <Metric label="Hotovo" value={`${progressPct(lateMicProgress.transcriptionsDone, lateMicProgress.transcriptionsTotal)} %`} />
              <Metric label="Výsledků" value={String(lateMicResults.length)} />
            </div>
            <div className="h-2 rounded bg-slate-800 overflow-hidden">
              <div
                className="h-full bg-blue-500 transition-all"
                style={{ width: `${progressPct(lateMicProgress.transcriptionsDone, lateMicProgress.transcriptionsTotal)}%` }}
              />
            </div>
            <div className="text-xs text-slate-400">{lateMicProgress.current}</div>
          </div>
        )}
        <div className="rounded border border-emerald-700 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-100">
          Backend ukládá každý přijatý segment jako autoritativní LateMic artefakt a výsledek označí `transcript_source=latemic_segment`.
        </div>
        {lateMicErrors.length > 0 && (
          <div className="rounded border border-red-700 bg-red-950/20 px-3 py-2 text-xs text-red-100">
            {lateMicErrors.map((item, index) => <div key={index}>{item}</div>)}
          </div>
        )}
        {lateMicSummary.length > 0 && (
          <div className="overflow-x-auto rounded border border-slate-700">
            <table className="w-full text-xs">
              <thead className="bg-slate-900/70 text-slate-400">
                <tr>
                  <th className="px-2 py-2 text-left">Model</th>
                  <th className="px-2 py-2 text-left">OK</th>
                  <th className="px-2 py-2 text-left">Pod lag</th>
                  <th className="px-2 py-2 text-left">Avg max lag</th>
                  <th className="px-2 py-2 text-left">Avg RTF</th>
                  <th className="px-2 py-2 text-left">Slova</th>
                </tr>
              </thead>
              <tbody>
                {lateMicSummary.map(row => (
                  <tr key={row.model_id} className="border-t border-slate-700">
                    <td className="px-2 py-1 font-mono text-white">{row.model_id}</td>
                    <td className="px-2 py-1">{row.ok}/{row.total}</td>
                    <td className={`px-2 py-1 ${row.underBudget === row.total ? 'text-emerald-300' : 'text-amber-300'}`}>{row.underBudget}/{row.total}</td>
                    <td className="px-2 py-1 font-mono">{row.avgMaxLagS.toFixed(1)}s</td>
                    <td className="px-2 py-1 font-mono">{row.avgRtf == null ? '—' : row.avgRtf.toFixed(2)}</td>
                    <td className="px-2 py-1">{row.avgWords.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {lateMicResults.length > 0 && (
          <div className="overflow-x-auto rounded border border-slate-700">
            <table className="w-full text-xs">
              <thead className="bg-slate-900/70 text-slate-400">
                <tr>
                  <th className="px-2 py-2 text-left">#</th>
                  <th className="px-2 py-2 text-left">Model</th>
                  <th className="px-2 py-2 text-left">Stav</th>
                  <th className="px-2 py-2 text-left">Lag</th>
                  <th className="px-2 py-2 text-left">Capture</th>
                  <th className="px-2 py-2 text-left">RTF</th>
                  <th className="px-2 py-2 text-left">Autorita</th>
                  <th className="px-2 py-2 text-left">Přepis</th>
                </tr>
              </thead>
              <tbody>
                {lateMicResults.map(result => (
                  <tr key={result.segment_id} className="border-t border-slate-700 align-top">
                    <td className="px-2 py-1 font-mono text-slate-300">{result.run_index}/{result.run_total}</td>
                    <td className="px-2 py-1 font-mono text-white">{result.model_id}</td>
                    <td className={result.status === 'ok' ? 'px-2 py-1 text-emerald-300' : 'px-2 py-1 text-red-300'}>
                      {result.status}{result.error ? `: ${result.error}` : ''}
                    </td>
                    <td className="px-2 py-1">
                      <div>budget {result.lag_budget_s}s</div>
                      <div>max {result.max_visible_lag_s.toFixed(1)}s</div>
                      <div className="text-slate-500">queue {result.queue_wait_s.toFixed(1)}s</div>
                      {typeof result.over_budget_s === 'number' && result.over_budget_s > 0 && (
                        <span className="ml-1 text-amber-300">+{result.over_budget_s.toFixed(1)}s</span>
                      )}
                    </td>
                    <td className="px-2 py-1">
                      <div>{result.capture_audio_s.toFixed(1)}s · {result.capture_ended_by}</div>
                      <div className="text-slate-500">RMS {result.capture_rms_dbfs.toFixed(1)} dBFS</div>
                    </td>
                    <td className="px-2 py-1 font-mono">{typeof result.rtf === 'number' ? result.rtf.toFixed(2) : '—'}</td>
                    <td className="px-2 py-1">
                      <div>{result.transcript_source}</div>
                      <div className="text-slate-500">{result.wav_retained ? 'WAV ponechán' : result.cleanup_status === 'deleted' ? 'WAV smazán po runu' : 'WAV smazán'}</div>
                    </td>
                    <td className="px-2 py-1 max-w-xl">
                      {result.transcript ? eightWordPreview(result.transcript) : <span className="text-slate-500">bez textu</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}

function Section({ step, title, subtitle, children }: {
  step: string
  title: string
  subtitle: string
  children: ReactNode
}) {
  return (
    <section className="rounded border border-slate-700 bg-slate-900/35 p-3 space-y-3">
      <div>
        <h3 className="text-sm font-bold text-white">{step}. {title}</h3>
        <p className="text-xs text-slate-400">{subtitle}</p>
      </div>
      {children}
    </section>
  )
}

function NumberField({ label, value, min, max, step = 1, hint = '', onChange }: {
  label: string
  value: number
  min: number
  max: number
  step?: number
  hint?: string
  onChange: (value: number) => void
}) {
  return (
    <label className="space-y-1">
      <FieldHintLabel hint={hint} tone="dark" className="text-xs">
        {label}
      </FieldHintLabel>
      <input
        type="number"
        value={Number.isFinite(value) ? value : 0}
        min={min}
        max={max}
        step={step}
        title={hint}
        onChange={event => onChange(Number(event.target.value))}
        className="w-full rounded border border-slate-600 bg-slate-700 px-2 py-2 text-sm text-white"
      />
    </label>
  )
}

function Metric({ label, value, tone = 'normal' }: {
  label: string
  value: string
  tone?: 'normal' | 'warn' | 'bad'
}) {
  const toneClass = tone === 'bad' ? 'text-red-300' : tone === 'warn' ? 'text-amber-300' : 'text-white'
  return (
    <div className="rounded border border-slate-700 bg-slate-950/40 px-2 py-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={`text-sm font-semibold ${toneClass}`}>{value}</div>
    </div>
  )
}

function recommendedSegmentForLag(lagBudgetS: number): SegmentConfig {
  const exact = RECOMMENDED_SEGMENTS.find(row => row.lagBudgetS === lagBudgetS)
  if (exact) return exact
  return RECOMMENDED_SEGMENTS.reduce((best, row) => (
    Math.abs(row.lagBudgetS - lagBudgetS) < Math.abs(best.lagBudgetS - lagBudgetS) ? row : best
  ), RECOMMENDED_SEGMENTS[0])
}

function modeButtonClass(active: boolean): string {
  return active
    ? 'rounded border border-blue-500 bg-blue-600 px-3 py-1.5 text-sm font-medium text-white'
    : 'rounded border border-slate-600 bg-slate-800 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-400'
}

function suitabilityClass(value: ModelDescriptor['transcript_suitability']): string {
  if (value === 'green') return 'bg-emerald-500/20 text-emerald-200'
  if (value === 'amber') return 'bg-amber-500/20 text-amber-200'
  return 'bg-red-500/20 text-red-200'
}

function rtfTone(value: number): string {
  if (value >= 1) return 'text-emerald-300'
  if (value >= 0.5) return 'text-amber-300'
  return 'text-red-300'
}

function toDbfs(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return -120
  return Math.max(-120, 20 * Math.log10(value))
}

function formatDb(value: number | null): string {
  return value == null ? '—' : `${value.toFixed(1)} dBFS`
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} kB`
  return `${bytes} B`
}

async function capturePcm16Segment({ minDurationS, maxPauseWaitS, deviceId, silenceThresholdDbfs, minPauseMs, shouldCancel }: {
  minDurationS: number
  maxPauseWaitS: number
  deviceId: string
  silenceThresholdDbfs: number
  minPauseMs: number
  shouldCancel?: () => boolean
}): Promise<CapturedPcmSegment> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('Prohlížeč nepodporuje getUserMedia.')
  }
  const constraints: MediaStreamConstraints = {
    audio: deviceId
      ? {
          deviceId: { exact: deviceId },
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        }
      : {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
  }
  const stream = await navigator.mediaDevices.getUserMedia(constraints)
  const AudioContextCtor = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!AudioContextCtor) {
    stream.getTracks().forEach(track => track.stop())
    throw new Error('Prohlížeč nepodporuje AudioContext.')
  }
  let audioContext: AudioContext
  try {
    audioContext = new AudioContextCtor({ sampleRate: 16000 } as AudioContextOptions)
  } catch {
    audioContext = new AudioContextCtor()
  }
  const source = audioContext.createMediaStreamSource(stream)
  const processor = audioContext.createScriptProcessor(4096, 1, 1)
  const chunks: Uint8Array[] = []
  let totalBytes = 0
  let totalSamples = 0
  let sumSqTotal = 0
  let peakTotal = 0
  let clippingTotal = 0
  let silenceMs = 0
  const startedPerf = performance.now()
  const captureStartedAt = new Date().toISOString()
  const hardCapS = Math.max(1, minDurationS) + Math.max(0, maxPauseWaitS)

  return new Promise<CapturedPcmSegment>((resolve, reject) => {
    let settled = false
    let guardTimer: number | null = null
    const cleanup = () => {
      if (guardTimer != null) window.clearInterval(guardTimer)
      processor.disconnect()
      source.disconnect()
      stream.getTracks().forEach(track => track.stop())
      audioContext.close().catch(() => {})
    }
    const finish = (endedBy: CapturedPcmSegment['endedBy']) => {
      if (settled) return
      settled = true
      const captureFinishedAt = new Date().toISOString()
      cleanup()
      if (totalBytes <= 0) {
        reject(new Error('Nenahrál se žádný PCM segment.'))
        return
      }
      const rms = Math.sqrt(sumSqTotal / Math.max(1, totalSamples))
      resolve({
        pcm16Base64: bytesToBase64(chunks, totalBytes),
        sampleRate: Math.round(audioContext.sampleRate),
        audioDurationS: totalSamples / Math.max(1, audioContext.sampleRate),
        bytes: totalBytes,
        rmsDbfs: toDbfs(rms),
        peakDbfs: toDbfs(peakTotal),
        clippingPct: totalSamples > 0 ? clippingTotal / totalSamples * 100 : 0,
        silenceMs: Math.round(silenceMs),
        endedBy,
        captureStartedAt,
        captureFinishedAt,
      })
    }
    processor.onaudioprocess = event => {
      if (settled) return
      if (shouldCancel?.()) {
        settled = true
        cleanup()
        reject(new Error('LateMic run zastaven uživatelem.'))
        return
      }
      const input = event.inputBuffer.getChannelData(0)
      let chunkSumSq = 0
      let chunkPeak = 0
      let chunkClipping = 0
      for (let i = 0; i < input.length; i += 1) {
        const sample = input[i]
        const abs = Math.abs(sample)
        chunkSumSq += sample * sample
        if (abs > chunkPeak) chunkPeak = abs
        if (abs >= 0.98) chunkClipping += 1
      }
      const chunkRmsDbfs = toDbfs(Math.sqrt(chunkSumSq / Math.max(1, input.length)))
      const chunkMs = input.length / Math.max(1, audioContext.sampleRate) * 1000
      silenceMs = chunkRmsDbfs <= silenceThresholdDbfs ? silenceMs + chunkMs : 0
      sumSqTotal += chunkSumSq
      if (chunkPeak > peakTotal) peakTotal = chunkPeak
      clippingTotal += chunkClipping

      const chunk = float32ToPcm16Bytes(input)
      chunks.push(chunk)
      totalBytes += chunk.byteLength
      totalSamples += input.length
      event.outputBuffer.getChannelData(0).fill(0)

      const elapsedS = (performance.now() - startedPerf) / 1000
      if (elapsedS >= Math.max(1, minDurationS) && silenceMs >= Math.max(0, minPauseMs)) {
        finish('pause')
      } else if (elapsedS >= hardCapS) {
        finish('hard_cap')
      }
    }
    try {
      source.connect(processor)
      processor.connect(audioContext.destination)
      guardTimer = window.setInterval(() => {
        if (settled) return
        if (shouldCancel?.()) {
          settled = true
          cleanup()
          reject(new Error('LateMic run zastaven uživatelem.'))
          return
        }
        const elapsedS = (performance.now() - startedPerf) / 1000
        if (elapsedS >= hardCapS) finish('hard_cap')
      }, 100)
    } catch (error) {
      settled = true
      cleanup()
      reject(error)
    }
  })
}

function float32ToPcm16Bytes(samples: Float32Array): Uint8Array {
  const out = new Uint8Array(samples.length * 2)
  const view = new DataView(out.buffer)
  for (let i = 0; i < samples.length; i += 1) {
    const clipped = Math.max(-1, Math.min(1, samples[i]))
    const value = clipped < 0 ? clipped * 32768 : clipped * 32767
    view.setInt16(i * 2, Math.round(value), true)
  }
  return out
}

function bytesToBase64(chunks: Uint8Array[], totalBytes: number): string {
  const merged = new Uint8Array(totalBytes)
  let offset = 0
  for (const chunk of chunks) {
    merged.set(chunk, offset)
    offset += chunk.byteLength
  }
  let binary = ''
  const step = 0x8000
  for (let i = 0; i < merged.length; i += step) {
    binary += String.fromCharCode(...merged.subarray(i, i + step))
  }
  return window.btoa(binary)
}

function eightWordPreview(value: string): string {
  const words = value.trim().split(/\s+/).filter(Boolean)
  if (words.length <= 8) return value
  return `${words.slice(0, 8).join(' ')} ...`
}

function summarizeLateMicResults(results: LateMicRunResult[]): Array<{
  model_id: string
  total: number
  ok: number
  underBudget: number
  avgMaxLagS: number
  avgRtf: number | null
  avgWords: number
}> {
  const byModel = new Map<string, LateMicRunResult[]>()
  for (const result of results) {
    byModel.set(result.model_id, [...(byModel.get(result.model_id) ?? []), result])
  }
  return [...byModel.entries()].map(([modelId, rows]) => {
    const okRows = rows.filter(row => row.status === 'ok')
    const rtfRows = okRows.filter(row => typeof row.rtf === 'number')
    const underBudget = rows.filter(row => typeof row.over_budget_s === 'number' && row.over_budget_s <= 0).length
    return {
      model_id: modelId,
      total: rows.length,
      ok: okRows.length,
      underBudget,
      avgMaxLagS: average(rows.map(row => row.max_visible_lag_s)),
      avgRtf: rtfRows.length > 0 ? average(rtfRows.map(row => Number(row.rtf))) : null,
      avgWords: average(okRows.map(row => row.transcript.trim().split(/\s+/).filter(Boolean).length)),
    }
  }).sort((a, b) => {
    const lagDiff = a.avgMaxLagS - b.avgMaxLagS
    if (Math.abs(lagDiff) > 0.001) return lagDiff
    return b.ok - a.ok
  })
}

function progressPct(done: number, total: number): number {
  if (!Number.isFinite(done) || !Number.isFinite(total) || total <= 0) return 0
  return Math.max(0, Math.min(100, Math.round(done / total * 100)))
}

function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min
  return Math.min(max, Math.max(min, value))
}

function average(values: number[]): number {
  if (values.length === 0) return 0
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

function round1(value: number): number {
  return Math.round(value * 10) / 10
}
