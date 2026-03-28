import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ModelStatus, ModelEvent, ModelDescriptor, WebAppAutostartStatus } from '../types'

// Popis adaptérů — mapování adapter ID → info
const ADAPTER_INFO: Record<string, { label: string; source: string; description: string }> = {
  whisper_cpp: {
    label: 'whisper.cpp',
    source: 'packages/adapters/whisper_cpp_runner.py',
    description: 'Offline inference přes whisper-cli binary (subprocess). Nepodporuje streaming — zpracovává celý soubor najednou. Potřebuje whisper-cli.exe v runtime/model_store/.',
  },
  vosk: {
    label: 'VOSK (Kaldi)',
    source: 'packages/adapters/vosk_runner.py',
    description: 'Offline streaming přes VOSK Python API (Kaldi backend). Dedikované české modely, velmi nízké nároky na RAM (<200 MB), nízká latence.',
  },
  sherpa_onnx: {
    label: 'sherpa-onnx',
    source: 'packages/adapters/sherpa_onnx_runner.py',
    description: 'Streaming ONNX inference. Cross-platform, nízká latence. Aktuální model je EN-only.',
  },
  qwen_asr: {
    label: 'Qwen3-ASR (HuggingFace transformers)',
    source: 'packages/adapters/qwen_asr_runner.py',
    description: 'LLM-based ASR přes HuggingFace transformers. Vysoká přesnost, vysoké HW nároky (RAM 4–8 GB). Na Windows VŽDY float32 — bfloat16 způsobuje crash.',
  },
  moonshine: {
    label: 'Moonshine (Useful Sensors)',
    source: 'packages/adapters/moonshine_runner.py',
    description: 'Moderní streaming ASR model. 245M params, WER 6.65% na EN LibriSpeech. Zatím pouze angličtina.',
  },
}

// Popis settingů (chunk params)
const SETTINGS_DOC = [
  { id: 'low_latency',   label: 'Low latency (15s)',   chunk_seconds: 15, threads: 4, beam_size: 1,  no_fallback: true,
    description: 'Nejkratší chunky — nejnižší latence, ale nižší přesnost. Vhodné pro živý dialog.' },
  { id: 'balanced',      label: 'Balanced (30s)',      chunk_seconds: 30, threads: 4, beam_size: 5,  no_fallback: true,
    description: 'Kompromis latence vs přesnost. Výchozí nastavení pro většinu testů.' },
  { id: 'high_accuracy', label: 'High accuracy (60s)', chunk_seconds: 60, threads: 4, beam_size: 5,  no_fallback: false,
    description: 'Nejdelší chunky — nejlepší přesnost, ale nejvyšší latence. Vhodné pro offline přepis.' },
  { id: 'memory_saver',  label: 'Memory saver (30s)',  chunk_seconds: 30, threads: 2, beam_size: 1,  no_fallback: true,
    description: 'Méně vláken a beam_size=1 — šetří RAM a CPU. Pro slabší stroje.' },
]

