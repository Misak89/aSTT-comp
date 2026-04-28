/**
 * TranscribePanelLayout — přizpůsobitelné rozložení pro stránku Přepis.
 *
 * Módy:
 * - horizontal: levý a pravý panel vedle sebe, táhnutelný oddělovač
 * - vertical: horní a dolní panel nad sebou, táhnutelný oddělovač
 * - tabs: záložky (levý/pravý obsah v jednom okně)
 *
 * Důležité: všechny tři módy jsou vždy přítomny v DOM (jen skryté),
 * aby nedocházelo k odmontování komponent a ztrátě stavu při přepnutí.
 */
import {
  useRef,
  useState,
  useCallback,
  useEffect,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'

type LayoutMode = 'horizontal' | 'vertical' | 'tabs'

interface Props {
  leftContent: ReactNode
  rightContent: ReactNode
  leftLabel?: string
  rightLabel?: string
  defaultMode?: LayoutMode
  defaultSplit?: number  // 0–100 procent pro levý/horní panel
  onLayoutChange?: (mode: LayoutMode, split?: number) => void
}

export function TranscribePanelLayout({
  leftContent,
  rightContent,
  leftLabel = 'Vlevo',
  rightLabel = 'Vpravo',
  defaultMode = 'horizontal',
  defaultSplit = 35,
  onLayoutChange,
}: Props) {
  const [mode, setMode] = useState<LayoutMode>(defaultMode)
  const [split, setSplit] = useState(defaultSplit)
  const [activeTab, setActiveTab] = useState<'left' | 'right'>('left')
  const hContainerRef = useRef<HTMLDivElement>(null)
  const vContainerRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)
  const dragMode = useRef<LayoutMode>('horizontal')
  const splitRef = useRef(split)

  useEffect(() => {
    splitRef.current = split
  }, [split])

  const updateSplitFromPoint = useCallback((clientX: number, clientY: number, m: LayoutMode) => {
    const container = m === 'horizontal' ? hContainerRef.current : vContainerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return
    const pct = m === 'horizontal'
      ? ((clientX - rect.left) / rect.width) * 100
      : ((clientY - rect.top) / rect.height) * 100
    const next = Math.max(15, Math.min(85, pct))
    splitRef.current = next
    setSplit(next)
  }, [])

  const startDrag = useCallback((m: LayoutMode, clientX: number, clientY: number) => {
    dragging.current = true
    dragMode.current = m
    document.body.style.cursor = m === 'horizontal' ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'
    updateSplitFromPoint(clientX, clientY, m)
  }, [updateSplitFromPoint])

  const onPointerDragStart = useCallback((m: LayoutMode, e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.currentTarget.setPointerCapture?.(e.pointerId)
    startDrag(m, e.clientX, e.clientY)
  }, [startDrag])

  const onMouseDragStart = useCallback((m: LayoutMode, e: ReactMouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    startDrag(m, e.clientX, e.clientY)
  }, [startDrag])

  const onDragEnd = useCallback(() => {
    if (!dragging.current) return
    dragging.current = false
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    onLayoutChange?.(mode, splitRef.current)
  }, [mode, onLayoutChange])

  useEffect(() => {
    const move = (e: PointerEvent | MouseEvent) => {
      if (!dragging.current) return
      e.preventDefault()
      updateSplitFromPoint(e.clientX, e.clientY, dragMode.current)
    }
    const end = () => onDragEnd()
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', end)
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', end)
    window.addEventListener('blur', end)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', end)
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', end)
      window.removeEventListener('blur', end)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      dragging.current = false
    }
  }, [onDragEnd, updateSplitFromPoint])

  const modeBtn = (m: LayoutMode, icon: string, label: string) => (
    <button
      onClick={() => { setMode(m); onLayoutChange?.(m, split) }}
      title={label}
      className={`px-2 py-1 text-xs rounded border transition-colors ${mode === m ? 'bg-blue-600 border-blue-500 text-white' : 'bg-gray-100 border-gray-300 text-gray-600 hover:bg-gray-200'}`}
    >
      {icon}
    </button>
  )

  return (
    <div className="flex flex-col h-full">
      {/* Layout switcher */}
      <div className="flex items-center gap-2 px-4 py-2 bg-gray-50 border-b border-gray-200">
        <span className="text-xs text-gray-500 mr-1">Rozložení:</span>
        {modeBtn('horizontal', '⬛⬛', 'Vedle sebe (horizontal)')}
        {modeBtn('vertical', '⬜\n⬜', 'Nad sebou (vertical)')}
        {modeBtn('tabs', '📑', 'Záložky')}
        <span className="text-xs text-gray-400 ml-2">Táhni oddělovač pro změnu velikosti</span>
      </div>

      {/* Tabs header — viditelný jen v tabs módu */}
      <div className={mode === 'tabs' ? 'flex border-b border-gray-200' : 'hidden'}>
        <button
          onClick={() => setActiveTab('left')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === 'left' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
        >
          {leftLabel}
        </button>
        <button
          onClick={() => setActiveTab('right')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === 'right' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
        >
          {rightLabel}
        </button>
      </div>

      {/* Obsah — všechny tři módy vždy přítomny v DOM, jen skryté */}
      <div className="flex-1 overflow-hidden relative">

        {/* Horizontal split */}
        <div
          ref={hContainerRef}
          className={`absolute inset-0 flex ${mode === 'horizontal' ? '' : 'invisible pointer-events-none'}`}
        >
          <div style={{ flexBasis: `${split}%` }} className="overflow-hidden flex flex-col min-w-0 flex-none">
            {leftContent}
          </div>
          <div
            onPointerDown={(e) => onPointerDragStart('horizontal', e)}
            onMouseDown={(e) => onMouseDragStart('horizontal', e)}
            className="w-3 bg-gray-200 hover:bg-blue-400 cursor-col-resize flex-shrink-0 transition-colors active:bg-blue-500"
            title="Táhni pro změnu šířky"
          />
          <div className="overflow-hidden flex flex-col min-w-0 flex-1">
            {rightContent}
          </div>
        </div>

        {/* Vertical split */}
        <div
          ref={vContainerRef}
          className={`absolute inset-0 flex flex-col ${mode === 'vertical' ? '' : 'invisible pointer-events-none'}`}
        >
          <div style={{ flexBasis: `${split}%` }} className="overflow-hidden flex flex-col flex-none min-h-0">
            {leftContent}
          </div>
          <div
            onPointerDown={(e) => onPointerDragStart('vertical', e)}
            onMouseDown={(e) => onMouseDragStart('vertical', e)}
            className="h-3 bg-gray-200 hover:bg-blue-400 cursor-row-resize flex-shrink-0 transition-colors active:bg-blue-500"
            title="Táhni pro změnu výšky"
          />
          <div className="overflow-hidden flex flex-col flex-1 min-h-0">
            {rightContent}
          </div>
        </div>

        {/* Tabs content */}
        <div className={`absolute inset-0 ${mode === 'tabs' ? '' : 'invisible pointer-events-none'}`}>
          <div className={`h-full overflow-hidden flex flex-col ${activeTab === 'left' ? '' : 'invisible pointer-events-none'}`}>
            {leftContent}
          </div>
          <div className={`absolute inset-0 overflow-hidden flex flex-col ${activeTab === 'right' ? '' : 'invisible pointer-events-none'}`}>
            {rightContent}
          </div>
        </div>

      </div>
    </div>
  )
}
