import { useEffect, useRef, useState } from 'react'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts'
import { api } from '../api/client'
import type { LibraryItem, ModelDescriptor, TuningJobStatus, TuningTrialResult } from '../types'
import { WerBadge } from '../components/WerBadge'

type Strategy = 'grid' | 'ablation' | 'random'

// Parametry dostupné pro tuning whisper_cpp
const WHISPER_PARAM_DEFS = [
  { name: 'beam_size',      label: 'Beam size',       type: 'int',  values: [1, 2, 3, 5, 8],       default: 5 },
  { name: 'best_of',        label: 'Best of',         type: 'int',  values: [1, 2, 3, 5],           default: 5 },
  { name: 'threads',        label: 'Vlákna CPU',      type: 'int',  values: [2, 4, 6, 8],           default: 4 },
  { name: 'no_fallback',    label: 'Bez fallbacku',   type: 'bool', values: [true, false],          default: true },
  { name: 'chunk_seconds',  label: 'Chunk délka (s)', type: 'int',  values: [10, 15, 20, 30, 45, 60], default: 30 },
  { name: 'initial_prompt', label: 'Initial prompt',  type: 'str',  values: ['', 'Rozhovor v češtině:', 'Toto je rozhovor v češtině o technologii.', 'Dobrý den,'], default: '' },
]

