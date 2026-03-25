import { useEffect, useRef, useState } from 'react'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts'
import { api } from '../api/client'
import type { LibraryItem, ModelDescriptor, TuningJobStatus, TuningTrialResult } from '../types'
import { WerBadge } from '../components/WerBadge'

type Strategy = 'grid' | 'ablation' | 'random'

// Parametry (bez initial_prompt — ten má vlastní UI)
const WHISPER_PARAM_DEFS = [
  { name: 'beam_size',     label: 'Beam size',       type: 'int',  values: [1, 2, 3, 5, 8],          default: 5 },
  { name: 'best_of',       label: 'Best of',         type: 'int',  values: [1, 2, 3, 5],              default: 5 },
  { name: 'threads',       label: 'Vlákna CPU',      type: 'int',  values: [2, 4, 6, 8],              default: 4 },
  { name: 'no_fallback',   label: 'Bez fallbacku',   type: 'bool', values: [true, false],             default: true },
  { name: 'chunk_seconds', label: 'Chunk délka (s)', type: 'int',  values: [10, 15, 20, 30, 45, 60],  default: 30 },
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
      { label: 'Obecný rozhovor', text: 'Rozhovor v češtině:' },
      { label: 'Kontext + pozdrav', text: 'Dobrý den, toto je rozhovor v češtině.' },
      { label: 'Delší kontext', text: 'Toto je rozhovor dvou lidí v českém jazyce. Mluví plynně česky.' },
    ],
  },
  {
    id: 'topic_auto', label: 'Téma z názvu videa', domain: 'auto', color: 'violet',
    prompts: [], // generováno dynamicky dle vybraného videa
  },
  {
    id: 'medicine', label: 'Medicína', domain: 'medical', color: 'red',
    prompts: [
      { label: 'Obecné lékařské vyšetření', text: 'Toto je lékařské vyšetření. Lékař hovoří s pacientem o zdravotním stavu, symptomech a léčbě.' },
      { label: 'Kardiologie', text: 'Toto je kardiologické vyšetření. Lékař kardiolog diskutuje o srdečních onemocněních, EKG, krevním tlaku a léčbě.' },
      { label: 'Neurologie', text: 'Toto je neurologické vyšetření. Neurolog hodnotí reflexy, pohybové funkce a neurologické symptomy pacienta.' },
      { label: 'Psychiatrie', text: 'Toto je psychiatrická konzultace. Psychiatr hovoří s pacientem o duševním zdraví, náladě a psychologických symptomech.' },
      { label: 'Praktický lékař', text: 'Ordinace praktického lékaře. Pacient popisuje potíže, lékař doporučuje vyšetření a léčbu.' },
      { label: 'Onkologie', text: 'Onkologická konzultace. Lékař diskutuje o diagnóze nádorového onemocnění, chemoterapii a prognóze.' },
      { label: 'Operace / chirurgie', text: 'Chirurgické pracoviště. Lékaři diskutují o operačním zákroku, přípravě pacienta a postoperační péči.' },
      { label: 'Alergologie a imunologie', text: 'Alergologická a imunologická konzultace. Lékař hodnotí alergické reakce, imunitní systém, přecitlivělost a imunoterapii pacienta.' },
    ],
  },
  {
    id: 'technology', label: 'Technologie', domain: 'tech', color: 'indigo',
    prompts: [
      { label: 'IT a software', text: 'Technický rozhovor o softwaru, programování a informatice.' },
      { label: 'Hardware a počítače', text: 'Rozhovor o počítačovém hardwaru, procesorech, grafických kartách a technologiích.' },
      { label: 'Herní průmysl', text: 'Rozhovor o videohrách, vývoji her a herním průmyslu.' },
      { label: 'Umělá inteligence', text: 'Diskuse o umělé inteligenci, strojovém učení a neuronových sítích.' },
    ],
  },
  {
    id: 'sport', label: 'Sport', domain: 'sport', color: 'green',
    prompts: [
      { label: 'Obecný sport', text: 'Sportovní rozhovor. Sportovci a trenéři diskutují o výkonu, tréninku a soutěžích.' },
      { label: 'Fotbal', text: 'Fotbalový rozhovor. Hráči a trenéři diskutují o zápasech, taktice a lize.' },
      { label: 'Esport', text: 'Rozhovor s profesionálním hráčem esportu o turnajích, strategii a týmové spolupráci.' },
    ],
  },
  {
    id: 'podcast', label: 'Podcast / pořad', domain: 'media', color: 'orange',
    prompts: [
      { label: 'Obecný podcast', text: 'Podcastový rozhovor. Moderátor diskutuje s hostem o různých tématech.' },
      { label: 'Věda a vzdělávání', text: 'Vzdělávací pořad. Odborník vysvětluje vědecká témata srozumitelně pro veřejnost.' },
      { label: 'Byznys a ekonomika', text: 'Obchodní rozhovor o ekonomice, podnikání, investicích a finančních trzích.' },
      { label: 'Politika a společnost', text: 'Politická diskuse. Politici a novináři hovoří o společenských otázkách a vládní politice.' },
    ],
  },
]

