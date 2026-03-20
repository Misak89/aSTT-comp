/** Zobrazí WER číslo s barvou: zelená < 5%, žlutá < 15%, červená jinak */
export function WerBadge({ value, label = 'WER' }: { value: number | null; label?: string }) {
  if (value == null) return <span className="text-gray-400 text-xs">–</span>
  const pct = (value * 100).toFixed(1)
  const color =
    value < 0.05 ? 'text-green-600' :
    value < 0.15 ? 'text-yellow-600' :
    'text-red-600'
  return (
    <span className={`text-sm font-mono font-semibold ${color}`}>
      {label} {pct}%
    </span>
  )
}
