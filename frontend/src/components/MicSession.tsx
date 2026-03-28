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
import { useState, useRef, useEffect, useCallback } from 'react'
import type { ModelDescriptor, AudioDevice } from '../types'
import { api } from '../api/client'
import { ModelParamsForm } from './ModelParamsForm'

interface Props {
  /** Modely které podporují mic (supports_microphone: true) */
  availableModels: ModelDescriptor[]
}

type Status = 'idle' | 'connecting' | 'recording' | 'stopping' | 'done' | 'error'

type MicMetrics = {
  latency_ms?: number
  rtf?: number
  elapsed_s?: number
  segment_finalize_ms_p95?: number
  drop_rate?: number
  worker_rss_peak_mb?: number
  reason_code?: string | null
}

const WS_BASE = `ws://${window.location.host}`
const SAMPLE_RATE = 16000

export function MicSession({ availableModels }: Props) {
  const [modelId, setModelId] = useState(availableModels[0]?.model_id ?? '')
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [status, setStatus] = useState<Status>('idle')
  const [transcript, setTranscript] = useState('')
  const [metrics, setMetrics] = useState<MicMetrics | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [devices, setDevices] = useState<AudioDevice[]>([])
  const [deviceIndex, setDeviceIndex] = useState<number | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const processorRef = useRef<ScriptProcessorNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)

  const selectedModel = availableModels.find(m => m.model_id === modelId)

  // Načti dostupná audio zařízení
  useEffect(() => {
    api.mic.devices().then(setDevices).catch(() => setDevices([]))
  }, [])

  const start = useCallback(async () => {
    setError(null)
    setTranscript('')
    setMetrics(null)
    setStatus('connecting')

    try {
      // 1. Vytvoř backend session
      const { session_id } = await api.mic.createSession(modelId, params)

      // 2. Otevři WebSocket
      const ws = new WebSocket(`${WS_BASE}/api/mic/sessions/${session_id}/stream`)
      wsRef.current = ws

      ws.onopen = () => {
        setStatus('recording')
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'partial' || msg.type === 'started') {
            if (msg.text) setTranscript(msg.text)
          } else if (msg.type === 'final') {
            setTranscript(msg.text || '')
            setMetrics({
              latency_ms: msg.first_word_latency_ms,
              rtf: msg.rtf,
              elapsed_s: msg.elapsed_s,
              segment_finalize_ms_p95: msg.segment_finalize_ms_p95,
              drop_rate: msg.drop_rate,
              worker_rss_peak_mb: msg.worker_rss_peak_mb,
              reason_code: msg.reason_code,
            })
            setStatus('done')
            _stopAudio()
          } else if (msg.error) {
            setError(msg.reason_code ? `${msg.error} (${msg.reason_code})` : msg.error)
            setStatus('error')
            _stopAudio()
          }
        } catch {
          // ignoruj neJSON zprávy
        }
      }

      ws.onerror = () => {
        setError('WebSocket chyba')
        setStatus('error')
        _stopAudio()
      }

      ws.onclose = () => {
        if (status === 'recording') {
          setStatus('done')
          _stopAudio()
        }
      }

      // 3. Otevři mikrofon
      const constraints: MediaStreamConstraints = {
        audio: deviceIndex !== null
          ? { deviceId: { exact: devices[deviceIndex]?.name } }
          : true,
      }
      const stream = await navigator.mediaDevices.getUserMedia(constraints)
      streamRef.current = stream

      const audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE })
      audioCtxRef.current = audioCtx
      const source = audioCtx.createMediaStreamSource(stream)
      sourceRef.current = source

      // ScriptProcessorNode pro Float32 → Int16 → binary WS
      // (AudioWorklet by byl lepší, ale ScriptProcessor je jednodušší bez extra souboru)
      const bufferSize = 4096
      const processor = audioCtx.createScriptProcessor(bufferSize, 1, 1)
      processorRef.current = processor

      processor.onaudioprocess = (e) => {
        if (ws.readyState !== WebSocket.OPEN) return
        const float32 = e.inputBuffer.getChannelData(0)
        const int16 = new Int16Array(float32.length)
        for (let i = 0; i < float32.length; i++) {
          int16[i] = Math.max(-32768, Math.min(32767, float32[i] * 32767))
        }
        ws.send(int16.buffer)
      }

      source.connect(processor)
      processor.connect(audioCtx.destination)

    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
      setStatus('error')
    }
  }, [modelId, params, deviceIndex, devices, status])

  const stop = useCallback(() => {
    setStatus('stopping')
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: 'stop' }))
    }
    _stopAudio()
  }, [])

  function _stopAudio() {
    try { processorRef.current?.disconnect() } catch {}
    try { sourceRef.current?.disconnect() } catch {}
    try { audioCtxRef.current?.close() } catch {}
    try { streamRef.current?.getTracks().forEach(t => t.stop()) } catch {}
    processorRef.current = null
    sourceRef.current = null
    audioCtxRef.current = null
    streamRef.current = null
  }

  // Cleanup při unmount
  useEffect(() => () => {
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
            onChange={e => { setModelId(e.target.value); setParams({}) }}
            disabled={status === 'recording' || status === 'connecting'}
            className="bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-white"
          >
            {availableModels.map(m => (
              <option key={m.model_id} value={m.model_id}>
                {m.label} [{m.languages.join(', ')}]
              </option>
            ))}
          </select>
        </div>

        {devices.length > 0 && (
          <div>
            <label className="block text-xs text-gray-400 mb-1">Mikrofon</label>
            <select
              value={deviceIndex ?? ''}
              onChange={e => setDeviceIndex(e.target.value === '' ? null : Number(e.target.value))}
              disabled={status === 'recording' || status === 'connecting'}
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
              onChange={setParams}
              compact
            />
          </div>
        </details>
      )}

      {/* Ovládání */}
      <div className="flex gap-2 items-center">
        {status === 'idle' || status === 'done' || status === 'error' ? (
          <button
            onClick={start}
            className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded font-medium text-sm"
          >
            {status === 'done' || status === 'error' ? '● Znovu' : '● Start'}
          </button>
        ) : status === 'recording' ? (
          <button
            onClick={stop}
            className="px-4 py-2 bg-gray-600 hover:bg-gray-500 text-white rounded font-medium text-sm"
          >
            ■ Stop
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
      </div>

      {/* Chyba */}
      {error && (
        <div className="bg-red-900/40 border border-red-700 rounded p-2 text-sm text-red-300">
          {error}
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
    </div>
  )
}
