import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { BenchmarkJobStatus, BenchmarkOptions, LibraryItem, ModelDescriptor, RunDetail } from '../types'
import { videoLabel } from '../utils'
import { StatusBadge } from '../components/StatusBadge'
import { LiveJobPanel } from '../components/LiveJobPanel'
import { MicSession } from '../components/MicSession'
import { ModelParamsForm } from '../components/ModelParamsForm'
import { ActionButton, FieldHintLabel } from '../components/UiPrimitives'
import { WorkflowGuide, type WorkflowStep } from '../components/WorkflowGuide'
import { formatDateTimeDayMonthHm } from '../lib/time'
import { LateMicPage } from './LateMicPage'

type Tab = 'benchmark' | 'mic' | 'latemic'
type EvalMode = 'synthetic' | 'streaming' | 'real'
type VideoSortKey = 'language' | 'duration' | 'title' | 'upload_date' | 'genre' | 'view_count'

const VIDEO_SORT_LABELS: Array<{ key: VideoSortKey; label: string }> = [
  { key: 'language', label: 'Jazyk' },
  { key: 'duration', label: 'Délka' },
  { key: 'title', label: 'Název' },
  { key: 'upload_date', label: 'Datum' },
  { key: 'genre', label: 'Žánr' },
  { key: 'view_count', label: 'Zhlédnutí' },
]

const VIDEO_SORT_DEFAULT_DIR: Record<VideoSortKey, 'asc' | 'desc'> = {
  language: 'asc',
  duration: 'asc',
  title: 'asc',
  upload_date: 'desc',
  genre: 'asc',
  view_count: 'desc',
}

const BENCHMARK_WORKFLOWS: Record<Tab, WorkflowStep[]> = {
  benchmark: [
    { label: 'Zdroje', detail: 'Vyber videa a jejich pořadí.' },
    { label: 'Modely', detail: 'Zvol STT modely a profil nastavení.' },
    { label: 'Parametry', detail: 'Délka klipu, seed a režim evaluace.' },
    { label: 'Start', detail: 'Spusť job a sleduj stav.' },
    { label: 'Výsledky', detail: 'Otevři porovnání runu.' },
  ],
  mic: [
    { label: 'Zdroj', detail: 'Volný mic nebo referenční video.' },
    { label: 'Audio loop', detail: 'Pauza, opakování a párovací balíček.' },
    { label: 'Mikrofon', detail: 'Zařízení, model a parametry.' },
    { label: 'Sekvence', detail: 'Pořadí modelů a ladicí varianty.' },
    { label: 'Start', detail: 'Spusť sekvenci nebo ladění.' },
  ],
  latemic: [
    { label: 'Mic důkaz', detail: 'Ověř skutečný vstup z mikrofonu.' },
    { label: 'Segmenty', detail: 'Zpoždění, délky a pauzy.' },
    { label: 'Modely', detail: 'Vyber modely a jejich parametry.' },
    { label: 'Plán', detail: 'Model × lag × opakování.' },
    { label: 'Start', detail: 'Nahraj segmenty a vyhodnoť lag.' },
  ],
}

function benchmarkTabFromPath(pathname: string): Tab {
  const normalized = pathname.replace(/\/+$/, '')
  if (normalized === '/benchmark/latemic') return 'latemic'
  return normalized === '/benchmark/mic' ? 'mic' : 'benchmark'
}

