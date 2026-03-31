import { useEffect, useState } from 'react'

const R = '#dc2626'
const red = (text: string) => <span style={{color: R}}>{text}</span>

export function HWFlowPage() {
  const [startedAt, setStartedAt] = useState<string | null>(null)
  const [ram, setRam] = useState<{used: number; total: number; pct: number} | null>(null)

  useEffect(() => {
    const fetch_ = () => fetch('/api/health').then(r => r.json()).then(d => {
      if (d.started_at) setStartedAt(d.started_at)
      if (d.ram_total_mb) setRam({ used: d.ram_used_mb, total: d.ram_total_mb, pct: d.ram_percent })
    }).catch(() => {})
    fetch_()
    const t = setInterval(fetch_, 10_000)
    return () => clearInterval(t)
  }, [])

  const fmtDt = (iso: string) => {
    try { return new Date(iso).toLocaleString('cs-CZ', { dateStyle: 'short', timeStyle: 'medium' }) }
    catch { return iso }
  }

  return (
    <div className="space-y-5">
      {/* Server info banner */}
      {startedAt && (
        <div className="bg-gray-50 rounded border border-gray-200 p-3 flex flex-wrap gap-6 text-sm">
          <div><span className="text-gray-500">Backend spuštěn:</span>{' '}
            <span className="font-mono font-medium">{fmtDt(startedAt)}</span>
          </div>
          {ram && (
            <div><span className="text-gray-500">RAM:</span>{' '}
              <span className={`font-mono font-medium ${ram.pct > 85 ? 'text-red-600' : 'text-gray-800'}`}>
                {ram.used.toLocaleString()} / {ram.total.toLocaleString()} MB ({ram.pct.toFixed(0)}%)
              </span>
            </div>
          )}
        </div>
      )}
      <div className="bg-white rounded border border-gray-200 p-5">
        <h1 className="text-xl font-bold text-gray-900">HW Flow pro Live STT</h1>
        <p className="text-sm text-gray-600 mt-2">
          Cíl: stabilní offline přepis češtiny v {red('reálném čase')} i na slabším kancelářském HW.
          Nestačí jen model a parametry. Rozhoduje i celý tok: {red('mikrofon')}, OS, CPU/{red('RAM')} rozpočet a akustika.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white rounded border border-gray-200 p-4">
          <h2 className="font-semibold text-gray-800 mb-2">Co je zásadní mimo software</h2>
          <ul className="text-sm text-gray-700 space-y-1">
            <li>• Mikrofonní řetězec: vzdálenost, citlivost, {red('clipping')}, šum místnosti.</li>
            <li>• Stabilní napájení {red('CPU')}: režim výkonu, bez agresivního {red('throttlingu')}.</li>
            <li>• Reálná dostupná {red('RAM')}: ne nominální, ale volná RAM během hovoru.</li>
            <li>• {red('I/O')} a {red('DPC latence')}: BT headsety, USB huby, audio ovladače.</li>
            <li>• Zátěž na pozadí: update služby, {red('antivir')}, synchronizace cloudů.</li>
          </ul>
        </div>

        <div className="bg-white rounded border border-gray-200 p-4">
          <h2 className="font-semibold text-gray-800 mb-2">Praktický provozní budget</h2>
          <ul className="text-sm text-gray-700 space-y-1">
            <li>• {red('RTF')} p95 ≤ 0.85 pro rezervu na horší chvíle.</li>
            <li>• First token p95 ≤ {red('1200 ms')}.</li>
            <li>• Capture jitter p95 ≤ 80 ms, capture lag p95 ≤ {red('250 ms')}.</li>
            <li>• {red('Drop rate')} ≤ 1 %, backpressure eventy blízko nule.</li>
            <li>• Queue {red('debt peak')} ideálně pod 400 ms.</li>
          </ul>
        </div>
      </div>

      <div className="bg-white rounded border border-gray-200 p-4">
        <h2 className="font-semibold text-gray-800 mb-2">Doporučený postup na slabším HW</h2>
        <ol className="text-sm text-gray-700 space-y-1">
          <li>1. Kalibrace {red('mic')}: RMS/clipping/noise floor, pak krátký real-mic smoke.</li>
          <li>2. Začni low-latency parametry (nižší {red('beam')}, kratší interval analýzy).</li>
          <li>3. Sleduj {red('p95')} metriky, ne jen průměr; p95 rozhoduje o použitelnosti.</li>
          <li>4. Při debt/backpressure nejdřív sniž {red('kvalitu')}, až pak model.</li>
          <li>5. Potvrď finalistu v opakováních ({red('n≥3')}) a po delším soak běhu.</li>
        </ol>
      </div>

      <div className="bg-white rounded border border-gray-200 p-4">
        <h2 className="font-semibold text-gray-800 mb-3 flex items-center gap-2">
          <span style={{color: R}}>Timing</span> auto-sekvence modelů - <span style={{color: R}}>Mikrofon</span>
          <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-red-100">
            <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="2" width="6" height="11" rx="3" className="stroke-gray-700"/>
              <path d="M5 10a7 7 0 0 0 14 0" className="stroke-gray-700"/>
              <line x1="12" y1="19" x2="12" y2="22" className="stroke-gray-700"/>
              <line x1="9" y1="22" x2="15" y2="22" className="stroke-gray-700"/>
              <circle cx="18" cy="6" r="3" className="fill-red-500 stroke-none"/>
            </svg>
          </span>
        </h2>
        <p className="text-sm text-gray-600 mb-3">
          Při spuštění sekvenčního testu (více modelů po sobě) funguje {red('časování')} takto:
        </p>
        <ul className="text-sm text-gray-700 space-y-2">
          <li>
            <span className="font-mono text-xs bg-gray-100 px-1 rounded">leadStartSeconds = 2-2,5 s</span>
            {' '}- každý další model spouští WebSocket {red('2-2,5 sekundy')} před začátkem svého slotu.
            Během tohoto okna backend načítá {red('model')} do paměti a inicializuje live session.
          </li>
          <li>
            <span className="font-mono text-xs bg-gray-100 px-1 rounded">loopCycleS = speechS + pauseS</span>
            {' '}- délka jednoho {red('slotu')} = čas nahrávání + pauza. Každý model dostane přesně jeden slot.
          </li>
          <li>
            <span className="font-mono text-xs bg-gray-100 px-1 rounded">hardTrialSeconds</span>
            {' '}- absolutní {red('maximum')} délky trialu. Pokud model nestihne doběhnout ani po stopu,
            je po <span className="font-mono text-xs bg-gray-100 px-1 rounded">graceSeconds</span> přeskočen.
          </li>
          <li>
            <strong>Problém pomalého načítání:</strong> Vosk / Sherpa-ONNX se načtou za &lt; 0,5 s - lead stačí.
            Whisper.cpp small trvá ~1-2 s - na hraně. Faster-Whisper large: {red('3-5 s')} - překročí lead time,
            první audio chunky se hromadí ve frontě ({red('Q-peak')} v historii bude vysoký hned na začátku).
          </li>
          <li>
            <strong>Adaptive early stop:</strong> Pokud model vrátí první slovo příliš pozdě
            (<span className="font-mono text-xs bg-gray-100 px-1 rounded">first_word_wall_ms &gt; latencyGuardSeconds</span>),
            systém zkrátí zbývající čas {red('trialu')}, aby stihl přechod na další model.
          </li>
        </ul>
        <p className="text-sm text-gray-500 mt-3">
          Tip: sleduj {red('Q-peak s')} v historii pokusů - vysoká hodnota hned v prvních sekundách trialu
          indikuje, že model nebyl načten včas a audio se hromadilo.
        </p>
      </div>

      <div className="bg-indigo-50 rounded border border-indigo-200 p-4">
        <h2 className="font-semibold text-indigo-900 mb-2">Vize</h2>
        <p className="text-sm text-indigo-900">
          aSTT-comp má být {red('rozhodovací systém')}, ne jen benchmark.
          U každého HW profilu má dát jednoznačné doporučení {red('model+parametry')} s měřitelnou jistotou,
          včetně varování, kdy už sestava není pro live přepis {red('bezpečná')}.
        </p>
      </div>
    </div>
  )
}
