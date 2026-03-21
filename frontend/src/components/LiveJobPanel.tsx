import { useEffect, useRef, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { api } from '../api/client'
import type { BenchmarkJobStatus, HwSample, LibraryItem } from '../types'

interface Props {
  job: BenchmarkJobStatus
}

/**
 * Panel pro live monitoring benchmarku — zobrazuje se i po dokončení.
 *
 * Req 1: message_log — přehled všech zpráv, nemaže se
 * Req 2: transcript — živý nebo finální přepis textu
 * Req 3: CPU/RAM před startem + v průběhu (grafy)
 * Req 4: přesné nastavení modelu (model_params_used)
 * Req 5: tlačítko pro zobrazení titulků
 * Req 7: doba přepisu vs délka videa
 */
export function LiveJobPanel({ job }: Props) {
  const [percent, setPercent] = useState(0)
  const [messageLog, setMessageLog] = useState<string[]>([])
  const [hwSeries, setHwSeries] = useState<HwSample[]>([])
  const [transcript, setTranscript] = useState('')
  const [preCpu, setPreCpu] = useState<number | null>(null)
  const [preRamMb, setPreRamMb] = useState<number | null>(null)
  const [modelParams, setModelParams] = useState<Record<string, unknown>>({})
  const [libraryItem, setLibraryItem] = useState<LibraryItem | null>(null)
  const [showSubtitles, setShowSubtitles] = useState(false)
  const [subtitleContent, setSubtitleContent] = useState('')
  const [subtitleLoading, setSubtitleLoading] = useState(false)
  const logEndRef = useRef<HTMLDivElement>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const isActive = job.status === 'running' || job.status === 'pending'

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
      // Po dokončení: jeden závěrečný poll pro transcript
      setTimeout(poll, 500)
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [job.job_id, job.status])

  // Načti info o videu z knihovny (pro titulky — req 5)
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
        <div className="flex items-center gap-2">
          <h2 className="font-semibold text-sm text-gray-800">
            {job.label || job.job_id.slice(-8)}
          </h2>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            job.status === 'running' ? 'bg-blue-100 text-blue-700' :
            job.status === 'completed' ? 'bg-green-100 text-green-700' :
            job.status === 'failed' ? 'bg-red-100 text-red-700' :
            'bg-gray-100 text-gray-600'
          }`}>{job.status}</span>
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
          {isActive && <span className="text-blue-500 animate-pulse">● běží</span>}
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

      {/* Req 1: Log zpráv (nemaže se) */}
      {messageLog.length > 0 && (
        <div className="bg-gray-900 rounded p-2 max-h-32 overflow-y-auto">
          <p className="text-xs text-gray-500 mb-1 font-medium">Log zpráv</p>
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
        {/* YouTube embed */}
        {videoId ? (
          <div className="aspect-video rounded overflow-hidden border border-gray-200">
            <iframe
              src={`https://www.youtube.com/embed/${videoId}?autoplay=0&cc_load_policy=1`}
              title="Video benchmark"
              className="w-full h-full"
              allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
            />
          </div>
        ) : (
          <div className="aspect-video rounded bg-gray-100 border border-gray-200 flex items-center justify-center text-gray-400 text-sm">
            Žádné video
          </div>
        )}

        {/* HW grafy — req 3 */}
        <div className="space-y-2">
          {/* Pre-benchmark hodnoty */}
          {(preCpu != null || preRamMb != null) && (
            <div className="bg-yellow-50 border border-yellow-200 rounded px-3 py-2 text-xs flex gap-4">
              <span className="text-yellow-700 font-medium">Před startem:</span>
              {preCpu != null && <span>CPU: <strong>{preCpu}%</strong></span>}
              {preRamMb != null && <span>RAM: <strong>{Math.round(preRamMb)} MB</strong></span>}
              {job.conditions_clean != null && (
                <span className={job.conditions_clean ? 'text-green-600' : 'text-orange-600'}>
                  {job.conditions_clean ? '✓ čistý systém' : '⚠ zatížený systém'}
                </span>
              )}
            </div>
          )}

          <div>
            <p className="text-xs font-medium text-gray-600 mb-1">CPU % (subprocess)</p>
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
            <p className="text-xs font-medium text-gray-600 mb-1">RAM MB (subprocess)</p>
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

      {/* Req 4: Nastavení modelu */}
      {Object.keys(modelParams).length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-gray-500 hover:text-gray-700 select-none">
            ⚙ Nastavení modelu
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

      {/* Req 2: Živý / finální přepis */}
      {(transcript || isActive) && (
        <div className="bg-white border border-gray-200 rounded p-3">
          <div className="flex items-center justify-between mb-1">
            <p className="text-xs font-medium text-gray-600">
              {isActive ? '⌨ Přepis (live)' : '✓ Přepis (finální)'}
            </p>
          </div>
          <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap min-h-8">
            {transcript || <span className="text-gray-400 italic">čeká na přepis...</span>}
          </p>
        </div>
      )}

      {/* Req 5: Titulky */}
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
