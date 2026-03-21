import { useEffect, useRef, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { api } from '../api/client'
import type { BenchmarkJobStatus, HwSample, LibraryItem } from '../types'

interface Props {
  job: BenchmarkJobStatus
}

export function LiveJobPanel({ job }: Props) {
  const [percent, setPercent] = useState(0)
  const [messageLog, setMessageLog] = useState<string[]>([])
  const [hwSeries, setHwSeries] = useState<HwSample[]>([])
  const [transcript, setTranscript] = useState('')
  const [preCpu, setPreCpu] = useState<number | null>(job.pre_cpu ?? null)
  const [preRamMb, setPreRamMb] = useState<number | null>(job.pre_ram_mb ?? null)
  const [modelParams, setModelParams] = useState<Record<string, unknown>>({})
  const [libraryItem, setLibraryItem] = useState<LibraryItem | null>(null)
  const [showSubtitles, setShowSubtitles] = useState(false)
  const [subtitleContent, setSubtitleContent] = useState('')
  const [subtitleLoading, setSubtitleLoading] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const logEndRef = useRef<HTMLDivElement>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const isActive = job.status === 'running' || job.status === 'pending'

  // Elapsed time counter
  useEffect(() => {
    if (!isActive || !job.started_at) return
    const start = new Date(job.started_at).getTime()
    setElapsed(Math.floor((Date.now() - start) / 1000))
    elapsedRef.current = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000))
    }, 1000)
    return () => { if (elapsedRef.current) clearInterval(elapsedRef.current) }
  }, [job.job_id, job.status, job.started_at])

  // Polling live dat
  useEffect(() => {
    const poll = async () => {
      try {
        const live = await api.benchmark.getLive(job.job_id)
        setPercent(live.percent)
        setMessageLog(live.message_log ?? [])
        setHwSeries(live.hw_series.slice(-60))
        setTranscript(live.transcript ?? '')
        if (live.pre_cpu != null) setPreCpu(live.pre_cpu)
        if (live.pre_ram_mb != null) setPreRamMb(live.pre_ram_mb)
        if (Object.keys(live.model_params_used ?? {}).length > 0) {
          setModelParams(live.model_params_used)
        }
      } catch {
        // backend restart — tiše ignoruj
      }
    }

    poll()
    if (isActive) {
      timerRef.current = setInterval(poll, 1000)
    } else {
      setTimeout(poll, 500)
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [job.job_id, job.status])

  // Načti info o videu z knihovny
  useEffect(() => {
    const videoId = job.video_ids?.[0]
    if (!videoId) return
    api.library.list().then(items => {
      const item = items.find(i => i.video_id === videoId)
      if (item) setLibraryItem(item)
    }).catch(() => {})
  }, [job.video_ids])

  // Auto-scroll log dolů
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messageLog])

  const chartData = hwSeries.map((s, i) => ({
    t: i,
    cpu: s.cpu ?? 0,
    ram: s.ram_mb ? Math.round(s.ram_mb) : 0,
  }))
  const videoId = job.video_ids?.[0] ?? null
  const hasSubtitles = (libraryItem?.subtitle_files?.length ?? 0) > 0

  async function loadSubtitles() {
    if (!libraryItem || !libraryItem.subtitle_files[0]) return
    setSubtitleLoading(true)
    try {
      const file = libraryItem.subtitle_files[0]
      const r = await fetch(`/api/library/subtitle/${libraryItem.video_id}/${file.filename}`)
      const text = await r.text()
      setSubtitleContent(text)
      setShowSubtitles(true)
    } catch {
      setSubtitleContent('Titulky se nepodařilo načíst.')
      setShowSubtitles(true)
    } finally {
      setSubtitleLoading(false)
    }
  }

  function fmtElapsed(s: number) {
    const m = Math.floor(s / 60)
    const sec = s % 60
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`
  }

  function fmtFinished() {
    if (!job.started_at || !job.finished_at) return null
    const secs = Math.round(
      (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000
    )
    return fmtElapsed(secs)
  }

  const statusColor = {
    running: 'border-blue-300 bg-blue-50',
    pending: 'border-yellow-300 bg-yellow-50',
    completed: 'border-green-300 bg-green-50',
    failed: 'border-red-300 bg-red-50',
    cancelled: 'border-gray-300 bg-gray-50',
  }[job.status] ?? 'border-gray-200 bg-white'

  return (
    <div className={`border rounded-lg p-4 space-y-4 ${statusColor}`}>
      {/* Hlavička */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <h2 className="font-semibold text-sm text-gray-800">
            {job.label || job.job_id.slice(-8)}
          </h2>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            job.status === 'running' ? 'bg-blue-100 text-blue-700' :
            job.status === 'completed' ? 'bg-green-100 text-green-700' :
            job.status === 'failed' ? 'bg-red-100 text-red-700' :
            'bg-gray-100 text-gray-600'
          }`}>{job.status}</span>
          {/* Elapsed time */}
          {isActive && (
            <span className="text-xs font-mono text-blue-600 animate-pulse">
              ⏱ {fmtElapsed(elapsed)}
            </span>
          )}
          {!isActive && fmtFinished() && (
            <span className="text-xs font-mono text-gray-500">
              ⏱ celkem {fmtFinished()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          {hasSubtitles && (
            <button
              onClick={showSubtitles ? () => setShowSubtitles(false) : loadSubtitles}
              disabled={subtitleLoading}
              className="text-blue-600 hover:underline"
            >
              {subtitleLoading ? 'Načítám...' : showSubtitles ? '▲ Skrýt titulky' : '📄 Titulky'}
            </button>
          )}
          <span className="font-mono">{job.job_id.slice(-12)}</span>
        </div>
      </div>

      {/* Progress bar */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-gray-500">
          <span className="font-mono">{percent}%</span>
          {isActive && <span className="text-blue-500 animate-pulse">● přepisuji</span>}
        </div>
        <div className="w-full bg-gray-200 rounded-full h-2">
          <div
            className={`h-2 rounded-full transition-all duration-500 ${
              job.status === 'completed' ? 'bg-green-500' :
              job.status === 'failed' ? 'bg-red-500' : 'bg-blue-500'
            }`}
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>

      {/* Log zpráv (nemaže se) */}
      {messageLog.length > 0 && (
        <div className="bg-gray-900 rounded p-2 max-h-36 overflow-y-auto">
          <p className="text-xs text-gray-500 mb-1 font-medium">Log průběhu</p>
          {messageLog.map((msg, i) => (
            <p key={i} className={`text-xs font-mono ${
              i === messageLog.length - 1 ? 'text-green-300' : 'text-gray-400'
            }`}>
              {msg}
            </p>
          ))}
          <div ref={logEndRef} />
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* YouTube embed — autoplay při spuštění */}
        {videoId ? (
          <div className="aspect-video rounded overflow-hidden border border-gray-200">
            <iframe
              src={`https://www.youtube.com/embed/${videoId}?autoplay=${isActive ? 1 : 0}&cc_load_policy=0`}
              title="Video benchmark"
              className="w-full h-full"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
            />
          </div>
        ) : (
          <div className="aspect-video rounded bg-gray-100 border border-gray-200 flex items-center justify-center text-gray-400 text-sm">
            Žádné video
          </div>
        )}

        {/* HW grafy */}
        <div className="space-y-2">
          {/* Pre-benchmark hodnoty */}
          {(preCpu != null || preRamMb != null) && (
            <div className="bg-yellow-50 border border-yellow-200 rounded px-3 py-2 text-xs flex gap-4 flex-wrap">
              <span className="text-yellow-700 font-medium">Před startem (systém):</span>
              {preCpu != null && (
                <span>
                  CPU: <strong className={preCpu >= 20 ? 'text-orange-600' : 'text-green-700'}>{preCpu}%</strong>
                  <span className="text-gray-500 ml-1">({preCpu >= 20 ? 'zatíženo' : 'volno'})</span>
                </span>
              )}
              {preRamMb != null && (
                <span>RAM: <strong>{Math.round(preRamMb)} MB</strong> obsazeno</span>
              )}
            </div>
          )}

          <div>
            <p className="text-xs font-medium text-gray-600 mb-1">
              CPU % (subprocess)
              {chartData.length > 0 && (
                <span className="ml-2 text-gray-400 font-normal">
                  aktuálně {chartData[chartData.length - 1]?.cpu ?? 0}%
                </span>
              )}
            </p>
            <ResponsiveContainer width="100%" height={80}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="2 2" stroke="#f0f0f0" />
                <XAxis dataKey="t" hide />
                <YAxis domain={[0, 100]} width={28} tick={{ fontSize: 10 }} unit="%" />
                <Tooltip formatter={(v: number) => [`${v}%`, 'CPU']} labelFormatter={() => ''} />
                <Line type="monotone" dataKey="cpu" stroke="#3b82f6" dot={false} strokeWidth={1.5} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div>
            <p className="text-xs font-medium text-gray-600 mb-1">
              RAM MB (subprocess)
              {chartData.length > 0 && (
                <span className="ml-2 text-gray-400 font-normal">
                  aktuálně {chartData[chartData.length - 1]?.ram ?? 0} MB
                </span>
              )}
            </p>
            <ResponsiveContainer width="100%" height={80}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="2 2" stroke="#f0f0f0" />
                <XAxis dataKey="t" hide />
                <YAxis width={40} tick={{ fontSize: 10 }} unit="MB" />
                <Tooltip formatter={(v: number) => [`${v} MB`, 'RAM']} labelFormatter={() => ''} />
                <Line type="monotone" dataKey="ram" stroke="#10b981" dot={false} strokeWidth={1.5} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Nastavení modelu */}
      {Object.keys(modelParams).length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-gray-500 hover:text-gray-700 select-none">
            ⚙ Nastavení modelu použité při přepisu
          </summary>
          <div className="mt-1 bg-gray-50 border border-gray-200 rounded p-2 font-mono text-xs">
            {Object.entries(modelParams).map(([k, v]) => (
              <div key={k} className="flex gap-2">
                <span className="text-gray-500 min-w-32">{k}:</span>
                <span className="text-gray-800">{JSON.stringify(v)}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Živý / finální přepis — vždy viditelný */}
      <div className="bg-white border border-gray-200 rounded p-3">
        <p className="text-xs font-medium text-gray-600 mb-1">
          {isActive ? '⌨ Přepis (live)' : '✓ Přepis (finální)'}
        </p>
        <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap min-h-12">
          {transcript
            ? transcript
            : <span className="text-gray-400 italic">
                {isActive ? 'čeká na přepis...' : 'žádný přepis (streaming mode nebo prázdný výstup)'}
              </span>
          }
        </p>
      </div>

      {/* Titulky */}
      {showSubtitles && subtitleContent && (
        <div className="bg-gray-50 border border-gray-200 rounded p-3">
          <p className="text-xs font-medium text-gray-600 mb-2">
            📄 Titulky ({libraryItem?.subtitle_files[0]?.filename})
          </p>
          <pre className="text-xs text-gray-700 max-h-48 overflow-y-auto whitespace-pre-wrap font-mono">
            {subtitleContent}
          </pre>
        </div>
      )}
    </div>
  )
}
