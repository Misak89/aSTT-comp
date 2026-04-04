export const APP_TIME_LOCALE = 'cs-CZ'
export const APP_TIME_ZONE = 'Europe/Prague'

type DateInput = string | number | Date

function normalizeDate(input: DateInput): Date | null {
  const d = input instanceof Date ? input : new Date(input)
  if (Number.isNaN(d.getTime())) return null
  return d
}

function formatWith(
  input: DateInput,
  opts: Intl.DateTimeFormatOptions,
  fallback: string,
): string {
  const d = normalizeDate(input)
  if (!d) return fallback
  try {
    return new Intl.DateTimeFormat(APP_TIME_LOCALE, {
      timeZone: APP_TIME_ZONE,
      hour12: false,
      ...opts,
    }).format(d)
  } catch {
    return fallback
  }
}

export function formatDateTimeShort(input: DateInput): string {
  return formatWith(input, { dateStyle: 'short', timeStyle: 'short' }, String(input))
}

export function formatDateTimeMedium(input: DateInput): string {
  return formatWith(input, { dateStyle: 'short', timeStyle: 'medium' }, String(input))
}

export function formatClockHms(input: DateInput): string {
  return formatWith(
    input,
    { hour: '2-digit', minute: '2-digit', second: '2-digit' },
    '--:--:--',
  )
}

export function formatDateTimeDayMonthHm(input: DateInput): string {
  return formatWith(
    input,
    { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' },
    String(input),
  )
}

export function nowUtcIso(): string {
  return new Date().toISOString()
}

export function formatFileStamp(input: DateInput = Date.now()): string {
  const d = normalizeDate(input)
  if (!d) return '00000000_0000'
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: APP_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(d)
  const map: Record<string, string> = {}
  for (const p of parts) {
    if (p.type === 'year' || p.type === 'month' || p.type === 'day' || p.type === 'hour' || p.type === 'minute') {
      map[p.type] = p.value
    }
  }
  return `${map.year ?? '0000'}${map.month ?? '00'}${map.day ?? '00'}_${map.hour ?? '00'}${map.minute ?? '00'}`
}

export function formatFileStampWithSeconds(input: DateInput = Date.now()): string {
  const d = normalizeDate(input)
  if (!d) return '00000000_000000'
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: APP_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(d)
  const map: Record<string, string> = {}
  for (const p of parts) {
    if (
      p.type === 'year'
      || p.type === 'month'
      || p.type === 'day'
      || p.type === 'hour'
      || p.type === 'minute'
      || p.type === 'second'
    ) {
      map[p.type] = p.value
    }
  }
  return `${map.year ?? '0000'}${map.month ?? '00'}${map.day ?? '00'}_${map.hour ?? '00'}${map.minute ?? '00'}${map.second ?? '00'}`
}
