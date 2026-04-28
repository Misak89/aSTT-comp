import { useEffect, useState } from 'react'
import type { LibraryItem } from '../types'

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

const LIB_SETTINGS_KEY = 'astt_library_settings_v1'
const LIB_SORT_CHANGED_EVENT = 'astt:library-sort-changed'

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

function isLibrarySortKey(value: unknown): value is LibrarySortKey {
  return (
    value === 'title' ||
    value === 'language' ||
    value === 'duration' ||
    value === 'genre' ||
    value === 'view_count' ||
    value === 'subtitle_languages' ||
    value === 'subtitles' ||
    value === 'audio' ||
    value === 'visible_in_menus' ||
    value === 'added_at' ||
    value === 'wer'
  )
}

export function loadLibrarySortSettings(): {
  sortOrder: LibrarySortKey[]
  sortDirMap: Record<LibrarySortKey, 'asc' | 'desc'>
} {
  try {
    const raw = window.localStorage.getItem(LIB_SETTINGS_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    const rawOrder = Array.isArray(parsed?.sortOrder) ? parsed.sortOrder : []
    const sortOrder = rawOrder.filter(isLibrarySortKey)
    const rawDirMap = parsed?.sortDirMap && typeof parsed.sortDirMap === 'object' ? parsed.sortDirMap : {}
    const sortDirMap = { ...LIB_SORT_DEFAULT_DIR }
    for (const [key, dir] of Object.entries(rawDirMap)) {
      if (!isLibrarySortKey(key)) continue
      if (dir === 'asc' || dir === 'desc') sortDirMap[key] = dir
    }
    return { sortOrder: sortOrder.length > 0 ? sortOrder : ['added_at'], sortDirMap }
  } catch {
    return { sortOrder: ['added_at'], sortDirMap: { ...LIB_SORT_DEFAULT_DIR } }
  }
}

export function usesLibraryWerSort(): boolean {
  return loadLibrarySortSettings().sortOrder.includes('wer')
}

export function notifyLibrarySortSettingsChanged(): void {
  try {
    window.dispatchEvent(new CustomEvent(LIB_SORT_CHANGED_EVENT))
  } catch {
    // Browser events are best-effort; localStorage still remains the source.
  }
}

export function useLibrarySortRevision(): number {
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const bump = () => setRevision(value => value + 1)
    const onStorage = (event: StorageEvent) => {
      if (event.key === LIB_SETTINGS_KEY) bump()
    }
    window.addEventListener(LIB_SORT_CHANGED_EVENT, bump)
    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener(LIB_SORT_CHANGED_EVENT, bump)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  return revision
}

export function sortLibraryItemsLikeLibraryPage(
  items: LibraryItem[],
  werByVideoId: Record<string, number | null> = {},
): LibraryItem[] {
  const { sortOrder, sortDirMap } = loadLibrarySortSettings()
  return [...items].sort((a, b) => {
    for (const key of sortOrder) {
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
      else if (key === 'wer') { va = werByVideoId[a.video_id] ?? 999; vb = werByVideoId[b.video_id] ?? 999 }
      const cmp = va < vb ? -1 : va > vb ? 1 : 0
      if (cmp !== 0) return sortDirMap[key] === 'asc' ? cmp : -cmp
    }
    return 0
  })
}
