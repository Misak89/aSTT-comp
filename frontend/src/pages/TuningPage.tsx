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
  {
    name: 'beam_size', label: 'Beam size', type: 'int', values: [1, 2, 3, 5, 8, 10, 12], default: 5,
    description: 'Počet kandidátních sekvencí při beam search. Vyšší = přesnější přepis, pomalejší. beam=1 je greedy (nejrychlejší, nejhorší WER). beam=5 je dobrý kompromis. ⚠ Hodnoty 10 a 12 mohou selhat u menších modelů (small, base) — whisper-cli vrátí "failed to process audio".',
  },
  {
    name: 'best_of', label: 'Best of', type: 'int', values: [1, 2, 3, 5], default: 5,
    description: 'Počet vzorkování při temperature > 0. Má vliv jen pokud model použije temperature fallback (ne při beam search s no_fallback=true). Při no_fallback=true nemá žádný efekt.',
    valueDescriptions: { 1: 'bez vzorkování', 5: 'výchozí whisper.cpp' },
  },
  {
    name: 'threads', label: 'Vlákna CPU', type: 'int', values: [2, 4, 6, 8], default: 4,
    description: 'Počet CPU vláken pro inference. Více vláken = nižší RTF (rychlejší zpracování). Optimum závisí na CPU — obvykle polovina fyzických jader.',
  },
  {
    name: 'no_fallback', label: 'Bez fallbacku', type: 'bool', values: [true, false], default: true,
    description: 'Zakáže temperature fallback. Pokud je true, whisper nikdy nezkusí vyšší temperature při nejistém výsledku — rychlejší, deterministické. false = whisper může zopakovat dekódování s vyšší teplotou.',
    valueDescriptions: { true: 'deterministické, rychlejší', false: 'může opakovat s vyšší temp.' },
  },
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
      { label: 'Obecný rozhovor', text: 'Rozhovor v cestine:' },
      { label: 'Kontext krátký', text: 'Dobry den, toto je rozhovor v cestine.' },
      { label: 'Delší kontext', text: 'Toto je rozhovor dvou lidi v ceskem jazyce. Mluvi plynne cesky.' },
    ],
  },
  {
    id: 'topic_auto', label: 'Téma z názvu videa', domain: 'auto', color: 'violet',
    prompts: [], // generováno dynamicky dle vybraného videa
  },
  {
    id: 'medicine', label: 'Medicína', domain: 'medical', color: 'red',
    prompts: [
      { label: 'Obecné lékařské vyšetření', text: 'Toto je lekarske vysetreni. Lekar hovori s pacientem o zdravotnim stavu, symptomech a lecbe.' },
      { label: 'Kardiologie', text: 'Toto je kardiologicke vysetreni. Lekar kardiolog diskutuje o srdecnich onemocnenich, EKG, krevnim tlaku a lecbe.' },
      { label: 'Neurologie', text: 'Toto je neurologicke vysetreni. Neurolog hodnoti reflexy, pohybove funkce a neurologicke symptomy pacienta.' },
      { label: 'Psychiatrie', text: 'Toto je psychiatricka konzultace. Psychiatr hovori s pacientem o dusevnim zdravi, nalade a psychologickych symptomech.' },
      { label: 'Praktický lékař', text: 'Ordinace praktickeho lekare. Pacient popisuje potize, lekar doporucuje vysetreni a lecbu.' },
      { label: 'Onkologie', text: 'Onkologicka konzultace. Lekar diskutuje o diagnoze nadoroveho onemocneni, chemoterapii a prognoze.' },
      { label: 'Operace / chirurgie', text: 'Chirurgicke pracoviste. Lekari diskutuji o operacnim zakroku, priprave pacienta a postoperacni peci.' },
      { label: 'Alergologie a imunologie', text: 'Alergologicka a imunologicka konzultace. Lekar hodnoti alergicke reakce, imunitni system, precitlivelos a imunoterapii pacienta.' },
    ],
  },
  {
    id: 'technology', label: 'Technologie', domain: 'tech', color: 'indigo',
    prompts: [
      { label: 'IT a software', text: 'Technicky rozhovor o softwaru, programovani a informatice.' },
      { label: 'Hardware a počítače', text: 'Rozhovor o pocitacovem hardwaru, procesorech, grafickych kartach a technologiich.' },
      { label: 'Herní průmysl', text: 'Rozhovor o videohrach, vyvoji her a hernim prumyslu.' },
      { label: 'Umělá inteligence', text: 'Diskuse o umele inteligenci, strojovem uceni a neuronovych sitich.' },
    ],
  },
  {
    id: 'sport', label: 'Sport', domain: 'sport', color: 'green',
    prompts: [
      { label: 'Obecný sport', text: 'Sportovni rozhovor. Sportovci a treneri diskutuji o vykonu, treninku a soutezich.' },
      { label: 'Fotbal', text: 'Fotbalovy rozhovor. Hraci a treneri diskutuji o zapasech, taktice a lize.' },
      { label: 'Esport', text: 'Rozhovor s profesionalnim hracem esportu o turnajich, strategii a tymove spolupraci.' },
    ],
  },
  {
    id: 'podcast', label: 'Podcast / pořad', domain: 'media', color: 'orange',
    prompts: [
      { label: 'Obecný podcast', text: 'Podcastovy rozhovor. Moderator diskutuje s hostem o ruznych tematech.' },
      { label: 'Věda a vzdělávání', text: 'Vzdelavaci porad. Odbornik vysvetluje vedecka temata srozumitelne pro verejnost.' },
      { label: 'Byznys a ekonomika', text: 'Obchodni rozhovor o ekonomice, podnikani, investicich a financnich trzich.' },
      { label: 'Politika a společnost', text: 'Politicka diskuse. Politici a novinari hovori o spolecenskych otazkach a vladni politice.' },
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
  const [selectedModels, setSelectedModels] = useState<string[]>(() => {
    if (cfg.selectedModels?.length) return cfg.selectedModels
    if (cfg.selectedModel) return [cfg.selectedModel]  // backward compat
    return ['whisper_cpp_small']
  })
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
  const [selectedPrompts, setSelectedPrompts] = useState<Set<string>>(() => {
    // Odfiltruj uložené prompty s diakritikou (staré hodnoty z localStorage)
    const saved: string[] = cfg.selectedPrompts ?? ['']
    const clean = saved.filter((p: string) => /^[\x00-\x7F]*$/.test(p))
    return new Set(clean.length > 0 ? clean : [''])
  })
  const [customPrompt, setCustomPrompt] = useState<string>(() => {
    const p = cfg.customPrompt ?? ''
    return /^[\x00-\x7F]*$/.test(p) ? p : ''
  })
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
      selectedModels, selectedVideos, strategy, sampleSeconds, maxTrials, label, clipSeed,
      paramValues: Object.fromEntries(
        Object.entries(paramValues).map(([k, v]) => [k, [...v]])
      ),
      selectedPrompts: [...selectedPrompts],
      customPrompt, videoSortBy, videoSortDir,
    }
    localStorage.setItem(LS_KEY, JSON.stringify(cfg))
  }, [selectedModels, selectedVideos, strategy, sampleSeconds, maxTrials, label, clipSeed,
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
    // ASCII-only — whisper-cli na Windows crashuje při non-ASCII v -p
    const ascii = title.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^\x00-\x7F]/g, '')
    return `Toto je rozhovor v cestine. Tema: ${ascii}.`
  }

  function allSelectedPrompts(): string[] {
    const prompts = new Set(selectedPrompts)
    if (customPrompt.trim()) prompts.add(customPrompt.trim())
    return [...prompts]
  }

  function countTrials(): number {
    const modelCount = Math.max(1, selectedModels.length)
    const promptCount = Math.max(1, allSelectedPrompts().length)
    if (strategy === 'ablation') {
      let n = 1
      for (const p of WHISPER_PARAM_DEFS) {
        n += Math.max(0, (paramValues[p.name]?.size ?? 1) - 1)
      }
      n += Math.max(0, promptCount - 1)
      return n * modelCount
    }
    if (strategy === 'grid') {
      const chunkCount = paramValues['chunk_seconds']?.size ?? 1
      const beamValues = [...(paramValues['beam_size'] ?? [5])] as number[]
      const bestOfValues = [...(paramValues['best_of'] ?? [5])] as number[]
      const otherCount = WHISPER_PARAM_DEFS
        .filter(p => p.name !== 'chunk_seconds' && p.name !== 'beam_size' && p.name !== 'best_of')
        .reduce((acc, p) => acc * (paramValues[p.name]?.size ?? 1), 1)
      // Odfiltruj neplatné kombinace beam_size × best_of (best_of > beam_size)
      let validBeamBestOf = 0
      for (const beam of beamValues)
        for (const bestOf of bestOfValues)
          if (bestOf <= beam) validBeamBestOf++
      return validBeamBestOf * otherCount * chunkCount * promptCount * modelCount
    }
    return maxTrials * modelCount
  }

  async function startTuning() {
    if (!selectedModels.length) { setMsg('Vyber alespoň jeden model.'); return }
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
        model_ids: selectedModels,
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
  const isSlowModel = selectedModels.some(m => m.includes('large'))

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
          <button
            className="font-semibold text-sm text-gray-700 hover:text-blue-600 hover:underline text-left"
            title="Otevřít adresář s modely"
            onClick={() => api.openDir.modelStore().catch(() => {})}
          >Modely ↗</button>
          {whisperModels.map(m => (
            <label key={m.model_id} className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" value={m.model_id}
                checked={selectedModels.includes(m.model_id)}
                onChange={() => setSelectedModels(prev =>
                  prev.includes(m.model_id)
                    ? prev.filter(id => id !== m.model_id)
                    : [...prev, m.model_id]
                )} />
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
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="font-semibold text-sm text-gray-700">Parametrický prostor</h2>
          <span className="text-xs text-gray-400">— klikni na hodnoty, které chceš testovat (modre = vybráno)</span>
          <div className="ml-auto flex gap-2">
            <button onClick={() => {
              setParamValues(Object.fromEntries(
                WHISPER_PARAM_DEFS.map(p => [p.name, new Set<unknown>([p.default])])
              ))
            }} className="px-2.5 py-1 text-xs rounded border border-gray-300 hover:bg-gray-50">
              Reset
            </button>
            <button onClick={() => {
              // Doporučený rozsah pro typický tuning run
              setParamValues({
                beam_size: new Set<unknown>([1, 3, 5]),
                best_of: new Set<unknown>([1, 3, 5]),
                threads: new Set<unknown>([2, 4]),
                no_fallback: new Set<unknown>([true, false]),
                chunk_seconds: new Set<unknown>([15, 30]),
              })
            }} className="px-2.5 py-1 text-xs rounded border border-blue-400 text-blue-700 hover:bg-blue-50">
              Doporuceny rozsah
            </button>
            <button onClick={() => {
              setParamValues(Object.fromEntries(
                WHISPER_PARAM_DEFS.map(p => [p.name, new Set<unknown>(p.values)])
              ))
            }} className="px-2.5 py-1 text-xs rounded border border-gray-300 hover:bg-gray-50">
              Vse
            </button>
          </div>
        </div>

        {/* Numerické / bool parametry */}
        <div className="space-y-3">
          {WHISPER_PARAM_DEFS.map(p => (
            <div key={p.name} className="flex items-start gap-4">
              <div className="w-36 shrink-0 pt-1 relative group/label">
                <span className="text-xs font-medium text-gray-600 cursor-help underline decoration-dotted decoration-gray-400">
                  {p.label}
                </span>
                <div className="pointer-events-none absolute left-0 top-5 z-20 hidden group-hover/label:block w-72 bg-gray-900 text-white text-xs rounded px-2.5 py-2 shadow-xl leading-relaxed">
                  {p.description}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(p.values as readonly unknown[]).map(v => {
                  const selected = paramValues[p.name]?.has(v) ?? false
                  const isDefault = v === p.default
                  const vStr = p.type === 'bool' ? (v ? 'true' : 'false') : String(v)
                  const vDesc = (p as { valueDescriptions?: Record<string, string> }).valueDescriptions?.[String(v)]
                  return (
                    <div key={vStr} className="relative group/val">
                      <button
                        onClick={() => toggleParamValue(p.name, v)}
                        className={`px-2.5 py-1 rounded text-xs font-mono border transition-colors ${
                          selected ? 'bg-blue-600 text-white border-blue-600'
                                 : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
                        }`}
                      >
                        {vStr}
                        {isDefault && <span className="ml-1 opacity-50">●</span>}
                      </button>
                      {vDesc && (
                        <div className="pointer-events-none absolute left-0 top-7 z-20 hidden group-hover/val:block whitespace-nowrap bg-gray-900 text-white text-xs rounded px-2 py-1 shadow-xl">
                          {vDesc}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
              <div className="text-xs text-gray-400 pt-1">
                {paramValues[p.name]?.size ?? 1} hodnot
              </div>
            </div>
          ))}
        </div>

        {/* Initial prompt — zakázáno, binárka crashuje */}
        <div className="border-t border-gray-100 pt-4">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium text-gray-700">Initial prompt</span>
            <span className="text-xs text-gray-400">— kontext pro model</span>
          </div>
          <div className="rounded bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
            ✗ Nefunkční s touto binárkou whisper-cli — <code>-p</code> způsobuje crash při jakémkoliv neprázdném promptu (0 z 229 pokusů prošlo). Prompty jsou z tuningu vyřazeny. Oprava vyžaduje upgrade whisper-cli.
          </div>
        </div>

        <div className="flex items-center gap-4 pt-2 border-t border-gray-100">
          <div className="text-sm text-gray-700">
            Vygeneruje <strong>{trialCount}</strong> kombinac{trialCount === 1 ? 'i' : trialCount < 5 ? 'e' : 'í'}.
            Odhadovaný čas: <strong>~{Math.round(trialCount * sampleSeconds * 0.6 / 60)} min</strong>
            <span className="text-gray-400 ml-1">(RTF≈0.5)</span>
          </div>
          <button onClick={startTuning}
            className="ml-auto bg-blue-600 hover:bg-blue-700 text-white px-4 py-1.5 rounded text-sm font-medium">
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
      {selectedJob && <TuningJobDetail job={selectedJob} onCancel={async () => {
        await api.tuning.cancelJob(selectedJob.job_id)
        const cancelled = { ...selectedJob, status: 'cancelled' as const }
        setSelectedJob(cancelled)
        setJobs(prev => prev.map((j: TuningJobStatus) => j.job_id === selectedJob.job_id ? cancelled : j))
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
      }} />}
    </div>
  )
}

type SortCol = 'wer' | 'cer' | 'wer_normalized' | 'rtf' | 'rtf_viable' | 'perceived_delay_s' | 'latency_ms' | 'is_pareto'

function TuningJobDetail({ job, onCancel }: { job: TuningJobStatus; onCancel: () => void }) {
  const [expandedTrial, setExpandedTrial] = useState<number | null>(null)
  const [msgAge, setMsgAge] = useState<number>(0)
  const [sortCol, setSortCol] = useState<SortCol>('wer')
  const [sortDir, setSortDir] = useState<1 | -1>(1)
  const [ram, setRam] = useState<{ used: number; total: number; pct: number } | null>(null)

  useEffect(() => {
    if (job.status !== 'running') { setMsgAge(0); return }
    const tick = () => {
      const ts = job.updated_ts
      if (ts) setMsgAge(Math.floor((Date.now() - new Date(ts).getTime()) / 1000))
    }
    tick()
    const iv = setInterval(tick, 1000)
    return () => clearInterval(iv)
  }, [job.status, job.updated_ts])

  useEffect(() => {
    if (job.status !== 'running') return
    const fetchRam = () => fetch('/api/health').then(r => r.json()).then(d => {
      if (d.ram_total_mb) setRam({ used: d.ram_used_mb, total: d.ram_total_mb, pct: d.ram_percent })
    }).catch(() => {})
    fetchRam()
    const iv = setInterval(fetchRam, 3000)
    return () => clearInterval(iv)
  }, [job.status])

  function toggleSort(col: SortCol) {
    if (sortCol === col) setSortDir(d => d === 1 ? -1 : 1)
    else { setSortCol(col); setSortDir(1) }
  }

  const best = job.best_trial_idx != null ? job.results[job.best_trial_idx] : null
  const sorted = [...job.results].filter(r => r.wer != null).sort((a, b) => {
    const av = (a as any)[sortCol]
    const bv = (b as any)[sortCol]
    if (typeof av === 'boolean' || typeof bv === 'boolean') {
      // true (viable/pareto) = "better" → sort ascending puts false first, so flip for booleans
      const an = av === true ? 1 : av === false ? 0 : -1
      const bn = bv === true ? 1 : bv === false ? 0 : -1
      return (bn - an) * sortDir  // descending by default (true first)
    }
    const an = av ?? Infinity
    const bn = bv ?? Infinity
    return (an - bn) * sortDir
  })

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
          {(job.status === 'running' || job.status === 'pending') && (
            <button onClick={onCancel}
              className="text-xs text-red-600 hover:text-red-800 border border-red-200 hover:border-red-400 rounded px-2 py-0.5">
              ⏹ Zastavit
            </button>
          )}
          {job.status === 'running' && ram && (
            <span className={`text-xs font-mono px-2 py-0.5 rounded border ${ram.pct > 90 ? 'text-red-700 bg-red-50 border-red-200' : ram.pct > 75 ? 'text-amber-700 bg-amber-50 border-amber-200' : 'text-gray-600 bg-gray-50 border-gray-200'}`}
              title="RAM systému">
              RAM {ram.used.toLocaleString()} / {ram.total.toLocaleString()} MB ({ram.pct}%)
            </span>
          )}
          <button onClick={() => api.tuning.openJobDir(job.job_id)}
            className="ml-auto text-xs text-gray-500 hover:text-gray-800 border border-gray-200 rounded px-2 py-0.5">
            📁 Otevřít složku
          </button>
        </div>
        {job.audio_ready?.length > 0 && (
          <div className="mt-2 text-xs px-3 py-1.5 bg-green-50 text-green-800 rounded flex gap-3 flex-wrap">
            {job.audio_ready.map(vid => (
              <span key={vid}>✓ Audio {vid} staženo</span>
            ))}
          </div>
        )}
        {job.progress_message && (
          <div className={`mt-2 text-xs font-mono px-3 py-2 rounded ${
            job.status === 'running' ? 'bg-blue-50 text-blue-800' : 'bg-gray-50 text-gray-600'
          }`}>
            {job.status === 'running' && <span className="animate-pulse mr-2">⌛</span>}
            {job.progress_message}
            {job.status === 'running' && msgAge > 0 && (
              <span className={`ml-3 ${msgAge > 60 ? 'text-red-600 font-bold' : msgAge > 30 ? 'text-amber-600' : 'text-blue-400'}`}>
                {msgAge > 60 ? '⚠ zaseknuté?' : `+${msgAge}s`}
              </span>
            )}
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
                {job.model_ids?.length > 1 && <th className="px-3 py-2 text-left">Model</th>}
                <th className="px-3 py-2 text-left">Parametry</th>
                {([ ['wer','WER'], ['cer','CER'], ['wer_normalized','WER norm.'], ['rtf','RTF'],
                    ['rtf_viable','Live mic'], ['perceived_delay_s','Zpoždění'], ['latency_ms','Latence'], ['is_pareto','Pareto']
                ] as [SortCol, string][]).map(([col, label], i) => (
                  <th key={i} onClick={() => toggleSort(col)}
                    className="px-3 py-2 text-center cursor-pointer select-none hover:bg-gray-100 whitespace-nowrap"
                    title={col === 'perceived_delay_s' ? 'Čas od promluvení do zobrazení přepisu = chunk + chunk×RTF' : undefined}>
                    {label}{sortCol === col ? (sortDir === 1 ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
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
                    {job.model_ids?.length > 1 && (
                      <td className="px-3 py-2 text-xs font-mono text-blue-700 whitespace-nowrap">
                        {r.model_id?.replace('whisper_cpp_', '') ?? '—'}
                      </td>
                    )}
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
                    <td className="px-3 py-2 text-center">
                      <WerBadge value={r.wer} label="" />
                      {r.error && r.wer != null && (
                        <span className="ml-1 text-orange-500 text-xs" title={`Částečný výsledek — trial selhal: ${r.error}`}>⚠</span>
                      )}
                    </td>
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
                                  const op = d.op
                                  if (op === '=' || op === 'equal') return <span key={i} className="text-gray-700">{d.hyp} </span>
                                  if (op === 'S' || op === 'replace') return <span key={i}><span className="bg-yellow-100 text-yellow-800 rounded px-0.5">{d.hyp}</span><span className="bg-gray-100 text-gray-400 line-through rounded px-0.5 ml-0.5 text-xs">{d.ref}</span> </span>
                                  if (op === 'I' || op === 'insert') return <span key={i} className="bg-red-100 text-red-700 rounded px-0.5 line-through">{d.hyp} </span>
                                  if (op === 'D' || op === 'delete') return <span key={i} className="bg-blue-100 text-blue-700 rounded px-0.5">[{d.ref}] </span>
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