export function BenchmarkPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>(() => benchmarkTabFromPath(location.pathname))
  const [options, setOptions] = useState<BenchmarkOptions | null>(null)
  const [library, setLibrary] = useState<LibraryItem[]>([])
  const [registry, setRegistry] = useState<ModelDescriptor[]>([])
  const [selectedVideos, setSelectedVideos] = useState<string[]>([])
  const [selectedModels, setSelectedModels] = useState<string[]>(['whisper_cpp_small'])
  const [selectedSettings, setSelectedSettings] = useState<string[]>(['balanced'])
  const [videoSortOrder, setVideoSortOrder] = useState<VideoSortKey[]>(['language', 'duration', 'title'])
  const [videoSortDirMap, setVideoSortDirMap] = useState<Record<VideoSortKey, 'asc' | 'desc'>>(
    () => ({ ...VIDEO_SORT_DEFAULT_DIR }),
  )
  const [clipSeconds, setClipSeconds] = useState(120)
  const [clipSeed, setClipSeed] = useState<number | ''>('')
  const [label, setLabel] = useState('')
  const [evalMode, setEvalMode] = useState<EvalMode>('streaming')
  const [perModelParams, setPerModelParams] = useState<Record<string, Record<string, unknown>>>({})
  const [jobs, setJobs] = useState<BenchmarkJobStatus[]>([])
  const [msg, setMsg] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const micModels = registry.filter(m => m.supports_microphone)
  const visibleLibrary = useMemo(
    () => library.filter(item => item.visible_in_menus !== false),
    [library],
  )

  const sortedLibrary = useMemo(() => {
    const rows = [...visibleLibrary]
    rows.sort((a, b) => {
      const activeOrder: VideoSortKey[] = videoSortOrder.length > 0 ? videoSortOrder : ['language', 'duration', 'title']
      for (const key of activeOrder) {
        let cmp = 0
        if (key === 'language') {
          const aIsCz = isCzechLanguage(a.language)
          const bIsCz = isCzechLanguage(b.language)
          if (aIsCz !== bIsCz) cmp = aIsCz ? -1 : 1
          else cmp = (a.language || '').localeCompare(b.language || '', 'cs')
        } else if (key === 'duration') {
          const aDur = typeof a.duration_seconds === 'number' ? a.duration_seconds : Number.POSITIVE_INFINITY
          const bDur = typeof b.duration_seconds === 'number' ? b.duration_seconds : Number.POSITIVE_INFINITY
          cmp = aDur === bDur ? 0 : (aDur < bDur ? -1 : 1)
        } else if (key === 'title') {
          cmp = (a.title || '').localeCompare(b.title || '', 'cs')
        } else if (key === 'upload_date') {
          const aDate = a.upload_date ?? a.added_at ?? ''
          const bDate = b.upload_date ?? b.added_at ?? ''
          cmp = aDate < bDate ? -1 : aDate > bDate ? 1 : 0
        } else if (key === 'genre') {
          cmp = (a.genre || '').localeCompare(b.genre || '', 'cs')
        } else if (key === 'view_count') {
          const aViews = a.view_count ?? -1
          const bViews = b.view_count ?? -1
          cmp = aViews === bViews ? 0 : (aViews < bViews ? -1 : 1)
        }
        if (cmp !== 0) return videoSortDirMap[key] === 'asc' ? cmp : -cmp
      }
      return 0
    })
    return rows
  }, [visibleLibrary, videoSortOrder, videoSortDirMap])

  useEffect(() => {
    const visibleIds = new Set(visibleLibrary.map(v => v.video_id))
    setSelectedVideos(prev => prev.filter(id => visibleIds.has(id)))
  }, [visibleLibrary])

  useEffect(() => {
    Promise.all([api.benchmark.options(), api.library.list(), api.models.registry()])
      .then(([opts, lib, reg]) => { setOptions(opts); setLibrary(lib); setRegistry(reg) })
    loadJobs()
  }, [])

  useEffect(() => {
    setTab(benchmarkTabFromPath(location.pathname))
  }, [location.pathname])

  async function loadJobs() {
    const { jobs } = await api.benchmark.listJobs()
    setJobs(jobs)
    // Poll pokud běží job
    const running = jobs.some(j => j.status === 'pending' || j.status === 'running')
    if (running && !pollRef.current) {
      pollRef.current = setInterval(async () => {
        const { jobs: fresh } = await api.benchmark.listJobs()
        setJobs(fresh)
        if (!fresh.some(j => j.status === 'pending' || j.status === 'running')) {
          clearInterval(pollRef.current!); pollRef.current = null
        }
      }, 2000)
    }
  }

  function toggleItem<T>(arr: T[], item: T): T[] {
    return arr.includes(item) ? arr.filter(x => x !== item) : [...arr, item]
  }

  function toggleVideoSortPriority(key: VideoSortKey) {
    setVideoSortOrder(prev => (
      prev.includes(key)
        ? prev.filter(k => k !== key)
        : [...prev, key]
    ))
  }

  function toggleVideoSortDir(key: VideoSortKey) {
    setVideoSortDirMap(prev => ({ ...prev, [key]: prev[key] === 'asc' ? 'desc' : 'asc' }))
  }

  async function startBenchmark() {
    if (!selectedVideos.length) {
      setMsg('Benchmark potřebuje alespoň jedno video. Pokud chceš jen live mikrofon bez videa, použij záložku „Mikrofon“.')
      return
    }
    if (!selectedModels.length) { setMsg('Vyber alespoň jeden model.'); return }
    setMsg('')
    try {
      await api.benchmark.createJob({
        video_ids: selectedVideos,
        model_ids: selectedModels,
        setting_ids: selectedSettings,
        sample_seconds: clipSeconds,
        clip_seed: clipSeed === '' ? undefined : clipSeed,
        label: label || undefined,
        evaluation_mode: evalMode,
        model_params: Object.keys(perModelParams).length > 0 ? perModelParams : undefined,
      })
      await loadJobs()
    } catch (e: any) { setMsg(`Chyba: ${e.message}`) }
  }

  async function cancelJob(id: string) {
    await api.benchmark.cancelJob(id)
    await loadJobs()
  }

  function switchTab(next: Tab) {
    setTab(next)
    setMsg('')
    navigate(next === 'mic' ? '/benchmark/mic' : next === 'latemic' ? '/benchmark/latemic' : '/benchmark')
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-bold">Benchmark</h1>
        <div className="flex gap-1 bg-gray-100 rounded p-1 text-sm">
          <button
            onClick={() => switchTab('benchmark')}
            className={`px-3 py-1 rounded ${tab === 'benchmark' ? 'bg-white shadow text-gray-900 font-medium' : 'text-gray-500 hover:text-gray-700'}`}
          >
            Benchmark
          </button>
          <button
            onClick={() => switchTab('mic')}
            className={`px-3 py-1 rounded inline-flex items-center gap-1.5 ${tab === 'mic' ? 'bg-white shadow text-gray-900 font-medium' : 'text-gray-500 hover:text-gray-700'}`}
          >
            <span className="relative inline-flex">
              <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="2" width="6" height="11" rx="3"/>
                <path d="M5 10a7 7 0 0 0 14 0"/>
                <line x1="12" y1="19" x2="12" y2="22"/>
                <line x1="9" y1="22" x2="15" y2="22"/>
              </svg>
              <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-red-500"/>
            </span>
            Mikrofon
          </button>
          <button
            onClick={() => switchTab('latemic')}
            className={`px-3 py-1 rounded inline-flex items-center gap-1.5 ${tab === 'latemic' ? 'bg-white shadow text-gray-900 font-medium' : 'text-gray-500 hover:text-gray-700'}`}
          >
            <span className="relative inline-flex">
              <svg viewBox="0 0 24 24" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9"/>
                <path d="M12 7v5l3 2"/>
              </svg>
              <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-amber-500"/>
            </span>
            Little late Mic
          </button>
        </div>
        <Link
          to="/hwflow"
          title="Dokumentace: timing sekvence, HW nároky, provozní pravidla"
          className="ml-2 px-3 py-1 rounded border border-gray-300 text-xs text-gray-500 hover:text-gray-800 hover:border-gray-400"
        >
          ? Help
        </Link>
      </div>

      <WorkflowGuide
        title={tab === 'benchmark' ? 'Workflow klasického benchmarku' : tab === 'mic' ? 'Workflow live mikrofonu' : 'Workflow Little late Mic'}
        steps={BENCHMARK_WORKFLOWS[tab]}
      />

      {tab === 'mic' && (
        micModels.length > 0
          ? <MicSession availableModels={micModels} library={visibleLibrary} />
          : <p className="text-sm text-gray-500">Žádný model nepodporuje mikrofon.</p>
      )}

      {tab === 'latemic' && (
        <LateMicPage models={registry} library={visibleLibrary} />
      )}

      {tab === 'benchmark' && <><div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Výběr videí */}
        <div className="bg-white rounded border border-gray-200 p-4">
          <h2 className="font-semibold text-sm mb-3 text-gray-700">Videa</h2>
          <div className="mb-2 flex flex-wrap gap-1 text-xs">
            {VIDEO_SORT_LABELS.map(({ key, label }) => {
              const idx = videoSortOrder.indexOf(key)
              const active = idx >= 0
              return (
                <label key={key} className={`inline-flex items-center gap-1 px-2 py-1 rounded border ${active ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-300 text-gray-600'}`}>
                  <input
                    type="checkbox"
                    checked={active}
                    onChange={() => toggleVideoSortPriority(key)}
                  />
                  <button
                    type="button"
                    onClick={() => toggleVideoSortDir(key)}
                    className="hover:underline"
                    title={`Směr řazení: ${label}`}
                  >
                    {label}{active ? ` ${idx + 1}.${videoSortDirMap[key] === 'asc' ? '▲' : '▼'}` : ''}
                  </button>
                </label>
              )
            })}
          </div>
          <div className="space-y-1 max-h-64 overflow-y-auto">
            {sortedLibrary.map(item => (
              <label key={item.video_id} className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={selectedVideos.includes(item.video_id)}
                  onChange={() => setSelectedVideos(v => toggleItem(v, item.video_id))} />
                <span className={`shrink-0 px-1 rounded font-mono font-bold text-xs ${item.language === 'cs' ? 'bg-blue-100 text-blue-700' : 'bg-orange-100 text-orange-700'}`} title={item.language === 'cs' ? 'Čeština' : item.language === 'en' ? 'Angličtina' : item.language}>{item.language.toUpperCase()}</span>
                <span className="truncate" title={item.title}>
                  {videoLabel(item.title, item.video_id)}
                  {!item.subtitles_local && <span className="text-orange-400 ml-1 text-xs">(bez titulků)</span>}
                </span>
                <span className="ml-auto text-[11px] text-gray-500 tabular-nums">
                  {formatDurationSeconds(item.duration_seconds)}
                </span>
              </label>
            ))}
            {sortedLibrary.length === 0 && <span className="text-gray-400 text-xs">Knihovna prázdná.</span>}
          </div>
        </div>

        {/* Modely */}
        <div className="bg-white rounded border border-gray-200 p-4">
          <button
            className="font-semibold text-sm mb-3 text-gray-700 hover:text-blue-600 hover:underline text-left"
            title="Otevřít adresář s modely"
            onClick={() => api.openDir.modelStore().catch(() => {})}
          >Modely ↗</button>
          <div className="space-y-1">
            {options?.models.map(m => (
              <label key={m.id} className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={selectedModels.includes(m.id)}
                  onChange={() => setSelectedModels(v => toggleItem(v, m.id))} />
                <span>{m.label}</span>
              </label>
            ))}
          </div>
          <h2 className="font-semibold text-sm mt-4 mb-2 text-gray-700">Nastavení</h2>
          <div className="space-y-1">
            {options?.settings.map(s => (
              <label key={s.id} className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={selectedSettings.includes(s.id)}
                  onChange={() => setSelectedSettings(v => toggleItem(v, s.id))} />
                <span>{s.label}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Parametry */}
        <div className="bg-white rounded border border-gray-200 p-4 space-y-3">
          <h2 className="font-semibold text-sm mb-1 text-gray-700">Parametry</h2>
          <div className="flex flex-col gap-1">
            <FieldHintLabel className="text-xs" hint="Určuje, jak se zdroj audio dat dostane do benchmarku. Streaming je nejbližší reálnému průběžnému zpracování.">
              Mód evaluace
            </FieldHintLabel>
            <select value={evalMode} onChange={e => setEvalMode(e.target.value as EvalMode)}
              title="Syntetický je rychlý test, streaming simuluje průběžné zpracování, real použije lokální soubor."
              className="border rounded px-2 py-1 text-sm">
              <option value="synthetic">Syntetický (rychlý)</option>
              <option value="streaming">Streaming (yt-dlp pipe)</option>
              <option value="real">Real (lokální soubor)</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <FieldHintLabel className="text-xs" hint="Kolik sekund z každého zdroje se použije. Kratší test je rychlejší, delší je stabilnější pro porovnání kvality.">
              Délka klipu (s)
            </FieldHintLabel>
            <input type="number" value={clipSeconds} onChange={e => setClipSeconds(+e.target.value)}
              title="Délka testovaného úseku v sekundách."
              className="border rounded px-2 py-1 text-sm w-24" min={10} max={3600} />
          </div>
          {evalMode === 'streaming' && (
            <p className="text-xs text-gray-400">Délka chunků je definována nastavením (Low latency = 15s, Balanced = 30s, High accuracy = 60s).</p>
          )}
          <div className="flex flex-col gap-1">
            <FieldHintLabel className="text-xs" hint="Seed určuje opakovatelný výběr klipu. Když ho necháš prázdný, úsek se vybere náhodně.">
              Seed
            </FieldHintLabel>
            <input type="number" value={clipSeed} onChange={e => setClipSeed(e.target.value === '' ? '' : +e.target.value)}
              title="Stejný seed pomůže zopakovat stejný testovací výběr."
              placeholder="prázdné = náhodný" className="border rounded px-2 py-1 text-sm w-32" />
          </div>
          <div className="flex flex-col gap-1">
            <FieldHintLabel className="text-xs" hint="Krátká poznámka, podle které později poznáš, proč byl test spuštěn.">
              Popis runu
            </FieldHintLabel>
            <input value={label} onChange={e => setLabel(e.target.value)}
              title="Volitelný popis se zobrazí v historii jobů a výsledcích."
              placeholder="volitelný popis" className="border rounded px-2 py-1 text-sm" />
          </div>
          <ActionButton
            onClick={startBenchmark}
            variant="start"
            title="Spustí klasický benchmark pro vybraná videa, modely a nastavení."
            className="w-full py-2 mt-2"
          >
            Spustit benchmark
          </ActionButton>
          {msg && <p className="text-sm text-red-600">{msg}</p>}
        </div>
      </div>

      {/* Req 6: Per-model params */}
      {registry.filter(d => selectedModels.includes(d.model_id) && d.params.length > 0).map(d => (
        <details key={d.model_id} className="bg-gray-800 rounded border border-gray-700 text-sm">
          <summary className="cursor-pointer px-4 py-2 text-gray-300 hover:text-white select-none font-medium">
            ⚙ Parametry: {d.label}
          </summary>
          <div className="px-4 pb-3 pt-2">
            <ModelParamsForm
              modelId={d.model_id}
              params={d.params}
              values={perModelParams[d.model_id] ?? {}}
              onChange={vals => setPerModelParams(prev => ({ ...prev, [d.model_id]: vals }))}
              compact={false}
            />
          </div>
        </details>
      ))}

      {/* Live panel pro běžící i dokončené joby (Req 1: panel se nezavírá) */}
      {jobs.filter(j => ['running', 'pending', 'completed', 'failed'].includes(j.status)).slice(0, 3).map(j => (
        <LiveJobPanel key={j.job_id} job={j} onCancel={loadJobs} />
      ))}

      {/* Seznam jobů */}
      <div className="bg-white rounded border border-gray-200">
        <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
          <h2 className="font-semibold text-sm text-gray-700">Joby</h2>
          <button onClick={loadJobs} className="text-xs text-blue-600 hover:underline">Obnovit</button>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
            <tr>
              <th className="px-4 py-2 text-left">Datum / čas</th>
              <th className="px-4 py-2 text-left">Popis</th>
              <th className="px-4 py-2 text-left">Stav / průběh</th>
              <th className="px-4 py-2 text-left">Nejlepší model</th>
              <th className="px-4 py-2 text-left">HW před startem</th>
              <th className="px-4 py-2 text-left">Poslední zpráva</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map(job => (
              <tr key={job.job_id} className="border-t border-gray-100">
                <td className="px-4 py-2 text-xs text-gray-500">
                  <div className="font-mono">{formatJobDate(job.created_at)}</div>
                  <div className="text-gray-400 font-mono text-xs">{job.job_id.slice(-8)}</div>
                </td>
                <td className="px-4 py-2 text-gray-700">{job.label || <span className="text-gray-400 italic text-xs">bez popisu</span>}</td>
                <td className="px-4 py-2">
                  <div className="flex items-center gap-1">
                    <StatusBadge status={job.status} />
                    {job.status === 'running' && job.progress_percent > 0 && (
                      <span className="text-xs font-mono text-blue-600">{job.progress_percent}%</span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-2">
                  {job.status === 'completed' && job.run_id
                    ? <BestModelBadge runId={job.run_id} />
                    : <span className="text-gray-400 text-xs">–</span>}
                </td>
                <td className="px-4 py-2 text-xs">
                  {job.pre_cpu != null || job.pre_ram_mb != null ? (
                    <span className={`font-mono ${(job.pre_cpu ?? 0) >= 20 ? 'text-orange-500' : 'text-green-700'}`}>
                      {job.pre_cpu != null ? `CPU ${job.pre_cpu}%` : ''}
                      {job.pre_cpu != null && job.pre_ram_mb != null ? ' / ' : ''}
                      {job.pre_ram_mb != null ? `RAM ${Math.round(job.pre_ram_mb)}MB` : ''}
                      {job.conditions_clean != null && (
                        <span className="ml-1 text-gray-400">
                          {job.conditions_clean ? '(čistý)' : '(zatížen)'}
                        </span>
                      )}
                    </span>
                  ) : (
                    <span className="text-gray-400">–</span>
                  )}
                </td>
                <td className="px-4 py-2 text-xs text-gray-500 max-w-xs truncate">
                  {job.error
                    ? <span className="text-red-500">{job.error.slice(0, 80)}</span>
                    : job.progress_message || '–'}
                </td>
                <td className="px-4 py-2">
                  {(job.status === 'pending' || job.status === 'running') && (
                    <ActionButton
                      onClick={() => cancelJob(job.job_id)}
                      variant="stop"
                      title="Zruší běžící nebo čekající benchmark job."
                      className="px-2 py-1 text-xs"
                    >
                      Zrušit
                    </ActionButton>
                  )}
                  {job.status === 'completed' && job.run_id && (
                    <a href={`/results?run=${job.run_id}`} className="text-xs text-blue-600 hover:underline">
                      Výsledky
                    </a>
                  )}
                </td>
              </tr>
            ))}
            {jobs.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-6 text-center text-gray-400 text-sm">Zatím žádné joby.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      </>}
    </div>
  )
}

function formatJobDate(iso: string | null | undefined): string {
  if (!iso) return '–'
  return formatDateTimeDayMonthHm(iso)
}

function isCzechLanguage(language: string | null | undefined): boolean {
  const l = String(language || '').trim().toLowerCase()
  return l === 'cs' || l.startsWith('cs-')
}

function formatDurationSeconds(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return '—'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

function BestModelBadge({ runId }: { runId: string }) {
  const [data, setData] = useState<RunDetail | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const run = await api.runs.get(runId)
      setData(run)
    } catch { /* run ještě neexistuje nebo chyba */ }
    setLoading(false)
  }, [runId])

  useEffect(() => { load() }, [load])

  if (loading) return <span className="text-gray-400 text-xs">…</span>
  if (!data) return <span className="text-gray-400 text-xs">–</span>

  // Najdi nejlepší: nejnižší WER při RTF < 1.2
  const candidates = data.results
    .filter(r => r.aggregate.wer != null && (r.aggregate.rtf == null || r.aggregate.rtf <= 1.2))
    .sort((a, b) => (a.aggregate.wer ?? 99) - (b.aggregate.wer ?? 99))

  if (!candidates.length) {
    // Všechny mají RTF > 1.2 — ukázat alespoň nejlepší WER
    const best = [...data.results].sort((a, b) => (a.aggregate.wer ?? 99) - (b.aggregate.wer ?? 99))[0]
    if (!best) return <span className="text-gray-400 text-xs">–</span>
    return (
      <div className="text-xs space-y-0.5">
        <div className="font-mono text-gray-700">{best.model_id}</div>
        <div className="text-gray-500">{best.setting_label}</div>
        {best.aggregate.wer != null && (
          <span className="text-orange-500 font-bold">WER {(best.aggregate.wer * 100).toFixed(1)} %</span>
        )}
        {best.aggregate.rtf != null && (
          <span className="ml-1 text-red-400">RTF {best.aggregate.rtf.toFixed(2)} ⚠</span>
        )}
      </div>
    )
  }

  const best = candidates[0]
  const werPct = best.aggregate.wer != null ? (best.aggregate.wer * 100).toFixed(1) : null
  const werColor = !werPct ? 'text-gray-500'
    : +werPct < 10 ? 'text-green-600'
    : +werPct < 20 ? 'text-yellow-600'
    : +werPct < 35 ? 'text-orange-500'
    : 'text-red-600'

  return (
    <div className="text-xs space-y-0.5">
      <div className="font-mono text-gray-800 font-medium">{best.model_id}</div>
      <div className="text-gray-500">{best.setting_label}</div>
      <div className="flex items-center gap-2 flex-wrap">
        {werPct && <span className={`font-bold ${werColor}`}>WER {werPct} %</span>}
        {best.aggregate.latency_ms != null && (
          <span className="text-gray-400 font-mono">{best.aggregate.latency_ms.toFixed(0)} ms</span>
        )}
        {best.aggregate.rtf != null && (
          <span className={best.aggregate.rtf > 1 ? 'text-red-500' : 'text-green-600'}>
            RTF {best.aggregate.rtf.toFixed(2)}
          </span>
        )}
      </div>
      {candidates.length > 1 && (
        <div className="text-gray-400">+{candidates.length - 1} dalších</div>
      )}
    </div>
  )
}