export function TuningPage() {
  const [library, setLibrary] = useState<LibraryItem[]>([])
  const [registry, setRegistry] = useState<ModelDescriptor[]>([])
  const [jobs, setJobs] = useState<TuningJobStatus[]>([])
  const [selectedModel, setSelectedModel] = useState('whisper_cpp_small')
  const [selectedVideos, setSelectedVideos] = useState<string[]>([])
  const [strategy, setStrategy] = useState<Strategy>('ablation')
  const [sampleSeconds, setSampleSeconds] = useState(60)
  const [maxTrials, setMaxTrials] = useState(16)
  const [label, setLabel] = useState('')
  const [selectedJob, setSelectedJob] = useState<TuningJobStatus | null>(null)
  const [msg, setMsg] = useState('')
  // Výběr hodnot pro každý parametr
  const [paramValues, setParamValues] = useState<Record<string, Set<unknown>>>(() =>
    Object.fromEntries(WHISPER_PARAM_DEFS.map(p => [p.name, new Set([p.default])]))
  )
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    Promise.all([api.library.list(), api.models.registry(), api.tuning.listJobs()])
      .then(([lib, reg, j]) => { setLibrary(lib); setRegistry(reg); setJobs(j) })
  }, [])

  function toggleParamValue(paramName: string, value: unknown) {
    setParamValues(prev => {
      const next = new Set(prev[paramName])
      next.has(value) ? next.delete(value) : next.add(value)
      if (next.size === 0) next.add(value) // vždy alespoň jedna hodnota
      return { ...prev, [paramName]: next }
    })
  }

  function countTrials(): number {
    if (strategy === 'ablation') {
      let n = 1 // baseline
      for (const p of WHISPER_PARAM_DEFS) {
        if (p.name === 'chunk_seconds') {
          n += (paramValues[p.name]?.size ?? 1) - 1
        } else {
          n += Math.max(0, (paramValues[p.name]?.size ?? 1) - 1)
        }
      }
      return n
    }
    if (strategy === 'grid') {
      return WHISPER_PARAM_DEFS.reduce((acc, p) => acc * (paramValues[p.name]?.size ?? 1), 1)
    }
    return maxTrials
  }

  async function startTuning() {
    if (!selectedVideos.length) { setMsg('Vyber alespoň jedno video.'); return }
    setMsg('')
    const paramSpace = WHISPER_PARAM_DEFS
      .filter(p => p.name !== 'chunk_seconds')
      .map(p => ({ name: p.name, values: [...(paramValues[p.name] ?? [p.default])] }))
    const chunkValues = [...(paramValues['chunk_seconds'] ?? [30])]
    paramSpace.push({ name: 'chunk_seconds', values: chunkValues })

    const baseline: Record<string, unknown> = {}
    for (const p of WHISPER_PARAM_DEFS) {
      baseline[p.name] = p.default
    }

    try {
      const job = await api.tuning.createJob({
        model_id: selectedModel,
        video_ids: selectedVideos,
        sample_seconds: sampleSeconds,
        strategy,
        max_trials: maxTrials,
        param_space: paramSpace,
        baseline_params: baseline,
        label: label || undefined,
      })
      setJobs(prev => [job, ...prev])
      setSelectedJob(job)
      startPolling(job.job_id)
    } catch (e: any) {
      setMsg(`Chyba: ${e.message}`)
    }
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

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Tuning — hledání nejlepšího nastavení</h1>
      <p className="text-sm text-gray-500">
        Automaticky prochází kombinace parametrů modelu a hledá nejlepší poměr WER ↔ RTF.
        Výsledky zobrazí Pareto frontier — kombinace kde nelze zlepšit jedno bez zhoršení druhého.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Model */}
        <div className="bg-white rounded border border-gray-200 p-4 space-y-2">
          <h2 className="font-semibold text-sm text-gray-700">Model</h2>
          {whisperModels.map(m => (
            <label key={m.model_id} className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="radio" name="model" value={m.model_id}
                checked={selectedModel === m.model_id}
                onChange={() => setSelectedModel(m.model_id)} />
              <span>{m.label}</span>
            </label>
          ))}
          <p className="text-xs text-gray-400 pt-1">Tuning aktuálně podporuje whisper.cpp modely.</p>
        </div>

        {/* Videa */}
        <div className="bg-white rounded border border-gray-200 p-4 space-y-1">
          <h2 className="font-semibold text-sm text-gray-700 mb-2">Evaluační video</h2>
          <div className="space-y-1 max-h-52 overflow-y-auto">
            {library.filter(v => v.subtitles_local).map(v => (
              <label key={v.video_id} className="flex items-center gap-2 text-xs cursor-pointer">
                <input type="checkbox" checked={selectedVideos.includes(v.video_id)}
                  onChange={() => setSelectedVideos(prev =>
                    prev.includes(v.video_id) ? prev.filter(x => x !== v.video_id) : [...prev, v.video_id]
                  )} />
                <span className="truncate" title={v.title}>{v.title}</span>
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
              ] as [Strategy, string][]).map(([s, label]) => (
                <label key={s} className="flex items-center gap-2 text-xs cursor-pointer">
                  <input type="radio" name="strategy" value={s}
                    checked={strategy === s} onChange={() => setStrategy(s)} />
                  {label}
                </label>
              ))}
            </div>
          </div>
          <div className="flex gap-3">
            <div>
              <label className="text-xs text-gray-500">Délka klipu (s)</label>
              <input type="number" value={sampleSeconds} min={20} max={300}
                onChange={e => setSampleSeconds(+e.target.value)}
                className="border rounded px-2 py-1 text-sm w-20 block mt-0.5" />
            </div>
            {strategy === 'random' && (
              <div>
                <label className="text-xs text-gray-500">Max triálů</label>
                <input type="number" value={maxTrials} min={4} max={50}
                  onChange={e => setMaxTrials(+e.target.value)}
                  className="border rounded px-2 py-1 text-sm w-20 block mt-0.5" />
              </div>
            )}
          </div>
          <div>
            <label className="text-xs text-gray-500">Popis</label>
            <input value={label} onChange={e => setLabel(e.target.value)}
              placeholder="volitelný popis..." className="border rounded px-2 py-1 text-sm w-full mt-0.5" />
          </div>
        </div>
      </div>

      {/* Parametrický prostor */}
      <div className="bg-white rounded border border-gray-200 p-4 space-y-4">
        <h2 className="font-semibold text-sm text-gray-700">Parametrický prostor</h2>
        <div className="space-y-3">
          {WHISPER_PARAM_DEFS.map(p => (
            <div key={p.name} className="flex items-start gap-4">
              <div className="w-36 shrink-0 text-xs font-medium text-gray-600 pt-1">{p.label}</div>
              <div className="flex flex-wrap gap-1.5">
                {p.values.map(v => {
                  const selected = paramValues[p.name]?.has(v) ?? false
                  const isDefault = v === p.default
                  return (
                    <button key={String(v)}
                      onClick={() => toggleParamValue(p.name, v)}
                      className={`px-2.5 py-1 rounded text-xs font-mono border transition-colors ${
                        selected
                          ? 'bg-blue-600 text-white border-blue-600'
                          : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
                      }`}
                    >
                      {p.type === 'bool' ? (v ? 'true' : 'false') : String(v)}
                      {isDefault && <span className="ml-1 opacity-60 text-xs">●</span>}
                    </button>
                  )
                })}
              </div>
              <div className="text-xs text-gray-400 pt-1">
                {paramValues[p.name]?.size ?? 1} hodnot{(paramValues[p.name]?.size ?? 1) > 1 ? 'y' : 'a'}
              </div>
            </div>
          ))}
        </div>
        <div className="flex items-center gap-4 pt-2 border-t border-gray-100">
          <div className="text-sm text-gray-700">
            Vygeneruje <strong>{trialCount}</strong> kombinac{trialCount === 1 ? 'i' : trialCount < 5 ? 'e' : 'í'}.
            Odhadovaný čas: <strong>~{Math.round(trialCount * sampleSeconds * 0.6 / 60)} min</strong>
            <span className="text-gray-400 ml-1">(RTF≈0.5)</span>
          </div>
          <button onClick={startTuning}
            className="ml-auto bg-blue-600 hover:bg-blue-700 text-white px-6 py-2 rounded text-sm font-medium">
            ▶ Spustit tuning
          </button>
          {msg && <span className="text-sm text-red-500">{msg}</span>}
        </div>
        <p className="text-xs text-gray-400">● = výchozí hodnota (baseline). Vyber více hodnot pro sweep.</p>
      </div>

      {/* Historie jobů */}
      {jobs.length > 0 && (
        <div className="bg-white rounded border border-gray-200">
          <div className="px-4 py-3 border-b border-gray-100">
            <h2 className="font-semibold text-sm text-gray-700">Historie tuning jobů</h2>
          </div>
          <div className="divide-y divide-gray-100">
            {jobs.map(j => (
              <button key={j.job_id}
                onClick={() => { setSelectedJob(j); if (j.status === 'running') startPolling(j.job_id) }}
                className={`w-full text-left px-4 py-2.5 flex items-center gap-4 hover:bg-gray-50 text-sm ${selectedJob?.job_id === j.job_id ? 'bg-blue-50' : ''}`}>
                <span className={`w-2 h-2 rounded-full shrink-0 ${
                  j.status === 'completed' ? 'bg-green-500' :
                  j.status === 'running' ? 'bg-blue-500 animate-pulse' :
                  j.status === 'failed' ? 'bg-red-500' : 'bg-gray-400'
                }`} />
                <span className="font-mono text-xs text-gray-400 w-32 shrink-0">{j.job_id.slice(-12)}</span>
                <span className="text-gray-700">{j.label || j.model_id}</span>
                <span className="text-gray-400 text-xs">{j.completed_trials}/{j.total_trials} triálů</span>
                {j.best_trial_idx != null && j.results[j.best_trial_idx] && (
                  <span className="ml-auto text-xs">
                    🏆 WER {((j.results[j.best_trial_idx].wer ?? 0) * 100).toFixed(1)} %
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Detail vybraného jobu */}
      {selectedJob && <TuningJobDetail job={selectedJob} />}
    </div>
  )
}

function TuningJobDetail({ job }: { job: TuningJobStatus }) {
  const best = job.best_trial_idx != null ? job.results[job.best_trial_idx] : null
  const sorted = [...job.results].filter(r => r.wer != null).sort((a, b) => (a.wer ?? 99) - (b.wer ?? 99))

  const scatterData = job.results
    .filter(r => r.wer != null && r.rtf != null)
    .map(r => ({
      x: r.rtf!,
      y: +(r.wer! * 100).toFixed(2),
      label: Object.entries(r.params)
        .filter(([k]) => k !== 'chunk_seconds')
        .map(([k, v]) => `${k}=${v}`)
        .join(', ') + ` chunk=${r.chunk_seconds}s`,
      isPareto: r.is_pareto,
      isBest: r.trial_idx === job.best_trial_idx,
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
          {job.status === 'running' && (
            <div className="w-32 h-2 bg-gray-200 rounded overflow-hidden">
              <div className="h-full bg-blue-500 transition-all"
                style={{ width: `${Math.round(job.completed_trials / job.total_trials * 100)}%` }} />
            </div>
          )}
        </div>

        {best && (
          <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded">
            <div className="text-xs font-semibold text-green-800 mb-2">🏆 Nejlepší konfigurace (nejnižší WER, RTF ≤ 1.2)</div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(best.params).map(([k, v]) => (
                <span key={k} className="bg-white border border-green-200 rounded px-2 py-0.5 text-xs font-mono">
                  <span className="text-gray-500">{k}=</span><strong>{String(v)}</strong>
                </span>
              ))}
            </div>
            <div className="flex gap-4 mt-2 text-xs">
              <WerBadge value={best.wer} />
              {best.cer != null && <span className="text-gray-600">CER {(best.cer * 100).toFixed(1)} %</span>}
              {best.rtf != null && <span className={best.rtf > 1 ? 'text-red-500' : 'text-green-600'}>RTF {best.rtf.toFixed(2)}</span>}
              {best.latency_ms != null && <span className="text-gray-500">{best.latency_ms.toFixed(0)} ms latence</span>}
            </div>
          </div>
        )}
      </div>

      {/* Pareto scatter */}
      {scatterData.length > 0 && (
        <div className="bg-white rounded border border-gray-200 p-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-1">Pareto frontier — WER vs RTF</h3>
          <p className="text-xs text-gray-400 mb-3">
            Zlaté body = Pareto optimální (nelze zlepšit WER bez zhoršení RTF a naopak).
            RTF &lt; 1.0 = stíhá živý přepis.
          </p>
          <ResponsiveContainer width="100%" height={280}>
            <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="x" name="RTF" unit="" label={{ value: 'RTF', position: 'insideBottom', offset: -10 }} type="number" />
              <YAxis dataKey="y" name="WER" unit="%" label={{ value: 'WER %', angle: -90, position: 'insideLeft' }} />
              <ReferenceLine x={1.0} stroke="#ef4444" strokeDasharray="4 4" label={{ value: 'RTF=1', position: 'top', fontSize: 10, fill: '#ef4444' }} />
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

      {/* Tabulka výsledků */}
      {sorted.length > 0 && (
        <div className="bg-white rounded border border-gray-200 overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-gray-500 uppercase">
              <tr>
                <th className="px-3 py-2 text-center">#</th>
                <th className="px-3 py-2 text-left">Parametry</th>
                <th className="px-3 py-2 text-center">WER</th>
                <th className="px-3 py-2 text-center">CER</th>
                <th className="px-3 py-2 text-center">WER norm.</th>
                <th className="px-3 py-2 text-center">RTF</th>
                <th className="px-3 py-2 text-center">Latence</th>
                <th className="px-3 py-2 text-center">Pareto</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, rank) => (
                <tr key={r.trial_idx}
                  className={`border-t border-gray-100 ${r.trial_idx === job.best_trial_idx ? 'bg-green-50' : 'hover:bg-gray-50'}`}>
                  <td className="px-3 py-2 text-center text-gray-400 font-mono">
                    {rank === 0 ? '🏆' : rank + 1}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(r.params).map(([k, v]) => (
                        <span key={k} className="bg-gray-100 rounded px-1.5 py-0.5 font-mono text-gray-700">
                          {k}=<strong>{String(v)}</strong>
                        </span>
                      ))}
                    </div>
                    {r.error && <div className="text-red-500 text-xs mt-1">{r.error}</div>}
                  </td>
                  <td className="px-3 py-2 text-center"><WerBadge value={r.wer} label="" /></td>
                  <td className="px-3 py-2 text-center"><WerBadge value={r.cer} label="" /></td>
                  <td className="px-3 py-2 text-center"><WerBadge value={r.wer_normalized} label="" /></td>
                  <td className="px-3 py-2 text-center font-mono">
                    {r.rtf != null
                      ? <span className={r.rtf > 1 ? 'text-red-500 font-bold' : 'text-green-600'}>{r.rtf.toFixed(3)}</span>
                      : '–'}
                  </td>
                  <td className="px-3 py-2 text-center font-mono text-gray-600">
                    {r.latency_ms != null ? `${r.latency_ms.toFixed(0)}ms` : '–'}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {r.is_pareto ? <span className="text-yellow-600">★</span> : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Čekání na první výsledky */}
      {job.status === 'running' && job.results.length === 0 && (
        <div className="text-center text-sm text-gray-400 py-6">
          Probíhá první trial… <span className="animate-pulse">⌛</span>
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
