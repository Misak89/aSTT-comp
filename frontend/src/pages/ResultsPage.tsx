import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { api } from '../api/client'
import type { RunDetail, RunResult, SourceMetric } from '../types'
import { WerBadge } from '../components/WerBadge'
import { WerDiff } from '../components/WerDiff'

export function ResultsPage() {
  const [params] = useSearchParams()
  const runId = params.get('run') || ''
  const [runIdInput, setRunIdInput] = useState(runId)
  const [run, setRun] = useState<RunDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => { if (runId) loadRun(runId) }, [runId])

  async function loadRun(id: string) {
    setError('')
    try {
      const data = await api.runs.get(id)
      setRun(data)
    } catch (e: any) {
      setError(`Run "${id}" nenalezen. ${e.message}`)
      setRun(null)
    }
  }

  const scatterData = run?.results.map(r => ({
    name: `${r.model_id}/${r.setting_id}`,
    wer: r.aggregate.wer != null ? +(r.aggregate.wer * 100).toFixed(1) : null,
    latency: r.aggregate.latency_ms,
    ram: r.aggregate.ram_mb,
    rtf: r.aggregate.rtf,
  })).filter(d => d.wer != null) || []

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Výsledky</h1>

      {/* Načtení runu */}
      <div className="flex gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">Run ID</label>
          <input value={runIdInput} onChange={e => setRunIdInput(e.target.value)}
            placeholder="run_20260320_..." className="border rounded px-2 py-1 text-sm w-72" />
        </div>
        <button onClick={() => loadRun(runIdInput)}
          className="bg-blue-600 text-white px-4 py-1.5 rounded text-sm">
          Načíst
        </button>
        {error && <span className="text-sm text-red-500">{error}</span>}
      </div>

      {run && (
        <>
          {/* Metadata runu */}
          <div className="bg-white rounded border border-gray-200 p-4 text-sm text-gray-600 flex gap-6 flex-wrap">
            <span><strong>Run:</strong> {run.run_id}</span>
            <span><strong>Čas:</strong> {run.created_at_utc}</span>
            <span><strong>Clip:</strong> {run.sample_seconds}s</span>
            <span><strong>Zdroje:</strong> {run.sources.length}</span>
            <span><strong>Výsledků:</strong> {run.results.length}</span>
          </div>

          {/* Tabulka výsledků */}
          <ResultsTable results={run.results} />

          {/* Grafy */}
          {scatterData.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <ChartCard title="WER vs Latence (ms)">
                <ScatterChart>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="latency" name="Latence ms" unit="ms" />
                  <YAxis dataKey="wer" name="WER" unit="%" />
                  <Tooltip cursor={{ strokeDasharray: '3 3' }} />
                  <Scatter data={scatterData} fill="#3b82f6" name="modely" />
                </ScatterChart>
              </ChartCard>
              <ChartCard title="WER vs RAM (MB)">
                <ScatterChart>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="ram" name="RAM" unit="MB" />
                  <YAxis dataKey="wer" name="WER" unit="%" />
                  <Tooltip cursor={{ strokeDasharray: '3 3' }} />
                  <Scatter data={scatterData} fill="#10b981" name="modely" />
                </ScatterChart>
              </ChartCard>
            </div>
          )}

          {/* Top 3 doporučení */}
          <Recommendation results={run.results} />
        </>
      )}

      {!run && !error && (
        <p className="text-gray-400 text-sm">Zadej Run ID z Benchmark stránky.</p>
      )}
    </div>
  )
}

