/**
 * ModelParamsForm — dynamický formulář pro parametry STT modelu.
 *
 * Generuje se automaticky z ParamSpec[] (z /api/models/registry nebo /api/models/{id}/params).
 * Podporuje typy: int, float, str, bool, select.
 */
import type { ParamSpec } from '../types'
import { FieldHintLabel } from './UiPrimitives'

interface Props {
  modelId: string
  params: ParamSpec[]
  values: Record<string, unknown>
  onChange: (values: Record<string, unknown>) => void
  compact?: boolean
  hints?: Record<string, string>
  disabled?: boolean
  recommendations?: Record<string, unknown>
  onApplyRecommended?: (name: string, value: unknown) => void
}

export function ModelParamsForm({
  modelId,
  params,
  values,
  onChange,
  compact = false,
  hints,
  disabled = false,
  recommendations,
  onApplyRecommended,
}: Props) {
  if (!params || params.length === 0) return null

  function set(name: string, value: unknown) {
    onChange({ ...values, [name]: value })
  }

  function current(p: ParamSpec): unknown {
    return p.name in values ? values[p.name] : p.default
  }

  function hintFor(p: ParamSpec): string {
    const custom = hints?.[p.name]
    const parts = [
      custom && custom.trim() ? custom.trim() : (p.description || '').trim(),
      `Výchozí hodnota: ${formatInlineValue(p.default)}.`,
    ]
    if (typeof p.min === 'number' || typeof p.max === 'number') {
      parts.push(`Povolený rozsah: ${p.min ?? 'bez minima'} až ${p.max ?? 'bez maxima'}.`)
    }
    if (p.type === 'select' && p.options.length > 0) {
      parts.push(`Možnosti: ${p.options.join(', ')}.`)
    }
    return parts.filter(Boolean).join(' ')
  }

  return (
    <div className={compact ? 'space-y-1' : 'space-y-3'}>
      {params.map(p => (
        <div key={p.name} className={compact ? 'space-y-0.5' : 'flex flex-col gap-1'}>
          <div className={compact ? 'flex items-center gap-2 text-sm flex-wrap' : 'flex items-center gap-2'}>
            <div className="shrink-0 min-w-28">
              <FieldHintLabel hint={hintFor(p)} tone="dark" className="text-sm whitespace-nowrap">
                {p.label}
              </FieldHintLabel>
            </div>
            <ParamInput param={p} value={current(p)} onChange={v => set(p.name, v)} compact={compact} disabled={disabled} hint={hintFor(p)} />
            {recommendations && Object.prototype.hasOwnProperty.call(recommendations, p.name) && (
              <div className="flex items-center gap-1 text-[10px] text-emerald-300">
                <span title={`Doporučeno: ${formatInlineValue(recommendations[p.name])}`}>
                  dop: {formatInlineValue(recommendations[p.name])}
                </span>
                {onApplyRecommended && (
                  <button
                    type="button"
                    onClick={() => onApplyRecommended(p.name, recommendations[p.name])}
                    disabled={disabled}
                    title={`Nastavit doporučenou hodnotu ${formatInlineValue(recommendations[p.name])} pro ${p.label}.`}
                    className="rounded border border-emerald-800 px-1 py-0 text-[10px] text-emerald-200 hover:text-white disabled:opacity-50"
                  >
                    Použít
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function formatInlineValue(value: unknown): string {
  if (value == null || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'ano' : 'ne'
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '—'
  return String(value)
}

export function ParamInput({ param, value, onChange, compact, disabled = false, hint }: {
  param: ParamSpec
  value: unknown
  onChange: (v: unknown) => void
  compact: boolean
  disabled?: boolean
  hint?: string
}) {
  const cls = compact
    ? 'bg-gray-700 border border-gray-600 rounded px-1.5 py-0.5 text-sm text-white w-24 disabled:opacity-60'
    : 'bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-white w-full disabled:opacity-60'

  if (param.type === 'bool') {
    return (
      <input
        type="checkbox"
        checked={Boolean(value ?? param.default)}
        onChange={e => onChange(e.target.checked)}
        disabled={disabled}
        title={hint}
        className="w-4 h-4 accent-blue-500 disabled:opacity-60"
      />
    )
  }

  if (param.type === 'select') {
    return (
      <select
        value={String(value ?? param.default)}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        title={hint}
        className={cls}
      >
        {param.options.map(opt => (
          <option key={opt} value={opt}>{opt}</option>
        ))}
      </select>
    )
  }

  if (param.type === 'int') {
    return (
      <input
        type="number"
        step={1}
        min={param.min ?? undefined}
        max={param.max ?? undefined}
        value={String(value ?? param.default)}
        onChange={e => onChange(parseInt(e.target.value, 10) || param.default)}
        disabled={disabled}
        title={hint}
        className={cls}
      />
    )
  }

  if (param.type === 'float') {
    return (
      <input
        type="number"
        step={0.01}
        min={param.min ?? undefined}
        max={param.max ?? undefined}
        value={String(value ?? param.default)}
        onChange={e => onChange(parseFloat(e.target.value) || param.default)}
        disabled={disabled}
        title={hint}
        className={cls}
      />
    )
  }

  // str
  return (
    <input
    type="text"
    value={String(value ?? param.default)}
    onChange={e => onChange(e.target.value)}
    disabled={disabled}
    title={hint}
    className={cls}
  />
  )
}
