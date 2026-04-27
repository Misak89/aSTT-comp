import { Fragment, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type {
  LibraryItem,
  LatestResult,
  YTSearchResult,
  LocalFileEntry,
  SegmentBundle,
  SegmentBundlePreviewRequest,
} from '../types'
import { WerBadge } from '../components/WerBadge'
import { listTranscripts, deleteTranscript as deleteLsTranscript, type TranscriptEntry } from '../components/transcribe/useTranscribeStorage'
import { formatDateTimeShort } from '../lib/time'

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

function fmtDurationPrecise(s: number) {
  const total = Math.max(0, Number.isFinite(s) ? s : 0)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const sec = total % 60
  const secText = sec < 10 ? `0${sec.toFixed(1)}` : sec.toFixed(1)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${secText}`
  return `${String(m).padStart(2, '0')}:${secText}`
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

const PRIORITY_LANGS = new Set(['cs', 'en', 'sk', 'de', 'pl', 'ru', 'uk'])

function LangBadge({ code }: { code: string }) {
  const color = code === 'cs' ? 'bg-blue-100 text-blue-700' : 'bg-orange-100 text-orange-700'
  return (
    <span className={`px-1 py-0.5 rounded font-mono font-bold text-xs ${color}`} title={LANG_LABELS[code] ?? code}>
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

const LIB_SETTINGS_KEY = 'astt_library_settings_v1'

function loadLibrarySettings(): { sortOrder?: LibrarySortKey[]; sortDirMap?: Record<LibrarySortKey, 'asc' | 'desc'> } {
  try { return JSON.parse(localStorage.getItem(LIB_SETTINGS_KEY) ?? '{}') } catch { return {} }
}

function saveLibrarySettings(s: { sortOrder?: LibrarySortKey[]; sortDirMap?: Record<LibrarySortKey, 'asc' | 'desc'> }) {
  try {
    const cur = loadLibrarySettings()
    localStorage.setItem(LIB_SETTINGS_KEY, JSON.stringify({ ...cur, ...s }))
  } catch { /* ignore */ }
}

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

function SubtitleLangsCell({ langs }: { langs: string[] }) {
  const [expanded, setExpanded] = useState(false)
  if (!langs || langs.length === 0) return <span className="text-gray-400">–</span>

  const priority = langs.filter(l => PRIORITY_LANGS.has(l))
  const rest = langs.filter(l => !PRIORITY_LANGS.has(l))

  return (
    <div className="flex flex-wrap gap-0.5 items-center">
      {priority.map(l => <LangBadge key={l} code={l} />)}
      {rest.length > 0 && !expanded && (
        <button
          type="button"
          onClick={e => { e.stopPropagation(); setExpanded(true) }}
          className="text-[10px] text-gray-400 hover:text-blue-500 hover:underline px-0.5"
          title={`Ostatní: ${rest.join(', ')}`}
        >+{rest.length}</button>
      )}
      {rest.length > 0 && expanded && (
        <>
          {rest.map(l => <LangBadge key={l} code={l} />)}
          <button
            type="button"
            onClick={e => { e.stopPropagation(); setExpanded(false) }}
            className="text-[10px] text-gray-400 hover:underline px-0.5"
          >méně</button>
        </>
      )}
    </div>
  )
}

// ── LocalImportPanel ─────────────────────────────────────────────────────────

function fmtSize(bytes: number) {
  if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)} GB`
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}

function LocalImportPanel({ onImported }: { onImported: () => void }) {
  const [dirPath, setDirPath] = useState('')
  const [scanning, setScanning] = useState(false)
  const [scanError, setScanError] = useState('')
  const [files, setFiles] = useState<LocalFileEntry[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [titleMap, setTitleMap] = useState<Record<string, string>>({})
  const [langMap, setLangMap] = useState<Record<string, string>>({})
  const [importing, setImporting] = useState<Set<string>>(new Set())
  const [imported, setImported] = useState<Set<string>>(new Set())
  const [importMsg, setImportMsg] = useState<Record<string, string>>({})

  async function doScan() {
    const path = dirPath.trim()
    if (!path) return
    setScanning(true); setScanError(''); setFiles([]); setSelected(new Set())
    setTitleMap({}); setLangMap({}); setImported(new Set()); setImportMsg({})
    try {
      const result = await api.library.scanDirectory(path)
      setFiles(result)
      const titles: Record<string, string> = {}
      const langs: Record<string, string> = {}
      for (const f of result) {
        titles[f.path] = f.filename.replace(/\.[^.]+$/, '')
        langs[f.path] = 'cs'
      }
      setTitleMap(titles)
      setLangMap(langs)
    } catch (e: unknown) {
      setScanError(e instanceof Error ? e.message : String(e))
    }
    setScanning(false)
  }

  function toggleSelect(path: string) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  function toggleAll() {
    if (selected.size === files.length) setSelected(new Set())
    else setSelected(new Set(files.map(f => f.path)))
  }

  async function doImport() {
    const toImport = files.filter(f => selected.has(f.path) && !imported.has(f.path))
    if (!toImport.length) return
    for (const f of toImport) {
      setImporting(prev => new Set([...prev, f.path]))
      setImportMsg(prev => ({ ...prev, [f.path]: 'Importuji...' }))
      try {
        const title = (titleMap[f.path] || f.filename).trim() || f.filename
        const lang = langMap[f.path] || 'cs'
        await api.library.importLocalFile(f.path, ensureLanguagePrefix(title, lang), lang)
        setImported(prev => new Set([...prev, f.path]))
        setImportMsg(prev => ({ ...prev, [f.path]: '✓ Přidáno' }))
      } catch (e: unknown) {
        setImportMsg(prev => ({ ...prev, [f.path]: `Chyba: ${e instanceof Error ? e.message : String(e)}` }))
      }
      setImporting(prev => { const n = new Set(prev); n.delete(f.path); return n })
    }
    onImported()
  }

  const selectedNotImported = files.filter(f => selected.has(f.path) && !imported.has(f.path))

  return (
    <div className="bg-white rounded border border-gray-200 p-4 mb-6">
      <h2 className="text-sm font-bold text-gray-700 mb-3">Importovat ze složky / hledat audio na disku</h2>

      <div className="flex gap-2 items-end mb-3">
        <div className="flex flex-col gap-1 flex-1">
          <label className="text-xs text-gray-500">Cesta ke složce (např. C:\Users\...)</label>
          <input
            value={dirPath}
            onChange={e => setDirPath(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') void doScan() }}
            placeholder="C:\Users\adamf\Záznamy"
            className="border rounded px-2 py-1 text-sm font-mono"
          />
        </div>
        <button
          onClick={() => void doScan()}
          disabled={scanning || !dirPath.trim()}
          className="bg-gray-700 text-white px-4 py-1.5 rounded text-sm disabled:opacity-50 whitespace-nowrap"
        >
          {scanning ? 'Hledám...' : 'Prohledat'}
        </button>
      </div>

      {scanError && <p className="text-red-600 text-xs mb-2">{scanError}</p>}

      {files.length > 0 && (
        <>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-gray-500">
              Nalezeno {files.length} souborů — zaškrtněte pro import
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={toggleAll}
                className="text-xs text-blue-500 hover:underline"
              >
                {selected.size === files.length ? 'Odznačit vše' : 'Vybrat vše'}
              </button>
              <button
                onClick={() => void doImport()}
                disabled={selectedNotImported.length === 0}
                className="bg-blue-600 text-white text-xs px-3 py-1 rounded disabled:opacity-50"
              >
                Přidat vybrané ({selectedNotImported.length})
              </button>
            </div>
          </div>

          <div className="max-h-72 overflow-y-auto border border-gray-100 rounded">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 text-gray-500 sticky top-0">
                <tr>
                  <th className="px-2 py-1 w-6"></th>
                  <th className="px-2 py-1 text-left">Soubor</th>
                  <th className="px-2 py-1 text-left w-20">Délka</th>
                  <th className="px-2 py-1 text-left w-16">Velikost</th>
                  <th className="px-2 py-1 text-left w-48">Název v knihovně</th>
                  <th className="px-2 py-1 text-left w-16">Jazyk</th>
                  <th className="px-2 py-1 w-16"></th>
                </tr>
              </thead>
              <tbody>
                {files.map(f => (
                  <tr key={f.path} className={`border-t border-gray-100 ${selected.has(f.path) ? 'bg-blue-50' : 'hover:bg-gray-50'}`}>
                    <td className="px-2 py-1 text-center">
                      <input
                        type="checkbox"
                        checked={selected.has(f.path)}
                        disabled={imported.has(f.path)}
                        onChange={() => toggleSelect(f.path)}
                      />
                    </td>
                    <td className="px-2 py-1 font-mono text-gray-700 max-w-[200px] truncate" title={f.path}>
                      {f.filename}
                    </td>
                    <td className="px-2 py-1 text-gray-500">
                      {f.duration_seconds != null ? fmtDuration(f.duration_seconds) : '–'}
                    </td>
                    <td className="px-2 py-1 text-gray-500">{fmtSize(f.size_bytes)}</td>
                    <td className="px-2 py-1">
                      <input
                        value={titleMap[f.path] ?? ''}
                        onChange={e => setTitleMap(prev => ({ ...prev, [f.path]: e.target.value }))}
                        disabled={imported.has(f.path)}
                        className="border rounded px-1 py-0 text-xs w-full disabled:bg-gray-50"
                      />
                    </td>
                    <td className="px-2 py-1">
                      <select
                        value={langMap[f.path] ?? 'cs'}
                        onChange={e => setLangMap(prev => ({ ...prev, [f.path]: e.target.value }))}
                        disabled={imported.has(f.path)}
                        className="border rounded px-1 py-0 text-xs w-full disabled:bg-gray-50"
                      >
                        {LANGS.map(l => <option key={l} value={l}>{l.toUpperCase()}</option>)}
                      </select>
                    </td>
                    <td className="px-2 py-1 text-center whitespace-nowrap">
                      {imported.has(f.path)
                        ? <span className="text-green-600 font-medium">✓</span>
                        : importing.has(f.path)
                          ? <span className="text-gray-400">...</span>
                          : importMsg[f.path]
                            ? <span className="text-red-500" title={importMsg[f.path]}>!</span>
                            : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!scanning && files.length === 0 && dirPath && !scanError && (
        <p className="text-xs text-gray-400">Žádné audio/video soubory nenalezeny.</p>
      )}
    </div>
  )
}

const SEGMENT_PRESET_MINUTES = [5, 10, 15, 30, 45, 60]
const SEGMENT_MAX_MANUAL_POINTS = 21
const SEGMENT_MIN_GAP_S = 0.2

function roundToMs(seconds: number): number {
  return Math.round(seconds * 1000) / 1000
}

function buildPresetPoints(durationS: number, presetMinutes: number): number[] {
  const out: number[] = []
  const step = Math.max(60, Math.round(presetMinutes * 60))
  for (let cursor = step; cursor < durationS; cursor += step) out.push(roundToMs(cursor))
  return out
}

function normalizeManualPoints(points: number[], durationS: number): number[] {
  return [...new Set(points.map(p => roundToMs(p)))]
    .filter(p => p > 0 && p < durationS)
    .sort((a, b) => a - b)
}

function SegmentSlicerCard({ item }: { item: LibraryItem }) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const durationS = Number(item.audio_duration_seconds ?? item.duration_seconds ?? 0)
  const [mode, setMode] = useState<'preset' | 'manual'>('preset')
  const [presetMinutes, setPresetMinutes] = useState<number>(15)
  const [manualPoints, setManualPoints] = useState<number[]>([])
  const [toleranceSeconds, setToleranceSeconds] = useState<number>(2.0)
  const [pauseAware, setPauseAware] = useState(true)
  const [pauseSilenceDbfs, setPauseSilenceDbfs] = useState(-40.0)
  const [pauseMinSilenceMs, setPauseMinSilenceMs] = useState(250)
  const [selectedPointIdx, setSelectedPointIdx] = useState<number | null>(null)
  const [audioPositionS, setAudioPositionS] = useState(0)
  const [previewBundle, setPreviewBundle] = useState<SegmentBundle | null>(null)
  const [loadingExisting, setLoadingExisting] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoadingExisting(true)
    setStatus('')
    setError('')
    setPreviewBundle(null)
    setSelectedPointIdx(null)
    setMode('preset')
    setPresetMinutes(15)
    setManualPoints([])
    ;(async () => {
      try {
        const existing = await api.library.getSegmentBundle(item.video_id)
        if (cancelled) return
        setPreviewBundle(existing)
        setMode(existing.mode)
        setToleranceSeconds(existing.tolerance_seconds || 2.0)
        if (existing.mode === 'preset') {
          setPresetMinutes(existing.preset_minutes || 15)
          setManualPoints([])
        } else {
          setManualPoints(normalizeManualPoints(existing.points_seconds || [], durationS).slice(0, SEGMENT_MAX_MANUAL_POINTS))
          setSelectedPointIdx((existing.points_seconds || []).length > 0 ? 0 : null)
        }
        setStatus('Načten uložený segment bundle.')
      } catch {
        // bundle nemusí existovat
      } finally {
        if (!cancelled) setLoadingExisting(false)
      }
    })()
    return () => { cancelled = true }
  }, [item.video_id, durationS])

  const presetPoints = buildPresetPoints(durationS, presetMinutes)
  const workingPoints = mode === 'manual' ? manualPoints : presetPoints
  const timelinePoints = previewBundle?.mode === mode ? (previewBundle.points_seconds || workingPoints) : workingPoints

  function buildRequest(): SegmentBundlePreviewRequest {
    return {
      source_id: item.video_id,
      source_type: 'library_item',
      mode,
      audio_duration_seconds: durationS,
      tolerance_seconds: toleranceSeconds,
      pause_aware: pauseAware,
      pause_silence_dbfs: pauseSilenceDbfs,
      pause_min_silence_ms: pauseMinSilenceMs,
      preset_minutes: mode === 'preset' ? presetMinutes : null,
      manual_points_seconds: mode === 'manual' ? manualPoints : [],
    }
  }

  function seekAudio(seconds: number) {
    const next = Math.max(0, Math.min(durationS, seconds))
    setAudioPositionS(next)
    if (audioRef.current) audioRef.current.currentTime = next
  }

  function addManualPoint(seconds: number) {
    if (mode !== 'manual') return
    setError('')
    setStatus('')
    const normalized = normalizeManualPoints([...manualPoints, seconds], durationS)
    if (normalized.length === manualPoints.length) return
    if (normalized.length > SEGMENT_MAX_MANUAL_POINTS) {
      setError(`Manuální režim podporuje max ${SEGMENT_MAX_MANUAL_POINTS} bodů.`)
      return
    }
    setManualPoints(normalized)
    setSelectedPointIdx(Math.max(0, normalized.findIndex(v => Math.abs(v - roundToMs(seconds)) < 0.0005)))
  }

  function updateManualPoint(idx: number, nextValue: number) {
    const prev = idx > 0 ? manualPoints[idx - 1] : 0
    const next = idx < manualPoints.length - 1 ? manualPoints[idx + 1] : durationS
    const clamped = Math.max(prev + SEGMENT_MIN_GAP_S, Math.min(next - SEGMENT_MIN_GAP_S, nextValue))
    const draft = [...manualPoints]
    draft[idx] = roundToMs(clamped)
    setManualPoints(normalizeManualPoints(draft, durationS))
  }

  function removeManualPoint(idx: number) {
    const next = manualPoints.filter((_, i) => i !== idx)
    setManualPoints(next)
    if (!next.length) setSelectedPointIdx(null)
    else if (selectedPointIdx != null) setSelectedPointIdx(Math.min(selectedPointIdx, next.length - 1))
  }

  async function preview() {
    if (!durationS || durationS <= 0) {
      setError('Tato položka zatím nemá známou délku audia.')
      return
    }
    setPreviewing(true)
    setError('')
    setStatus('')
    try {
      const bundle = await api.library.previewSegmentBundle(buildRequest())
      setPreviewBundle(bundle)
      setStatus(`Preview připraven: ${bundle.segments.length} segmentů.`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPreviewing(false)
    }
  }

  async function saveBundle() {
    if (!durationS || durationS <= 0) {
      setError('Tato položka zatím nemá známou délku audia.')
      return
    }
    setSaving(true)
    setError('')
    setStatus('')
    try {
      const bundle = await api.library.upsertSegmentBundle(item.video_id, buildRequest())
      setPreviewBundle(bundle)
      setStatus(`Uloženo: ${bundle.segments.length} segmentů. Na stránce Přepis lze použít režim "Segment bundle".`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const segmentDurations = previewBundle?.segments?.map(s => s.duration_s) ?? []
  const minSegment = segmentDurations.length ? Math.min(...segmentDurations) : null
  const maxSegment = segmentDurations.length ? Math.max(...segmentDurations) : null

  return (
    <div className="rounded border border-emerald-200 bg-emerald-50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <h4 className="text-sm font-semibold text-emerald-900">Kráječ dlouhých nahrávek (V6)</h4>
        <span className="text-xs text-emerald-700">
          Délka: {durationS > 0 ? fmtDurationPrecise(durationS) : 'neznámá'}
        </span>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-2 text-xs mb-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-gray-600">Režim:</span>
          <button
            type="button"
            onClick={() => { setMode('preset'); setSelectedPointIdx(null) }}
            className={`px-2 py-1 rounded border ${mode === 'preset' ? 'bg-emerald-600 text-white border-emerald-600' : 'bg-white border-gray-300 text-gray-700'}`}
          >
            preset
          </button>
          <button
            type="button"
            onClick={() => setMode('manual')}
            className={`px-2 py-1 rounded border ${mode === 'manual' ? 'bg-emerald-600 text-white border-emerald-600' : 'bg-white border-gray-300 text-gray-700'}`}
          >
            manual
          </button>
          {mode === 'preset' && (
            <select
              value={presetMinutes}
              onChange={e => setPresetMinutes(parseInt(e.target.value, 10))}
              className="border rounded px-2 py-1 bg-white"
            >
              {SEGMENT_PRESET_MINUTES.map(v => <option key={v} value={v}>{v} min</option>)}
            </select>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex items-center gap-1">
            <input type="checkbox" checked={pauseAware} onChange={e => setPauseAware(e.target.checked)} />
            pause-aware
          </label>
          <label className="inline-flex items-center gap-1">
            tolerance ±
            <input
              type="number"
              min={0}
              max={30}
              step={0.1}
              value={toleranceSeconds}
              onChange={e => setToleranceSeconds(Math.max(0, Math.min(30, Number(e.target.value) || 0)))}
              className="w-16 border rounded px-1 py-0.5 bg-white"
            />
            s
          </label>
          <label className="inline-flex items-center gap-1">
            silence dBFS
            <input
              type="number"
              min={-90}
              max={-5}
              step={1}
              value={pauseSilenceDbfs}
              onChange={e => setPauseSilenceDbfs(Math.max(-90, Math.min(-5, Number(e.target.value) || -40)))}
              className="w-16 border rounded px-1 py-0.5 bg-white"
            />
          </label>
          <label className="inline-flex items-center gap-1">
            min pause
            <input
              type="number"
              min={50}
              max={5000}
              step={10}
              value={pauseMinSilenceMs}
              onChange={e => setPauseMinSilenceMs(Math.max(50, Math.min(5000, Number(e.target.value) || 250)))}
              className="w-16 border rounded px-1 py-0.5 bg-white"
            />
            ms
          </label>
        </div>
      </div>

      <audio
        ref={audioRef}
        controls
        preload="metadata"
        src={`/api/transcribe/library-audio/${item.video_id}`}
        className="w-full mb-2"
        onTimeUpdate={e => setAudioPositionS((e.target as HTMLAudioElement).currentTime || 0)}
      />

      <div
        className={`relative h-12 rounded border ${mode === 'manual' ? 'border-emerald-400 bg-white cursor-crosshair' : 'border-emerald-300 bg-white cursor-pointer'}`}
        onClick={e => {
          if (durationS <= 0) return
          const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect()
          const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)))
          const sec = roundToMs(ratio * durationS)
          seekAudio(sec)
          if (mode === 'manual') addManualPoint(sec)
        }}
        title={mode === 'manual' ? 'Kliknutí přidá bod hranice segmentu' : 'Kliknutí přesune přehrávač'}
      >
        <div className="absolute inset-y-0 left-0 right-0 bg-gradient-to-r from-emerald-100 to-cyan-100 opacity-60" />
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-red-500"
          style={{ left: `${durationS > 0 ? (audioPositionS / durationS) * 100 : 0}%` }}
          title={`Pozice ${fmtDurationPrecise(audioPositionS)}`}
        />
        {timelinePoints.map((p, idx) => {
          const left = durationS > 0 ? (p / durationS) * 100 : 0
          const selected = mode === 'manual' && idx === selectedPointIdx
          return (
            <button
              key={`${item.video_id}_pt_${idx}_${p}`}
              type="button"
              onClick={ev => {
                ev.stopPropagation()
                if (mode === 'manual') setSelectedPointIdx(idx)
                seekAudio(p)
              }}
              className={`absolute top-0 bottom-0 w-0.5 ${selected ? 'bg-orange-600' : 'bg-emerald-700'}`}
              style={{ left: `${Math.max(0, Math.min(100, left))}%` }}
              title={`Bod ${idx + 1}: ${fmtDurationPrecise(p)}`}
            />
          )
        })}
      </div>

      {mode === 'manual' && (
        <div className="mt-2">
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-gray-600">Body: {manualPoints.length}/{SEGMENT_MAX_MANUAL_POINTS}</span>
            <button
              type="button"
              onClick={() => { setManualPoints([]); setSelectedPointIdx(null) }}
              className="px-2 py-0.5 rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
            >
              Vyčistit body
            </button>
          </div>
          {manualPoints.length === 0 ? (
            <div className="text-xs text-gray-500">Klikněte do timeline pro přidání hranic segmentů.</div>
          ) : (
            <div className="max-h-36 overflow-y-auto space-y-1">
              {manualPoints.map((point, idx) => (
                <div
                  key={`${item.video_id}_manual_${idx}_${point}`}
                  className={`flex items-center gap-2 rounded border px-2 py-1 ${
                    idx === selectedPointIdx ? 'border-orange-300 bg-orange-50' : 'border-gray-200 bg-white'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => { setSelectedPointIdx(idx); seekAudio(point) }}
                    className="font-mono text-xs text-gray-700 min-w-28 text-left"
                  >
                    #{idx + 1} {fmtDurationPrecise(point)}
                  </button>
                  <button
                    type="button"
                    onClick={() => updateManualPoint(idx, point - 0.1)}
                    className="px-1.5 py-0.5 rounded border border-gray-300 bg-white hover:bg-gray-50"
                    title="Posunout o -0.1 s"
                  >
                    ←
                  </button>
                  <button
                    type="button"
                    onClick={() => updateManualPoint(idx, point + 0.1)}
                    className="px-1.5 py-0.5 rounded border border-gray-300 bg-white hover:bg-gray-50"
                    title="Posunout o +0.1 s"
                  >
                    →
                  </button>
                  <button
                    type="button"
                    onClick={() => removeManualPoint(idx)}
                    className="ml-auto px-1.5 py-0.5 rounded border border-red-300 bg-red-50 text-red-700 hover:bg-red-100"
                    title="Smazat bod"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 mt-3">
        <button
          type="button"
          onClick={() => void preview()}
          disabled={previewing || loadingExisting || durationS <= 0}
          className="px-3 py-1.5 rounded bg-emerald-700 text-white text-xs disabled:opacity-50"
        >
          {previewing ? 'Preview...' : 'Preview'}
        </button>
        <button
          type="button"
          onClick={() => void saveBundle()}
          disabled={saving || loadingExisting || durationS <= 0}
          className="px-3 py-1.5 rounded bg-cyan-700 text-white text-xs disabled:opacity-50"
        >
          {saving ? 'Ukládám...' : 'Uložit bundle'}
        </button>
        {status && <span className="text-xs text-emerald-800">{status}</span>}
        {error && <span className="text-xs text-red-600">{error}</span>}
      </div>

      {previewBundle && (
        <div className="mt-3 rounded border border-emerald-200 bg-white p-2">
          <div className="text-xs text-gray-700 mb-1">
            Segmenty: <strong>{previewBundle.segments.length}</strong>
            {minSegment != null && maxSegment != null && (
              <span> | min {fmtDurationPrecise(minSegment)} | max {fmtDurationPrecise(maxSegment)}</span>
            )}
          </div>
          <div className="max-h-40 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="text-gray-500">
                <tr>
                  <th className="text-left py-0.5">#</th>
                  <th className="text-left py-0.5">Start</th>
                  <th className="text-left py-0.5">End</th>
                  <th className="text-left py-0.5">Délka</th>
                  <th className="text-left py-0.5">Pause snap</th>
                </tr>
              </thead>
              <tbody>
                {previewBundle.segments.map(seg => (
                  <tr key={`${item.video_id}_seg_${seg.idx}`} className="border-t border-gray-100">
                    <td className="py-0.5 font-mono">{seg.idx + 1}</td>
                    <td className="py-0.5 font-mono">{fmtDurationPrecise(seg.start_s)}</td>
                    <td className="py-0.5 font-mono">{fmtDurationPrecise(seg.end_s)}</td>
                    <td className="py-0.5 font-mono">{fmtDurationPrecise(seg.duration_s)}</td>
                    <td className="py-0.5">
                      {seg.snapped
                        ? <span className="text-amber-700">ano ({seg.snap_delta_ms != null ? `${seg.snap_delta_ms.toFixed(0)}ms` : ''})</span>
                        : <span className="text-gray-400">ne</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
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
  const [showLocalImport, setShowLocalImport] = useState(false)
  const [showLsArchive, setShowLsArchive] = useState(false)
  const [lsArchive, setLsArchive] = useState<TranscriptEntry[]>([])
  const [sortOrder, setSortOrder] = useState<LibrarySortKey[]>(() => loadLibrarySettings().sortOrder ?? ['added_at'])
  const [sortDirMap, setSortDirMap] = useState<Record<LibrarySortKey, 'asc' | 'desc'>>(
    () => ({ ...LIB_SORT_DEFAULT_DIR, ...(loadLibrarySettings().sortDirMap ?? {}) }),
  )
  const [fetchingMeta, setFetchingMeta] = useState(false)
  const [editingVideoId, setEditingVideoId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [savingVideoId, setSavingVideoId] = useState<string | null>(null)
  const [togglingVisibilityVideoId, setTogglingVisibilityVideoId] = useState<string | null>(null)
  const [refreshingMetadata, setRefreshingMetadata] = useState(false)

  function toggleSortPriority(col: LibrarySortKey) {
    setSortOrder(prev => {
      const next = prev.includes(col) ? prev.filter(k => k !== col) : [...prev, col]
      saveLibrarySettings({ sortOrder: next })
      return next
    })
  }

  function toggleSortDirection(col: LibrarySortKey) {
    setSortDirMap(prev => {
      const next = { ...prev, [col]: prev[col] === 'asc' ? 'desc' as const : 'asc' as const }
      saveLibrarySettings({ sortDirMap: next })
      return next
    })
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

  async function handleUrlBlur() {
    const url = addUrl.trim()
    if (!url || !url.includes('youtube') && !url.includes('youtu.be')) return
    if (addTitle) return  // already has title — don't overwrite
    setFetchingMeta(true)
    setMsg('Načítám název...')
    try {
      const info = await api.library.fetchVideoInfo(url)
      if (info.title) {
        const title = ensureLanguagePrefix(info.title, info.language || 'cs')
        setAddTitle(title)
        setMsg('')
      }
    } catch {
      setMsg('Nepodařilo se načíst název — vyplňte ručně.')
    }
    setFetchingMeta(false)
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
          <button onClick={() => setShowLocalImport(v => !v)}
            className={`text-sm px-3 py-1.5 rounded border ${showLocalImport
              ? 'bg-green-600 text-white border-green-600'
              : 'bg-white text-green-700 border-green-300 hover:bg-green-50'}`}>
            {showLocalImport ? '▲ Skrýt import' : '📂 Ze složky'}
          </button>
          <button
            onClick={() => {
              void (async () => {
                setMsg('Otevírám složku Down Audio...')
                try {
                  const res = await api.openDir.audioCache()
                  setMsg(`Down Audio: ${res.path} (pokud se okno neotevřelo, použij tuto cestu).`)
                } catch (e: unknown) {
                  setMsg(`Chyba při otevření Down Audio: ${e instanceof Error ? e.message : String(e)}`)
                }
              })()
            }}
            className="text-sm px-3 py-1.5 rounded border bg-white text-amber-700 border-amber-300 hover:bg-amber-50"
            title="Dokumenty\aSTT-comp\runtime\audio_cache"
          >
            📁 Down Audio
          </button>
          <button
            onClick={() => { setLsArchive(listTranscripts()); setShowLsArchive(true) }}
            className="text-sm px-3 py-1.5 rounded border bg-white text-purple-700 border-purple-300 hover:bg-purple-50"
            title="Uložené přepisy v LocalStorage prohlížeče"
          >
            📝 Přepisy ({listTranscripts().length})
          </button>
        </div>
      </div>

      {/* YouTube search panel */}
      {showSearch && <SearchPanel onAddVideo={load} />}

      {/* Local file import panel */}
      {showLocalImport && <LocalImportPanel onImported={load} />}

      {/* Přidat video ručně */}
      <div className="bg-white rounded border border-gray-200 p-4 mb-6 flex gap-3 items-end flex-wrap">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">Název</label>
          <input value={addTitle} onChange={e => setAddTitle(e.target.value)}
            placeholder="Název videa" className="border rounded px-2 py-1 text-sm w-64" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">YouTube URL {fetchingMeta && <span className="text-blue-500">načítám...</span>}</label>
          <input value={addUrl}
            onChange={e => setAddUrl(e.target.value)}
            onBlur={() => { void handleUrlBlur() }}
            placeholder="https://www.youtube.com/watch?v=..." className="border rounded px-2 py-1 text-sm w-80" />
        </div>
        <button onClick={addVideo} disabled={loading || fetchingMeta || !addUrl || !addTitle}
          className="bg-blue-600 text-white px-4 py-1.5 rounded text-sm disabled:opacity-50">
          Přidat ručně
        </button>
        {msg && <span className="text-sm text-gray-600 break-all">{msg}</span>}
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
                  <th key={key} className={`px-2 py-2 ${align} select-none`}>
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
              <th className="px-2 py-2"></th>
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
                  <td className="px-2 py-2 font-medium text-gray-800 max-w-xs" title={item.title}>
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
                      <span
                        className={`truncate ${/test/i.test(item.title) ? 'text-red-600 font-semibold' : ''}`}
                        title={item.title}
                      >
                        {item.title}
                      </span>
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
                  <td className="px-2 py-2">
                    <LangBadge code={item.language} />
                  </td>
                  <td className="px-2 py-2 text-gray-500">
                    {item.duration_seconds ? fmtDuration(item.duration_seconds) : '–'}
                  </td>
                  <td className="px-2 py-2 text-gray-500">
                    {item.genre ? <span title={item.genre}>{item.genre}</span> : '–'}
                  </td>
                  <td className="px-2 py-2 text-gray-500">
                    {typeof item.view_count === 'number' ? fmtViews(item.view_count) : '–'}
                  </td>
                  <td className="px-2 py-2 text-gray-500">
                    <SubtitleLangsCell langs={item.subtitle_languages ?? []} />
                  </td>
                  <td className="px-2 py-2">
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
                  <td className="px-2 py-2">
                    {item.audio_cached
                      ? (
                        <span className="text-green-600 text-xs font-medium" title={[
                          'Plné audio staženo v cache',
                          item.audio_size_bytes != null ? `${(item.audio_size_bytes / 1024 / 1024).toFixed(0)} MB` : null,
                          item.audio_duration_seconds != null ? `${Math.round(item.audio_duration_seconds)}s` : null,
                        ].filter(Boolean).join(' · ')}>
                          ✓ WAV{item.audio_size_bytes != null ? ` ${(item.audio_size_bytes / 1024 / 1024).toFixed(0)} MB` : ''}
                        </span>
                      )
                      : <span className="text-gray-300 text-xs" title="Audio se stahuje na pozadí…">⏳</span>}
                  </td>
                  <td className="px-2 py-2 text-gray-500 text-xs">
                    {item.upload_date
                      ? <span title={`Vydáno: ${item.upload_date}${item.added_at ? `\nPřidáno: ${item.added_at.slice(0, 10)}` : ''}`}>{item.upload_date}</span>
                      : item.added_at
                        ? <span className="text-gray-300" title="Datum vydání se načítá...">přidáno {item.added_at.slice(0, 10)}</span>
                        : '–'}
                  </td>
                  <td className="px-2 py-2">
                    {results[item.video_id]?.[0]
                      ? <WerBadge value={results[item.video_id][0].wer} />
                      : <span className="text-gray-400 text-xs">–</span>}
                  </td>
                  <td className="px-2 py-2 text-gray-400 text-xs">
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
                      <div className="mb-3">
                        <SegmentSlicerCard item={item} />
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

      {/* LocalStorage přepisy — archiv modal */}
      {showLsArchive && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
          onClick={e => { if (e.target === e.currentTarget) setShowLsArchive(false) }}>
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col">
            <div className="flex items-center justify-between px-5 py-3 border-b">
              <h2 className="font-semibold text-gray-800">Uložené přepisy — LocalStorage ({lsArchive.length})</h2>
              <button onClick={() => setShowLsArchive(false)} className="text-gray-400 hover:text-gray-700 text-xl leading-none">×</button>
            </div>
            <div className="flex-1 overflow-y-auto p-3">
              {lsArchive.length === 0 && (
                <div className="text-gray-400 text-sm p-6 text-center">Žádné uložené přepisy</div>
              )}
              <div className="space-y-2">
                {lsArchive.map(t => (
                  <div key={t.transcript_id} className="flex items-start gap-3 p-3 border border-gray-200 rounded-lg hover:bg-gray-50">
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-gray-800 truncate">{t.title}</div>
                      <div className="text-xs text-gray-500 flex flex-wrap gap-3 mt-0.5">
                        <span>🕐 {formatDateTimeShort(t.updated_at)}</span>
                        {t.source_label && <span>📹 {t.source_label}</span>}
                        {t.model_id && <span>🤖 {t.model_id}</span>}
                        {t.range_from && t.range_to && <span>⏱ {t.range_from}–{t.range_to}</span>}
                      </div>
                      {t.plain_text && (
                        <div className="text-xs text-gray-400 mt-1 truncate">{t.plain_text.slice(0, 150)}</div>
                      )}
                    </div>
                    <button
                      onClick={() => {
                        if (confirm(`Smazat "${t.title}"?`)) {
                          deleteLsTranscript(t.transcript_id)
                          setLsArchive(prev => prev.filter(e => e.transcript_id !== t.transcript_id))
                        }
                      }}
                      className="px-2.5 py-1 text-xs bg-red-100 text-red-600 rounded hover:bg-red-200 flex-shrink-0"
                    >
                      Smazat
                    </button>
                  </div>
                ))}
              </div>
            </div>
            <div className="px-5 py-3 border-t text-xs text-gray-400">
              Přepisy jsou uloženy v LocalStorage prohlížeče. Pro otevření/editaci použijte stránku Přepis.
            </div>
          </div>
        </div>
      )}
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