function ResultsTable({ results }: { results: RunResult[] }) {
  const [expanded, setExpanded] = useState<string | null>(null)

  return (
    <div className="bg-white rounded border border-gray-200 overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
          <tr>
            <th className="px-4 py-2 text-left">Model</th>
            <th className="px-4 py-2 text-left">Nastavení</th>
            <th className="px-4 py-2">WER</th>
            <th className="px-4 py-2">CER</th>
            <th className="px-4 py-2">Latence</th>
            <th className="px-4 py-2">RTF</th>
            <th className="px-4 py-2">CPU%</th>
            <th className="px-4 py-2">RAM MB</th>
            <th className="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {results.map(r => {
            const key = `${r.model_id}-${r.setting_id}`
            const isOpen = expanded === key
            const hasDiff = r.source_metrics?.some(
              (s: SourceMetric) => s.transcript && s.reference_text
            )
            return (
              <>
                <tr key={key} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs">{r.model_id}</td>
                  <td className="px-4 py-2 text-gray-600">{r.setting_label}</td>
                  <td className="px-4 py-2 text-center"><WerBadge value={r.aggregate.wer} label="" /></td>
                  <td className="px-4 py-2 text-center"><WerBadge value={r.aggregate.cer} label="" /></td>
                  <td className="px-4 py-2 text-center text-xs font-mono">
                    {r.aggregate.latency_ms != null ? `${r.aggregate.latency_ms.toFixed(0)}ms` : '–'}
                  </td>
                  <td className="px-4 py-2 text-center text-xs font-mono">
                    {r.aggregate.rtf != null
                      ? <span className={r.aggregate.rtf > 1 ? 'text-red-500' : 'text-green-600'}>
                          {r.aggregate.rtf.toFixed(2)}
                        </span>
                      : '–'}
                  </td>
                  <td className="px-4 py-2 text-center text-xs">{r.aggregate.cpu_percent?.toFixed(0) ?? '–'}%</td>
                  <td className="px-4 py-2 text-center text-xs">{r.aggregate.ram_mb?.toFixed(0) ?? '–'}</td>
                  <td className="px-4 py-2 text-center">
                    {hasDiff && (
                      <button
                        onClick={() => setExpanded(isOpen ? null : key)}
                        className="text-xs text-blue-600 hover:underline whitespace-nowrap"
                      >
                        {isOpen ? '▲ skrýt' : '▼ diff'}
                      </button>
                    )}
                  </td>
                </tr>
                {isOpen && r.source_metrics?.map((s: SourceMetric, si: number) => (
                  s.transcript && s.reference_text ? (
                    <tr key={`${key}-diff-${si}`} className="border-t border-blue-50 bg-blue-50/30">
                      <td colSpan={9} className="px-4 py-3">
                        <div className="flex items-center gap-4 text-xs text-gray-500 mb-2 font-medium flex-wrap">
                          <span>
                            Zdroj {si + 1}{s.video_id ? ` — ${s.video_id}` : ''}
                            {s.clip_start_seconds != null ? ` @ ${s.clip_start_seconds}s` : ''}
                          </span>
                          {/* Req 7: doba přepisu vs délka audia */}
                          {(s.engine_elapsed_seconds != null || s.clip_seconds != null) && (
                            <span className="bg-white border border-gray-200 rounded px-2 py-0.5 font-mono">
                              {s.engine_elapsed_seconds != null
                                ? `přepis ${s.engine_elapsed_seconds.toFixed(1)}s`
                                : ''}
                              {s.engine_elapsed_seconds != null && s.clip_seconds != null ? ' / ' : ''}
                              {s.clip_seconds != null ? `audio ${s.clip_seconds.toFixed(0)}s` : ''}
                              {s.rtf != null && (
                                <span className={`ml-1 font-bold ${s.rtf > 1 ? 'text-red-500' : 'text-green-600'}`}>
                                  RTF {s.rtf.toFixed(2)}
                                </span>
                              )}
                            </span>
                          )}
                        </div>
                        <WerDiff
                          reference={s.reference_text}
                          transcript={s.transcript}
                          wer={s.wer}
                        />
                      </td>
                    </tr>
                  ) : null
                ))}
              </>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Recommendation({ results }: { results: RunResult[] }) {
  const ranked = [...results]
    .filter(r => r.aggregate.rtf == null || r.aggregate.rtf <= 1.2) // eliminuj příliš pomalé
    .sort((a, b) => {
      const wa = a.aggregate.wer ?? 99; const wb = b.aggregate.wer ?? 99
      return wa - wb
    })
    .slice(0, 3)

  if (!ranked.length) return null

  return (
    <div className="bg-green-50 border border-green-200 rounded p-4">
      <h3 className="font-semibold text-sm text-green-800 mb-3">Top 3 doporučení (WER, RTF &le; 1.2)</h3>
      <div className="space-y-2">
        {ranked.map((r, i) => (
          <div key={`${r.model_id}-${r.setting_id}`} className="flex items-center gap-3 text-sm">
            <span className="font-bold text-green-700 w-4">{i + 1}.</span>
            <span className="font-mono text-xs bg-white px-2 py-0.5 rounded border border-green-200">
              {r.model_id} / {r.setting_id}
            </span>
            <WerBadge value={r.aggregate.wer} />
            {r.aggregate.latency_ms != null && (
              <span className="text-gray-500 text-xs">{r.aggregate.latency_ms.toFixed(0)}ms latence</span>
            )}
            {r.aggregate.ram_mb != null && (
              <span className="text-gray-500 text-xs">{r.aggregate.ram_mb.toFixed(0)}MB RAM</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded border border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3">{title}</h3>
      <ResponsiveContainer width="100%" height={250}>
        {children as React.ReactElement}
      </ResponsiveContainer>
    </div>
  )
}
