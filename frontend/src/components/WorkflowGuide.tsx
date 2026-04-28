export interface WorkflowStep {
  label: string
  detail?: string
}

interface WorkflowGuideProps {
  title?: string
  steps: WorkflowStep[]
  activeIndex?: number
  tone?: 'light' | 'dark'
  compact?: boolean
}

export function WorkflowGuide({
  title = 'Workflow',
  steps,
  activeIndex,
  tone = 'light',
  compact = false,
}: WorkflowGuideProps) {
  const dark = tone === 'dark'
  const wrapClass = dark
    ? 'rounded border border-slate-700 bg-slate-900/40 text-slate-100'
    : 'rounded border border-gray-200 bg-white text-gray-900'
  const titleClass = dark ? 'text-slate-300' : 'text-gray-600'
  const itemBase = dark ? 'border-slate-700 bg-slate-800/70' : 'border-gray-200 bg-gray-50'
  const itemActive = dark ? 'border-blue-400 bg-blue-950/60' : 'border-blue-500 bg-blue-50'
  const numberClass = dark ? 'bg-slate-700 text-slate-100' : 'bg-gray-200 text-gray-700'
  const activeNumberClass = 'bg-blue-600 text-white'
  const detailClass = dark ? 'text-slate-400' : 'text-gray-500'

  return (
    <div className={`${wrapClass} ${compact ? 'px-3 py-2' : 'p-3'}`} aria-label={title}>
      <div className={`mb-2 text-[11px] font-semibold uppercase tracking-wide ${titleClass}`}>
        {title}
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {steps.map((step, index) => {
          const active = activeIndex === index
          return (
            <div
              key={`${step.label}:${index}`}
              className={`min-w-[150px] flex-1 rounded border px-2 py-2 ${active ? itemActive : itemBase}`}
            >
              <div className="flex items-center gap-2">
                <span className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${active ? activeNumberClass : numberClass}`}>
                  {index + 1}
                </span>
                <span className="truncate text-sm font-semibold" title={step.label}>{step.label}</span>
              </div>
              {step.detail && (
                <div className={`mt-1 line-clamp-2 text-xs ${detailClass}`} title={step.detail}>
                  {step.detail}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
