/**
 * useTranscribeStorage — LocalStorage úložiště pro stránku Přepis.
 *
 * Archiv přepisů a nastavení se ukládají lokálně v prohlížeči.
 * Funguje okamžitě bez restartu backendu.
 */

const ARCHIVE_KEY = 'astt_transcripts_v1'
const SETTINGS_KEY = 'astt_transcribe_settings_v1'

// ── Typy ────────────────────────────────────────────────────────────────────

export interface TranscriptEntry {
  transcript_id: string
  title: string
  html: string
  plain_text: string
  created_at: string
  updated_at: string
  source_label?: string
  model_id?: string
  range_from?: string
  range_to?: string
}

export interface TranscribeSettings {
  model?: string
  source_tab?: 'library' | 'upload'
  range_mode?: 'full' | 'segment'
  range_from?: string
  range_to?: string
  layout_mode?: 'horizontal' | 'vertical' | 'tabs'
  layout_split?: number
  player_speed?: number
  player_step?: number
  player_bigstep?: number
}

// ── Archiv přepisů ───────────────────────────────────────────────────────────

function loadArchive(): TranscriptEntry[] {
  try {
    const raw = localStorage.getItem(ARCHIVE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveArchive(entries: TranscriptEntry[]): void {
  localStorage.setItem(ARCHIVE_KEY, JSON.stringify(entries))
}

export function listTranscripts(): TranscriptEntry[] {
  return loadArchive()
}

export function saveTranscript(entry: Omit<TranscriptEntry, 'transcript_id' | 'created_at' | 'updated_at'> & { transcript_id?: string }): TranscriptEntry {
  const archive = loadArchive()
  const now = new Date().toISOString()

  if (entry.transcript_id) {
    const idx = archive.findIndex(e => e.transcript_id === entry.transcript_id)
    if (idx >= 0) {
      archive[idx] = { ...archive[idx], ...entry, updated_at: now } as TranscriptEntry
      saveArchive(archive)
      return archive[idx]
    }
  }

  const newEntry: TranscriptEntry = {
    transcript_id: `t_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    created_at: now,
    updated_at: now,
    title: entry.title,
    html: entry.html,
    plain_text: entry.plain_text,
    source_label: entry.source_label,
    model_id: entry.model_id,
    range_from: entry.range_from,
    range_to: entry.range_to,
  }
  archive.unshift(newEntry)
  saveArchive(archive)
  return newEntry
}

export function getTranscript(id: string): TranscriptEntry | null {
  return loadArchive().find(e => e.transcript_id === id) ?? null
}

export function deleteTranscript(id: string): void {
  saveArchive(loadArchive().filter(e => e.transcript_id !== id))
}

// ── Nastavení ────────────────────────────────────────────────────────────────

export function loadSettings(): TranscribeSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

export function saveSettings(settings: TranscribeSettings): void {
  const current = loadSettings()
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...current, ...settings }))
}
