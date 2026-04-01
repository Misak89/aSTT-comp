/**
 * TranscribePage — stránka Přepis.
 *
 * Ukládání a nastavení jsou v LocalStorage (funguje bez restartu backendu).
 * Auto-save každých 30s. Archiv přepisů s možností otevřít/smazat.
 * Všechna nastavení (model, rozsah, layout, rychlost, kroky) se pamatují.
 */
import { useState, useRef, useCallback, useEffect } from 'react'
import { TranscribeJobPanel } from '../components/transcribe/TranscribeJobPanel'
import { TranscribeAudioPlayer, type TranscribeAudioPlayerHandle } from '../components/transcribe/TranscribeAudioPlayer'
import { TranscribeEditor } from '../components/transcribe/TranscribeEditor'
import { TranscribePanelLayout } from '../components/transcribe/TranscribePanelLayout'
import {
  listTranscripts, saveTranscript, getTranscript, deleteTranscript,
  loadSettings, saveSettings, type TranscriptEntry,
} from '../components/transcribe/useTranscribeStorage'

const AUTOSAVE_INTERVAL_MS = 30_000

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

function buildTranscriptTitle(sourceLabel: string, videoId: string): string {
  const now = new Date()
  const ts = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}_${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}`
  const clean = (sourceLabel || 'prepis').replace(/[<>:"/\\|?*]/g, '').trim().slice(0, 20).trim().replace(/\s+/g, '_')
  const ytPart = videoId ? `_${videoId}` : ''
  return `${ts}_${clean}${ytPart}`
}

export function TranscribePage() {
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [currentAudioTime, setCurrentAudioTime] = useState(0)
  const [audioDuration, setAudioDuration] = useState(0)
  const [transcriptContent, setTranscriptContent] = useState('')
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle')
  const [currentTranscriptId, setCurrentTranscriptId] = useState<string | null>(null)
  const [currentSourceLabel, setCurrentSourceLabel] = useState('')
  const [currentModelId, setCurrentModelId] = useState('')
  const [currentVideoId, setCurrentVideoId] = useState('')
  const [transcriptTitle, setTranscriptTitle] = useState('')
  const [showArchive, setShowArchive] = useState(false)
  const [archiveList, setArchiveList] = useState<TranscriptEntry[]>([])
  const playerRef = useRef<TranscribeAudioPlayerHandle>(null)
  const editorHtmlRef = useRef('')
  const editorTextRef = useRef('')
  const transcriptStartTimeRef = useRef('')
  const lastTsBoundaryRef = useRef(-1)   // poslední vložená minutová hranice (v sekundách)
  const accumulatedBodyRef = useRef('')  // body HTML bez hlavičky (akumulovaný přepis s ts)

  const autoSaveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const saveStatusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Persist nastavení layoutu
  const savedSettings = loadSettings()
  const [layoutMode, setLayoutMode] = useState<'horizontal' | 'vertical' | 'tabs'>(
    (savedSettings.layout_mode as 'horizontal' | 'vertical' | 'tabs') ?? 'horizontal'
  )
  const [layoutSplit, setLayoutSplit] = useState(savedSettings.layout_split ?? 38)

  const handleLayoutChange = useCallback((mode: 'horizontal' | 'vertical' | 'tabs', split?: number) => {
    setLayoutMode(mode)
    if (split !== undefined) setLayoutSplit(split)
    saveSettings({ layout_mode: mode, ...(split !== undefined ? { layout_split: split } : {}) })
  }, [])

  // Archiv
  const refreshArchive = useCallback(() => {
    setArchiveList(listTranscripts())
  }, [])

  useEffect(() => {
    if (showArchive) refreshArchive()
  }, [showArchive, refreshArchive])

  // Auto-save
  const doSave = useCallback((html: string, plainText: string) => {
    if (!html || html === '<p></p>' || !plainText.trim()) return
    setSaveStatus('saving')
    if (saveStatusTimerRef.current) clearTimeout(saveStatusTimerRef.current)
    try {
      const saveTitle = transcriptTitle || buildTranscriptTitle(currentSourceLabel, currentVideoId) || currentSourceLabel || 'Přepis'
      const entry = saveTranscript({
        transcript_id: currentTranscriptId ?? undefined,
        title: saveTitle,
        html,
        plain_text: plainText,
        source_label: currentSourceLabel || undefined,
        model_id: currentModelId || undefined,
      })
      setCurrentTranscriptId(entry.transcript_id)
      setSaveStatus('saved')
      saveStatusTimerRef.current = setTimeout(() => setSaveStatus('idle'), 3000)
    } catch {
      setSaveStatus('error')
      saveStatusTimerRef.current = setTimeout(() => setSaveStatus('idle'), 5000)
    }
  }, [currentTranscriptId, currentSourceLabel, currentModelId, currentVideoId, transcriptTitle])

  useEffect(() => {
    if (autoSaveTimerRef.current) clearInterval(autoSaveTimerRef.current)
    autoSaveTimerRef.current = setInterval(() => {
      if (editorHtmlRef.current && editorHtmlRef.current !== '<p></p>') {
        doSave(editorHtmlRef.current, editorTextRef.current)
      }
    }, AUTOSAVE_INTERVAL_MS)
    return () => {
      if (autoSaveTimerRef.current) clearInterval(autoSaveTimerRef.current)
      if (saveStatusTimerRef.current) clearTimeout(saveStatusTimerRef.current)
    }
  }, [doSave])

  const handleSave = useCallback((html: string, plainText: string) => {
    editorHtmlRef.current = html
    editorTextRef.current = plainText
    doSave(html, plainText)
  }, [doSave])

  const handleAudioReady = useCallback((url: string) => {
    setAudioUrl(url)
  }, [])

  const handleTranscriptUpdate = useCallback((text: string, audioSecs?: number) => {
    if (!transcriptStartTimeRef.current) {
      transcriptStartTimeRef.current = new Date().toLocaleString('cs-CZ', { dateStyle: 'short', timeStyle: 'short' })
      lastTsBoundaryRef.current = -1
      const title = buildTranscriptTitle(currentSourceLabel, currentVideoId)
      setTranscriptTitle(title)
    }

    // Načti nastavení jednou
    const settings = loadSettings()

    // Timestamp marker — interval dle nastavení
    const tsEnabled = settings.ts_enabled !== false  // výchozí true
    const tsInterval = settings.ts_interval_s ?? 60
    if (tsEnabled && audioSecs != null && audioSecs > 0) {
      const boundary = Math.floor(audioSecs / tsInterval) * tsInterval
      if (boundary > lastTsBoundaryRef.current && boundary > 0) {
        const m = Math.floor(boundary / 60)
        const s = boundary % 60
        const ts = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
        accumulatedBodyRef.current += `<p><span style="color:#dc2626;font-size:0.85em">[${ts}]</span></p>`
        lastTsBoundaryRef.current = boundary
      }
    }

    // Streaming runner posílá kumulativní text — nahraď celý textový obsah (za timestamps)
    const textHtml = text.split('\n').filter(l => l.trim()).map(l => `<p>${l}</p>`).join('')

    // Hlavička
    const rangeInfo = settings.range_mode === 'segment' && settings.range_from
      ? ` | Rozsah: ${settings.range_from}–${settings.range_to || '?'}`
      : ''
    const headerHtml = `<p><strong>${currentSourceLabel || 'Přepis'}</strong></p><p><em>Model: ${currentModelId || '–'} | ${transcriptStartTimeRef.current}${rangeInfo}</em></p><hr/>`

    const html = headerHtml + accumulatedBodyRef.current + textHtml
    setTranscriptContent(html)
    editorHtmlRef.current = html
    editorTextRef.current = text
  }, [currentSourceLabel, currentModelId, currentVideoId])

  const handleTimestampClick = useCallback((seconds: number) => {
    playerRef.current?.seekTo(seconds)
  }, [])

  const handleOpenTranscript = useCallback((id: string) => {
    const entry = getTranscript(id)
    if (!entry) return
    setTranscriptContent(entry.html)
    setCurrentTranscriptId(entry.transcript_id)
    setCurrentSourceLabel(entry.source_label || '')
    setCurrentModelId(entry.model_id || '')
    editorHtmlRef.current = entry.html
    editorTextRef.current = entry.plain_text
    setShowArchive(false)
  }, [])

  const handleDeleteTranscript = useCallback((id: string) => {
    deleteTranscript(id)
    setArchiveList(prev => prev.filter(t => t.transcript_id !== id))
    if (currentTranscriptId === id) setCurrentTranscriptId(null)
  }, [currentTranscriptId])

  const fmtDate = (iso: string) => {
    try { return new Date(iso).toLocaleString('cs-CZ', { dateStyle: 'short', timeStyle: 'short' }) }
    catch { return iso }
  }

  const archiveModal = showArchive ? (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={e => { if (e.target === e.currentTarget) setShowArchive(false) }}>
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <h2 className="font-semibold text-gray-800">Archiv přepisů ({archiveList.length})</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => fetch('/api/open-dir/transcripts', { method: 'POST' }).catch(() => {})}
              className="px-2.5 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200"
              title="Otevřít složku runtime/transcripts/ v průzkumníku">
              📂 Otevřít složku
            </button>
            <button onClick={() => setShowArchive(false)} className="text-gray-400 hover:text-gray-700 text-xl leading-none">×</button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-3">
          {archiveList.length === 0 && (
            <div className="text-gray-400 text-sm p-6 text-center">Žádné uložené přepisy</div>
          )}
          <div className="space-y-2">
            {archiveList.map(t => (
              <div key={t.transcript_id} className="flex items-start gap-3 p-3 border border-gray-200 rounded-lg hover:bg-gray-50">
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-gray-800 truncate">{t.title}</div>
                  <div className="text-xs text-gray-500 flex flex-wrap gap-3 mt-0.5">
                    <span>🕐 {fmtDate(t.updated_at)}</span>
                    {t.source_label && <span>📹 {t.source_label}</span>}
                    {t.model_id && <span>🤖 {t.model_id}</span>}
                    {t.range_from && t.range_to && <span>⏱ {t.range_from}–{t.range_to}</span>}
                  </div>
                  {t.plain_text && (
                    <div className="text-xs text-gray-400 mt-1 truncate">{t.plain_text.slice(0, 120)}</div>
                  )}
                </div>
                <div className="flex gap-1 flex-shrink-0">
                  <button onClick={() => handleOpenTranscript(t.transcript_id)}
                    className="px-2.5 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-500">Otevřít</button>
                  <button onClick={() => { if (confirm(`Smazat "${t.title}"?`)) handleDeleteTranscript(t.transcript_id) }}
                    className="px-2.5 py-1 text-xs bg-red-100 text-red-600 rounded hover:bg-red-200">Smazat</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  ) : null

  const leftPanel = (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-hidden">
        <TranscribeJobPanel
          onAudioReady={handleAudioReady}
          onTranscriptUpdate={handleTranscriptUpdate}
          onJobStop={() => {
            if (editorHtmlRef.current && editorHtmlRef.current !== '<p></p>') {
              doSave(editorHtmlRef.current, editorTextRef.current)
            }
          }}
          audioDuration={audioDuration}
          onModelChange={id => { setCurrentModelId(id); saveSettings({ model: id }) }}
          onSourceLabelChange={label => setCurrentSourceLabel(label)}
          onVideoIdChange={id => setCurrentVideoId(id)}
        />
      </div>
      <div className="flex-shrink-0 p-3 bg-gray-800 border-t border-gray-700">
        <TranscribeAudioPlayer
          ref={playerRef}
          audioUrl={audioUrl}
          onTimeUpdate={setCurrentAudioTime}
          onReady={setAudioDuration}
        />
      </div>
    </div>
  )

  const rightPanel = (
    <div className="flex flex-col h-full">
      <TranscribeEditor
        initialContent={transcriptContent}
        currentAudioTime={currentAudioTime}
        onTimestampClick={handleTimestampClick}
        onSave={handleSave}
        saveStatus={saveStatus}
        sourceLabel={currentSourceLabel}
        videoId={currentVideoId}
      />
    </div>
  )

  return (
    <div className="flex flex-col" style={{ height: 'calc(100vh - 64px)' }}>
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-1.5 bg-gray-50 border-b border-gray-200 flex-shrink-0 text-sm">
        <span className="font-medium text-gray-700">Přepis</span>
        {saveStatus === 'saved' && <span className="text-green-600 text-xs">✓ Uloženo</span>}
        {saveStatus === 'saving' && <span className="text-blue-500 text-xs">⏳ Ukládám...</span>}
        {saveStatus === 'error' && <span className="text-red-500 text-xs">✗ Chyba uložení</span>}
        {(transcriptTitle || currentSourceLabel) && <span className="text-gray-400 text-xs truncate max-w-xs font-mono">{transcriptTitle || currentSourceLabel}</span>}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-gray-400">Auto-save 30s</span>
          <button onClick={() => { refreshArchive(); setShowArchive(true) }}
            className="px-3 py-1 bg-gray-700 text-gray-100 rounded hover:bg-gray-600 text-xs">
            📁 Archiv ({listTranscripts().length})
          </button>
        </div>
      </div>

      <TranscribePanelLayout
        leftContent={leftPanel}
        rightContent={rightPanel}
        leftLabel="Přepis + Audio"
        rightLabel="Editor"
        defaultMode={layoutMode}
        defaultSplit={layoutSplit}
        onLayoutChange={handleLayoutChange}
      />

      {archiveModal}
    </div>
  )
}
