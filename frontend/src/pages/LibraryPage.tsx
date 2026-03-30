import { Fragment, useEffect, useState } from 'react'
import { api } from '../api/client'
import type { LibraryItem, LatestResult, YTSearchResult } from '../types'
import { WerBadge } from '../components/WerBadge'

// ── helpers ──────────────────────────────────────────────────────────────────

const LANG_LABELS: Record<string, string> = {
  cs: 'Čeština', sk: 'Slovenčina', pl: 'Polština',
  uk: 'Ukrajiština', en: 'Angličtina', de: 'Němčina',
}
const LANGS = ['cs', 'sk', 'pl', 'uk', 'en', 'de']
const CATEGORIES = [
  { id: 'music', label: 'Hudba' },
  { id: 'film', label: 'Film' },
  { id: 'gaming', label: 'Hry' },
  { id: 'news', label: 'Zprávy' },
  { id: 'sport', label: 'Sport' },
  { id: 'podcast', label: 'Podcast' },
]

function fmtDuration(s: number) {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

function fmtViews(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`
  return String(n)
}

function fmtDate(d: string) {
  // YYYYMMDD → YYYY-MM-DD
  if (!d || d.length !== 8) return d
  return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`
}

function LangBadge({ code }: { code: string }) {
  const cs = code === 'cs' ? 'bg-blue-100 text-blue-700' : 'bg-orange-100 text-orange-700'
  return (
    <span className={`px-1.5 py-0.5 rounded font-mono font-bold text-xs ${cs}`}
      title={LANG_LABELS[code] ?? code}>
      {code.toUpperCase()}
    </span>
  )
}

function toggle<T>(arr: T[], val: T): T[] {
  return arr.includes(val) ? arr.filter(x => x !== val) : [...arr, val]
}

function languageTitlePrefix(language: string): 'CZ' | 'EN' | null {
  const lang = (language || '').trim().toLowerCase()
  if (lang === 'cs') return 'CZ'
  if (lang === 'en') return 'EN'
  return null
}

function applyTitlePrefix(title: string, prefix: 'CZ' | 'EN'): string {
  const raw = (title || '').trim()
  const base = raw.replace(/^(CZ|EN)_/i, '').trim()
  return `${prefix}_${base || 'Bez názvu'}`
}

function ensureLanguagePrefix(title: string, language: string): string {
  const prefix = languageTitlePrefix(language)
  if (!prefix) return (title || '').trim()
  return applyTitlePrefix(title, prefix)
}

type LibrarySortKey =
  | 'title'
  | 'language'
  | 'duration'
  | 'genre'
  | 'view_count'
  | 'subtitle_languages'
  | 'subtitles'
  | 'audio'
  | 'visible_in_menus'
  | 'added_at'
  | 'wer'

const LIB_SORT_COLUMNS: Array<{ key: LibrarySortKey; label: string; align: string }> = [
  { key: 'title', label: 'Název', align: 'text-left' },
  { key: 'language', label: 'Jazyk', align: 'text-left' },
  { key: 'duration', label: 'Délka', align: 'text-left' },
  { key: 'genre', label: 'Žánr', align: 'text-left' },
  { key: 'view_count', label: 'Zhlédnutí', align: 'text-left' },
  { key: 'subtitle_languages', label: 'Jazyky tit.', align: 'text-left' },
  { key: 'subtitles', label: 'Titulky', align: 'text-left' },
  { key: 'audio', label: 'Audio', align: 'text-left' },
  { key: 'added_at', label: 'Datum', align: 'text-left' },
  { key: 'wer', label: 'Nejlepší WER', align: 'text-left' },
]

const LIB_SORT_DEFAULT_DIR: Record<LibrarySortKey, 'asc' | 'desc'> = {
  title: 'asc',
  language: 'asc',
  duration: 'asc',
  genre: 'asc',
  view_count: 'desc',
  subtitle_languages: 'asc',
  subtitles: 'desc',
  audio: 'desc',
  visible_in_menus: 'desc',
  added_at: 'desc',
  wer: 'asc',
}

// ── SearchPanel ───────────────────────────────────────────────────────────────

interface SearchState {
  q: string
  contentType: string
  audioLangs: string[]
  subtitleLangs: string[]
  subtitleType: string
  categories: string[]
  minDuration: string
  maxDuration: string
  minViews: string
  uploadedAfter: string
  maxResults: string
}

const defaultSearch: SearchState = {
  q: '', contentType: 'any',
  audioLangs: [], subtitleLangs: ['cs'],
  subtitleType: 'any', categories: [],
  minDuration: '', maxDuration: '', minViews: '',
  uploadedAfter: '', maxResults: '20',
}

function SearchPanel({ onAddVideo }: { onAddVideo: () => void }) {
  const [s, setS] = useState<SearchState>(defaultSearch)
  const [results, setResults] = useState<YTSearchResult[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [addingId, setAddingId] = useState<string | null>(null)
  const [addedIds, setAddedIds] = useState<Set<string>>(new Set())
  const [addMsg, setAddMsg] = useState<Record<string, string>>({})

  async function doSearch() {
    setLoading(true); setError(''); setResults(null)
    try {
      const params: Record<string, unknown> = {
        q: s.q.trim(),
        max_results: parseInt(s.maxResults) || 20,
        content_type: s.contentType,
        subtitle_type: s.subtitleType,
      }
      if (s.audioLangs.length) params.audio_langs = s.audioLangs
      if (s.subtitleLangs.length) params.subtitle_langs = s.subtitleLangs
      if (s.categories.length) params.categories = s.categories
      if (s.minDuration) params.min_duration = parseInt(s.minDuration)
      if (s.maxDuration) params.max_duration = parseInt(s.maxDuration)
      if (s.minViews) params.min_views = parseInt(s.minViews)
      if (s.uploadedAfter) params.uploaded_after = s.uploadedAfter.replace(/-/g, '')
      const data = await api.library.search(params as Parameters<typeof api.library.search>[0])
      setResults(Array.isArray(data) ? data : [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setLoading(false)
  }

  async function addAndDownload(r: YTSearchResult) {
    setAddingId(r.video_id)
    setAddMsg(prev => ({ ...prev, [r.video_id]: 'Přidávám...' }))
    try {
      const lang = (r.audio_language || 'cs').toLowerCase()
      const title = ensureLanguagePrefix(r.title, lang)
      await api.library.upsert({ video_id: r.video_id, title, url: r.url,
        duration_seconds: r.duration_seconds, language: lang })
      setAddMsg(prev => ({ ...prev, [r.video_id]: 'Stahuji titulky...' }))
      await api.library.downloadSubtitles(r.video_id, r.url)
      setAddedIds(prev => new Set([...prev, r.video_id]))
      setAddMsg(prev => ({ ...prev, [r.video_id]: 'Přidáno ✓' }))
      onAddVideo()
    } catch (e: unknown) {
      setAddMsg(prev => ({ ...prev, [r.video_id]: `Chyba: ${e instanceof Error ? e.message : String(e)}` }))
    }
    setAddingId(null)
  }

  const upd = (k: keyof SearchState) => (v: string) => setS(prev => ({ ...prev, [k]: v }))

  return (
    <div className="bg-white rounded border border-gray-200 p-4 mb-6">
      <h2 className="text-sm font-bold text-gray-700 mb-3">Vyhledat na YouTube</h2>

      {/* Row 1: keywords + počet výsledků + search button */}
      <div className="flex gap-2 flex-wrap items-end mb-3">
        <div className="flex flex-col gap-1 flex-1 min-w-48">
          <label className="text-xs text-gray-500">Klíčová slova / téma</label>
          <input value={s.q} onChange={e => upd('q')(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && doSearch()}
            placeholder="rozhovor, podcast, přednáška..." className="border rounded px-2 py-1 text-sm" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">Typ obsahu</label>
          <select value={s.contentType} onChange={e => upd('contentType')(e.target.value)}
            className="border rounded px-2 py-1 text-sm">
            <option value="any">Vše</option>
            <option value="interview">Rozhovor</option>
            <option value="monolog">Monolog / přednáška</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">Výsledků max.</label>
          <select value={s.maxResults} onChange={e => upd('maxResults')(e.target.value)}
            className="border rounded px-2 py-1 text-sm w-20">
            {['10','20','30','50'].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <button onClick={doSearch} disabled={loading}
          className="bg-blue-600 text-white px-4 py-1.5 rounded text-sm disabled:opacity-50 self-end">
          {loading ? '⏳ Hledám...' : '🔍 Hledat'}
        </button>
        {!s.q.trim() && !loading && (
          <span className="text-xs text-gray-400 self-end pb-2">Bez klíčových slov hledá dle filtrů</span>
        )}
      </div>

      {/* Row 2: jazyk audia + titulky */}
      <div className="flex gap-4 flex-wrap mb-3 text-xs">
        <div>
          <div className="text-gray-500 mb-1 font-medium">Audio jazyk</div>
          <div className="flex gap-1 flex-wrap">
            {LANGS.map(l => (
              <button key={l}
                onClick={() => setS(prev => ({ ...prev, audioLangs: toggle(prev.audioLangs, l) }))}
                className={`px-2 py-0.5 rounded border font-mono font-bold ${
                  s.audioLangs.includes(l)
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400'
                }`} title={LANG_LABELS[l]}>
                {l.toUpperCase()}
              </button>
            ))}
            {s.audioLangs.length > 0 && (
              <button onClick={() => setS(prev => ({ ...prev, audioLangs: [] }))}
                className="text-gray-400 hover:text-gray-600 px-1">×</button>
            )}
          </div>
        </div>
        <div>
          <div className="text-gray-500 mb-1 font-medium">
            Titulky — jazyk
            <select value={s.subtitleType} onChange={e => upd('subtitleType')(e.target.value)}
              className="ml-2 border rounded px-1 py-0 text-xs text-gray-600">
              <option value="any">manuální + auto</option>
              <option value="manual">jen manuální</option>
              <option value="auto">jen auto</option>
            </select>
          </div>
          <div className="flex gap-1 flex-wrap">
            {LANGS.map(l => (
              <button key={l}
                onClick={() => setS(prev => ({ ...prev, subtitleLangs: toggle(prev.subtitleLangs, l) }))}
                className={`px-2 py-0.5 rounded border font-mono font-bold ${
                  s.subtitleLangs.includes(l)
                    ? 'bg-green-600 text-white border-green-600'
                    : 'bg-white text-gray-600 border-gray-300 hover:border-green-400'
                }`} title={LANG_LABELS[l]}>
                {l.toUpperCase()}
              </button>
            ))}
            {s.subtitleLangs.length > 0 && (
              <button onClick={() => setS(prev => ({ ...prev, subtitleLangs: [] }))}
                className="text-gray-400 hover:text-gray-600 px-1">×</button>
            )}
          </div>
        </div>
      </div>

      {/* Row 3: kategorie + délka + views + datum */}
      <div className="flex gap-4 flex-wrap items-end mb-3 text-xs">
        <div>
          <div className="text-gray-500 mb-1 font-medium">Kategorie</div>
          <div className="flex gap-1 flex-wrap">
            {CATEGORIES.map(c => (
              <button key={c.id}
                onClick={() => setS(prev => ({ ...prev, categories: toggle(prev.categories, c.id) }))}
                className={`px-2 py-0.5 rounded border ${
                  s.categories.includes(c.id)
                    ? 'bg-purple-600 text-white border-purple-600'
                    : 'bg-white text-gray-600 border-gray-300 hover:border-purple-400'
                }`}>
                {c.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-gray-500 font-medium">Délka (s)</label>
          <div className="flex gap-1 items-center">
            <input value={s.minDuration} onChange={e => upd('minDuration')(e.target.value)}
              placeholder="min" className="border rounded px-1 py-0.5 w-16" type="number" min="0" />
            <span className="text-gray-400">–</span>
            <input value={s.maxDuration} onChange={e => upd('maxDuration')(e.target.value)}
              placeholder="max" className="border rounded px-1 py-0.5 w-16" type="number" min="0" />
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-gray-500 font-medium">Min. shlédnutí</label>
          <input value={s.minViews} onChange={e => upd('minViews')(e.target.value)}
            placeholder="např. 10000" className="border rounded px-1 py-0.5 w-28" type="number" min="0" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-gray-500 font-medium">Nahráno po</label>
          <input value={s.uploadedAfter} onChange={e => upd('uploadedAfter')(e.target.value)}
            placeholder="YYYY-MM-DD" className="border rounded px-1 py-0.5 w-28" type="date" />
        </div>
      </div>

      {/* API note */}
      <p className="text-xs text-gray-400 mb-3">
        Hledání využívá yt-dlp bez API klíče. S&nbsp;YouTube Data API v3 by bylo možné filtrovat přesněji:
        skutečné kategorie, jazyk audia dle metadat, přesnější délka, řazení dle data, relevance nebo počtu shlédnutí.
      </p>

      {/* Error */}
      {error && <div className="text-red-600 text-xs mb-2">{error}</div>}

      {/* Results */}
      {results !== null && (
        results.length === 0
          ? <div className="text-gray-400 text-sm py-4 text-center">Žádné výsledky. Zkus jiná klíčová slova nebo méně filtrů.</div>
          : (
            <div className="space-y-2 max-h-[600px] overflow-y-auto">
              {results.map(r => (
                <SearchResultRow
                  key={r.video_id} r={r}
                  adding={addingId === r.video_id}
                  added={addedIds.has(r.video_id) || r.in_library}
                  msg={addMsg[r.video_id]}
                  onAdd={() => addAndDownload(r)}
                />
              ))}
            </div>
          )
      )}
    </div>
  )
}

function SearchResultRow({
  r, adding, added, msg, onAdd,
}: { r: YTSearchResult; adding: boolean; added: boolean; msg?: string; onAdd: () => void }) {
  const allSubs = [...new Set([...r.subtitle_manual, ...r.subtitle_auto])]
  return (
    <div className="flex gap-3 border border-gray-100 rounded p-2 hover:bg-gray-50">
      <img src={r.thumbnail} alt="" className="w-28 h-16 object-cover rounded flex-shrink-0 bg-gray-100" />
      <div className="flex-1 min-w-0">
        <div className="flex gap-2 items-start justify-between">
          <a href={r.url} target="_blank" rel="noreferrer"
            className="text-sm font-medium text-gray-800 hover:text-blue-600 line-clamp-2 leading-tight">
            {r.title}
          </a>
          <div className="flex-shrink-0 ml-2">
            {added ? (
              <span className="text-xs text-green-600 font-medium whitespace-nowrap">
                {msg && msg !== 'Přidáno ✓' ? msg : '✓ V knihovně'}
              </span>
            ) : (
              <button onClick={onAdd} disabled={adding}
                className="bg-blue-600 text-white text-xs px-2.5 py-1 rounded disabled:opacity-50 whitespace-nowrap">
                {adding ? (msg ?? '...') : '+ Přidat + titulky'}
              </button>
            )}
          </div>
        </div>
        <div className="flex gap-3 text-xs text-gray-500 mt-1 flex-wrap">
          <span>{r.channel}</span>
          <span>{fmtDate(r.upload_date)}</span>
          <span>{fmtDuration(r.duration_seconds)}</span>
          <span>{fmtViews(r.view_count)} zhl.</span>
          {r.audio_language && (
            <span className="flex items-center gap-1">
              Audio: <LangBadge code={r.audio_language} />
            </span>
          )}
          {allSubs.length > 0 && (
            <span className="flex items-center gap-1">
              Titulky:
              {allSubs.slice(0, 6).map(l => (
                <span key={l}
                  className={`px-1 py-0 rounded font-mono text-xs font-bold ${
                    r.subtitle_manual.includes(l)
                      ? 'bg-green-100 text-green-700'
                      : 'bg-yellow-100 text-yellow-700'
                  }`} title={r.subtitle_manual.includes(l) ? 'manuální' : 'automatické'}>
                  {l.toUpperCase()}
                </span>
              ))}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

// ── LibraryPage ───────────────────────────────────────────────────────────────

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
  const [showSearch, setShowSearch] = useState(false)
  const [sortOrder, setSortOrder] = useState<LibrarySortKey[]>(['added_at'])
  const [sortDirMap, setSortDirMap] = useState<Record<LibrarySortKey, 'asc' | 'desc'>>(
    () => ({ ...LIB_SORT_DEFAULT_DIR }),
  )
  const [editingVideoId, setEditingVideoId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [savingVideoId, setSavingVideoId] = useState<string | null>(null)
  const [togglingVisibilityVideoId, setTogglingVisibilityVideoId] = useState<string | null>(null)
  const [refreshingMetadata, setRefreshingMetadata] = useState(false)

  function toggleSortPriority(col: LibrarySortKey) {
    setSortOrder(prev => (
      prev.includes(col)
        ? prev.filter(k => k !== col)
        : [...prev, col]
    ))
  }

  function toggleSortDirection(col: LibrarySortKey) {
    setSortDirMap(prev => ({ ...prev, [col]: prev[col] === 'asc' ? 'desc' : 'asc' }))
  }

  const sortedItems = [...items].sort((a, b) => {
    const activeOrder: LibrarySortKey[] = sortOrder.length > 0 ? sortOrder : ['added_at']
    for (const key of activeOrder) {
      let va: string | number = ''
      let vb: string | number = ''
      if (key === 'title') { va = (a.title || '').toLowerCase(); vb = (b.title || '').toLowerCase() }
      else if (key === 'language') { va = a.language || ''; vb = b.language || '' }
      else if (key === 'duration') { va = a.duration_seconds ?? -1; vb = b.duration_seconds ?? -1 }
      else if (key === 'genre') { va = (a.genre || '').toLowerCase(); vb = (b.genre || '').toLowerCase() }
      else if (key === 'view_count') { va = a.view_count ?? -1; vb = b.view_count ?? -1 }
      else if (key === 'subtitle_languages') {
        va = (a.subtitle_languages || []).join(',').toLowerCase()
        vb = (b.subtitle_languages || []).join(',').toLowerCase()
      }
      else if (key === 'subtitles') { va = a.subtitles_local ? 1 : 0; vb = b.subtitles_local ? 1 : 0 }
      else if (key === 'audio') { va = a.audio_cached ? 1 : 0; vb = b.audio_cached ? 1 : 0 }
      else if (key === 'visible_in_menus') { va = a.visible_in_menus !== false ? 1 : 0; vb = b.visible_in_menus !== false ? 1 : 0 }
      else if (key === 'added_at') { va = a.upload_date ?? a.added_at ?? ''; vb = b.upload_date ?? b.added_at ?? '' }
      else if (key === 'wer') {
        va = results[a.video_id]?.[0]?.wer ?? 999
        vb = results[b.video_id]?.[0]?.wer ?? 999
      }
      const cmp = va < vb ? -1 : va > vb ? 1 : 0
      if (cmp !== 0) return sortDirMap[key] === 'asc' ? cmp : -cmp
    }
    return 0
  })

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
    setLoading(true); setMsg('')
    try {
      const video_id = extractVideoId(addUrl)
      const rawTitle = addTitle.trim()
      const explicitPrefix = rawTitle.match(/^(CZ|EN)_/i)?.[1]?.toLowerCase()
      const language = explicitPrefix === 'en' ? 'en' : 'cs'
      const title = ensureLanguagePrefix(rawTitle, language)
      await api.library.upsert({ video_id, title, url: addUrl, language, visible_in_menus: true })
      setAddUrl(''); setAddTitle('')
      await load()
      setMsg('Video přidáno.')
    } catch (e: unknown) { setMsg(`Chyba: ${e instanceof Error ? e.message : String(e)}`) }
    setLoading(false)
  }

  async function downloadSubs(item: LibraryItem) {
    setMsg(`Stahuji titulky pro ${item.video_id}...`)
    try {
      await api.library.downloadSubtitles(item.video_id, item.url)
      await load()
      setMsg('Titulky staženy.')
    } catch (e: unknown) { setMsg(`Chyba: ${e instanceof Error ? e.message : String(e)}`) }
  }

  async function refreshMetadata(item: LibraryItem) {
    setMsg(`Načítám metadata pro ${item.video_id}...`)
    try {
      await api.library.upsert({
        video_id: item.video_id,
        title: item.title,
        url: item.url,
        duration_seconds: item.duration_seconds ?? undefined,
        language: item.language || 'cs',
        genre: item.genre ?? undefined,
        visible_in_menus: item.visible_in_menus,
      })
      await load()
      setMsg(`Metadata pro ${item.video_id} aktualizována.`)
    } catch (e: unknown) {
      setMsg(`Chyba při aktualizaci metadat: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  async function refreshMissingMetadata() {
    const targets = items.filter(item =>
      !item.metadata_fetched_at ||
      item.view_count == null ||
      !item.genre ||
      !(item.subtitle_languages?.length > 0),
    )
    if (targets.length === 0) {
      setMsg('Všechna videa už mají metadata vyplněná.')
      return
    }
    setRefreshingMetadata(true)
    setMsg(`Doplňuji metadata (${targets.length} videí)...`)
    const failed: string[] = []
    for (const item of targets) {
      try {
        await api.library.upsert({
          video_id: item.video_id,
          title: item.title,
          url: item.url,
          duration_seconds: item.duration_seconds ?? undefined,
          language: item.language || 'cs',
          genre: item.genre ?? undefined,
          visible_in_menus: item.visible_in_menus,
        })
      } catch {
        failed.push(item.video_id)
      }
    }
    await load()
    if (failed.length > 0) {
      setMsg(`Metadata doplněna s chybami: ${failed.length}/${targets.length} (${failed.join(', ')}).`)
    } else {
      setMsg(`Metadata doplněna u ${targets.length} videí.`)
    }
    setRefreshingMetadata(false)
  }

  async function toggleVisibility(item: LibraryItem, visible: boolean) {
    const prevVisible = item.visible_in_menus !== false
    setItems(prev => prev.map(v => (
      v.video_id === item.video_id ? { ...v, visible_in_menus: visible } : v
    )))
    setTogglingVisibilityVideoId(item.video_id)
    setMsg('')
    try {
      await api.library.setVisibility(item.video_id, visible)
      setMsg(`${item.video_id}: ${visible ? 'zobrazuje se v menu' : 'skryto z ostatních menu'}.`)
    } catch (e: unknown) {
      // revert optimistic update
      setItems(prev => prev.map(v => (
        v.video_id === item.video_id ? { ...v, visible_in_menus: prevVisible } : v
      )))
      const message = e instanceof Error ? e.message : String(e)
      if (message.includes('/visibility') && message.includes('404')) {
        setMsg('Chyba při změně viditelnosti: backend je starší verze bez endpointu /visibility. Restartuj backend.')
      } else {
        setMsg(`Chyba při změně viditelnosti: ${message}`)
      }
    } finally {
      setTogglingVisibilityVideoId(null)
    }
  }

  function startEditTitle(item: LibraryItem) {
    setEditingVideoId(item.video_id)
    setEditingTitle(item.title)
    setMsg('')
  }

  function cancelEditTitle() {
    setEditingVideoId(null)
    setEditingTitle('')
  }

  async function saveEditTitle(item: LibraryItem) {
    const nextTitle = ensureLanguagePrefix(editingTitle.trim(), item.language || 'cs')
    if (!nextTitle) {
      setMsg('Název nesmí být prázdný.')
      return
    }
    setSavingVideoId(item.video_id)
    setMsg('')
    try {
      await api.library.upsert({
        video_id: item.video_id,
        title: nextTitle,
        url: item.url,
        duration_seconds: item.duration_seconds ?? undefined,
        language: item.language || 'cs',
        genre: item.genre ?? undefined,
      })
      await load()
      setMsg(`Název videa ${item.video_id} uložen.`)
      cancelEditTitle()
    } catch (e: unknown) {
      setMsg(`Chyba při uložení názvu: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSavingVideoId(null)
    }
  }

  async function applyLanguagePrefixesBulk() {
    setMsg('Doplňuji prefixy CZ_/EN_...')
    let changed = 0
    try {
      for (const item of items) {
        const nextTitle = ensureLanguagePrefix(item.title, item.language || '')
        if (!nextTitle) continue
        if (nextTitle === item.title) continue
        await api.library.upsert({
          video_id: item.video_id,
          title: nextTitle,
          url: item.url,
          duration_seconds: item.duration_seconds ?? undefined,
          language: item.language || 'cs',
          genre: item.genre ?? undefined,
        })
        changed += 1
      }
      await load()
      setMsg(changed > 0
        ? `Prefix doplněn u ${changed} videí.`
        : 'Žádná změna: názvy už mají správný prefix.')
    } catch (e: unknown) {
      setMsg(`Chyba při doplnění prefixů: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold">Knihovna videí</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void refreshMissingMetadata()}
            disabled={refreshingMetadata}
            className="text-sm px-3 py-1.5 rounded border bg-white text-gray-700 border-gray-300 hover:bg-gray-50 disabled:opacity-50"
            title="Doplní chybějící metadata u starších položek (žánr, zhlédnutí, jazyky titulků...)"
          >
            {refreshingMetadata ? '⏳ Metadata...' : '⟳ Doplň metadata'}
          </button>
          <button
            onClick={() => void applyLanguagePrefixesBulk()}
            className="text-sm px-3 py-1.5 rounded border bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
            title="Přidá prefix CZ_ nebo EN_ podle jazyka videa"
          >
            Prefix CZ/EN
          </button>
          <button onClick={() => setShowSearch(v => !v)}
            className={`text-sm px-3 py-1.5 rounded border ${showSearch
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white text-blue-600 border-blue-300 hover:bg-blue-50'}`}>
            {showSearch ? '▲ Skrýt hledání' : '🔍 Hledat na YouTube'}
          </button>
        </div>
      </div>

      {/* YouTube search panel */}
      {showSearch && <SearchPanel onAddVideo={load} />}

      {/* Přidat video ručně */}
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
          Přidat ručně
        </button>
        {msg && <span className="text-sm text-gray-600">{msg}</span>}
      </div>

      {/* Tabulka videí */}
      <div className="bg-white rounded border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-600 text-xs uppercase">
            <tr>
              <th className="px-3 py-2 text-center">Menu</th>
              {LIB_SORT_COLUMNS.map(({ key, label, align }) => {
                const idx = sortOrder.indexOf(key)
                const active = idx >= 0
                const dir = sortDirMap[key]
                return (
                  <th key={key} className={`px-4 py-2 ${align} select-none`}>
                    <label className="inline-flex items-center gap-1">
                      <input
                        type="checkbox"
                        checked={active}
                        onChange={() => toggleSortPriority(key)}
                        title={`Přidat/odebrat ${label} do priority řazení`}
                      />
                      <button
                        type="button"
                        onClick={() => toggleSortDirection(key)}
                        className={`hover:underline ${active ? 'text-gray-800 font-semibold' : 'text-gray-500'}`}
                        title={`Směr řazení pro ${label}`}
                      >
                        {label}
                        {active ? ` ${idx + 1}.${dir === 'asc' ? '▲' : '▼'}` : ''}
                      </button>
                    </label>
                  </th>
                )
              })}
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {sortedItems.map(item => (
              <Fragment key={item.video_id}>
                <tr
                  className="border-t border-gray-100 hover:bg-gray-50 cursor-pointer"
                  onClick={() => expand(item)}
                >
                  <td className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      checked={item.visible_in_menus !== false}
                      onClick={e => e.stopPropagation()}
                      onChange={e => { e.stopPropagation(); void toggleVisibility(item, e.target.checked) }}
                      disabled={togglingVisibilityVideoId === item.video_id}
                      title={item.visible_in_menus !== false ? 'Zobrazuje se v ostatních výběrových menu' : 'Skryto v ostatních výběrových menu'}
                    />
                  </td>
                  <td className="px-4 py-2 font-medium text-gray-800 max-w-xs" title={item.title}>
                    {editingVideoId === item.video_id ? (
                      <div className="flex items-center gap-2">
                        <input
                          value={editingTitle}
                          onChange={e => setEditingTitle(e.target.value)}
                          onClick={e => e.stopPropagation()}
                          onKeyDown={e => {
                            if (e.key === 'Enter') { e.preventDefault(); void saveEditTitle(item) }
                            if (e.key === 'Escape') { e.preventDefault(); cancelEditTitle() }
                          }}
                          className="border border-gray-300 rounded px-2 py-1 text-xs w-full max-w-[420px]"
                        />
                        <button
                          type="button"
                          onClick={e => { e.stopPropagation(); void saveEditTitle(item) }}
                          disabled={savingVideoId === item.video_id}
                          className="text-xs px-2 py-1 rounded border border-green-300 text-green-700 hover:bg-green-50 disabled:opacity-50"
                          title="Uložit název"
                        >
                          Uložit
                        </button>
                        <button
                          type="button"
                          onClick={e => { e.stopPropagation(); cancelEditTitle() }}
                          disabled={savingVideoId === item.video_id}
                          className="text-xs px-2 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
                          title="Zrušit úpravu"
                        >
                          Zrušit
                        </button>
                      </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <span className="truncate" title={item.title}>{item.title}</span>
                      <button
                        type="button"
                        onClick={e => { e.stopPropagation(); startEditTitle(item) }}
                          className="text-xs px-1.5 py-0.5 rounded border border-gray-300 text-gray-500 hover:text-gray-700 hover:border-gray-400"
                          title="Upravit název videa"
                        >
                          ✎
                        </button>
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <LangBadge code={item.language} />
                  </td>
                  <td className="px-4 py-2 text-gray-500">
                    {item.duration_seconds ? fmtDuration(item.duration_seconds) : '–'}
                  </td>
                  <td className="px-4 py-2 text-gray-500">
                    {item.genre ? <span title={item.genre}>{item.genre}</span> : '–'}
                  </td>
                  <td className="px-4 py-2 text-gray-500">
                    {typeof item.view_count === 'number' ? fmtViews(item.view_count) : '–'}
                  </td>
                  <td className="px-4 py-2 text-gray-500">
                    {item.subtitle_languages?.length
                      ? <span title={item.subtitle_languages.join(', ')}>{item.subtitle_languages.join(', ')}</span>
                      : '–'}
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
                          title={item.subtitle_files?.[0]?.filename ?? 'VTT titulky'}
                        >
                          {subtitleOpen === item.video_id ? '▲ Skrýt' : '📄'}{' '}
                          {item.subtitle_files?.[0]?.filename?.replace(/^[^_]+_/, '').replace('.vtt', '') ?? 'VTT'}
                        </button>
                      )
                      : (
                        <button onClick={e => { e.stopPropagation(); void downloadSubs(item) }}
                          className="text-blue-600 underline text-xs">Stáhnout</button>
                      )}
                  </td>
                  <td className="px-4 py-2">
                    {item.audio_cached
                      ? <span className="text-green-600 text-xs font-medium" title="Plné audio staženo v cache">✓ WAV</span>
                      : <span className="text-gray-300 text-xs" title="Audio se stahuje na pozadí…">⏳</span>}
                  </td>
                  <td className="px-4 py-2 text-gray-500 text-xs">
                    {item.upload_date
                      ? <span title={`Vydáno: ${item.upload_date}${item.added_at ? `\nPřidáno: ${item.added_at.slice(0, 10)}` : ''}`}>{item.upload_date}</span>
                      : item.added_at
                        ? <span className="text-gray-300" title="Datum vydání se načítá...">přidáno {item.added_at.slice(0, 10)}</span>
                        : '–'}
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
                    <td colSpan={12} className="px-6 py-3">
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
                    <td colSpan={12} className="px-6 py-3">
                      <div className="text-xs font-semibold text-gray-600 mb-2">
                        video_id: {item.video_id} &nbsp;|&nbsp;
                        <a href={item.url} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                          YouTube
                        </a>
                        <button
                          type="button"
                          className="ml-3 text-xs px-2 py-0.5 rounded border border-gray-300 text-gray-600 hover:bg-gray-100"
                          onClick={e => { e.stopPropagation(); void refreshMetadata(item) }}
                          title="Znovu načíst metadata videa (délka/jazyk/titulky/žánr/datum/zhlédnutí)"
                        >
                          ⟳ Metadata
                        </button>
                      </div>
                      <div className="mb-3 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2 text-xs">
                        <div className="rounded border border-gray-200 bg-white px-2 py-1.5">
                          <div className="text-gray-400">Žánr</div>
                          <div className="text-gray-700">{item.genre || '–'}</div>
                        </div>
                        <div className="rounded border border-gray-200 bg-white px-2 py-1.5">
                          <div className="text-gray-400">Zhlédnutí</div>
                          <div className="text-gray-700">
                            {typeof item.view_count === 'number' ? item.view_count.toLocaleString('cs-CZ') : '–'}
                          </div>
                        </div>
                        <div className="rounded border border-gray-200 bg-white px-2 py-1.5">
                          <div className="text-gray-400">Jazyky titulků</div>
                          <div className="text-gray-700">{item.subtitle_languages?.length ? item.subtitle_languages.join(', ') : '–'}</div>
                        </div>
                        <div className="rounded border border-gray-200 bg-white px-2 py-1.5">
                          <div className="text-gray-400">Metadata načtena</div>
                          <div className="text-gray-700">{item.metadata_fetched_at ? item.metadata_fetched_at.replace('T', ' ').slice(0, 19) : '–'}</div>
                        </div>
                      </div>
                      {results[item.video_id]?.length
                        ? <ResultsTable rows={results[item.video_id]} />
                        : <span className="text-gray-400 text-xs">Zatím žádné výsledky — spusť benchmark.</span>}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {items.length === 0 && (
              <tr><td colSpan={12} className="px-4 py-8 text-center text-gray-400">Knihovna je prázdná.</td></tr>
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
