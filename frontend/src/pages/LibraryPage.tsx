import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { LibraryItem, LatestResult } from '../types'
import { WerBadge } from '../components/WerBadge'

export function LibraryPage() {
  const [items, setItems] = useState<LibraryItem[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, LatestResult[]>>({})
  const [subtitleContent, setSubtitleContent] = useState<Record<string, string>>({})
  const [subtitleOpen, setSubtitleOpen] = useState<string | null>(null)
  const [addUrl, setAddUrl] = useState('')
  const [addTitle, setAddTitle] = useState('')
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => { load() }, [])

  async function load() {
    const data = await api.library.list()
    setItems(data)
  }

  async function expand(item: LibraryItem) {
    if (expanded === item.video_id) { setExpanded(null); return }
    setExpanded(item.video_id)
    if (!results[item.video_id]) {
      const r = await api.library.latestResults(item.video_id)
      setResults(prev => ({ ...prev, [item.video_id]: r }))
    }
  }

  async function toggleSubtitles(item: LibraryItem) {
    if (subtitleOpen === item.video_id) { setSubtitleOpen(null); return }
    if (!subtitleContent[item.video_id]) {
      const file = item.subtitle_files?.[0]
      if (!file) return
      try {
        const r = await fetch(`/api/library/subtitle/${item.video_id}/${file.filename}`)
        const text = await r.text()
        setSubtitleContent(prev => ({ ...prev, [item.video_id]: text }))
      } catch {
        setSubtitleContent(prev => ({ ...prev, [item.video_id]: 'Nepodařilo se načíst titulky.' }))
      }
    }
    setSubtitleOpen(item.video_id)
  }

  function extractVideoId(url: string): string {
    const m = url.match(/[?&]v=([^&]+)/) || url.match(/youtu\.be\/([^?]+)/)
    return m ? m[1] : url.trim()
  }

  async function addVideo() {
    if (!addUrl || !addTitle) return
    setLoading(true)
    setMsg('')
    try {
      const video_id = extractVideoId(addUrl)
      await api.library.upsert({ video_id, title: addTitle, url: addUrl })
      setAddUrl(''); setAddTitle('')
      await load()
      setMsg('Video přidáno.')
    } catch (e: any) { setMsg(`Chyba: ${e.message}`) }
    setLoading(false)
  }

  async function downloadSubs(item: LibraryItem) {
    setMsg(`Stahuji titulky pro ${item.video_id}...`)
    try {
      await api.library.downloadSubtitles(item.video_id, item.url)
      await load()
      setMsg('Titulky staženy.')
    } catch (e: any) { setMsg(`Chyba: ${e.message}`) }
  }

  return (
    <div>
      <h1 className="text-xl font-bold mb-4">Knihovna videí</h1>

      {/* Přidat video */}
      <div className="bg-white rounded border border-gray-200 p-4 mb-6 flex gap-3 items-end flex-wrap">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">Název</label>
          <input value={addTitle} onChange={e => setAddTitle(e.target.value)}
            placeholder="Název videa" className="border rounded px-2 py-1 text-sm w-64" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">YouTube URL</label>
          <input value={addUrl} onChange={e => setAddUrl(e.target.value)}
            placeholder="https://www.youtube.com/watch?v=..." className="border rounded px-2 py-1 text-sm w-80" />
        </div>
        <button onClick={addVideo} disabled={loading || !addUrl || !addTitle}
          className="bg-blue-600 text-white px-4 py-1.5 rounded text-sm disabled:opacity-50">
          Přidat
        </button>
        {msg && <span className="text-sm text-gray-600">{msg}</span>}
      </div>

      {/* Tabulka videí */}
      <div className="bg-white rounded border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600 text-xs uppercase">
            <tr>
              <th className="px-4 py-2 text-left">Název</th>
              <th className="px-4 py-2 text-left">Délka</th>
              <th className="px-4 py-2 text-left">Titulky</th>
              <th className="px-4 py-2 text-left">Nejlepší WER</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {items.map(item => (
              <>
                <tr key={item.video_id}
                  className="border-t border-gray-100 hover:bg-gray-50 cursor-pointer"
                  onClick={() => expand(item)}>
                  <td className="px-4 py-2 font-medium text-gray-800 max-w-xs truncate">{item.title}</td>
                  <td className="px-4 py-2 text-gray-500">
                    {item.duration_seconds ? `${Math.round(item.duration_seconds)}s` : '–'}
                  </td>
                  <td className="px-4 py-2">
                    {item.subtitles_local
                      ? (
                        <button
                          onClick={e => { e.stopPropagation(); toggleSubtitles(item) }}
                          className={`font-medium text-xs px-2 py-0.5 rounded border ${
                            subtitleOpen === item.video_id
                              ? 'bg-green-100 text-green-700 border-green-300'
                              : 'bg-green-50 text-green-600 border-green-200 hover:bg-green-100'
                          }`}
                          title="Klikni pro zobrazení VTT titulků"
                        >
                          {subtitleOpen === item.video_id ? '▲ Skrýt VTT' : '📄 VTT'}
                        </button>
                      )
                      : (
                        <button onClick={e => { e.stopPropagation(); downloadSubs(item) }}
                          className="text-blue-600 underline text-xs">Stáhnout</button>
                      )}
                  </td>
                  <td className="px-4 py-2">
                    {results[item.video_id]?.[0]
                      ? <WerBadge value={results[item.video_id][0].wer} />
                      : <span className="text-gray-400 text-xs">–</span>}
                  </td>
                  <td className="px-4 py-2 text-gray-400 text-xs">
                    {expanded === item.video_id ? '▲' : '▼'}
                  </td>
                </tr>

                {/* Titulky inline */}
                {subtitleOpen === item.video_id && subtitleContent[item.video_id] && (
                  <tr key={`${item.video_id}-subs`} className="bg-yellow-50">
                    <td colSpan={5} className="px-6 py-3">
                      <p className="text-xs font-semibold text-yellow-700 mb-1">
                        📄 {item.subtitle_files?.[0]?.filename} — {item.subtitle_files?.[0]?.size_bytes
                          ? `${Math.round(item.subtitle_files[0].size_bytes / 1024)} KB`
                          : ''}
                      </p>
                      <pre className="text-xs text-gray-700 max-h-64 overflow-y-auto whitespace-pre-wrap font-mono bg-white border border-yellow-200 rounded p-2">
                        {subtitleContent[item.video_id]}
                      </pre>
                    </td>
                  </tr>
                )}

                {/* Detail — rozbalené výsledky */}
                {expanded === item.video_id && (
                  <tr key={`${item.video_id}-detail`} className="bg-blue-50">
                    <td colSpan={5} className="px-6 py-3">
                      <div className="text-xs font-semibold text-gray-600 mb-2">
                        video_id: {item.video_id} &nbsp;|&nbsp;
                        <a href={item.url} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                          YouTube
                        </a>
                      </div>
                      {results[item.video_id]?.length
                        ? <ResultsTable rows={results[item.video_id]} />
                        : <span className="text-gray-400 text-xs">Zatím žádné výsledky — spusť benchmark.</span>}
                    </td>
                  </tr>
                )}
              </>
            ))}
            {items.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-400">Knihovna je prázdná.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ResultsTable({ rows }: { rows: LatestResult[] }) {
  return (
    <table className="text-xs">
      <thead>
        <tr className="text-gray-500">
          <th className="pr-4 text-left">Model</th>
          <th className="pr-4 text-left">Nastavení</th>
          <th className="pr-4">WER</th>
          <th className="pr-4">CER</th>
          <th className="text-left">Ukázka přepisu</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={`${r.model_id}-${r.setting_id}`} className="border-t border-blue-100">
            <td className="pr-4 py-1 font-mono">{r.model_id}</td>
            <td className="pr-4 text-gray-600">{r.setting_id}</td>
            <td className="pr-4"><WerBadge value={r.wer} label="" /></td>
            <td className="pr-4"><WerBadge value={r.cer} label="" /></td>
            <td className="text-gray-500 max-w-xs truncate">{r.transcript_snippet || '–'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