export function ModelsPage() {
  const [models, setModels] = useState<ModelStatus[]>([])
  const [registry, setRegistry] = useState<ModelDescriptor[]>([])
  const [autostart, setAutostart] = useState<WebAppAutostartStatus | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [noteInput, setNoteInput] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const autostartSupported = autostart?.supported ?? true

  useEffect(() => { loadAll() }, [])

  async function loadAll() {
    try {
      const [statuses, reg] = await Promise.all([api.models.list(), api.models.registry()])
      setModels(statuses)
      setRegistry(reg)
      try {
        const auto = await api.models.getWebAppAutostart()
        setAutostart(auto)
      } catch {
        setAutostart(null)
      }
    } catch (e: any) {
      setMsg(`Chyba načítání: ${e.message}`)
    }
  }

  async function recordInstall(id: string) {
    try {
      await api.models.recordInstall(id)
      await loadAll()
    } catch (e: any) { setMsg(`Chyba: ${e.message}`) }
  }

  async function recordUninstall(id: string) {
    try {
      await api.models.recordUninstall(id)
      await loadAll()
    } catch (e: any) { setMsg(`Chyba: ${e.message}`) }
  }

  async function addNote(id: string) {
    const note = noteInput[id]?.trim()
    if (!note) return
    try {
      await api.models.addNote(id, note)
      setNoteInput(prev => ({ ...prev, [id]: '' }))
      await loadAll()
    } catch (e: any) { setMsg(`Chyba: ${e.message}`) }
  }

  async function enableAutostart() {
    try {
      const res = await api.models.enableWebAppAutostart()
      setAutostart(res)
      setMsg('Auto-start zapnutý.')
    } catch (e: any) {
      setMsg(`Chyba zapnutí auto-startu: ${e.message}`)
    }
  }

  async function disableAutostart() {
    try {
      const res = await api.models.disableWebAppAutostart()
      setAutostart(res)
      setMsg('Auto-start vypnutý.')
    } catch (e: any) {
      setMsg(`Chyba vypnutí auto-startu: ${e.message}`)
    }
  }

  // Vytvoř mapu registry pro rychlý lookup
  const registryMap = new Map(registry.map(r => [r.model_id, r]))

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Modely</h1>
        <button onClick={loadAll} className="text-xs text-blue-600 hover:underline">Obnovit</button>
      </div>

      {msg && <p className="text-sm text-red-600">{msg}</p>}

      {/* === Jak spustit / restartovat === */}
      <div className="bg-slate-900 rounded-lg border border-slate-700 p-4 space-y-4 text-sm">
        <h2 className="font-bold text-white text-base">Spuštění a restart</h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="space-y-1">
            <p className="text-slate-300 font-semibold">Backend (FastAPI)</p>
            <p className="text-slate-500 text-xs">Port 8012 · uvicorn · --reload = auto-restart při změně souboru</p>
            <pre className="bg-slate-800 rounded px-3 py-2 text-green-300 text-xs overflow-x-auto whitespace-pre-wrap select-all">
{`cd C:\\Users\\adamf\\OneDrive\\Dokumenty\\aSTT-comp
.venv\\Scripts\\python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8012 --reload`}
            </pre>
            <p className="text-slate-300 font-semibold pt-1">Jedním skriptem (doporučeno)</p>
            <pre className="bg-slate-800 rounded px-3 py-2 text-green-300 text-xs overflow-x-auto whitespace-pre-wrap select-all">
{`cd C:\\Users\\adamf\\OneDrive\\Dokumenty\\aSTT-comp
start_web_app.cmd`}
            </pre>
            <p className="text-slate-500 text-xs">Restart: Ctrl+C v terminálu → spusť znovu. Nebo ulož jakýkoliv .py soubor (--reload).</p>
          </div>

          <div className="space-y-1">
            <p className="text-slate-300 font-semibold">Frontend build (Vite → dist/)</p>
            <p className="text-slate-500 text-xs">Po každé změně .tsx/.ts souboru je potřeba rebuild!</p>
            <pre className="bg-slate-800 rounded px-3 py-2 text-green-300 text-xs overflow-x-auto whitespace-pre-wrap select-all">
{`cd C:\\Users\\adamf\\OneDrive\\Dokumenty\\aSTT-comp
npm run build`}
            </pre>
            <p className="text-slate-500 text-xs">Výstup jde do <span className="font-mono text-slate-400">frontend/dist/</span> — backend ji servuje staticky.</p>
          </div>

          <div className="space-y-1">
            <p className="text-slate-300 font-semibold">Frontend dev server (live reload)</p>
            <p className="text-slate-500 text-xs">Alternativa k buildu — změny se projeví okamžitě na portu 5173</p>
            <pre className="bg-slate-800 rounded px-3 py-2 text-green-300 text-xs overflow-x-auto whitespace-pre-wrap select-all">
{`cd C:\\Users\\adamf\\OneDrive\\Dokumenty\\aSTT-comp
npm run dev`}
            </pre>
            <p className="text-slate-500 text-xs">Otevři <span className="font-mono text-slate-400">http://localhost:5173</span> (API proxuje na backend :8012).</p>
          </div>
        </div>

        <div className="rounded border border-slate-700 bg-slate-800/60 p-3 space-y-2">
          <div className="flex items-center gap-3 flex-wrap">
            <p className="text-slate-200 font-semibold">Auto-start web app po startu Windows</p>
            {autostart && (
              <span className={`text-xs px-2 py-0.5 rounded border ${autostart.enabled ? 'text-green-300 border-green-500/40 bg-green-900/20' : 'text-amber-300 border-amber-500/40 bg-amber-900/20'}`}>
                {autostart.enabled ? 'Zapnuto' : 'Vypnuto'}
              </span>
            )}
            <button
              onClick={enableAutostart}
              disabled={!autostartSupported}
              className="text-xs px-2.5 py-1 rounded border border-emerald-400 text-emerald-300 hover:bg-emerald-900/20 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Zapnout
            </button>
            <button
              onClick={disableAutostart}
              disabled={!autostartSupported}
              className="text-xs px-2.5 py-1 rounded border border-rose-400 text-rose-300 hover:bg-rose-900/20 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Vypnout
            </button>
            <button
              onClick={() => api.models.openWebAppStartupDir().catch(() => {})}
              disabled={!autostartSupported}
              className="text-xs px-2.5 py-1 rounded border border-slate-500 text-slate-300 hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Otevřít Startup složku
            </button>
          </div>
          <p className="text-slate-400 text-xs">
            Zapnutí vytvoří položku ve Startup složce, která po přihlášení spustí backend na <span className="font-mono text-slate-300">127.0.0.1:8012</span>.
          </p>
          {autostart?.entry_path && (
            <p className="text-slate-500 text-xs font-mono break-all">Entry: {autostart.entry_path}</p>
          )}
          {autostart?.script_path && (
            <p className="text-slate-500 text-xs font-mono break-all">Script: {autostart.script_path}</p>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-1">
            <p className="text-slate-300 font-semibold">Testy</p>
            <pre className="bg-slate-800 rounded px-3 py-2 text-green-300 text-xs overflow-x-auto whitespace-pre-wrap select-all">
{`# Unit testy (bez backendu)
.venv\\Scripts\\python -m pytest tests/unit/ -v

# Integration testy (potřebuje běžící backend)
.venv\\Scripts\\python -m pytest tests/integration/ -v`}
            </pre>
          </div>
          <div className="space-y-1">
            <p className="text-slate-300 font-semibold">Utility skripty</p>
            <pre className="bg-slate-800 rounded px-3 py-2 text-green-300 text-xs overflow-x-auto whitespace-pre-wrap select-all">
{`python scripts/check_health.py       # backend alive?
python scripts/preflight.py          # HW podmínky OK?
python scripts/check_model.py whisper_cpp_small
python scripts/copy_subtitles.py     # kopíruj VTT`}
            </pre>
          </div>
        </div>
      </div>

      {/* === Popis nastavení (settings / chunky) === */}
      <div className="bg-white rounded border border-gray-200">
        <div className="px-4 py-3 border-b border-gray-100">
          <h2 className="font-semibold text-sm text-gray-700">Benchmark nastavení (Settings)</h2>
          <p className="text-xs text-gray-400 mt-0.5">Každé nastavení definuje chunk_seconds, threads a beam_size — jedno video se testuje s každým nastavením zvlášť.</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-gray-500 uppercase">
              <tr>
                <th className="px-4 py-2 text-left">Název</th>
                <th className="px-4 py-2 text-right">chunk_seconds</th>
                <th className="px-4 py-2 text-right">threads</th>
                <th className="px-4 py-2 text-right">beam_size</th>
                <th className="px-4 py-2 text-center">no_fallback</th>
                <th className="px-4 py-2 text-left">Popis</th>
              </tr>
            </thead>
            <tbody>
              {SETTINGS_DOC.map(s => (
                <tr key={s.id} className="border-t border-gray-100">
                  <td className="px-4 py-2 font-medium text-gray-800">{s.label}</td>
                  <td className="px-4 py-2 text-right font-mono text-blue-700">{s.chunk_seconds}s</td>
                  <td className="px-4 py-2 text-right font-mono">{s.threads}</td>
                  <td className="px-4 py-2 text-right font-mono">{s.beam_size}</td>
                  <td className="px-4 py-2 text-center font-mono">{s.no_fallback ? '✓' : '✗'}</td>
                  <td className="px-4 py-2 text-gray-500">{s.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* === Seznam modelů === */}
      <div className="space-y-3">
        {models.map(m => {
          const reg = registryMap.get(m.model_id)
          const adapterInfo = reg ? ADAPTER_INFO[reg.adapter] : undefined
          const isExpanded = expanded === m.model_id

          return (
            <div key={m.model_id} className="bg-white rounded border border-gray-200">
              {/* Hlavička */}
              <div className="px-4 py-3 flex items-center gap-4 flex-wrap">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold text-sm text-gray-900">{m.label}</span>
                    <span className="font-mono text-xs text-gray-400 bg-gray-50 border border-gray-200 px-1.5 py-0.5 rounded">{m.model_id}</span>
                    {reg && (
                      <span className="text-xs text-gray-500 bg-blue-50 border border-blue-100 px-1.5 py-0.5 rounded">
                        {reg.adapter}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-0.5 text-xs text-gray-500 flex-wrap">
                    {m.installed
                      ? <>
                          <span className="text-green-600 font-medium">✓ Nainstalován</span>
                          {m.size_mb != null && <span>{m.size_mb} MB</span>}
                          {m.last_install && <span>od {m.last_install.slice(0, 10)}</span>}
                        </>
                      : <span className="text-orange-500">✗ Nenainstalován</span>}
                    {reg && (
                      <>
                        <span className="text-gray-300">|</span>
                        <span>Jazyky: {reg.languages.join(', ')}</span>
                        {reg.supports_streaming && <span className="text-green-600">streaming</span>}
                        {reg.supports_microphone && <span className="text-blue-600">mikrofon</span>}
                      </>
                    )}
                  </div>
                  {reg?.notes && (
                    <p className="text-xs text-amber-700 mt-1 bg-amber-50 border border-amber-100 rounded px-2 py-0.5 inline-block">
                      ⚠ {reg.notes}
                    </p>
                  )}
                </div>

                {/* Akce */}
                <div className="flex items-center gap-2 text-xs flex-wrap">
                  {m.installed && (
                    <button
                      onClick={() => api.models.openStoreDir(m.model_id).catch(() => {})}
                      className="bg-gray-50 hover:bg-gray-100 text-gray-600 border border-gray-200 px-2 py-1 rounded"
                      title={`Otevřít runtime/model_store/${m.model_id}/`}
                    >
                      📁 Soubory
                    </button>
                  )}
                  <button
                    onClick={() => recordInstall(m.model_id)}
                    className="bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 px-3 py-1 rounded"
                  >
                    + Zaznamenat install
                  </button>
                  {m.installed && (
                    <button
                      onClick={() => recordUninstall(m.model_id)}
                      className="bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 px-3 py-1 rounded"
                    >
                      Odinstalovat
                    </button>
                  )}
                  <button
                    onClick={() => setExpanded(isExpanded ? null : m.model_id)}
                    className="text-gray-500 hover:text-gray-700 border border-gray-200 px-3 py-1 rounded"
                  >
                    {isExpanded ? '▲ Skrýt' : '▼ Detail'}
                    {m.events.length > 0 && (
                      <span className="ml-1 bg-gray-100 text-gray-600 rounded-full px-1.5">{m.events.length}</span>
                    )}
                  </button>
                </div>
              </div>

              {/* Rozbalený detail */}
              {isExpanded && (
                <div className="border-t border-gray-100 px-4 py-4 space-y-4">

                  {/* Popis adaptéru */}
                  {adapterInfo && (
                    <div className="space-y-1">
                      <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Adaptér</p>
                      <p className="text-sm text-gray-800 font-medium">{adapterInfo.label}</p>
                      <p className="text-xs text-gray-500">{adapterInfo.description}</p>
                      <p className="text-xs text-gray-400 font-mono">Zdroj kódu: {adapterInfo.source}</p>
                    </div>
                  )}

                  {/* Parametry modelu */}
                  {reg && reg.params.length > 0 && (
                    <div className="space-y-1">
                      <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide">
                        Parametry modelu ({reg.params.length})
                      </p>
                      <div className="overflow-x-auto rounded border border-gray-100">
                        <table className="w-full text-xs">
                          <thead className="bg-gray-50 text-gray-500">
                            <tr>
                              <th className="px-3 py-1.5 text-left font-medium">Parametr</th>
                              <th className="px-3 py-1.5 text-left font-medium">Popis</th>
                              <th className="px-3 py-1.5 text-left font-medium">Typ</th>
                              <th className="px-3 py-1.5 text-right font-medium">Výchozí</th>
                              <th className="px-3 py-1.5 text-right font-medium">Min</th>
                              <th className="px-3 py-1.5 text-right font-medium">Max</th>
                              <th className="px-3 py-1.5 text-left font-medium">Možnosti</th>
                            </tr>
                          </thead>
                          <tbody>
                            {reg.params.map(p => (
                              <tr key={p.name} className="border-t border-gray-100">
                                <td className="px-3 py-1.5 font-mono text-gray-800 font-medium">{p.name}</td>
                                <td className="px-3 py-1.5 text-gray-600">{p.description || p.label}</td>
                                <td className="px-3 py-1.5 text-gray-400">{p.type}</td>
                                <td className="px-3 py-1.5 text-right font-mono text-blue-700">{String(p.default)}</td>
                                <td className="px-3 py-1.5 text-right font-mono text-gray-400">{p.min ?? '–'}</td>
                                <td className="px-3 py-1.5 text-right font-mono text-gray-400">{p.max ?? '–'}</td>
                                <td className="px-3 py-1.5 text-gray-400">{p.options?.length ? p.options.join(', ') : '–'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Log instalací */}
                  <div className="space-y-2">
                    <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide">Log instalací</p>
                    <div className="flex gap-2">
                      <input
                        value={noteInput[m.model_id] || ''}
                        onChange={e => setNoteInput(prev => ({ ...prev, [m.model_id]: e.target.value }))}
                        placeholder="Přidat poznámku..."
                        className="flex-1 border rounded px-2 py-1 text-xs"
                        onKeyDown={e => e.key === 'Enter' && addNote(m.model_id)}
                      />
                      <button
                        onClick={() => addNote(m.model_id)}
                        className="text-xs bg-gray-50 hover:bg-gray-100 border border-gray-200 px-3 py-1 rounded"
                      >
                        Přidat
                      </button>
                    </div>
                    {m.events.length === 0 ? (
                      <p className="text-xs text-gray-400">Žádné záznamy.</p>
                    ) : (
                      <div className="space-y-1">
                        {[...m.events].reverse().map((ev, i) => (
                          <EventRow key={i} ev={ev} />
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}

        {models.length === 0 && !msg && (
          <p className="text-gray-400 text-sm">Načítání modelů...</p>
        )}
      </div>

      {/* === Adresáře — klikatelné === */}
      <div className="bg-gray-50 rounded border border-gray-200 p-4 text-xs text-gray-500 space-y-3">
        <p className="font-semibold text-gray-700 text-sm">Adresáře (klikni pro otevření v průzkumníku)</p>

        <div className="space-y-1.5">
          <DirLink
            onClick={() => api.openDir.modelStore()}
            path="runtime/model_store/"
            desc="soubory modelů (.bin, .ggml, .onnx...)"
          />
          <DirLink
            onClick={() => api.openDir.modelsLog()}
            path="docs/models/"
            desc="JSON logy instalací, odinstalací a poznámek"
          />
          <DirLink
            onClick={() => api.openDir.jobs()}
            path="runtime/jobs/"
            desc="config, progress.json, worker_result.json, log.txt pro každý job"
          />
          <DirLink
            onClick={() => api.openDir.runs()}
            path="runtime/runs/"
            desc="benchmark_matrix.json a artefakty každého runu"
          />
          <DirLink
            onClick={() => api.models.openWebAppStartupDir()}
            path={autostart?.startup_dir ?? '%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup'}
            desc="Windows Startup složka (auto-start položka web app)"
          />
        </div>

        <div className="border-t border-gray-200 pt-3 space-y-1">
          <p className="font-medium text-gray-600">Jak nainstalovat model:</p>
          <p>1. Zkopíruj soubory do <span className="font-mono">runtime/model_store/{'{model_id}'}/ </span></p>
          <p>2. Klikni <strong>Zaznamenat install</strong> — uloží datum, velikost a verzi do logu.</p>
          <p>3. Pro whisper.cpp: <span className="font-mono">whisper-cli.exe</span> musí být v <span className="font-mono">runtime/model_store/whisper_cpp_runtime/</span></p>
        </div>
      </div>
    </div>
  )
}

function DirLink({ onClick, path, desc }: { onClick: () => Promise<unknown>; path: string; desc: string }) {
  const [status, setStatus] = useState<'idle' | 'ok' | 'err'>('idle')
  async function handle() {
    try { await onClick(); setStatus('ok') } catch { setStatus('err') }
    setTimeout(() => setStatus('idle'), 2000)
  }
  return (
    <button
      onClick={handle}
      className="flex items-center gap-2 w-full text-left group hover:bg-gray-100 rounded px-2 py-1.5 transition-colors"
    >
      <span className="text-base">📁</span>
      <span className="font-mono text-blue-700 group-hover:underline">{path}</span>
      <span className="text-gray-400">—</span>
      <span className="text-gray-500">{desc}</span>
      {status === 'ok' && <span className="ml-auto text-green-600 text-xs">✓ otevřeno</span>}
      {status === 'err' && <span className="ml-auto text-red-500 text-xs">chyba — restartuj backend</span>}
    </button>
  )
}

function EventRow({ ev }: { ev: ModelEvent }) {
  const icons: Record<string, string> = {
    install: '⬇',
    uninstall: '✕',
    note: '📝',
  }
  const colors: Record<string, string> = {
    install: 'text-green-700 bg-green-50 border-green-200',
    uninstall: 'text-red-700 bg-red-50 border-red-200',
    note: 'text-gray-700 bg-gray-50 border-gray-200',
  }

  return (
    <div className={`flex items-start gap-2 text-xs rounded border px-2 py-1.5 ${colors[ev.type] || colors.note}`}>
      <span className="mt-0.5">{icons[ev.type] || '•'}</span>
      <div className="flex-1 min-w-0">
        <span className="font-medium capitalize">{ev.type}</span>
        {ev.version && <span className="ml-2 text-gray-500">v{ev.version}</span>}
        {ev.size_mb != null && <span className="ml-2 text-gray-500">{ev.size_mb} MB</span>}
        {ev.reason && <span className="ml-2 text-gray-500">({ev.reason})</span>}
        {ev.note && <span className="ml-2">{ev.note}</span>}
      </div>
      <span className="text-gray-400 whitespace-nowrap">{ev.date.slice(0, 16).replace('T', ' ')}</span>
    </div>
  )
}
