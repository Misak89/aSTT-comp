import { useEffect, useRef, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { api } from '../api/client'
import type { BenchmarkJobStatus, HwSample, LibraryItem } from '../types'

interface Props {
  job: BenchmarkJobStatus
  onCancel?: () => void  // callback po zrušení — parent refreshne seznam jobů
}

export function LiveJobPanel({ job, onCancel }: Props) {
  const [percent, setPercent] = useState(0)
  const [messageLog, setMessageLog] = useState<string[]>([])
  const [hwSeries, setHwSeries] = useState<HwSample[]>([])
  const [transcript, setTranscript] = useState('')
  const [allTranscripts, setAllTranscripts] = useState<{
    label: string
    model_id: string; model_label: string; setting_id: string; setting_label: string
    transcript: string; reference: string | null
    wer: number | null; cer: number | null; rtf: number | null
    latency_ms: number | null; engine_elapsed_s: number | null; clip_seconds: number | null
    agg_cpu: number | null; agg_ram: number | null
    chunk_metrics: { chunk_start_s: number; chunk_end_s: number; chunk_duration_s: number; processing_s: number; rtf: number; total_elapsed_s: number; words: number }[] | null
  }[]>([])
  const [activeTranscriptIdx, setActiveTranscriptIdx] = useState(0)
  const [preCpu, setPreCpu] = useState<number | null>(job.pre_cpu ?? null)
  const [preRamMb, setPreRamMb] = useState<number | null>(job.pre_ram_mb ?? null)
  const [modelParams, setModelParams] = useState<Record<string, unknown>>({})
  const [libraryItem, setLibraryItem] = useState<LibraryItem | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [showTranscript, setShowTranscript] = useState(true)
  const [showTimestamps, setShowTimestamps] = useState(false)
  const [transcriptTs, setTranscriptTs] = useState('')
  const [showSubtitles, setShowSubtitles] = useState(false)
  const [subtitleContent, setSubtitleContent] = useState('')
  const [subtitleLoading, setSubtitleLoading] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const logContainerRef = useRef<HTMLDivElement>(null)
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
        if (live.transcript_ts) setTranscriptTs(live.transcript_ts)
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
      // Completed job: zkus 3× s prodlevami (backend mohl být při prvním pokusu restart)
      const delays = [500, 1500, 3000]
      delays.forEach(ms => setTimeout(poll, ms))
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

  // Auto-scroll log dolů — pouze uvnitř log kontejneru, ne celá stránka
  useEffect(() => {
    const el = logContainerRef.current
    if (el) el.scrollTop = el.scrollHeight
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
          {isActive && (
            <button
              onClick={async () => {
                setCancelling(true)
                try {
                  await api.benchmark.cancelJob(job.job_id)
                  onCancel?.()
                } catch { setCancelling(false) }
              }}
              disabled={cancelling}
              className="px-2 py-0.5 rounded bg-red-100 text-red-700 hover:bg-red-200 font-medium disabled:opacity-50"
            >
              {cancelling ? 'Zastavuji...' : '⏹ Zastavit'}
            </button>
          )}
          {!isActive && job.run_id && (
            <a href={`/results?run=${job.run_id}`} className="text-green-600 hover:underline font-medium">
              📊 Výsledky
            </a>
          )}
          <button
            onClick={() => setShowTranscript(v => !v)}
            className="text-blue-600 hover:underline"
            title="Zobrazit/skrýt přepis"
          >
            {showTranscript ? '▲ Skrýt přepis' : '📝 Přepis'}
          </button>
          {hasSubtitles && (
            <button
              onClick={showSubtitles ? () => setShowSubtitles(false) : loadSubtitles}
              disabled={subtitleLoading}
              className="text-blue-600 hover:underline"
            >
              {subtitleLoading ? 'Načítám...' : showSubtitles ? '▲ Skrýt titulky' : '📄 Titulky'}
            </button>
          )}
          <button
            onClick={() => api.benchmark.openJobDir(job.job_id).catch(() => {})}
            className="text-gray-500 hover:text-gray-800 hover:underline"
            title={`Otevřít adresář jobu v průzkumníku: runtime/jobs/${job.job_id}`}
          >
            📁 Logy
          </button>
          <span className="font-mono text-gray-400">{job.job_id.slice(-12)}</span>
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
        <div ref={logContainerRef} className="bg-gray-900 rounded p-2 max-h-36 overflow-y-auto">
          <p className="text-xs text-gray-500 mb-1 font-medium">Log průběhu</p>
          {messageLog.map((msg, i) => (
            <p key={i} className={`text-xs font-mono ${
              i === messageLog.length - 1 ? 'text-green-300' : 'text-gray-400'
            }`}>
              {msg}
            </p>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* YouTube embed — autoplay při spuštění */}
        {videoId ? (
          <div className="aspect-video rounded overflow-hidden border border-gray-200">
            <iframe
              src={`https://www.youtube.com/embed/${videoId}?autoplay=${isActive ? 1 : 0}&mute=${isActive ? 1 : 0}&cc_load_policy=0`}
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

      {/* Přepisy */}
      {showTranscript && <div className="bg-white border border-gray-200 rounded p-3 space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium text-gray-600">
            {isActive ? '⌨ Přepis (live)' : '✓ Přepisy (finální)'}
          </p>
          <div className="flex items-center gap-2">
            {!isActive && allTranscripts.length === 0 && job.evaluation_mode !== 'synthetic' && (
              <button
                onClick={async () => {
                  try {
                    if (job.run_id) {
                      const run = await api.runs.get(job.run_id)
                      const collected: typeof allTranscripts = []
                      for (const res of run.results ?? []) {
                        for (const sm of res.source_metrics ?? []) {
                          if (sm.transcript) {
                            collected.push({
                              label: `${res.model_label} × ${res.setting_label}`,
                              model_id: res.model_id,
                              model_label: res.model_label,
                              setting_id: res.setting_id,
                              setting_label: res.setting_label,
                              transcript: sm.transcript,
                              reference: sm.reference_text ?? null,
                              wer: sm.wer ?? null,
                              cer: sm.cer ?? null,
                              rtf: sm.rtf ?? null,
                              latency_ms: sm.latency_ms ?? null,
                              engine_elapsed_s: sm.engine_elapsed_seconds ?? null,
                              clip_seconds: sm.clip_seconds ?? null,
                              agg_cpu: res.aggregate?.cpu_percent ?? null,
                              agg_ram: res.aggregate?.ram_mb ?? null,
                              chunk_metrics: (sm.chunk_metrics as any) ?? null,
                            })
                          }
                        }
                      }
                      if (collected.length > 0) {
                        setAllTranscripts(collected)
                        setActiveTranscriptIdx(0)
                        return
                      }
                    }
                    // Fallback: progress.json (live transcript z posledního runu)
                    const live = await api.benchmark.getLive(job.job_id)
                    if (live.transcript) setTranscript(live.transcript)
                    if (live.transcript_ts) setTranscriptTs(live.transcript_ts)
                  } catch {}
                }}
                className="text-xs text-blue-500 hover:underline"
              >
                ↻ Načíst přepisy
              </button>
            )}
            {(transcript || transcriptTs) && (
              <label className="flex items-center gap-1 text-xs text-gray-500 cursor-pointer select-none">
                <input type="checkbox" checked={showTimestamps} onChange={e => setShowTimestamps(e.target.checked)} />
                ⏱ Časové značky
              </label>
            )}
          </div>
        </div>

        {/* Záložky model × setting */}
        {!isActive && allTranscripts.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {allTranscripts.map((t, i) => (
              <button
                key={i}
                onClick={() => setActiveTranscriptIdx(i)}
                className={`text-xs px-2 py-0.5 rounded border transition-colors ${
                  i === activeTranscriptIdx
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-gray-50 text-gray-600 border-gray-200 hover:bg-gray-100'
                }`}
              >
                {t.label}
                {t.wer != null && (
                  <span className={`ml-1 font-mono ${i === activeTranscriptIdx ? 'text-blue-200' : t.wer < 0.15 ? 'text-green-600' : t.wer < 0.4 ? 'text-yellow-600' : 'text-red-600'}`}>
                    WER {(t.wer * 100).toFixed(0)}%
                  </span>
                )}
              </button>
            ))}
          </div>
        )}

        {/* Detail aktivního přepisu */}
        {!isActive && allTranscripts.length > 0
          ? (() => {
              const t = allTranscripts[activeTranscriptIdx]
              if (!t) return null
              return (
                <div className="space-y-2">
                  {/* Identifikace */}
                  <div className="bg-gray-50 rounded px-3 py-2 text-xs space-y-0.5">
                    <div className="flex gap-4 flex-wrap font-medium text-gray-700">
                      <span>Model: <span className="font-mono text-blue-700">{t.model_id}</span></span>
                      <span>Nastavení: <span className="font-mono text-blue-700">{t.setting_label}</span></span>
                      {t.clip_seconds != null && <span>Délka klipu: <span className="font-mono">{t.clip_seconds}s</span></span>}
                    </div>
                  </div>

                  {/* Statistiky */}
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
                    <StatBox label="WER" value={t.wer != null ? `${(t.wer * 100).toFixed(1)}%` : '–'}
                      sub="Word Error Rate" color={t.wer == null ? '' : t.wer < 0.15 ? 'text-green-700' : t.wer < 0.4 ? 'text-yellow-700' : 'text-red-700'} />
                    <StatBox label="CER" value={t.cer != null ? `${(t.cer * 100).toFixed(1)}%` : '–'}
                      sub="Char Error Rate" color={t.cer == null ? '' : t.cer < 0.08 ? 'text-green-700' : t.cer < 0.2 ? 'text-yellow-700' : 'text-red-700'} />
                    <StatBox label="RTF" value={t.rtf != null ? t.rtf.toFixed(3) : '–'}
                      sub={t.rtf != null ? (t.rtf < 1 ? '✓ stíhá live' : '✗ nestíhá live') : 'Real-Time Factor'}
                      color={t.rtf == null ? '' : t.rtf < 1 ? 'text-green-700' : 'text-red-700'} />
                    <StatBox label="Latence" value={t.latency_ms != null ? `${(t.latency_ms / 1000).toFixed(1)}s` : '–'}
                      sub="1. výsledek / celý přepis" />
                    <StatBox label="Elapsed" value={t.engine_elapsed_s != null ? `${t.engine_elapsed_s.toFixed(1)}s` : '–'}
                      sub="Skutečný čas přepisu" />
                    {t.agg_cpu != null && <StatBox label="CPU" value={`${t.agg_cpu.toFixed(0)}%`} sub="Průměr (subprocess)" />}
                    {t.agg_ram != null && <StatBox label="RAM" value={`${Math.round(t.agg_ram)} MB`} sub="Peak (subprocess)" />}
                  </div>

                  {/* Chunk metriky */}
                  {t.chunk_metrics && t.chunk_metrics.length > 0 && (
                    <details className="text-xs" open={t.chunk_metrics.length <= 5}>
                      <summary className="cursor-pointer text-gray-500 hover:text-gray-700 select-none font-medium">
                        Chunky ({t.chunk_metrics.length}× — RTF per chunk)
                      </summary>
                      <div className="mt-1 overflow-x-auto rounded border border-gray-100">
                        <table className="w-full text-xs">
                          <thead className="bg-gray-50 text-gray-500">
                            <tr>
                              <th className="px-2 py-1 text-left">#</th>
                              <th className="px-2 py-1 text-right">Start</th>
                              <th className="px-2 py-1 text-right">Délka</th>
                              <th className="px-2 py-1 text-right">Přepis (s)</th>
                              <th className="px-2 py-1 text-right font-bold">RTF</th>
                              <th className="px-2 py-1 text-right">Elapsed</th>
                              <th className="px-2 py-1 text-right">Slov</th>
                            </tr>
                          </thead>
                          <tbody>
                            {t.chunk_metrics.map((c, ci) => (
                              <tr key={ci} className="border-t border-gray-100">
                                <td className="px-2 py-1 text-gray-400">{ci + 1}</td>
                                <td className="px-2 py-1 text-right font-mono">{c.chunk_start_s.toFixed(0)}s</td>
                                <td className="px-2 py-1 text-right font-mono">{c.chunk_duration_s.toFixed(0)}s</td>
                                <td className="px-2 py-1 text-right font-mono">{c.processing_s.toFixed(2)}s</td>
                                <td className={`px-2 py-1 text-right font-mono font-bold ${c.rtf < 1 ? 'text-green-700' : 'text-red-700'}`}>
                                  {c.rtf.toFixed(3)}
                                </td>
                                <td className="px-2 py-1 text-right font-mono text-gray-500">{c.total_elapsed_s.toFixed(1)}s</td>
                                <td className="px-2 py-1 text-right font-mono">{c.words}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  )}

                  {/* Přepis text */}
                  <div className="bg-gray-50 rounded p-2">
                    <p className="text-xs text-gray-500 mb-1 font-medium">Přepis modelu</p>
                    <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap">{t.transcript}</p>
                  </div>

                  {/* Referenční text */}
                  {t.reference && (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-gray-400 hover:text-gray-600 select-none">
                        Referenční text (titulky YouTube)
                      </summary>
                      <p className="mt-1 bg-green-50 border border-green-100 rounded p-2 text-gray-600 italic whitespace-pre-wrap">{t.reference}</p>
                    </details>
                  )}
                </div>
              )
            })()
          : <div>
              {showTimestamps && transcriptTs
                ? <pre className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap font-mono min-h-12">{transcriptTs}</pre>
                : <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap min-h-12">
                    {transcript
                      ? transcript
                      : <span className="text-gray-400 italic">
                          {isActive
                            ? 'čeká na přepis...'
                            : job.evaluation_mode === 'synthetic'
                              ? 'Syntetický mód — přepis se negeneruje'
                              : allTranscripts.length === 0
                                ? 'Klikni ↻ Načíst přepisy'
                                : 'žádný přepis'}
                        </span>
                    }
                  </p>
              }
            </div>
        }
      </div>}

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


function StatBox({ label, value, sub, color = '' }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="bg-gray-50 border border-gray-200 rounded px-3 py-2 text-center">
      <p className="text-xs text-gray-400">{label}</p>
      <p className={`text-base font-bold font-mono ${color || 'text-gray-800'}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  )
}
