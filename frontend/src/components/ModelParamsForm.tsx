/**
 * ModelParamsForm — dynamický formulář pro parametry STT modelu.
 *
 * Generuje se automaticky z ParamSpec[] (z /api/models/registry nebo /api/models/{id}/params).
 * Podporuje typy: int, float, str, bool, select.
 */
import type { ParamSpec } from '../types'

interface Props {
  modelId: string
  params: ParamSpec[]
  values: Record<string, unknown>
  onChange: (values: Record<string, unknown>) => void
  compact?: boolean
}

export function ModelParamsForm({ modelId, params, values, onChange, compact = false }: Props) {
  if (!params || params.length === 0) return null

  function set(name: string, value: unknown) {
    onChange({ ...values, [name]: value })
  }

  function current(p: ParamSpec): unknown {
    return p.name in values ? values[p.name] : p.default
  }

  return (
    <div className={compact ? 'space-y-1' : 'space-y-3'}>
      {params.map(p => (
        <div key={p.name} className={compact ? 'flex items-center gap-2 text-sm' : 'flex flex-col gap-1'}>
          <label className="text-gray-300 whitespace-nowrap min-w-28 text-sm" title={p.description}>
            {p.label}
          </label>
          <ParamInput param={p} value={current(p)} onChange={v => set(p.name, v)} compact={compact} />
          {!compact && p.description && (
            <span className="text-xs text-gray-500">{p.description}</span>
          )}
        </div>
      ))}
    </div>
  )
}

function ParamInput({ param, value, onChange, compact }: {
  param: ParamSpec
  value: unknown
  onChange: (v: unknown) => void
  compact: boolean
}) {
  const cls = compact
    ? 'bg-gray-700 border border-gray-600 rounded px-1.5 py-0.5 text-sm text-white w-24'
    : 'bg-gray-700 border border-gray-600 rounded px-2 py-1 text-sm text-white w-full'

  if (param.type === 'bool') {
    return (
      <input
        type="checkbox"
        checked={Boolean(value ?? param.default)}
        onChange={e => onChange(e.target.checked)}
        className="w-4 h-4 accent-blue-500"
      />
    )
  }

  if (param.type === 'select') {
    return (
      <select
        value={String(value ?? param.default)}
        onChange={e => onChange(e.target.value)}
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
      className={cls}
    />
  )
}
