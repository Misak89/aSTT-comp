/**
 * TranscribePanelLayout — přizpůsobitelné rozložení pro stránku Přepis.
 *
 * Módy:
 * - horizontal: levý a pravý panel vedle sebe, táhnutelný oddělovač
 * - vertical: horní a dolní panel nad sebou, táhnutelný oddělovač
 * - tabs: záložky (levý/pravý obsah v jednom okně)
 */
import { useRef, useState, useCallback, type ReactNode } from 'react'

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
  const containerRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)

  const onMouseDown = useCallback(() => {
    dragging.current = true
    document.body.style.cursor = mode === 'horizontal' ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'
  }, [mode])

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragging.current || !containerRef.current) return
    const rect = containerRef.current.getBoundingClientRect()
    let pct: number
    if (mode === 'horizontal') {
      pct = ((e.clientX - rect.left) / rect.width) * 100
    } else {
      pct = ((e.clientY - rect.top) / rect.height) * 100
    }
    setSplit(Math.max(15, Math.min(85, pct)))
  }, [mode])

  const onMouseUp = useCallback(() => {
    if (dragging.current) {
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      onLayoutChange?.(mode, split)
    }
  }, [mode, split, onLayoutChange])

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

      {/* Tabs */}
      {mode === 'tabs' && (
        <div className="flex flex-col h-full">
          <div className="flex border-b border-gray-200">
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
          <div className="flex-1 overflow-hidden">
            {activeTab === 'left' ? leftContent : rightContent}
          </div>
        </div>
      )}

      {/* Horizontal split */}
      {mode === 'horizontal' && (
        <div
          ref={containerRef}
          className="flex flex-1 overflow-hidden"
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseUp}
        >
          <div style={{ width: `${split}%` }} className="overflow-hidden flex flex-col">
            {leftContent}
          </div>
          {/* Drag handle */}
          <div
            onMouseDown={onMouseDown}
            className="w-1.5 bg-gray-200 hover:bg-blue-400 cursor-col-resize flex-shrink-0 transition-colors active:bg-blue-500"
            title="Táhni pro změnu šířky"
          />
          <div style={{ width: `${100 - split}%` }} className="overflow-hidden flex flex-col">
            {rightContent}
          </div>
        </div>
      )}

      {/* Vertical split */}
      {mode === 'vertical' && (
        <div
          ref={containerRef}
          className="flex flex-col flex-1 overflow-hidden"
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseUp}
        >
          <div style={{ height: `${split}%` }} className="overflow-hidden flex flex-col">
            {leftContent}
          </div>
          {/* Drag handle */}
          <div
            onMouseDown={onMouseDown}
            className="h-1.5 bg-gray-200 hover:bg-blue-400 cursor-row-resize flex-shrink-0 transition-colors active:bg-blue-500"
            title="Táhni pro změnu výšky"
          />
          <div style={{ height: `${100 - split}%` }} className="overflow-hidden flex flex-col">
            {rightContent}
          </div>
        </div>
      )}
    </div>
  )
}
