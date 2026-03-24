import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { api } from '../api/client'
import type { RunDetail, RunResult, SourceMetric, ChunkMetric } from '../types'
import { WerBadge } from '../components/WerBadge'

type RunSummary = { run_id: string; created_at_utc: string; sample_seconds: number; result_count: number; label?: string }

export function ResultsPage() {
  const [params] = useSearchParams()
  const runId = params.get('run') || ''
  const [runIdInput, setRunIdInput] = useState(runId)
  const [run, setRun] = useState<RunDetail | null>(null)
  const [error, setError] = useState('')
  const [runList, setRunList] = useState<RunSummary[]>([])

  useEffect(() => {
    if (runId) loadRun(runId)
    fetch('/api/runs').then(r => r.ok ? r.json() : []).then(setRunList).catch(() => {})
  }, [runId])

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
      <div className="space-y-2">
        {runList.length > 0 && (
          <div className="bg-white border border-gray-200 rounded p-3">
            <div className="text-xs text-gray-500 font-medium mb-2">Historie runů ({runList.length})</div>
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {runList.map(r => (
                <button key={r.run_id} onClick={() => { setRunIdInput(r.run_id); loadRun(r.run_id) }}
                  className={`w-full text-left text-xs px-3 py-1.5 rounded flex items-center gap-3 hover:bg-blue-50 ${run?.run_id === r.run_id ? 'bg-blue-100 text-blue-800' : 'text-gray-700'}`}>
                  <span className="font-mono text-gray-400 w-36 shrink-0">{r.run_id.slice(-16)}</span>
                  <span className="text-gray-500 shrink-0">{new Date(r.created_at_utc).toLocaleString('cs-CZ', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</span>
                  <span className="text-gray-400">{r.sample_seconds}s · {r.result_count} výsl.</span>
                  {r.label && <span className="text-gray-600 italic">{r.label}</span>}
                </button>
              ))}
            </div>
          </div>
        )}
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
          <ResultsTable results={run.results} runId={run.run_id} />

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

      {/* Legenda metrik — vždy viditelná */}
      <MetricsLegend />
    </div>
  )
}

function MetricsLegend() {
  const metrics = [
    {
      key: 'WER',
      name: 'Word Error Rate',
      formula: '(S + D + I) / N',
      color: 'blue',
      bg: 'bg-blue-50',
      border: 'border-blue-200',
      badge: 'bg-blue-100 text-blue-800',
      desc: 'Základní metrika přesnosti. Počítá, kolik procent slov bylo přepsáno chybně — jako záměna, vynechání nebo přidání. Citlivá na interpunkci a velká písmena.',
      tiers: [
        { label: '< 10 %', color: 'text-green-600', note: 'výborný' },
        { label: '10–20 %', color: 'text-yellow-600', note: 'dobrý' },
        { label: '20–35 %', color: 'text-orange-500', note: 'použitelný' },
        { label: '> 35 %', color: 'text-red-600', note: 'slabý' },
      ],
    },
    {
      key: 'CER',
      name: 'Character Error Rate',
      formula: '(edit distance) / len(reference)',
      color: 'purple',
      bg: 'bg-purple-50',
      border: 'border-purple-200',
      badge: 'bg-purple-100 text-purple-800',
      desc: 'Jemnější pohled než WER — měří chyby na úrovni znaků. Lépe zachytí drobné překlepy nebo chyby v diakritice. Nižší CER při stejném WER = model si píše slovní tvar „skoro správně".',
      tiers: [
        { label: '< 5 %', color: 'text-green-600', note: 'výborný' },
        { label: '5–10 %', color: 'text-yellow-600', note: 'dobrý' },
        { label: '10–20 %', color: 'text-orange-500', note: 'použitelný' },
        { label: '> 20 %', color: 'text-red-600', note: 'slabý' },
      ],
    },
    {
      key: 'WER norm.',
      name: 'WER normalizovaný',
      formula: 'WER bez interpunkce a šumu',
      color: 'teal',
      bg: 'bg-teal-50',
      border: 'border-teal-200',
      badge: 'bg-teal-100 text-teal-800',
      desc: 'WER po agresivní normalizaci — odstraní interpunkci, velká písmena a šumové tagy jako [hudba]. Odhalí skutečnou sémantickou přesnost modelu, bez penalizace za stylové rozdíly.',
      tiers: [
        { label: 'WER norm. ≪ WER', color: 'text-teal-700', note: 'model se plete jen v interpunkci' },
        { label: 'WER norm. ≈ WER', color: 'text-gray-600', note: 'chyby jsou ve skutečných slovech' },
      ],
    },
    {
      key: 'MER',
      name: 'Match Error Rate',
      formula: 'Chyby / (H + Chyby)',
      color: 'orange',
      bg: 'bg-orange-50',
      border: 'border-orange-200',
      badge: 'bg-orange-100 text-orange-800',
      desc: 'Alternativa WER — chyby se dělí počtem správně rozpoznaných slov plus chybami (nikoli celkovým počtem referenčních slov). Méně citlivá na delší referenci, vhodná pro srovnání přes různě dlouhé klipy.',
      tiers: [
        { label: '< 10 %', color: 'text-green-600', note: 'výborný' },
        { label: '10–25 %', color: 'text-yellow-600', note: 'dobrý' },
        { label: '> 25 %', color: 'text-red-600', note: 'slabý' },
      ],
    },
    {
      key: 'WIL',
      name: 'Word Information Lost',
      formula: '1 − (H/N) · (H/P)',
      color: 'rose',
      bg: 'bg-rose-50',
      border: 'border-rose-200',
      badge: 'bg-rose-100 text-rose-800',
      desc: 'Měří ztrátu informace z pohledu teorie informace. Penalizuje jak vynechání (recall), tak přidání slov navíc (precision). WIL = 0 je perfektní, WIL = 1 je totální selhání.',
      tiers: [
        { label: '< 0.15', color: 'text-green-600', note: 'výborný' },
        { label: '0.15–0.35', color: 'text-yellow-600', note: 'dobrý' },
        { label: '> 0.35', color: 'text-red-600', note: 'slabý' },
      ],
    },
    {
      key: 'RTF',
      name: 'Real-Time Factor',
      formula: 'čas přepisu / délka audia',
      color: 'green',
      bg: 'bg-green-50',
      border: 'border-green-200',
      badge: 'bg-green-100 text-green-800',
      desc: 'Klíčová metrika pro živý přepis. RTF = 0.3 znamená, že přepis trvá 30 % délky audia — model stíhá s rezervou. RTF > 1.0 = model nestíhá, není použitelný pro real-time dialog.',
      tiers: [
        { label: '< 0.5', color: 'text-green-600', note: 'výborný — velká rezerva' },
        { label: '0.5–0.8', color: 'text-yellow-600', note: 'dobrý' },
        { label: '0.8–1.0', color: 'text-orange-500', note: 'na hraně' },
        { label: '> 1.0', color: 'text-red-600', note: 'nestíhá živý přepis' },
      ],
    },
    {
      key: 'Latence',
      name: 'First-word latency',
      formula: 'čas do prvního výstupu (ms)',
      color: 'indigo',
      bg: 'bg-indigo-50',
      border: 'border-indigo-200',
      badge: 'bg-indigo-100 text-indigo-800',
      desc: 'Jak dlouho po začátku audia model vydá první přepsané slovo. Závisí na délce chunků a rychlosti dekódování. Pro dialog je nízká latence klíčová — uživatel nechce čekat.',
      tiers: [
        { label: '< 3 s', color: 'text-green-600', note: 'přijatelné pro dialog' },
        { label: '3–10 s', color: 'text-yellow-600', note: 'záleží na kontextu' },
        { label: '> 10 s', color: 'text-red-600', note: 'příliš pomalé' },
      ],
    },
  ]

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <h2 className="text-base font-semibold text-gray-800">Metriky hodnocení přepisu</h2>
        <span className="text-xs text-gray-400">Jak interpretovat hodnoty v tabulce</span>
      </div>

      {/* Přehledová tabulka */}
      <div className="bg-white rounded border border-gray-200 overflow-hidden">
        <div className="grid grid-cols-7 text-xs font-medium text-gray-500 bg-gray-50 px-4 py-2 border-b border-gray-200">
          <span>Metrika</span>
          <span className="col-span-2">Co měří</span>
          <span>Vzorec</span>
          <span className="col-span-3">Hodnocení</span>
        </div>
        {metrics.map(m => (
          <div key={m.key} className={`grid grid-cols-7 text-xs px-4 py-3 border-b border-gray-100 last:border-0 items-start gap-2 hover:bg-gray-50`}>
            <div>
              <span className={`inline-block px-2 py-0.5 rounded font-bold font-mono ${m.badge}`}>{m.key}</span>
            </div>
            <div className="col-span-2 text-gray-700 leading-relaxed">
              <span className="font-medium text-gray-900 block">{m.name}</span>
              {m.desc}
            </div>
            <div className="font-mono text-gray-400 text-xs leading-tight pt-0.5">{m.formula}</div>
            <div className="col-span-3 flex flex-wrap gap-x-4 gap-y-1">
              {m.tiers.map(t => (
                <span key={t.label} className="flex items-center gap-1">
                  <span className={`font-mono font-bold ${t.color}`}>{t.label}</span>
                  <span className="text-gray-400">= {t.note}</span>
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Zkratky a legenda operací */}
      <div className="bg-gray-50 border border-gray-200 rounded px-4 py-3 flex flex-wrap gap-6 text-xs text-gray-600">
        <span className="font-medium text-gray-700 self-center">Zkratky ve vzorcích:</span>
        <span><strong>N</strong> = počet slov v referenci</span>
        <span><strong>P</strong> = počet slov v hypotéze (přepis)</span>
        <span><strong>H</strong> = správně rozpoznaná slova</span>
        <span><strong>S</strong> = záměny (substitutions)</span>
        <span><strong>D</strong> = vynechání (deletions)</span>
        <span><strong>I</strong> = přidání navíc (insertions)</span>
      </div>

      {/* Sekce D — Segment-level WER */}
      <div className="bg-white border border-gray-200 rounded overflow-hidden">
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-3 flex items-center gap-3">
          <span className="bg-amber-100 text-amber-800 font-bold font-mono text-xs px-2 py-0.5 rounded">D</span>
          <span className="font-semibold text-gray-800 text-sm">Segment-level WER — přesnost po časových úsecích</span>
        </div>
        <div className="px-4 py-4 space-y-4 text-sm text-gray-700">
          <p>
            Globální WER říká <em>kolik procent slov bylo špatně celkem</em>, ale neříká <em>kde</em>.
            Segment-level WER rozdělí audio na časové úseky a pro každý úsek zvlášť porovná přepis modelu
            s referenčními titulky. Lze tak přesně vidět, ve které části rozhovoru model selhal.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            <div className="bg-amber-50 border border-amber-200 rounded p-3">
              <div className="font-semibold text-amber-900 mb-1">1. Whisper segmenty</div>
              <p className="text-amber-800">
                Whisper vrací každý přepsaný úsek s časovými razítky — <code className="font-mono bg-white px-1 rounded">offsets.from</code> a <code className="font-mono bg-white px-1 rounded">offsets.to</code> v ms od začátku klipu.
                Tyto segmenty jsou uloženy jako <code className="font-mono bg-white px-1 rounded">_segments</code> ve výsledku přepisu.
              </p>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded p-3">
              <div className="font-semibold text-blue-900 mb-1">2. VTT titulky</div>
              <p className="text-blue-800">
                Referenční titulky ve formátu <code className="font-mono bg-white px-1 rounded">.vtt</code> mají přesná časová razítka.
                Pro každý Whisper segment se z VTT extrahuje text, který v daném časovém okně zazní —
                to je ground truth pro daný úsek.
              </p>
            </div>
            <div className="bg-green-50 border border-green-200 rounded p-3">
              <div className="font-semibold text-green-900 mb-1">3. Per-segment WER</div>
              <p className="text-green-800">
                Pro každý segment se spočítá WER zvlášť. Výsledek je seznam
                <code className="font-mono bg-white px-1 rounded mx-1">{'{ start_s, end_s, asr_text, ref_text, wer }'}</code>
                — vidíš přesně kde model zaváhal.
              </p>
            </div>
          </div>

          {/* Vizuální příklad segment timeline */}
          <div>
            <div className="text-xs font-medium text-gray-500 mb-2">Příklad segment timeline:</div>
            <div className="space-y-1 font-mono text-xs">
              {[
                { start: '0.0s', end: '5.2s', wer: 0.0, asr: 'Dobrý den, vítám vás v pořadu', ref: 'Dobrý den, vítám vás v pořadu' },
                { start: '5.2s', end: '11.8s', wer: 0.08, asr: 'dnes budeme mluvit o autizmu', ref: 'dnes budeme mluvit o autismu' },
                { start: '11.8s', end: '19.4s', wer: 0.33, asr: 'Vrženého ochránce práv pro oblast', ref: 'veřejného ochránce práv pro oblast' },
                { start: '19.4s', end: '26.0s', wer: 0.5, asr: 'Pod louhem hledání se má dospila', ref: 'Po dlouhém hledání sama dospěla' },
              ].map((seg, i) => {
                const color = seg.wer === 0 ? 'bg-green-100 border-green-300 text-green-800'
                  : seg.wer < 0.15 ? 'bg-yellow-100 border-yellow-300 text-yellow-800'
                  : seg.wer < 0.3 ? 'bg-orange-100 border-orange-300 text-orange-800'
                  : 'bg-red-100 border-red-300 text-red-800'
                const werLabel = seg.wer === 0 ? 'WER 0 %' : `WER ${(seg.wer * 100).toFixed(0)} %`
                return (
                  <div key={i} className="border border-gray-200 rounded overflow-hidden">
                    <div className="flex items-center gap-2 bg-gray-50 px-3 py-1 border-b border-gray-200">
                      <span className="text-gray-400">{seg.start} – {seg.end}</span>
                      <span className={`px-1.5 py-0.5 rounded text-xs font-bold border ${color}`}>{werLabel}</span>
                    </div>
                    <div className="grid grid-cols-2 divide-x divide-gray-200">
                      <div className="px-3 py-1.5">
                        <span className="text-gray-400 text-xs">ASR: </span>
                        <span className="text-gray-700">{seg.asr}</span>
                      </div>
                      <div className="px-3 py-1.5">
                        <span className="text-gray-400 text-xs">REF: </span>
                        <span className="text-gray-700">{seg.ref}</span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Sekce — Word Diff vizualizace */}
      <div className="bg-white border border-gray-200 rounded overflow-hidden">
        <div className="bg-violet-50 border-b border-violet-200 px-4 py-3 flex items-center gap-3">
          <span className="bg-violet-100 text-violet-800 font-bold font-mono text-xs px-2 py-0.5 rounded">Viz</span>
          <span className="font-semibold text-gray-800 text-sm">Word Diff — vizualizace chyb na úrovni slov</span>
        </div>
        <div className="px-4 py-4 space-y-4 text-sm text-gray-700">
          <p>
            Word diff zobrazí přepis modelu vedle referenčního textu, přičemž každé slovo je obarveno podle toho,
            jak ho model přepsal. Jde o Levenshteinovo zarovnání na úrovni slov — stejný algoritmus jako WER, ale vizuálně.
          </p>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded px-3 py-2">
              <span className="bg-green-200 text-green-900 px-2 py-0.5 rounded font-mono font-bold">slovo</span>
              <span className="text-green-800">Správně (=)</span>
            </div>
            <div className="flex items-center gap-2 bg-yellow-50 border border-yellow-200 rounded px-3 py-2">
              <span className="bg-yellow-200 text-yellow-900 px-2 py-0.5 rounded font-mono font-bold line-through">ref</span>
              <span className="text-yellow-800">Záměna: ref→hyp (S)</span>
            </div>
            <div className="flex items-center gap-2 bg-red-50 border border-red-200 rounded px-3 py-2">
              <span className="bg-red-200 text-red-900 px-2 py-0.5 rounded font-mono font-bold line-through">slovo</span>
              <span className="text-red-800">Vynecháno (D)</span>
            </div>
            <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded px-3 py-2">
              <span className="bg-blue-200 text-blue-900 px-2 py-0.5 rounded font-mono font-bold">[navíc]</span>
              <span className="text-blue-800">Přidáno navíc (I)</span>
            </div>
          </div>

          {/* Vizuální příklad word diff */}
          <div>
            <div className="text-xs font-medium text-gray-500 mb-2">Příklad — jeden segment:</div>
            <div className="bg-gray-50 border border-gray-200 rounded p-3 leading-7 font-mono text-sm space-y-2">
              <div>
                <span className="text-gray-400 text-xs mr-2">REF:</span>
                <span className="bg-green-100 text-green-900 rounded px-1 mx-0.5">Po</span>
                <span className="bg-green-100 text-green-900 rounded px-1 mx-0.5">dlouhém</span>
                <span className="bg-green-100 text-green-900 rounded px-1 mx-0.5">hledání</span>
                <span className="bg-green-100 text-green-900 rounded px-1 mx-0.5">sama</span>
                <span className="bg-green-100 text-green-900 rounded px-1 mx-0.5">dospěla</span>
              </div>
              <div>
                <span className="text-gray-400 text-xs mr-2">HYP:</span>
                <span className="bg-yellow-100 text-yellow-900 rounded px-1 mx-0.5 line-through">Pod</span>
                <span className="bg-yellow-100 text-yellow-900 rounded px-1 mx-0.5">↓</span>
                <span className="bg-green-100 text-green-900 rounded px-1 mx-0.5">louhem</span>
                <span className="bg-green-100 text-green-900 rounded px-1 mx-0.5">hledání</span>
                <span className="bg-blue-100 text-blue-900 rounded px-1 mx-0.5">[se]</span>
                <span className="bg-blue-100 text-blue-900 rounded px-1 mx-0.5">[má]</span>
                <span className="bg-red-100 text-red-900 rounded px-1 mx-0.5 line-through">dospila</span>
              </div>
            </div>
            <div className="flex flex-wrap gap-4 text-xs text-gray-500 mt-2 px-1">
              <span>„Po" → „Pod" = záměna (S)</span>
              <span>„dlouhém" → „louhem" = záměna (S)</span>
              <span>„se", „má" = přidáno navíc (I)</span>
              <span>„sama" = vynecháno (D)</span>
              <span>„dospěla" → „dospila" = záměna (S)</span>
            </div>
          </div>

          <p className="text-xs text-gray-500">
            Word diff je dostupný v detailu výsledku po kliknutí na <strong>▼ diff</strong> v tabulce výsledků,
            nebo v záložce přepisu na stránce Benchmark.
          </p>
        </div>
      </div>
    </div>
  )
}

function ResultsTable({ results, runId }: { results: RunResult[]; runId: string }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const allKeys = results.map(r => `${r.model_id}-${r.setting_id}`)
  const [visible, setVisible] = useState<Set<string>>(new Set(allKeys))

  // Sync when results change
  useEffect(() => {
    setVisible(new Set(results.map(r => `${r.model_id}-${r.setting_id}`)))
  }, [results.length])

  function toggleVisible(key: string) {
    setVisible(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  const filtered = results.filter(r => visible.has(`${r.model_id}-${r.setting_id}`))

  return (
    <div className="space-y-2">
      {/* Filtr výsledků */}
      <div className="bg-white rounded border border-gray-200 p-3 flex flex-wrap gap-2 items-center">
        <span className="text-xs text-gray-500 font-medium mr-1">Zobrazit:</span>
        {results.map(r => {
          const key = `${r.model_id}-${r.setting_id}`
          return (
            <label key={key} className="flex items-center gap-1 text-xs cursor-pointer select-none">
              <input type="checkbox" checked={visible.has(key)} onChange={() => toggleVisible(key)}
                className="accent-blue-500" />
              <span className={visible.has(key) ? 'text-gray-800' : 'text-gray-400'}>
                {r.model_id} / {r.setting_id}
              </span>
            </label>
          )
        })}
        <button onClick={() => setVisible(new Set(allKeys))} className="text-xs text-blue-500 hover:underline ml-2">
          vše
        </button>
        <button onClick={() => setVisible(new Set())} className="text-xs text-gray-400 hover:underline">
          nic
        </button>
      </div>

    <div className="bg-white rounded border border-gray-200 overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-xs text-gray-500 uppercase">
          <tr>
            <th className="px-4 py-2 text-left">Model</th>
            <th className="px-4 py-2 text-left">Nastavení</th>
            <th className="px-4 py-2" title="Word Error Rate">WER ⓘ</th>
            <th className="px-4 py-2" title="Character Error Rate">CER ⓘ</th>
            <th className="px-4 py-2" title="WER po odstranění interpunkce a šumu">WER norm. ⓘ</th>
            <th className="px-4 py-2" title="Match Error Rate">MER ⓘ</th>
            <th className="px-4 py-2" title="Word Information Lost">WIL ⓘ</th>
            <th className="px-4 py-2">Latence</th>
            <th className="px-4 py-2" title="Real-Time Factor — RTF < 1.0 = stíhá živý přepis">RTF ⓘ</th>
            <th className="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((r, ri) => {
            const key = `${r.model_id}-${r.setting_id}`
            const isOpen = expanded === key
            const hasDiff = r.source_metrics?.some(
              (s: SourceMetric) => (s.transcript && s.reference_text) || (s.chunk_metrics && s.chunk_metrics.length > 0)
            )
            return (
              <>
                <tr key={key} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs">{r.model_id}</td>
                  <td className="px-4 py-2 text-gray-600">{r.setting_label}</td>
                  <td className="px-4 py-2 text-center"><WerBadge value={r.aggregate.wer} label="" /></td>
                  <td className="px-4 py-2 text-center"><WerBadge value={r.aggregate.cer} label="" /></td>
                  <td className="px-4 py-2 text-center"><WerBadge value={r.aggregate.wer_normalized} label="" /></td>
                  <td className="px-4 py-2 text-center"><WerBadge value={r.aggregate.mer} label="" /></td>
                  <td className="px-4 py-2 text-center">
                    {r.aggregate.wil != null
                      ? <span className={r.aggregate.wil > 0.35 ? 'text-red-500' : r.aggregate.wil > 0.15 ? 'text-yellow-600' : 'text-green-600'}>
                          {r.aggregate.wil.toFixed(3)}
                        </span>
                      : <span className="text-gray-400">–</span>}
                  </td>
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
                  <tr key={`${key}-diff-${si}`} className="border-t border-blue-50 bg-blue-50/30">
                    <td colSpan={10} className="px-4 py-3 space-y-3">
                      {/* Hlavička zdroje + per-source metriky */}
                      <div className="flex items-center gap-4 text-xs text-gray-500 font-medium flex-wrap">
                        <span>
                          Zdroj {si + 1}{s.video_id ? ` — ${s.video_id}` : ''}
                          {s.clip_start_seconds != null ? ` @ ${s.clip_start_seconds}s` : ''}
                        </span>
                        {(s.engine_elapsed_seconds != null || s.clip_seconds != null) && (
                          <span className="bg-white border border-gray-200 rounded px-2 py-0.5 font-mono">
                            {s.engine_elapsed_seconds != null ? `přepis ${s.engine_elapsed_seconds.toFixed(1)}s` : ''}
                            {s.engine_elapsed_seconds != null && s.clip_seconds != null ? ' / ' : ''}
                            {s.clip_seconds != null ? `audio ${s.clip_seconds.toFixed(0)}s` : ''}
                            {s.rtf != null && (
                              <span className={`ml-1 font-bold ${s.rtf > 1 ? 'text-red-500' : 'text-green-600'}`}>
                                RTF {s.rtf.toFixed(2)}
                              </span>
                            )}
                          </span>
                        )}
                        {s.wer_normalized != null && <span className="bg-teal-50 border border-teal-200 rounded px-2 py-0.5">WER norm. {(s.wer_normalized * 100).toFixed(1)} %</span>}
                        {s.mer != null && <span className="bg-orange-50 border border-orange-200 rounded px-2 py-0.5">MER {(s.mer * 100).toFixed(1)} %</span>}
                        {s.wil != null && <span className="bg-rose-50 border border-rose-200 rounded px-2 py-0.5">WIL {s.wil.toFixed(3)}</span>}
                      </div>

                      {/* Tabulka chunk metrik */}
                      {s.chunk_metrics && s.chunk_metrics.length > 0 && (
                        <ChunkMetricsTable chunks={s.chunk_metrics} />
                      )}

                      {/* Word diff přes API */}
                      {s.transcript && s.reference_text && (
                        <ApiWordDiff runId={runId} resultIdx={ri} sourceIdx={si} />
                      )}
                    </td>
                  </tr>
                ))}
              </>
            )
          })}
        </tbody>
      </table>
    </div>
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

function ChunkMetricsTable({ chunks }: { chunks: ChunkMetric[] }) {
  const avgRtf = chunks.reduce((s, c) => s + c.rtf, 0) / chunks.length
  const maxRtf = Math.max(...chunks.map(c => c.rtf))
  const totalWords = chunks.reduce((s, c) => s + c.words, 0)
  const lastChunk = chunks[chunks.length - 1]
  const totalDelay = lastChunk ? lastChunk.total_elapsed_s - lastChunk.chunk_end_s : null

  return (
    <div className="space-y-2">
      {/* Souhrn */}
      <div className="flex flex-wrap gap-3 text-xs font-mono bg-gray-50 border border-gray-200 rounded px-3 py-2">
        <span>Chunků: <strong>{chunks.length}</strong></span>
        <span>Slov: <strong>{totalWords}</strong></span>
        <span>Průměrné RTF: <strong className={avgRtf > 1 ? 'text-red-500' : 'text-green-600'}>{avgRtf.toFixed(2)}</strong></span>
        <span>Max RTF: <strong className={maxRtf > 1 ? 'text-red-500' : 'text-green-600'}>{maxRtf.toFixed(2)}</strong></span>
        {totalDelay != null && (
          <span>Celkové zpoždění: <strong>{totalDelay.toFixed(1)}s</strong></span>
        )}
      </div>
      {/* Per-chunk tabulka */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono border border-gray-200 rounded">
          <thead className="bg-gray-100 text-gray-500">
            <tr>
              <th className="px-2 py-1 text-left">#</th>
              <th className="px-2 py-1 text-right">Audio</th>
              <th className="px-2 py-1 text-right">Dur</th>
              <th className="px-2 py-1 text-right">Přepis</th>
              <th className="px-2 py-1 text-right">RTF</th>
              <th className="px-2 py-1 text-right">Elapsed</th>
              <th className="px-2 py-1 text-right">Zpoždění</th>
              <th className="px-2 py-1 text-right">Slov</th>
            </tr>
          </thead>
          <tbody>
            {chunks.map((c, i) => {
              const delay = c.total_elapsed_s - c.chunk_end_s
              return (
                <tr key={i} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="px-2 py-0.5 text-gray-400">{i + 1}</td>
                  <td className="px-2 py-0.5 text-right">{c.chunk_start_s.toFixed(0)}→{c.chunk_end_s.toFixed(0)}s</td>
                  <td className="px-2 py-0.5 text-right">{c.chunk_duration_s.toFixed(0)}s</td>
                  <td className="px-2 py-0.5 text-right">{c.processing_s.toFixed(1)}s</td>
                  <td className={`px-2 py-0.5 text-right font-bold ${c.rtf > 1 ? 'text-red-500' : 'text-green-600'}`}>
                    {c.rtf.toFixed(2)}
                  </td>
                  <td className="px-2 py-0.5 text-right text-gray-600">{c.total_elapsed_s.toFixed(1)}s</td>
                  <td className={`px-2 py-0.5 text-right ${delay > 5 ? 'text-orange-500' : 'text-gray-600'}`}>
                    +{delay.toFixed(1)}s
                  </td>
                  <td className="px-2 py-0.5 text-right text-gray-600">{c.words}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

type DiffEntry = { op: string; ref: string | null; hyp: string | null }
type DiffResult = { diff: DiffEntry[]; stats: { total: number; correct: number; substitutions: number; deletions: number; insertions: number } }

function ApiWordDiff({ runId, resultIdx, sourceIdx }: { runId: string; resultIdx: number; sourceIdx: number }) {
  const [data, setData] = useState<DiffResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  async function load() {
    setLoading(true); setErr('')
    try {
      const d = await api.runs.wordDiff(runId, resultIdx, sourceIdx)
      setData(d)
    } catch (e: any) { setErr(e.message) }
    setLoading(false)
  }

  if (!data && !loading && !err) {
    return (
      <button onClick={load} className="text-xs text-violet-600 hover:underline">
        Načíst word diff (API)
      </button>
    )
  }
  if (loading) return <span className="text-xs text-gray-400">Načítám diff…</span>
  if (err) return <span className="text-xs text-red-500">Chyba diffu: {err}</span>
  if (!data) return null

  const { diff, stats } = data
  const wer = stats.total > 0 ? ((stats.substitutions + stats.deletions + stats.insertions) / (stats.total - stats.insertions) * 100).toFixed(1) : '–'

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-3 text-xs font-mono bg-gray-50 border border-gray-200 rounded px-3 py-2">
        <span>Správně: <strong className="text-green-600">{stats.correct}</strong></span>
        <span>Záměny: <strong className="text-yellow-600">{stats.substitutions}</strong></span>
        <span>Vynecháno: <strong className="text-red-500">{stats.deletions}</strong></span>
        <span>Přidáno: <strong className="text-blue-500">{stats.insertions}</strong></span>
        <span>WER: <strong>{wer} %</strong></span>
      </div>
      <div className="bg-white border border-gray-200 rounded p-3 leading-7 font-mono text-sm flex flex-wrap gap-0.5">
        {diff.map((d, i) => {
          if (d.op === '=') return (
            <span key={i} className="bg-green-100 text-green-900 rounded px-1">{d.hyp}</span>
          )
          if (d.op === 'S') return (
            <span key={i} className="inline-flex flex-col items-center">
              <span className="bg-yellow-100 text-yellow-900 rounded px-1 line-through text-xs">{d.ref}</span>
              <span className="bg-yellow-200 text-yellow-900 rounded px-1">{d.hyp}</span>
            </span>
          )
          if (d.op === 'D') return (
            <span key={i} className="bg-red-100 text-red-900 rounded px-1 line-through opacity-70">{d.ref}</span>
          )
          if (d.op === 'I') return (
            <span key={i} className="bg-blue-100 text-blue-900 rounded px-1 italic">[{d.hyp}]</span>
          )
          return null
        })}
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