const LS_KEY = 'tuning_config_v1'

function loadConfig() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}') } catch { return {} }
}

export function TuningPage() {
  const [library, setLibrary] = useState<LibraryItem[]>([])
  const [registry, setRegistry] = useState<ModelDescriptor[]>([])
  const [jobs, setJobs] = useState<TuningJobStatus[]>([])

  const cfg = loadConfig()
  const [selectedModel, setSelectedModel] = useState<string>(cfg.selectedModel ?? 'whisper_cpp_small')
  const [selectedVideos, setSelectedVideos] = useState<string[]>(cfg.selectedVideos ?? [])
  const [strategy, setStrategy] = useState<Strategy>(cfg.strategy ?? 'ablation')
  const [sampleSeconds, setSampleSeconds] = useState<number>(cfg.sampleSeconds ?? 60)
  const [maxTrials, setMaxTrials] = useState<number>(cfg.maxTrials ?? 16)
  const [label, setLabel] = useState<string>(cfg.label ?? '')
  const [selectedJob, setSelectedJob] = useState<TuningJobStatus | null>(null)
  const [msg, setMsg] = useState('')
  // Výběr hodnot pro každý parametr
  const [paramValues, setParamValues] = useState<Record<string, Set<unknown>>>(() => {
    const saved: Record<string, unknown[]> = cfg.paramValues ?? {}
    return Object.fromEntries(
      WHISPER_PARAM_DEFS.map(p => [p.name, new Set(saved[p.name] ?? [p.default])])
    )
  })
  // Prompt management
  const [selectedPrompts, setSelectedPrompts] = useState<Set<string>>(
    () => new Set(cfg.selectedPrompts ?? [''])
  )
  const [customPrompt, setCustomPrompt] = useState<string>(cfg.customPrompt ?? '')
  const [clipSeed, setClipSeed] = useState<number>(cfg.clipSeed ?? 42)
  const [videoSortBy, setVideoSortBy] = useState<'title' | 'language' | 'duration' | 'upload_date'>(
    cfg.videoSortBy ?? 'title'
  )
  const [videoSortDir, setVideoSortDir] = useState<'asc' | 'desc'>(cfg.videoSortDir ?? 'asc')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function handleVideoSort(col: typeof videoSortBy) {
    if (videoSortBy === col) setVideoSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setVideoSortBy(col); setVideoSortDir('asc') }
  }

  const sortedLibrary = [...library.filter(v => v.subtitles_local)].sort((a, b) => {
    let va: string | number = ''
    let vb: string | number = ''
    if (videoSortBy === 'title') { va = a.title.toLowerCase(); vb = b.title.toLowerCase() }
    else if (videoSortBy === 'language') { va = a.language; vb = b.language }
    else if (videoSortBy === 'duration') { va = a.duration_seconds ?? -1; vb = b.duration_seconds ?? -1 }
    else if (videoSortBy === 'upload_date') { va = a.upload_date ?? a.added_at ?? ''; vb = b.upload_date ?? b.added_at ?? '' }
    const cmp = va < vb ? -1 : va > vb ? 1 : 0
    return videoSortDir === 'asc' ? cmp : -cmp
  })

  useEffect(() => {
    Promise.all([api.library.list(), api.models.registry(), api.tuning.listJobs()])
      .then(([lib, reg, j]) => { setLibrary(lib); setRegistry(reg); setJobs(j) })
  }, [])

  // Uložit konfiguraci do localStorage při každé změně
  useEffect(() => {
    const cfg = {
      selectedModel, selectedVideos, strategy, sampleSeconds, maxTrials, label, clipSeed,
      paramValues: Object.fromEntries(
        Object.entries(paramValues).map(([k, v]) => [k, [...v]])
      ),
      selectedPrompts: [...selectedPrompts],
      customPrompt, videoSortBy, videoSortDir,
    }
    localStorage.setItem(LS_KEY, JSON.stringify(cfg))
  }, [selectedModel, selectedVideos, strategy, sampleSeconds, maxTrials, label, clipSeed,
      paramValues, selectedPrompts, customPrompt, videoSortBy, videoSortDir])

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
    return `Toto je rozhovor v češtině. Téma: ${title}.`
  }

  function allSelectedPrompts(): string[] {
    const prompts = new Set(selectedPrompts)
    if (customPrompt.trim()) prompts.add(customPrompt.trim())
    return [...prompts]
  }

  function countTrials(): number {
    const promptCount = Math.max(1, allSelectedPrompts().length)
    if (strategy === 'ablation') {
      let n = 1
      for (const p of WHISPER_PARAM_DEFS) {
        n += Math.max(0, (paramValues[p.name]?.size ?? 1) - 1)
      }
      n += Math.max(0, promptCount - 1)
      return n
    }
    if (strategy === 'grid') {
      return WHISPER_PARAM_DEFS.reduce((acc, p) => acc * (paramValues[p.name]?.size ?? 1), 1) * promptCount
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
      const job = await api.tuning.createJob({
        model_id: selectedModel,
        video_ids: selectedVideos,
        sample_seconds: sampleSeconds,
        clip_seed: clipSeed,
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
  const isSlowModel = selectedModel.includes('large')

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
            <div className="flex gap-1 text-xs text-gray-400">
              {([['title','Název'],['language','Jazyk'],['duration','Délka'],['upload_date','Datum']] as [typeof videoSortBy, string][]).map(([col, lbl]) => (
                <button key={col} onClick={() => handleVideoSort(col)}
                  className={`px-1.5 py-0.5 rounded ${videoSortBy === col ? 'bg-gray-200 text-gray-700 font-medium' : 'hover:bg-gray-100'}`}>
                  {lbl}{videoSortBy === col ? (videoSortDir === 'asc' ? ' ▲' : ' ▼') : ''}
                </button>
              ))}
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
                <span className="truncate" title={v.title}>{v.title}</span>
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
              ] as [Strategy, string][]).map(([s, label]) => (
                <label key={s} className="flex items-center gap-2 text-xs cursor-pointer">
                  <input type="radio" name="strategy" value={s}
                    checked={strategy === s} onChange={() => setStrategy(s)} />
                  {label}
                </label>
              ))}
            </div>
          </div>
          <div className="flex gap-3 flex-wrap">
            <div>
              <label className="text-xs text-gray-500">Délka klipu (s)</label>
              <input type="number" value={sampleSeconds} min={20} max={300}
                onChange={e => setSampleSeconds(+e.target.value)}
                className="border rounded px-2 py-1 text-sm w-20 block mt-0.5" />
            </div>
            <div>
              <label className="text-xs text-gray-500" title="Seed pro výběr pozice v klipu — stejný seed = stejný úsek">
                Clip seed
              </label>
              <input type="number" value={clipSeed} min={0} max={9999}
                onChange={e => setClipSeed(+e.target.value)}
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

        {/* Numerické / bool parametry */}
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
                        selected ? 'bg-blue-600 text-white border-blue-600'
                               : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
                      }`}
                    >
                      {p.type === 'bool' ? (v ? 'true' : 'false') : String(v)}
                      {isDefault && <span className="ml-1 opacity-50">●</span>}
                    </button>
                  )
                })}
              </div>
              <div className="text-xs text-gray-400 pt-1">
                {paramValues[p.name]?.size ?? 1} hod.
              </div>
            </div>
          ))}
        </div>

        {/* Initial prompt — doménová knihovna */}
        <div className="border-t border-gray-100 pt-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-medium text-gray-700">Initial prompt</span>
            <span className="text-xs text-gray-400">— kontext pro model (co čekat za obsah a slovní zásobu)</span>
            <span className="ml-auto text-xs text-blue-600 font-medium">{allSelectedPrompts().length} vybráno</span>
          </div>

          <div className="space-y-3">
            {PROMPT_LIBRARY.map(group => {
              const colorMap: Record<string, string> = {
                gray: 'border-gray-200 bg-gray-50',
                blue: 'border-blue-200 bg-blue-50',
                violet: 'border-violet-200 bg-violet-50',
                red: 'border-red-200 bg-red-50',
                indigo: 'border-indigo-200 bg-indigo-50',
                green: 'border-green-200 bg-green-50',
                orange: 'border-orange-200 bg-orange-50',
              }
              const tagMap: Record<string, string> = {
                gray: 'bg-gray-200 text-gray-700',
                blue: 'bg-blue-200 text-blue-800',
                violet: 'bg-violet-200 text-violet-800',
                red: 'bg-red-200 text-red-800',
                indigo: 'bg-indigo-200 text-indigo-800',
                green: 'bg-green-200 text-green-800',
                orange: 'bg-orange-200 text-orange-800',
              }
              const btnSel = 'border-2 font-semibold'
              const btnUnsel = 'border opacity-70 hover:opacity-100'

              // Auto-prompt skupinu zobrazíme jinak
              if (group.id === 'topic_auto') {
                const autoPrompts = selectedVideos.map(vid => {
                  const item = library.find(l => l.video_id === vid)
                  return item ? { label: item.title, text: autoPromptFromTitle(item.title) } : null
                }).filter(Boolean) as { label: string; text: string }[]

                const isActive = selectedVideos.length > 0 && autoPrompts.some(p => selectedPrompts.has(p.text) || allSelectedPrompts().includes(p.text))

                return (
                  <div key={group.id} className={`rounded border p-3 ${colorMap[group.color]}`}>
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded ${tagMap[group.color]}`}>{group.label}</span>
                      <span className="text-xs text-gray-500">automaticky z názvu vybraného videa</span>
                    </div>
                    {selectedVideos.length === 0
                      ? <p className="text-xs text-gray-400 italic">Vyber video — prompt se vygeneruje automaticky z jeho názvu.</p>
                      : autoPrompts.map(p => (
                          <div key={p.text} className="flex items-start gap-2 mt-1">
                            <button onClick={() => togglePrompt(p.text)}
                              className={`shrink-0 text-xs px-2 py-0.5 rounded border ${selectedPrompts.has(p.text) ? `${btnSel} border-violet-500 bg-violet-100 text-violet-800` : `${btnUnsel} border-violet-300 bg-white text-violet-700`}`}>
                              {selectedPrompts.has(p.text) ? '✓' : '+'}
                            </button>
                            <div>
                              <div className="text-xs font-medium text-gray-700">{p.label}</div>
                              <div className="text-xs text-gray-500 font-mono mt-0.5 italic">„{p.text}"</div>
                            </div>
                          </div>
                        ))
                    }
                  </div>
                )
              }

              return (
                <div key={group.id} className={`rounded border p-3 ${colorMap[group.color]}`}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className={`text-xs font-medium px-2 py-0.5 rounded ${tagMap[group.color]}`}>{group.label}</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {group.prompts.map(p => {
                      const sel = selectedPrompts.has(p.text)
                      return (
                        <button key={p.text} onClick={() => togglePrompt(p.text)}
                          title={`"${p.text}"`}
                          className={`text-xs px-2.5 py-1.5 rounded border transition-all text-left ${
                            sel ? `${btnSel} border-blue-500 bg-white text-blue-800 shadow-sm`
                                : `${btnUnsel} border-gray-300 bg-white text-gray-700`
                          }`}>
                          {sel && <span className="mr-1">✓</span>}
                          {p.label}
                        </button>
                      )
                    })}
                  </div>
                  {/* Zobraz text vybraných promptů v téhle skupině */}
                  {group.prompts.filter(p => selectedPrompts.has(p.text)).map(p => (
                    <div key={p.text} className="mt-2 text-xs text-gray-500 font-mono bg-white/70 rounded px-2 py-1 italic">
                      „{p.text}"
                    </div>
                  ))}
                </div>
              )
            })}

            {/* Vlastní prompt */}
            <div className="rounded border border-dashed border-gray-300 p-3">
              <label className="text-xs font-medium text-gray-600 block mb-1">Vlastní prompt</label>
              <input value={customPrompt} onChange={e => setCustomPrompt(e.target.value)}
                placeholder="Napiš vlastní kontext pro model…"
                className="w-full border rounded px-2 py-1.5 text-xs font-mono text-gray-700" />
              {customPrompt.trim() && (
                <div className="text-xs text-green-600 mt-1">✓ Bude přidán jako jeden trial</div>
              )}
            </div>
          </div>
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
        <p className="text-xs text-gray-400">● = výchozí hodnota (baseline). Vyber více hodnot nebo promptů pro sweep.</p>
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
  const [expandedTrial, setExpandedTrial] = useState<number | null>(null)
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
          <button onClick={() => api.tuning.openJobDir(job.job_id)}
            className="ml-auto text-xs text-gray-500 hover:text-gray-800 border border-gray-200 rounded px-2 py-0.5">
            📁 Otevřít složku
          </button>
        </div>
        {job.progress_message && (
          <div className={`mt-2 text-xs font-mono px-3 py-2 rounded ${
            job.status === 'running' ? 'bg-blue-50 text-blue-800' : 'bg-gray-50 text-gray-600'
          }`}>
            {job.status === 'running' && <span className="animate-pulse mr-2">⌛</span>}
            {job.progress_message}
          </div>
        )}

        {best && (
          <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-semibold text-green-800">🏆 Nejlepší konfigurace (nejnižší WER, RTF ≤ 1.2)</span>
              <button
                onClick={() => {
                  localStorage.setItem('tuning_recommendation_v1', JSON.stringify({ model_id: job.model_id, params: best.params, wer: best.wer, rtf: best.rtf }))
                  alert(`Nastavení uloženo.\nModel: ${job.model_id}\nParams: ${JSON.stringify(best.params)}`)
                }}
                className="ml-auto text-xs bg-green-700 hover:bg-green-800 text-white px-3 py-1 rounded">
                Použít toto nastavení
              </button>
            </div>
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
              {best.perceived_delay_s != null && <span className="text-gray-500" title="Čas od promluvení do zobrazení přepisu">⏱ {best.perceived_delay_s.toFixed(1)}s zpoždění</span>}
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
                <th className="px-3 py-2 text-center">Live mic</th>
                <th className="px-3 py-2 text-center" title="Čas od promluvení do zobrazení přepisu = chunk + chunk×RTF">Zpoždění</th>
                <th className="px-3 py-2 text-center">Latence</th>
                <th className="px-3 py-2 text-center">Pareto</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, rank) => (
                <>
                  <tr key={r.trial_idx}
                    onClick={() => setExpandedTrial(expandedTrial === r.trial_idx ? null : r.trial_idx)}
                    className={`border-t border-gray-100 cursor-pointer ${r.trial_idx === job.best_trial_idx ? 'bg-green-50 hover:bg-green-100' : 'hover:bg-gray-50'}`}>
                    <td className="px-3 py-2 text-center text-gray-400 font-mono">
                      {rank === 0 ? '🏆' : rank + 1}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1 items-center">
                        <span className="text-blue-500 mr-1 text-xs font-bold">{expandedTrial === r.trial_idx ? '▼' : '▶'}</span>
                        {Object.entries(r.params).map(([k, v]) => (
                          <span key={k} className="bg-gray-100 rounded px-1.5 py-0.5 font-mono text-gray-700">
                            {k}=<strong>{String(v)}</strong>
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-center"><WerBadge value={r.wer} label="" /></td>
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
                    <td className="px-3 py-2 text-center font-mono text-gray-600" title="Čas od promluvení do zobrazení přepisu">
                      {r.perceived_delay_s != null ? `${r.perceived_delay_s.toFixed(1)}s` : '–'}
                    </td>
                    <td className="px-3 py-2 text-center font-mono text-gray-600">
                      {r.latency_ms != null ? `${r.latency_ms.toFixed(0)}ms` : '–'}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {r.is_pareto ? <span className="text-yellow-600 font-bold">★</span> : ''}
                    </td>
                  </tr>
                  {expandedTrial === r.trial_idx && (
                    <tr key={`${r.trial_idx}-detail`} className={r.trial_idx === job.best_trial_idx ? 'bg-green-50' : 'bg-gray-50'}>
                      <td colSpan={10} className="px-4 py-3 border-t border-gray-100">
                        <div className="space-y-4 text-xs">
                          {r.error && (
                            <div className="bg-red-50 border border-red-200 rounded p-2 text-red-700 font-mono">{r.error}</div>
                          )}

                          {/* Statistiky */}
                          <div className="flex flex-wrap gap-4 text-gray-600 bg-white border border-gray-200 rounded px-3 py-2">
                            <span>WER: <strong>{r.wer != null ? (r.wer*100).toFixed(1)+'%' : '–'}</strong></span>
                            <span>CER: <strong>{r.cer != null ? (r.cer*100).toFixed(1)+'%' : '–'}</strong></span>
                            <span>WER norm: <strong>{r.wer_normalized != null ? (r.wer_normalized*100).toFixed(1)+'%' : '–'}</strong></span>
                            <span>MER: <strong>{r.mer != null ? (r.mer*100).toFixed(1)+'%' : '–'}</strong></span>
                            <span>WIL: <strong>{r.wil != null ? (r.wil*100).toFixed(1)+'%' : '–'}</strong></span>
                            <span className={r.rtf != null && r.rtf > 1 ? 'text-red-700 font-bold' : 'text-green-700 font-semibold'}>
                              RTF: <strong>{r.rtf?.toFixed(3) ?? '–'}</strong>
                            </span>
                            <span>Latence: <strong>{r.latency_ms != null ? r.latency_ms.toFixed(0)+'ms' : '–'}</strong></span>
                            {r.perceived_delay_s != null && (
                              <span title="Čas od promluvení do zobrazení přepisu = chunk + chunk×RTF">
                                Zpoždění: <strong>{r.perceived_delay_s.toFixed(1)}s</strong>
                              </span>
                            )}
                            {r.elapsed_s != null && <span>Engine: <strong>{r.elapsed_s.toFixed(1)}s</strong></span>}
                            {r.total_audio_s != null && <span>Audio: <strong>{r.total_audio_s.toFixed(1)}s</strong></span>}
                            {r.word_count != null && <span>Slov: <strong>{r.word_count}</strong></span>}
                          </div>

                          {/* Per-video breakdown */}
                          {r.source_metrics && r.source_metrics.length > 1 && (
                            <div>
                              <div className="font-semibold text-gray-600 mb-1">Výsledky per video (průměr v tabulce):</div>
                              <div className="flex flex-wrap gap-2">
                                {r.source_metrics.map(sm => (
                                  <div key={sm.video_id} className={`border rounded px-2 py-1 font-mono text-center ${sm.error ? 'border-red-200 bg-red-50' : 'border-gray-200 bg-white'}`}>
                                    <div className="text-gray-500 text-xs truncate max-w-28">{sm.video_id}</div>
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

                          {/* Word diff */}
                          {r.word_diff && r.word_diff.length > 0 && (
                            <div>
                              <div className="font-semibold text-gray-600 mb-1">Word diff (přepis vs reference):</div>
                              <div className="bg-white border border-gray-200 rounded p-2 leading-6 max-h-48 overflow-y-auto">
                                {r.word_diff.map((d, i) => {
                                  if (d.op === 'equal') return <span key={i} className="text-gray-700">{d.hyp} </span>
                                  if (d.op === 'replace') return <span key={i}><span className="bg-yellow-100 text-yellow-800 rounded px-0.5">{d.hyp}</span><span className="bg-gray-100 text-gray-400 line-through rounded px-0.5 ml-0.5 text-xs">{d.ref}</span> </span>
                                  if (d.op === 'insert') return <span key={i} className="bg-red-100 text-red-700 rounded px-0.5 line-through">{d.hyp} </span>
                                  if (d.op === 'delete') return <span key={i} className="bg-blue-100 text-blue-700 rounded px-0.5">[{d.ref}] </span>
                                  return null
                                })}
                              </div>
                              <div className="flex gap-3 mt-1 text-gray-400">
                                <span><span className="bg-yellow-100 text-yellow-800 rounded px-1">slovo</span> záměna</span>
                                <span><span className="bg-red-100 text-red-700 rounded px-1 line-through">slovo</span> přebývá</span>
                                <span><span className="bg-blue-100 text-blue-700 rounded px-1">[slovo]</span> chybí</span>
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
