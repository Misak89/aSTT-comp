export function HWFlowPage() {
  return (
    <div className="space-y-5">
      <div className="bg-white rounded border border-gray-200 p-5">
        <h1 className="text-xl font-bold text-gray-900">HW Flow pro Live STT</h1>
        <p className="text-sm text-gray-600 mt-2">
          Cíl: stabilní offline přepis češtiny v reálném čase i na slabším kancelářském HW.
          Nestačí jen model a parametry. Rozhoduje i celý tok: mikrofon, OS, CPU/RAM rozpočet a akustika.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white rounded border border-gray-200 p-4">
          <h2 className="font-semibold text-gray-800 mb-2">Co je zásadní mimo software</h2>
          <ul className="text-sm text-gray-700 space-y-1">
            <li>• Mikrofonní řetězec: vzdálenost, citlivost, clipping, šum místnosti.</li>
            <li>• Stabilní napájení CPU: režim výkonu, bez agresivního throttlingu.</li>
            <li>• Reálná dostupná RAM: ne nominální, ale volná RAM během hovoru.</li>
            <li>• I/O a DPC latence: BT headsety, USB huby, audio ovladače.</li>
            <li>• Zátěž na pozadí: update služby, antivir, synchronizace cloudů.</li>
          </ul>
        </div>

        <div className="bg-white rounded border border-gray-200 p-4">
          <h2 className="font-semibold text-gray-800 mb-2">Praktický provozní budget</h2>
          <ul className="text-sm text-gray-700 space-y-1">
            <li>• RTF p95 ≤ 0.85 pro rezervu na horší chvíle.</li>
            <li>• First token p95 ≤ 1200 ms.</li>
            <li>• Capture jitter p95 ≤ 80 ms, capture lag p95 ≤ 250 ms.</li>
            <li>• Drop rate ≤ 1 %, backpressure eventy blízko nule.</li>
            <li>• Queue debt peak ideálně pod 400 ms.</li>
          </ul>
        </div>
      </div>

      <div className="bg-white rounded border border-gray-200 p-4">
        <h2 className="font-semibold text-gray-800 mb-2">Doporučený postup na slabším HW</h2>
        <ol className="text-sm text-gray-700 space-y-1">
          <li>1. Kalibrace mic: RMS/clipping/noise floor, pak krátký real-mic smoke.</li>
          <li>2. Začni low-latency parametry (nižší beam, kratší interval analýzy).</li>
          <li>3. Sleduj p95 metriky, ne jen průměr; p95 rozhoduje o použitelnosti.</li>
          <li>4. Při debt/backpressure nejdřív sniž kvalitu, až pak model.</li>
          <li>5. Potvrď finalistu v opakováních (n≥3) a po delším soak běhu.</li>
        </ol>
      </div>

      <div className="bg-indigo-50 rounded border border-indigo-200 p-4">
        <h2 className="font-semibold text-indigo-900 mb-2">Vize</h2>
        <p className="text-sm text-indigo-900">
          aSTT-comp má být rozhodovací systém, ne jen benchmark.
          U každého HW profilu má dát jednoznačné doporučení model+parametry s měřitelnou jistotou,
          včetně varování, kdy už sestava není pro live přepis bezpečná.
        </p>
      </div>
    </div>
  )
}
