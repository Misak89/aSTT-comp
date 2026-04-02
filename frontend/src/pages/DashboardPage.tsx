/**
 * DashboardPage — přehled stavu aplikace v reálném čase.
 *
 * Zobrazuje:
 * - Server zdraví (RAM, uptime, start čas)
 * - Aktivní / nedávné benchmark joby
 * - Statistiky knihovny (videa, audio cache, titulky)
 * - Statistiky přepisů z LocalStorage
 * - Recharts: RAM usage sparkline (poslední vzorky)
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid } from 'recharts'
import { api } from '../api/client'
import { listTranscripts } from '../components/transcribe/useTranscribeStorage'
import type { AppProcessInfo, AppProcessSnapshot, BenchmarkJobStatus, LibraryItem, SpecstoryLiveStatus } from '../types'

interface HealthState {
  startedAt: string
  ramUsed: number
  ramTotal: number
  ramPct: number
  cpuPct: number
  utc: string
  loggerRepoUrl: string
  loggerScriptRelPath: string
  loggerLogsDirRelPath: string
  loggerLogFileRelPath: string
}

interface ResourceSample {
  ts: number
  t: string
  app_pct: number
  key_pct: number
  other_pct: number
  key_label: string
}

const DEFAULT_LOGGER_REPO_URL = 'https://github.com/Misak89/process-logger'
const DEFAULT_LOGGER_SCRIPT_REL_PATH = 'scripts/log_cmd.py'
const DEFAULT_LOGGER_LOGS_DIR_REL_PATH = 'logs'
const DEFAULT_LOGGER_LOG_FILE_REL_PATH = 'logs/cmd.jsonl'
const PROCESS_FAST_BASE_MS = 18_000
const PROCESS_SLOW_BASE_MS = 28_000
const PROCESS_LOG_BASE_MS = 28_000
const PROCESS_PHASE_OFFSET_MS = 1_733
const PROCESS_COLLISION_GUARD_MS = 250
const PROCESS_MULT_MIN = 0.33 // approx 1/3
const PROCESS_MULT_MAX = 33
const PROCESS_MULT_DEFAULT = 3
const PROCESS_MULT_STEP = 0.1
const RAM_GRAPH_INTERVAL_MS = 2_200
const CPU_GRAPH_INTERVAL_MS = 2_200
const CPU_GRAPH_PHASE_OFFSET_MS = 300
const GRAPH_MAX_SAMPLES = 180
const LS_PROC_FAST_MULT = 'dashboard.process.fast.mult'
const LS_PROC_SLOW_MULT = 'dashboard.process.slow.mult'
const LS_PROC_LOG_MULT = 'dashboard.process.log.mult'
const LS_PROC_PANEL_MODE = 'dashboard.process.panel.mode'
const LS_PROC_SCAN_LOG = 'dashboard.process.scan.log'
const LS_MONITOR_MASTER_ON = 'dashboard.monitor.master.on'
const LS_RAM_SHOW = 'dashboard.monitor.ram.show'
const LS_RAM_LOG = 'dashboard.monitor.ram.log'
const LS_CPU_SHOW = 'dashboard.monitor.cpu.show'
const LS_CPU_LOG = 'dashboard.monitor.cpu.log'
const LS_MONITOR_VIEW = 'dashboard.monitor.view'
const LS_MONITOR_LOG_COEF = 'dashboard.monitor.log.coef'
const PROC_SCAN_LOG_MAX = 400
const IMPORTANT_PROFILE_IDS: ReadonlySet<string> = new Set([
  'backend_uvicorn',
  'benchmark_worker',
  'tuning_worker',
  'load_generator',
  'specstory_loop',
  'specstory_learning',
  'cmd_logger_windows',
  'cmd_logger_unix',
  'whisper_server',
  'whisper_cli',
  'ffmpeg',
  'yt_dlp',
  'frontend_build',
])

type ProcessSortKey = 'state' | 'profile' | 'pid' | 'status' | 'cpu' | 'ram' | 'gpu' | 'running_for'
type SortDir = 'asc' | 'desc'
type ProcessSortRule = { key: ProcessSortKey; dir: SortDir }
type ProcessPanelMode = 'show_log' | 'hide_log' | 'hide_no_log'
type MonitorViewMode = 'full' | 'graphs_only' | 'numbers_only'
type LogCoef = 1 | 2 | 5 | 10

interface ProcessRow {
  rowKey: string
  isRunning: boolean
  isZombie: boolean
  profileId: string
  profileLabel: string
  pid: number | null
  processName: string
  status: string
  startedAtUtc: string | null
  runningForSeconds: number | null
  cpuPercent: number | null
  ramMb: number | null
  gpuMemoryMb: number | null
  cmdlinePreview: string
  isRoot: boolean
}

interface ProcessScanLogEntry {
  ts_utc: string
  phase: string
  running: number
  total: number
  zombies: number
  warnings: number
}

interface ChartTooltipPayloadItem {
  name?: string
  color?: string
  payload?: ResourceSample
}

interface UsageTooltipProps {
  active?: boolean
  label?: string
  payload?: ChartTooltipPayloadItem[]
}

function StatCard({ label, value, sub, warn }: { label: string; value: string | number; sub?: string; warn?: boolean }) {
  return (
    <div className="bg-white rounded border border-gray-200 p-4">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={`text-2xl font-bold ${warn ? 'text-red-600' : 'text-gray-900'}`}>{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  )
}

function UsageTooltip({ active, label, payload }: UsageTooltipProps) {
  if (!active || !payload || payload.length === 0) return null
  const sample = payload[0]?.payload
  if (!sample) return null

  const rows = [
    { name: 'App total (orange)', value: Number(sample.app_pct ?? 0), color: '#f59e0b' },
    { name: 'Top important process (red)', value: Number(sample.key_pct ?? 0), color: '#ef4444' },
    { name: 'Other system processes (blue)', value: Number(sample.other_pct ?? 0), color: '#3b82f6' },
  ].sort((a, b) => b.value - a.value) // highest top, lowest bottom

  return (
    <div className="bg-slate-900/95 border border-slate-700 rounded px-2 py-1 text-[12px] leading-5">
      <div className="text-slate-200 mb-1">time: {String(label ?? '')}</div>
      {rows.map(row => (
        <div key={row.name} style={{ color: row.color }}>
          {row.name}: {row.value.toFixed(2)}%
        </div>
      ))}
    </div>
  )
}

function fmtDt(iso: string) {
  try { return new Date(iso).toLocaleString('cs-CZ', { dateStyle: 'short', timeStyle: 'medium' }) }
  catch { return iso }
}

function fmtUptime(startedAt: string) {
  const sec = Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}min ${sec % 60}s`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}min`
}

function fmtPercent(v: number | null | undefined) {
  if (v == null) return 'n/a'
  return `${v.toFixed(1)}%`
}

function fmtMb(v: number | null | undefined) {
  if (v == null) return 'n/a'
  return `${v.toFixed(1)} MB`
}

function clampMult(v: number) {
  if (!Number.isFinite(v)) return PROCESS_MULT_DEFAULT
  const rounded = Math.round(v * 100) / 100
  return Math.min(PROCESS_MULT_MAX, Math.max(PROCESS_MULT_MIN, rounded))
}

function loadMultFromStorage(key: string, fallback: number) {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return fallback
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) return fallback
    return clampMult(parsed)
  } catch {
    return fallback
  }
}

function fmtMult(v: number) {
  const rounded = Math.round(v * 100) / 100
  if (Math.abs(rounded - Math.round(rounded)) < 1e-9) return String(Math.round(rounded))
  return rounded.toFixed(2).replace(/\.?0+$/, '')
}

function isSameMult(a: number, b: number) {
  return Math.abs(a - b) < 0.001
}

function loadPanelModeFromStorage(): ProcessPanelMode {
  try {
    const raw = localStorage.getItem(LS_PROC_PANEL_MODE)
    if (raw === 'show_log' || raw === 'hide_log' || raw === 'hide_no_log') return raw
  } catch {
    // ignore localStorage errors
  }
  return 'show_log'
}

function loadBoolFromStorage(key: string, fallback: boolean) {
  try {
    const raw = localStorage.getItem(key)
    if (raw == null) return fallback
    if (raw === '1' || raw.toLowerCase() === 'true') return true
    if (raw === '0' || raw.toLowerCase() === 'false') return false
    return fallback
  } catch {
    return fallback
  }
}

function loadMonitorViewMode(): MonitorViewMode {
  try {
    const raw = localStorage.getItem(LS_MONITOR_VIEW)
    if (raw === 'full' || raw === 'graphs_only' || raw === 'numbers_only') return raw
  } catch {
    // ignore storage errors
  }
  return 'full'
}

function loadLogCoef(): LogCoef {
  try {
    const raw = Number(localStorage.getItem(LS_MONITOR_LOG_COEF) ?? '')
    if (raw === 1 || raw === 2 || raw === 5 || raw === 10) return raw
  } catch {
    // ignore storage errors
  }
  return 1
}

function loadScanLogCountFromStorage() {
  try {
    const raw = localStorage.getItem(LS_PROC_SCAN_LOG)
    if (!raw) return 0
    const parsed = JSON.parse(raw) as ProcessScanLogEntry[]
    if (!Array.isArray(parsed)) return 0
    return parsed.length
  } catch {
    return 0
  }
}

function appendScanLogEntry(entry: ProcessScanLogEntry) {
  try {
    const raw = localStorage.getItem(LS_PROC_SCAN_LOG)
    const parsed = raw ? JSON.parse(raw) as ProcessScanLogEntry[] : []
    const next = Array.isArray(parsed) ? [...parsed, entry].slice(-PROC_SCAN_LOG_MAX) : [entry]
    localStorage.setItem(LS_PROC_SCAN_LOG, JSON.stringify(next))
    return next.length
  } catch {
    return 0
  }
}

function fmtNextIn(msEpoch: number | null, nowMs: number) {
  if (!msEpoch) return 'n/a'
  const sec = Math.max(0, Math.ceil((msEpoch - nowMs) / 1000))
  return `${sec}s`
}

function logPlotPercent(valuePct: number, coef: LogCoef) {
  const v = clampPercent(valuePct)
  const top = Math.log10(1 + 100 * coef)
  if (top <= 0) return 0
  return (Math.log10(1 + v * coef) / top) * 100
}

function invLogPlotPercent(plotPct: number, coef: LogCoef) {
  const y = clampPercent(plotPct)
  const top = Math.log10(1 + 100 * coef)
  if (top <= 0 || coef <= 0) return 0
  return clampPercent((10 ** ((y / 100) * top) - 1) / coef)
}

function percentToPlot(valuePct: number, scale: 'log' | 'linear', coef: LogCoef) {
  return scale === 'log' ? logPlotPercent(valuePct, coef) : clampPercent(valuePct)
}

function plotToPercent(value: number, scale: 'log' | 'linear', coef: LogCoef) {
  return scale === 'log' ? invLogPlotPercent(value, coef) : clampPercent(value)
}

function fmtAxisPercent(plotValue: number, scale: 'log' | 'linear', coef: LogCoef) {
  const v = plotToPercent(plotValue, scale, coef)
  if (v < 1) return `${v.toFixed(1)}%`
  if (v < 10) return `${v.toFixed(1)}%`
  return `${v.toFixed(0)}%`
}

function clampPercent(v: number) {
  if (!Number.isFinite(v)) return 0
  return Math.max(0, Math.min(100, v))
}

function fmtSpan(seconds: number) {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  if (m < 60) return `${m}m ${s}s`
  const h = Math.floor(m / 60)
  const mm = m % 60
  return `${h}h ${mm}m`
}

function pickTopImportantProcess(processes: AppProcessInfo[], metric: 'ram' | 'cpu') {
  const important = processes.filter((p) => IMPORTANT_PROFILE_IDS.has(String(p.profile_id || '')))
  const pool = important.length > 0 ? important : processes
  let best: AppProcessInfo | null = null
  let bestValue = -1
  for (const p of pool) {
    const value = metric === 'ram' ? Number(p.ram_mb ?? -1) : Number(p.cpu_percent ?? -1)
    if (value > bestValue) {
      bestValue = value
      best = p
    }
  }
  return best
}

function buildUsageSample(snapshot: AppProcessSnapshot | null, health: HealthState | null, metric: 'ram' | 'cpu'): ResourceSample | null {
  if (!snapshot || !health) return null
  const running = (snapshot.processes ?? []).filter((p) => typeof p.pid === 'number' && p.pid > 0)
  const now = Date.now()
  const t = new Date(now).toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  if (running.length === 0) {
    return {
      ts: now,
      t,
      app_pct: 0,
      key_pct: 0,
      other_pct: 0,
      key_label: 'n/a',
    }
  }

  const top = pickTopImportantProcess(running, metric)
  if (metric === 'ram') {
    const appRamMb = running.reduce((sum, p) => sum + Number(p.ram_mb ?? 0), 0)
    const appPct = health.ramTotal > 0 ? (appRamMb / health.ramTotal) * 100 : 0
    const keyRamMb = Number(top?.ram_mb ?? 0)
    const keyPct = health.ramTotal > 0 ? (keyRamMb / health.ramTotal) * 100 : 0
    const otherPct = Math.max(0, Number(health.ramPct ?? 0) - appPct)
    return {
      ts: now,
      t,
      app_pct: clampPercent(appPct),
      key_pct: clampPercent(keyPct),
      other_pct: clampPercent(otherPct),
      key_label: top ? `${top.profile_label || top.name || 'process'} #${top.pid}` : 'n/a',
    }
  }

  const appCpuPct = running.reduce((sum, p) => sum + Number(p.cpu_percent ?? 0), 0)
  const keyCpuPct = Number(top?.cpu_percent ?? 0)
  const otherCpuPct = Math.max(0, Number(health.cpuPct ?? 0) - appCpuPct)
  return {
    ts: now,
    t,
    app_pct: clampPercent(appCpuPct),
    key_pct: clampPercent(keyCpuPct),
    other_pct: clampPercent(otherCpuPct),
    key_label: top ? `${top.profile_label || top.name || 'process'} #${top.pid}` : 'n/a',
  }
}

function buildProcessRows(snapshot: AppProcessSnapshot | null): ProcessRow[] {
  if (!snapshot) return []
  const runningRows: ProcessRow[] = (snapshot.processes ?? []).map((p: AppProcessInfo) => ({
    rowKey: `running:${p.pid}`,
    isRunning: true,
    isZombie: Boolean(p.zombie_candidate),
    profileId: p.profile_id || 'other_app_process',
    profileLabel: p.profile_label || 'Other aSTT-comp process',
    pid: p.pid,
    processName: p.name || 'n/a',
    status: p.status || 'n/a',
    startedAtUtc: p.started_at_utc,
    runningForSeconds: p.running_for_seconds,
    cpuPercent: p.cpu_percent ?? null,
    ramMb: p.ram_mb ?? null,
    gpuMemoryMb: p.gpu_memory_mb ?? null,
    cmdlinePreview: p.cmdline_preview || '',
    isRoot: Boolean(p.is_root),
  }))

  const runningProfileIds = new Set(runningRows.map(r => r.profileId))
  const expectedProfiles = (snapshot.profiles ?? []).filter(p => p.expected)
  const notRunningRows: ProcessRow[] = expectedProfiles
    .filter(profile => !runningProfileIds.has(profile.id))
    .map(profile => ({
      rowKey: `expected:${profile.id}`,
      isRunning: false,
      isZombie: false,
      profileId: profile.id,
      profileLabel: profile.label,
      pid: null,
      processName: '-',
      status: 'not_running',
      startedAtUtc: null,
      runningForSeconds: null,
      cpuPercent: null,
      ramMb: null,
      gpuMemoryMb: null,
      cmdlinePreview: '',
      isRoot: false,
    }))

  return [...runningRows, ...notRunningRows]
}

function compareProcessRows(a: ProcessRow, b: ProcessRow, rules: ProcessSortRule[]) {
  for (const rule of rules) {
    const dirMul = rule.dir === 'asc' ? 1 : -1
    let cmp = 0
    switch (rule.key) {
      case 'state': {
        const av = a.isRunning ? 0 : 1
        const bv = b.isRunning ? 0 : 1
        cmp = av - bv
        break
      }
      case 'profile':
        cmp = a.profileLabel.localeCompare(b.profileLabel)
        break
      case 'pid':
        cmp = (a.pid ?? Number.MAX_SAFE_INTEGER) - (b.pid ?? Number.MAX_SAFE_INTEGER)
        break
      case 'status':
        cmp = a.status.localeCompare(b.status)
        break
      case 'cpu':
        cmp = (a.cpuPercent ?? -1) - (b.cpuPercent ?? -1)
        break
      case 'ram':
        cmp = (a.ramMb ?? -1) - (b.ramMb ?? -1)
        break
      case 'gpu':
        cmp = (a.gpuMemoryMb ?? -1) - (b.gpuMemoryMb ?? -1)
        break
      case 'running_for':
        cmp = (a.runningForSeconds ?? -1) - (b.runningForSeconds ?? -1)
        break
      default:
        cmp = 0
    }
    if (cmp !== 0) return cmp * dirMul
  }
  return a.rowKey.localeCompare(b.rowKey)
}

function mergeProcessSnapshots(prev: AppProcessSnapshot | null, incoming: AppProcessSnapshot): AppProcessSnapshot {
  if (!prev) return incoming

  const byPid = new Map<number, AppProcessInfo>()
  for (const p of prev.processes ?? []) {
    if (typeof p.pid === 'number' && p.pid > 0) byPid.set(p.pid, p)
  }
  for (const p of incoming.processes ?? []) {
    if (typeof p.pid === 'number' && p.pid > 0) byPid.set(p.pid, p)
  }

  const mergedProcesses = Array.from(byPid.values())
  mergedProcesses.sort((a, b) => (a.pid ?? 0) - (b.pid ?? 0))

  const incomingHasGpu = Boolean(incoming.gpu?.provider) || incoming.gpu?.total_util_percent != null

  return {
    ...prev,
    ...incoming,
    processes: mergedProcesses,
    count: mergedProcesses.length,
    profiles: (incoming.profiles && incoming.profiles.length > 0) ? incoming.profiles : prev.profiles,
    warnings: incoming.warnings ?? prev.warnings,
    gpu: incomingHasGpu ? incoming.gpu : (incoming.gpu ?? prev.gpu),
  }
}

export function DashboardPage() {
  const [health, setHealth] = useState<HealthState | null>(null)
  const [ramHistory, setRamHistory] = useState<ResourceSample[]>([])
  const [cpuHistory, setCpuHistory] = useState<ResourceSample[]>([])
  const [ramScale, setRamScale] = useState<'log' | 'linear'>('log')
  const [cpuScale, setCpuScale] = useState<'log' | 'linear'>('log')
  const [monitorMasterOn, setMonitorMasterOn] = useState<boolean>(() => loadBoolFromStorage(LS_MONITOR_MASTER_ON, true))
  const [ramGraphVisible, setRamGraphVisible] = useState<boolean>(() => loadBoolFromStorage(LS_RAM_SHOW, true))
  const [ramLogEnabled, setRamLogEnabled] = useState<boolean>(() => loadBoolFromStorage(LS_RAM_LOG, true))
  const [cpuGraphVisible, setCpuGraphVisible] = useState<boolean>(() => loadBoolFromStorage(LS_CPU_SHOW, true))
  const [cpuLogEnabled, setCpuLogEnabled] = useState<boolean>(() => loadBoolFromStorage(LS_CPU_LOG, true))
  const [monitorViewMode, setMonitorViewMode] = useState<MonitorViewMode>(() => loadMonitorViewMode())
  const [monitorLogCoef, setMonitorLogCoef] = useState<LogCoef>(() => loadLogCoef())
  const [jobs, setJobs] = useState<BenchmarkJobStatus[]>([])
  const [library, setLibrary] = useState<LibraryItem[]>([])
  const [specstory, setSpecstory] = useState<SpecstoryLiveStatus | null>(null)
  const [processes, setProcesses] = useState<AppProcessSnapshot | null>(null)
  const [uptime, setUptime] = useState('')
  const [loggerOpenStatus, setLoggerOpenStatus] = useState<string>('')
  const [processPanelMode, setProcessPanelMode] = useState<ProcessPanelMode>(() => loadPanelModeFromStorage())
  const [processFastMult, setProcessFastMult] = useState<number>(() => loadMultFromStorage(LS_PROC_FAST_MULT, PROCESS_MULT_DEFAULT))
  const [processSlowMult, setProcessSlowMult] = useState<number>(() => loadMultFromStorage(LS_PROC_SLOW_MULT, PROCESS_MULT_DEFAULT))
  const [processLogMult, setProcessLogMult] = useState<number>(() => loadMultFromStorage(LS_PROC_LOG_MULT, PROCESS_MULT_DEFAULT))
  const [processPhaseLabel, setProcessPhaseLabel] = useState<string>('n/a')
  const [nextFastScanAtMs, setNextFastScanAtMs] = useState<number | null>(null)
  const [nextSlowScanAtMs, setNextSlowScanAtMs] = useState<number | null>(null)
  const [nextLogAtMs, setNextLogAtMs] = useState<number | null>(null)
  const [processScanLogCount, setProcessScanLogCount] = useState<number>(() => loadScanLogCountFromStorage())
  const [manualFullScanBusy, setManualFullScanBusy] = useState(false)
  const [pidCleanupBusy, setPidCleanupBusy] = useState(false)
  const [pidCleanupMsg, setPidCleanupMsg] = useState('')
  const [clockNowMs, setClockNowMs] = useState<number>(Date.now())
  const [showAllStoppedProcesses, setShowAllStoppedProcesses] = useState(false)
  const [processSort, setProcessSort] = useState<ProcessSortRule[]>([
    { key: 'state', dir: 'asc' },
    { key: 'profile', dir: 'asc' },
    { key: 'pid', dir: 'asc' },
  ])
  const healthRef = useRef<HealthState | null>(null)
  const processesRef = useRef<AppProcessSnapshot | null>(null)
  const lastFastRunAtRef = useRef<number>(0)
  const lastSlowRunAtRef = useRef<number>(0)
  const lastLogWriteAtRef = useRef<number>(0)

  // Poll health every 5s
  useEffect(() => {
    const fetchHealth = () => {
      fetch('/api/health').then(r => r.json()).then((d: Record<string, unknown>) => {
        const logger = typeof d.logger === 'object' && d.logger != null
          ? d.logger as Record<string, unknown>
          : {}
        const nextHealth: HealthState = {
          // Keep the last valid cpu_pct to avoid zero spikes when a sample fails.
          // This stabilizes "other system" CPU series in charts.
          cpuPct: Number.isFinite(Number(d.cpu_percent))
            ? Number(d.cpu_percent)
            : (healthRef.current?.cpuPct ?? 0),
          startedAt: (d.started_at as string) ?? '',
          ramUsed: (d.ram_used_mb as number) ?? 0,
          ramTotal: (d.ram_total_mb as number) ?? 0,
          ramPct: (d.ram_percent as number) ?? 0,
          utc: (d.utc as string) ?? '',
          loggerRepoUrl: (logger.repo_url as string) ?? DEFAULT_LOGGER_REPO_URL,
          loggerScriptRelPath: (logger.script_rel_path as string) ?? DEFAULT_LOGGER_SCRIPT_REL_PATH,
          loggerLogsDirRelPath: (logger.logs_dir_rel_path as string) ?? DEFAULT_LOGGER_LOGS_DIR_REL_PATH,
          loggerLogFileRelPath: (logger.log_file_rel_path as string) ?? DEFAULT_LOGGER_LOG_FILE_REL_PATH,
        }
        healthRef.current = nextHealth
        setHealth(nextHealth)
      }).catch(() => {})
      api.health.specstory().then(setSpecstory).catch(() => {})
    }
    fetchHealth()
    const t = setInterval(fetchHealth, 5_000)
    return () => clearInterval(t)
  }, [])

  // Process scan cadence (phase-shifted):
  // fast = lightweight refresh, slow = heavier refresh with marker/GPU.
  useEffect(() => {
    const shouldScan = processPanelMode !== 'hide_no_log'
    const shouldLog = processPanelMode !== 'hide_no_log'

    if (!shouldScan) {
      setProcessPhaseLabel('paused')
      setNextFastScanAtMs(null)
      setNextSlowScanAtMs(null)
      setNextLogAtMs(null)
      return
    }

    const fastEveryMs = PROCESS_FAST_BASE_MS * clampMult(processFastMult)
    const slowEveryMs = PROCESS_SLOW_BASE_MS * clampMult(processSlowMult)
    const logEveryMs = PROCESS_LOG_BASE_MS * clampMult(processLogMult)
    let cancelled = false
    let fastTimer: number | null = null
    let slowTimer: number | null = null

    const scheduleFast = (delayMs: number) => {
      const nextAt = Date.now() + delayMs
      setNextFastScanAtMs(nextAt)
      fastTimer = window.setTimeout(runFast, delayMs)
    }

    const scheduleSlow = (delayMs: number) => {
      const nextAt = Date.now() + delayMs
      setNextSlowScanAtMs(nextAt)
      slowTimer = window.setTimeout(runSlow, delayMs)
    }

    const maybeWriteScanLog = (snapshot: AppProcessSnapshot, phase: string) => {
      if (!shouldLog) return
      const now = Date.now()
      if (lastLogWriteAtRef.current > 0 && (now - lastLogWriteAtRef.current) < logEveryMs) {
        setNextLogAtMs(lastLogWriteAtRef.current + logEveryMs)
        return
      }
      lastLogWriteAtRef.current = now
      setNextLogAtMs(now + logEveryMs)
      const running = (snapshot.processes ?? []).length
      const total = Math.max(running, (snapshot.profiles ?? []).filter(p => p.expected).length)
      const count = appendScanLogEntry({
        ts_utc: new Date(now).toISOString(),
        phase,
        running,
        total,
        zombies: snapshot.zombie_count ?? 0,
        warnings: (snapshot.warnings ?? []).length,
      })
      setProcessScanLogCount(count)
    }

    const runFast = async () => {
      if (cancelled) return
      lastFastRunAtRef.current = Date.now()
      setProcessPhaseLabel('fast')
      try {
        const snapshot = await api.health.processes('fast')
        if (!cancelled) {
          setProcessPhaseLabel(snapshot.scan_phase ?? 'fast')
          setProcesses(prev => mergeProcessSnapshots(prev, snapshot))
          maybeWriteScanLog(snapshot, snapshot.scan_phase ?? 'fast')
        }
      } catch {
        // ignore polling errors
      } finally {
        if (!cancelled) scheduleFast(fastEveryMs)
      }
    }

    const runSlow = async () => {
      if (cancelled) return
      const now = Date.now()
      const nearFast = Math.abs(now - lastFastRunAtRef.current) < PROCESS_COLLISION_GUARD_MS
      if (nearFast) {
        scheduleSlow(PROCESS_PHASE_OFFSET_MS)
        return
      }
      lastSlowRunAtRef.current = now
      setProcessPhaseLabel('slow')
      try {
        const snapshot = await api.health.processes('slow')
        if (!cancelled) {
          setProcessPhaseLabel(snapshot.scan_phase ?? 'slow')
          setProcesses(prev => mergeProcessSnapshots(prev, snapshot))
          maybeWriteScanLog(snapshot, snapshot.scan_phase ?? 'slow')
        }
      } catch {
        // ignore polling errors
      } finally {
        if (!cancelled) scheduleSlow(slowEveryMs)
      }
    }

    // Initial run: fast now, slow phase-shifted.
    scheduleFast(0)
    scheduleSlow(PROCESS_PHASE_OFFSET_MS)
    if (shouldLog) {
      setNextLogAtMs(lastLogWriteAtRef.current > 0 ? lastLogWriteAtRef.current + logEveryMs : Date.now() + logEveryMs)
    }

    return () => {
      cancelled = true
      if (fastTimer != null) window.clearTimeout(fastTimer)
      if (slowTimer != null) window.clearTimeout(slowTimer)
    }
  }, [processFastMult, processSlowMult, processLogMult, processPanelMode])

  // Persist process multipliers
  useEffect(() => {
    try { localStorage.setItem(LS_PROC_FAST_MULT, String(clampMult(processFastMult))) } catch {}
  }, [processFastMult])
  useEffect(() => {
    try { localStorage.setItem(LS_PROC_SLOW_MULT, String(clampMult(processSlowMult))) } catch {}
  }, [processSlowMult])
  useEffect(() => {
    try { localStorage.setItem(LS_PROC_LOG_MULT, String(clampMult(processLogMult))) } catch {}
  }, [processLogMult])
  useEffect(() => {
    try { localStorage.setItem(LS_PROC_PANEL_MODE, processPanelMode) } catch {}
  }, [processPanelMode])
  useEffect(() => {
    try { localStorage.setItem(LS_MONITOR_MASTER_ON, monitorMasterOn ? '1' : '0') } catch {}
  }, [monitorMasterOn])
  useEffect(() => {
    try { localStorage.setItem(LS_RAM_SHOW, ramGraphVisible ? '1' : '0') } catch {}
  }, [ramGraphVisible])
  useEffect(() => {
    try { localStorage.setItem(LS_RAM_LOG, ramLogEnabled ? '1' : '0') } catch {}
  }, [ramLogEnabled])
  useEffect(() => {
    try { localStorage.setItem(LS_CPU_SHOW, cpuGraphVisible ? '1' : '0') } catch {}
  }, [cpuGraphVisible])
  useEffect(() => {
    try { localStorage.setItem(LS_CPU_LOG, cpuLogEnabled ? '1' : '0') } catch {}
  }, [cpuLogEnabled])
  useEffect(() => {
    try { localStorage.setItem(LS_MONITOR_VIEW, monitorViewMode) } catch {}
  }, [monitorViewMode])
  useEffect(() => {
    try { localStorage.setItem(LS_MONITOR_LOG_COEF, String(monitorLogCoef)) } catch {}
  }, [monitorLogCoef])

  useEffect(() => {
    healthRef.current = health
  }, [health])

  useEffect(() => {
    processesRef.current = processes
  }, [processes])

  useEffect(() => {
    const sampleRam = () => {
      if (!monitorMasterOn || !ramLogEnabled) return
      const sample = buildUsageSample(processesRef.current, healthRef.current, 'ram')
      if (!sample) return
      setRamHistory(prev => [...prev.slice(-(GRAPH_MAX_SAMPLES - 1)), sample])
    }
    if (monitorMasterOn && ramLogEnabled) sampleRam()
    const t = window.setInterval(sampleRam, RAM_GRAPH_INTERVAL_MS)
    return () => window.clearInterval(t)
  }, [monitorMasterOn, ramLogEnabled])

  useEffect(() => {
    const sampleCpu = () => {
      if (!monitorMasterOn || !cpuLogEnabled) return
      const sample = buildUsageSample(processesRef.current, healthRef.current, 'cpu')
      if (!sample) return
      setCpuHistory(prev => [...prev.slice(-(GRAPH_MAX_SAMPLES - 1)), sample])
    }
    let intervalId: number | null = null
    const timeoutId = window.setTimeout(() => {
      if (monitorMasterOn && cpuLogEnabled) sampleCpu()
      intervalId = window.setInterval(sampleCpu, CPU_GRAPH_INTERVAL_MS)
    }, CPU_GRAPH_PHASE_OFFSET_MS)
    return () => {
      window.clearTimeout(timeoutId)
      if (intervalId != null) window.clearInterval(intervalId)
    }
  }, [monitorMasterOn, cpuLogEnabled])

  // 1s ticker for "next in Xs"
  useEffect(() => {
    const t = window.setInterval(() => setClockNowMs(Date.now()), 1_000)
    return () => window.clearInterval(t)
  }, [])

  // Uptime ticker every second
  useEffect(() => {
    if (!health?.startedAt) return
    const t = setInterval(() => setUptime(fmtUptime(health.startedAt)), 1_000)
    setUptime(fmtUptime(health.startedAt))
    return () => clearInterval(t)
  }, [health?.startedAt])

  // Jobs + library (refresh every 10s)
  useEffect(() => {
    const load = () => {
      api.benchmark.listJobs().then(r => setJobs(r.jobs ?? [])).catch(() => {})
      api.library.list().then(setLibrary).catch(() => {})
    }
    load()
    const t = setInterval(load, 10_000)
    return () => clearInterval(t)
  }, [])

  const transcripts = listTranscripts()
  const activeJobs = jobs.filter(j => j.status === 'running' || j.status === 'pending')
  const recentJobs = jobs.slice(0, 8)
  const audioCached = library.filter(i => i.audio_cached).length
  const withSubtitles = library.filter(i => i.subtitles_local).length

  const statusColor = (s: string) => {
    if (s === 'completed') return 'text-green-600'
    if (s === 'failed' || s === 'cancelled') return 'text-red-500'
    if (s === 'running') return 'text-blue-500'
    return 'text-gray-400'
  }

  const specstoryTone = (status: SpecstoryLiveStatus['status'] | undefined) => {
    if (status === 'ok') return 'border-emerald-200 bg-emerald-50 text-emerald-800'
    if (status === 'stale') return 'border-amber-200 bg-amber-50 text-amber-800'
    if (status === 'missing') return 'border-slate-200 bg-slate-50 text-slate-700'
    return 'border-red-200 bg-red-50 text-red-700'
  }

  const specstoryLabel = (status: SpecstoryLiveStatus['status'] | undefined) => {
    if (status === 'ok') return 'Running'
    if (status === 'stale') return 'Stale'
    if (status === 'missing') return 'Not started'
    if (status === 'error') return 'Error'
    return 'Unknown'
  }

  const fmtRunningFor = (seconds: number | null | undefined) => {
    if (seconds == null) return 'n/a'
    if (seconds < 60) return `${seconds}s`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  }

  const openLoggerLogsDir = async () => {
    setLoggerOpenStatus('')
    try {
      await api.openDir.loggerLogs()
      setLoggerOpenStatus('Opened in file manager')
    } catch {
      setLoggerOpenStatus('Open failed')
    }
  }

  const updateFastMult = (v: number) => setProcessFastMult(clampMult(v))
  const updateSlowMult = (v: number) => setProcessSlowMult(clampMult(v))
  const updateLogMult = (v: number) => setProcessLogMult(clampMult(v))
  const setAllMonitoringOff = () => {
    setMonitorMasterOn(false)
  }
  const enableMonitoring = () => {
    setMonitorMasterOn(true)
  }
  const applyGraphsNoLoggingPreset = () => {
    setMonitorMasterOn(true)
    setMonitorViewMode('graphs_only')
    setRamGraphVisible(true)
    setCpuGraphVisible(true)
    setRamLogEnabled(false)
    setCpuLogEnabled(false)
  }

  const runManualFullScan = async () => {
    if (manualFullScanBusy) return
    setManualFullScanBusy(true)
    setProcessPhaseLabel('manual-full')
    try {
      const snapshot = await api.health.processes('full')
      setProcessPhaseLabel(snapshot.scan_phase ?? 'full')
      setProcesses(prev => mergeProcessSnapshots(prev, snapshot))
    } catch {
      setProcessPhaseLabel('manual-full-error')
    } finally {
      setManualFullScanBusy(false)
    }
  }

  const cleanupStalePids = async () => {
    if (pidCleanupBusy) return
    setPidCleanupBusy(true)
    setPidCleanupMsg('')
    try {
      const result = await api.health.cleanupStalePids()
      setPidCleanupMsg(`removed: ${result.removed_count}, kept: ${result.kept_count}, errors: ${result.error_count}`)
      const snapshot = await api.health.processes('full')
      setProcessPhaseLabel(snapshot.scan_phase ?? 'full')
      setProcesses(prev => mergeProcessSnapshots(prev, snapshot))
    } catch {
      setPidCleanupMsg('cleanup failed')
    } finally {
      setPidCleanupBusy(false)
    }
  }

  const onProcessSortClick = (key: ProcessSortKey, multi: boolean) => {
    setProcessSort(prev => {
      const existing = prev.find(r => r.key === key)
      const nextDir: SortDir = existing == null ? 'asc' : (existing.dir === 'asc' ? 'desc' : 'asc')
      if (!multi) {
        return [{ key, dir: nextDir }]
      }
      const without = prev.filter(r => r.key !== key)
      return [{ key, dir: nextDir }, ...without]
    })
  }

  const processRows = useMemo(() => buildProcessRows(processes), [processes])
  const sortedProcessRows = useMemo(() => {
    const rows = [...processRows]
    rows.sort((a, b) => compareProcessRows(a, b, processSort))
    return rows
  }, [processRows, processSort])
  const runningProcessRows = sortedProcessRows.filter(r => r.isRunning)
  const stoppedProcessRows = sortedProcessRows.filter(r => !r.isRunning)
  const visibleStoppedRows = showAllStoppedProcesses ? stoppedProcessRows : stoppedProcessRows.slice(0, 1)
  const visibleProcessRows = [...runningProcessRows, ...visibleStoppedRows]
  const hasStalePidWarning = Boolean((processes?.warnings ?? []).some(w => w.toLowerCase().includes('stale pid file')))
  const currentRamSample = useMemo(() => buildUsageSample(processes, health, 'ram'), [processes, health])
  const currentCpuSample = useMemo(() => buildUsageSample(processes, health, 'cpu'), [processes, health])
  const ramSpanSeconds = useMemo(() => {
    if (ramHistory.length < 2) return 0
    return Math.max(0, Math.round((ramHistory[ramHistory.length - 1].ts - ramHistory[0].ts) / 1000))
  }, [ramHistory])
  const cpuSpanSeconds = useMemo(() => {
    if (cpuHistory.length < 2) return 0
    return Math.max(0, Math.round((cpuHistory[cpuHistory.length - 1].ts - cpuHistory[0].ts) / 1000))
  }, [cpuHistory])
  const ramChartData = useMemo(
    () => ramHistory.map(s => ({
      ...s,
      app_plot: percentToPlot(s.app_pct, ramScale, monitorLogCoef),
      key_plot: percentToPlot(s.key_pct, ramScale, monitorLogCoef),
      other_plot: percentToPlot(s.other_pct, ramScale, monitorLogCoef),
    })),
    [ramHistory, ramScale, monitorLogCoef]
  )
  const cpuChartData = useMemo(
    () => cpuHistory.map(s => ({
      ...s,
      app_plot: percentToPlot(s.app_pct, cpuScale, monitorLogCoef),
      key_plot: percentToPlot(s.key_pct, cpuScale, monitorLogCoef),
      other_plot: percentToPlot(s.other_pct, cpuScale, monitorLogCoef),
    })),
    [cpuHistory, cpuScale, monitorLogCoef]
  )
  const linearAxisTicks = [0, 25, 50, 75, 100]
  const logAxisPercentTicks = [0, 0.5, 1, 2, 5, 10, 20, 50, 100]
  const ramAxisTicks = ramScale === 'log'
    ? logAxisPercentTicks.map(v => percentToPlot(v, 'log', monitorLogCoef))
    : linearAxisTicks
  const cpuAxisTicks = cpuScale === 'log'
    ? logAxisPercentTicks.map(v => percentToPlot(v, 'log', monitorLogCoef))
    : linearAxisTicks
  const latestRamKeyLabel = ramHistory.length > 0 ? ramHistory[ramHistory.length - 1].key_label : 'n/a'
  const latestCpuKeyLabel = cpuHistory.length > 0 ? cpuHistory[cpuHistory.length - 1].key_label : 'n/a'
  const ramKeyLabelForUi = latestRamKeyLabel !== 'n/a' ? latestRamKeyLabel : (currentRamSample?.key_label ?? 'n/a')
  const cpuKeyLabelForUi = latestCpuKeyLabel !== 'n/a' ? latestCpuKeyLabel : (currentCpuSample?.key_label ?? 'n/a')
  const wantGraphs = monitorViewMode !== 'numbers_only'
  const wantNumbers = monitorViewMode !== 'graphs_only'
  const showRamGraphCard = monitorMasterOn && wantGraphs && ramGraphVisible
  const showCpuGraphCard = monitorMasterOn && wantGraphs && cpuGraphVisible
  const showNumbersCard = monitorMasterOn && wantNumbers

  const processSortBadge = (key: ProcessSortKey) => {
    const idx = processSort.findIndex(r => r.key === key)
    if (idx < 0) return ''
    const rule = processSort[idx]
    return `${idx + 1}${rule.dir === 'asc' ? '↑' : '↓'}`
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Dashboard</h1>
        {health && (
          <div className="text-xs text-gray-400">
            Backend: <span className="font-mono">{fmtDt(health.startedAt)}</span>
            {' · '}uptime: <span className="font-mono text-gray-600">{uptime}</span>
          </div>
        )}
      </div>

      <div className="bg-white rounded border border-gray-200 p-3 text-xs text-gray-600 space-y-1">
        <div className="font-semibold text-gray-700">Process logger</div>
        <div>
          repo:{' '}
          <a
            href={health?.loggerRepoUrl ?? DEFAULT_LOGGER_REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="text-blue-600 hover:text-blue-700 hover:underline"
          >
            {health?.loggerRepoUrl ?? DEFAULT_LOGGER_REPO_URL}
          </a>
        </div>
        <div className="flex items-center gap-2">
          <span>logs dir:</span>
          <button
            type="button"
            onClick={openLoggerLogsDir}
            className="font-mono text-blue-600 hover:text-blue-700 hover:underline"
            title="Open logs directory in file manager"
          >
            {health?.loggerLogsDirRelPath ?? DEFAULT_LOGGER_LOGS_DIR_REL_PATH}
          </button>
          {loggerOpenStatus && <span className="text-gray-400">({loggerOpenStatus})</span>}
        </div>
        <div className="font-mono">
          log file: {health?.loggerLogFileRelPath ?? DEFAULT_LOGGER_LOG_FILE_REL_PATH}
        </div>
        <div className="font-mono whitespace-pre-wrap">
          {(health?.loggerScriptRelPath ?? DEFAULT_LOGGER_SCRIPT_REL_PATH)}
          {'\n'}v0.22 TODO: režim spuštění:
          {'\n'}--run hidden (na pozadí)
          {'\n'}--run online (v konzoli, živý výpis)
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="RAM využití"
          value={health ? `${health.ramPct.toFixed(0)}%` : '–'}
          sub={health ? `${health.ramUsed.toLocaleString()} / ${health.ramTotal.toLocaleString()} MB` : undefined}
          warn={health ? health.ramPct > 85 : false}
        />
        <StatCard label="Videa v knihovně" value={library.length} sub={`${audioCached} s audio cache`} />
        <StatCard label="S titulky" value={withSubtitles} sub={`z ${library.length} videí`} />
        <StatCard label="Uložené přepisy" value={transcripts.length} sub="v LocalStorage" />
      </div>

      <div className={`rounded border p-4 ${specstoryTone(specstory?.status)}`}>
        <div className="text-xs font-semibold uppercase tracking-wide">SpecStory Live Loop (bez commitu)</div>
        <div className="text-sm font-semibold mt-1">{specstoryLabel(specstory?.status)}</div>
        {specstory && (
          <div className="text-xs mt-1 space-y-0.5">
            <div>
              last_run: {specstory.last_run_utc ? fmtDt(specstory.last_run_utc) : 'n/a'}
              {specstory.interval_seconds ? ` · interval: ${specstory.interval_seconds}s` : ''}
              {specstory.history_files_count != null ? ` · files: ${specstory.history_files_count}` : ''}
            </div>
            <div>
              runs: {specstory.runs_total ?? 0}
              {specstory.runs_failed != null ? ` · failed: ${specstory.runs_failed}` : ''}
              {specstory.loop_pid ? ` · pid: ${specstory.loop_pid}` : ''}
            </div>
            {specstory.last_error && <div>error: {specstory.last_error}</div>}
          </div>
        )}
      </div>

      <div className="bg-white rounded border border-gray-200 p-4">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          App procesy (phase scan)
        </div>
        <div className="text-[11px] text-gray-400 mb-2">
          mode: {processPanelMode}
          {' · '}log: {(PROCESS_LOG_BASE_MS / 1000).toFixed(1)}s ×{fmtMult(processLogMult)} (next {fmtNextIn(nextLogAtMs, clockNowMs)})
          {' · '}log entries: {processScanLogCount}
        </div>
        <div className="text-[11px] text-gray-400 mb-2">
          phase: {processPhaseLabel}
          {' · '}fast: {(PROCESS_FAST_BASE_MS / 1000).toFixed(1)}s ×{fmtMult(processFastMult)} (next {fmtNextIn(nextFastScanAtMs, clockNowMs)})
          {' · '}slow: {(PROCESS_SLOW_BASE_MS / 1000).toFixed(1)}s ×{fmtMult(processSlowMult)} (next {fmtNextIn(nextSlowScanAtMs, clockNowMs)})
          {' · '}offset: {(PROCESS_PHASE_OFFSET_MS / 1000).toFixed(3)}s
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs mb-2">
          <button
            type="button"
            onClick={runManualFullScan}
            disabled={manualFullScanBusy || processPanelMode === 'hide_no_log'}
            className={`px-2 py-1 border rounded ${
              manualFullScanBusy || processPanelMode === 'hide_no_log'
                ? 'border-gray-300 text-gray-400'
                : 'border-red-500 text-red-700 hover:bg-red-50'
            }`}
            title="Run full process scan immediately"
          >
            {manualFullScanBusy ? 'Full scan…' : 'Full scan now'}
          </button>
          {hasStalePidWarning && (
            <button
              type="button"
              onClick={cleanupStalePids}
              disabled={pidCleanupBusy}
              className={`px-2 py-1 border rounded ${
                pidCleanupBusy ? 'border-gray-300 text-gray-400' : 'border-amber-500 text-amber-700 hover:bg-amber-50'
              }`}
              title="Remove stale PID files that point to missing processes"
            >
              {pidCleanupBusy ? 'Cleaning stale PID…' : 'Clean stale PID files'}
            </button>
          )}
          {pidCleanupMsg && <span className="text-[11px] text-gray-500">{pidCleanupMsg}</span>}
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs mb-2">
          <div className="flex items-center gap-1">
            <span className="text-gray-500">mode</span>
            <button
              type="button"
              className={`px-2 border rounded ${processPanelMode === 'show_log' ? 'border-blue-500 text-blue-600' : 'border-gray-300 text-gray-500'}`}
              onClick={() => setProcessPanelMode('show_log')}
            >
              show + log
            </button>
            <button
              type="button"
              className={`px-2 border rounded ${processPanelMode === 'hide_log' ? 'border-blue-500 text-blue-600' : 'border-gray-300 text-gray-500'}`}
              onClick={() => setProcessPanelMode('hide_log')}
            >
              hide + log
            </button>
            <button
              type="button"
              className={`px-2 border rounded ${processPanelMode === 'hide_no_log' ? 'border-blue-500 text-blue-600' : 'border-gray-300 text-gray-500'}`}
              onClick={() => setProcessPanelMode('hide_no_log')}
            >
              hide + no-log
            </button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs mb-2">
          <div className="flex items-center gap-1">
            <span className="text-gray-500">fast mult</span>
            <button type="button" className="px-1 border border-gray-300 rounded" onClick={() => updateFastMult(processFastMult - PROCESS_MULT_STEP)}>-</button>
            <span className="font-mono min-w-12 text-center">{fmtMult(processFastMult)}x</span>
            <button type="button" className="px-1 border border-gray-300 rounded" onClick={() => updateFastMult(processFastMult + PROCESS_MULT_STEP)}>+</button>
            <input
              type="number"
              min={PROCESS_MULT_MIN}
              max={PROCESS_MULT_MAX}
              step={PROCESS_MULT_STEP}
              value={processFastMult}
              onChange={e => updateFastMult(Number(e.target.value))}
              className="w-20 border border-gray-300 rounded px-1 py-0.5 font-mono text-gray-700"
            />
            {[0.33, 0.5, 1, 2, 3, 10, 20, 33].map(m => (
              <button
                key={`fast-${m}`}
                type="button"
                className={`px-1 border rounded ${isSameMult(processFastMult, m) ? 'border-blue-500 text-blue-600' : 'border-gray-300 text-gray-500'}`}
                onClick={() => updateFastMult(m)}
              >
                {fmtMult(m)}x
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1">
            <span className="text-gray-500">slow mult</span>
            <button type="button" className="px-1 border border-gray-300 rounded" onClick={() => updateSlowMult(processSlowMult - PROCESS_MULT_STEP)}>-</button>
            <span className="font-mono min-w-12 text-center">{fmtMult(processSlowMult)}x</span>
            <button type="button" className="px-1 border border-gray-300 rounded" onClick={() => updateSlowMult(processSlowMult + PROCESS_MULT_STEP)}>+</button>
            <input
              type="number"
              min={PROCESS_MULT_MIN}
              max={PROCESS_MULT_MAX}
              step={PROCESS_MULT_STEP}
              value={processSlowMult}
              onChange={e => updateSlowMult(Number(e.target.value))}
              className="w-20 border border-gray-300 rounded px-1 py-0.5 font-mono text-gray-700"
            />
            {[0.33, 0.5, 1, 2, 3, 10, 20, 33].map(m => (
              <button
                key={`slow-${m}`}
                type="button"
                className={`px-1 border rounded ${isSameMult(processSlowMult, m) ? 'border-blue-500 text-blue-600' : 'border-gray-300 text-gray-500'}`}
                onClick={() => updateSlowMult(m)}
              >
                {fmtMult(m)}x
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1">
            <span className="text-gray-500">log mult</span>
            <button type="button" className="px-1 border border-gray-300 rounded" onClick={() => updateLogMult(processLogMult - PROCESS_MULT_STEP)}>-</button>
            <span className="font-mono min-w-12 text-center">{fmtMult(processLogMult)}x</span>
            <button type="button" className="px-1 border border-gray-300 rounded" onClick={() => updateLogMult(processLogMult + PROCESS_MULT_STEP)}>+</button>
            <input
              type="number"
              min={PROCESS_MULT_MIN}
              max={PROCESS_MULT_MAX}
              step={PROCESS_MULT_STEP}
              value={processLogMult}
              onChange={e => updateLogMult(Number(e.target.value))}
              className="w-20 border border-gray-300 rounded px-1 py-0.5 font-mono text-gray-700"
            />
            {[0.33, 0.5, 1, 2, 3, 10, 20, 33].map(m => (
              <button
                key={`log-${m}`}
                type="button"
                className={`px-1 border rounded ${isSameMult(processLogMult, m) ? 'border-blue-500 text-blue-600' : 'border-gray-300 text-gray-500'}`}
                onClick={() => updateLogMult(m)}
              >
                {fmtMult(m)}x
              </button>
            ))}
          </div>
        </div>
        {processPanelMode === 'hide_no_log' && (
          <div className="text-sm text-gray-500">Zobrazení i logování vypnuto.</div>
        )}
        {processPanelMode === 'hide_log' && (
          <div className="text-sm text-gray-500">
            Zobrazení vypnuto. Scan i logování běží na pozadí.
            {processes ? ` Running: ${runningProcessRows.length}, expected not running: ${stoppedProcessRows.length}.` : ''}
          </div>
        )}
        {processPanelMode === 'show_log' && processes?.gpu && (
          <div className="text-xs text-gray-400 mb-2">
            GPU: {processes.gpu.provider || 'n/a'}
            {processes.gpu.total_util_percent != null ? ` · load ${processes.gpu.total_util_percent.toFixed(1)}%` : ''}
          </div>
        )}
        {processPanelMode === 'show_log' && processes?.root_pids && processes.root_pids.length > 0 && (
          <div className="text-xs text-gray-400 mb-2">
            root pids: {processes.root_pids.join(', ')}
          </div>
        )}
        {processPanelMode === 'show_log' && processes?.warnings && processes.warnings.length > 0 && (
          <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mb-2">
            warnings: {processes.warnings.join(' | ')}
          </div>
        )}
        {processPanelMode === 'show_log' && typeof processes?.zombie_count === 'number' && processes.zombie_count > 0 && (
          <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1 mb-2">
            zombie candidates detected: {processes.zombie_count}
          </div>
        )}
        {processPanelMode === 'show_log' && (!processes || processRows.length === 0) ? (
          <div className="text-sm text-gray-400">Žádné nalezené app procesy</div>
        ) : null}
        {processPanelMode === 'show_log' && processes && processRows.length > 0 ? (
          <>
            <div className="text-[11px] text-gray-400 mb-2">
              Running: {runningProcessRows.length} · Not running (expected): {stoppedProcessRows.length}
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 border-b border-gray-100">
                  <th className="text-left pb-1 font-medium cursor-pointer select-none" onClick={(e) => onProcessSortClick('state', e.shiftKey)}>
                    State {processSortBadge('state')}
                  </th>
                  <th className="text-left pb-1 font-medium cursor-pointer select-none" onClick={(e) => onProcessSortClick('profile', e.shiftKey)}>
                    Profile {processSortBadge('profile')}
                  </th>
                  <th className="text-left pb-1 font-medium cursor-pointer select-none" onClick={(e) => onProcessSortClick('pid', e.shiftKey)}>
                    PID {processSortBadge('pid')}
                  </th>
                  <th className="text-left pb-1 font-medium cursor-pointer select-none" onClick={(e) => onProcessSortClick('status', e.shiftKey)}>
                    Status {processSortBadge('status')}
                  </th>
                  <th className="text-right pb-1 font-medium cursor-pointer select-none" onClick={(e) => onProcessSortClick('cpu', e.shiftKey)}>
                    CPU {processSortBadge('cpu')}
                  </th>
                  <th className="text-right pb-1 font-medium cursor-pointer select-none" onClick={(e) => onProcessSortClick('ram', e.shiftKey)}>
                    RAM {processSortBadge('ram')}
                  </th>
                  <th className="text-right pb-1 font-medium cursor-pointer select-none" onClick={(e) => onProcessSortClick('gpu', e.shiftKey)}>
                    GPU {processSortBadge('gpu')}
                  </th>
                  <th className="text-right pb-1 font-medium cursor-pointer select-none" onClick={(e) => onProcessSortClick('running_for', e.shiftKey)}>
                    Runtime {processSortBadge('running_for')}
                  </th>
                  <th className="text-left pb-1 font-medium">Cmd</th>
                </tr>
              </thead>
              <tbody>
                {visibleProcessRows.map(row => (
                  <tr key={row.rowKey} className={`border-b border-gray-50 ${row.isZombie ? 'bg-red-50' : ''}`}>
                    <td className={`py-0.5 ${row.isRunning ? 'text-emerald-700' : 'text-gray-400'}`}>
                      {row.isRunning ? 'running' : 'not running'}
                    </td>
                    <td className="py-0.5 text-gray-700">
                      {row.profileLabel}
                      {row.isRoot ? ' *' : ''}
                      {row.isZombie ? ' [zombie?]' : ''}
                    </td>
                    <td className="py-0.5 font-mono text-gray-500">{row.pid ?? '-'}</td>
                    <td className={`py-0.5 ${row.isZombie ? 'text-red-700' : 'text-gray-600'}`}>{row.status || 'n/a'}</td>
                    <td className="py-0.5 text-right text-gray-600">{fmtPercent(row.cpuPercent)}</td>
                    <td className="py-0.5 text-right text-gray-600">{fmtMb(row.ramMb)}</td>
                    <td className="py-0.5 text-right text-gray-600">{fmtMb(row.gpuMemoryMb)}</td>
                    <td className="py-0.5 text-right text-gray-500">{fmtRunningFor(row.runningForSeconds)}</td>
                    <td className="py-0.5 text-gray-400 max-w-xl truncate" title={row.cmdlinePreview || ''}>
                      {row.cmdlinePreview || 'n/a'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {stoppedProcessRows.length > 1 && (
              <button
                type="button"
                onClick={() => setShowAllStoppedProcesses(v => !v)}
                className="mt-2 text-xs text-blue-600 hover:text-blue-700 hover:underline"
              >
                {showAllStoppedProcesses
                  ? 'Hide extra not-running profiles'
                  : `Show ${stoppedProcessRows.length - 1} more not-running profiles`}
              </button>
            )}
            <div className="mt-1 text-[11px] text-gray-400">
              Sorting: click header = primary sort, Shift+click = add priority sort.
            </div>
          </>
        ) : null}
      </div>

      <div className="bg-white rounded border border-gray-200 p-4 space-y-3">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Performance Monitor</div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <button
            type="button"
            onClick={monitorMasterOn ? setAllMonitoringOff : enableMonitoring}
            className={`px-2 py-1 border rounded ${
              monitorMasterOn ? 'border-red-500 text-red-700 hover:bg-red-50' : 'border-emerald-500 text-emerald-700 hover:bg-emerald-50'
            }`}
          >
            {monitorMasterOn ? 'Turn monitoring OFF (all)' : 'Enable monitoring'}
          </button>
          <button
            type="button"
            onClick={applyGraphsNoLoggingPreset}
            className="px-2 py-1 border border-blue-500 text-blue-700 rounded hover:bg-blue-50"
          >
            Graphs only, no logging
          </button>
          <span className="text-gray-400">view:</span>
          {(['full', 'graphs_only', 'numbers_only'] as MonitorViewMode[]).map(mode => (
            <button
              key={mode}
              type="button"
              onClick={() => setMonitorViewMode(mode)}
              className={`px-2 py-1 border rounded ${
                monitorViewMode === mode ? 'border-blue-500 text-blue-700' : 'border-gray-300 text-gray-500'
              }`}
            >
              {mode}
            </button>
          ))}
          <span className="text-gray-400">log coef:</span>
          {[1, 2, 5, 10].map(c => (
            <button
              key={`coef-${c}`}
              type="button"
              onClick={() => setMonitorLogCoef(c as LogCoef)}
              className={`px-2 py-1 border rounded ${
                monitorLogCoef === c ? 'border-blue-500 text-blue-700' : 'border-gray-300 text-gray-500'
              }`}
              title="Used in log scale transform"
            >
              {c}x
            </button>
          ))}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
          <div className="border border-gray-200 rounded p-2 space-y-2">
            <div className="font-semibold text-gray-700">RAM channel</div>
            <div className="flex items-center gap-2">
              <span className="text-gray-500">show graph</span>
              <button
                type="button"
                onClick={() => setRamGraphVisible(v => !v)}
                className={`px-2 py-0.5 border rounded ${ramGraphVisible ? 'border-blue-500 text-blue-700' : 'border-gray-300 text-gray-500'}`}
              >
                {ramGraphVisible ? 'on' : 'off'}
              </button>
              <span className="text-gray-500">logging</span>
              <button
                type="button"
                onClick={() => setRamLogEnabled(v => !v)}
                className={`px-2 py-0.5 border rounded ${ramLogEnabled ? 'border-blue-500 text-blue-700' : 'border-gray-300 text-gray-500'}`}
              >
                {ramLogEnabled ? 'on' : 'off'}
              </button>
              <button
                type="button"
                onClick={() => setRamHistory([])}
                className="px-2 py-0.5 border border-gray-300 rounded text-gray-500"
              >
                clear
              </button>
            </div>
          </div>
          <div className="border border-gray-200 rounded p-2 space-y-2">
            <div className="font-semibold text-gray-700">CPU channel</div>
            <div className="flex items-center gap-2">
              <span className="text-gray-500">show graph</span>
              <button
                type="button"
                onClick={() => setCpuGraphVisible(v => !v)}
                className={`px-2 py-0.5 border rounded ${cpuGraphVisible ? 'border-blue-500 text-blue-700' : 'border-gray-300 text-gray-500'}`}
              >
                {cpuGraphVisible ? 'on' : 'off'}
              </button>
              <span className="text-gray-500">logging</span>
              <button
                type="button"
                onClick={() => setCpuLogEnabled(v => !v)}
                className={`px-2 py-0.5 border rounded ${cpuLogEnabled ? 'border-blue-500 text-blue-700' : 'border-gray-300 text-gray-500'}`}
              >
                {cpuLogEnabled ? 'on' : 'off'}
              </button>
              <button
                type="button"
                onClick={() => setCpuHistory([])}
                className="px-2 py-0.5 border border-gray-300 rounded text-gray-500"
              >
                clear
              </button>
            </div>
          </div>
        </div>
        <div className="text-[11px] text-gray-400">
          Intervals: RAM {(RAM_GRAPH_INTERVAL_MS / 1000).toFixed(1)}s, CPU {(CPU_GRAPH_INTERVAL_MS / 1000).toFixed(1)}s, phase offset {(CPU_GRAPH_PHASE_OFFSET_MS / 1000).toFixed(1)}s.
        </div>
      </div>

      {!monitorMasterOn && (
        <div className="bg-white rounded border border-gray-200 p-4 text-sm text-gray-500">
          Monitoring is OFF. Settings are preserved in local storage.
        </div>
      )}

      {showNumbersCard && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="bg-white rounded border border-gray-200 p-4">
            <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">RAM numbers</div>
            <div className="text-sm text-gray-700">App total (orange): {fmtPercent(currentRamSample?.app_pct)}</div>
            <div className="text-sm text-gray-700">Top important process (red): {fmtPercent(currentRamSample?.key_pct)}</div>
            <div className="text-sm text-gray-700">Other system processes (blue): {fmtPercent(currentRamSample?.other_pct)}</div>
            <div className="text-[11px] text-gray-400 mt-1">Red process: {ramKeyLabelForUi}</div>
          </div>
          <div className="bg-white rounded border border-gray-200 p-4">
            <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">CPU numbers</div>
            <div className="text-sm text-gray-700">App total (orange): {fmtPercent(currentCpuSample?.app_pct)}</div>
            <div className="text-sm text-gray-700">Top important process (red): {fmtPercent(currentCpuSample?.key_pct)}</div>
            <div className="text-sm text-gray-700">Other system processes (blue): {fmtPercent(currentCpuSample?.other_pct)}</div>
            <div className="text-[11px] text-gray-400 mt-1">Red process: {cpuKeyLabelForUi}</div>
          </div>
        </div>
      )}

      {/* RAM usage graph */}
      {showRamGraphCard && (
        <div className="bg-white rounded border border-gray-200 p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              RAM % ({ramHistory.length} samples, interval {(RAM_GRAPH_INTERVAL_MS / 1000).toFixed(1)}s, span {fmtSpan(ramSpanSeconds)})
            </div>
            <button
              type="button"
              className="text-xs px-2 py-1 border border-gray-300 rounded text-gray-600 hover:bg-gray-50"
              onClick={() => setRamScale(s => (s === 'log' ? 'linear' : 'log'))}
            >
              Scale: {ramScale === 'log' ? `log ×${monitorLogCoef}` : 'linear'}
            </button>
          </div>
          <div className="text-[11px] text-gray-400 mb-2">Red process: {ramKeyLabelForUi}</div>
          {ramChartData.length > 1 ? (
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={ramChartData} margin={{ top: 6, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" />
                <XAxis dataKey="t" tick={{ fontSize: 10 }} />
                <YAxis
                  domain={[0, 100]}
                  ticks={ramAxisTicks}
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v: number) => fmtAxisPercent(v, ramScale, monitorLogCoef)}
                />
                <Tooltip content={<UsageTooltip />} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="app_plot" name="App total (orange)" stroke="#f59e0b" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="key_plot" name="Top important process (red)" stroke="#ef4444" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="other_plot" name="Other system processes (blue)" stroke="#3b82f6" strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-sm text-gray-500">Not enough history points. Enable RAM logging or wait for samples.</div>
          )}
        </div>
      )}

      {/* CPU usage graph */}
      {showCpuGraphCard && (
        <div className="bg-white rounded border border-gray-200 p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              CPU % ({cpuHistory.length} samples, interval {(CPU_GRAPH_INTERVAL_MS / 1000).toFixed(1)}s, span {fmtSpan(cpuSpanSeconds)})
            </div>
            <button
              type="button"
              className="text-xs px-2 py-1 border border-gray-300 rounded text-gray-600 hover:bg-gray-50"
              onClick={() => setCpuScale(s => (s === 'log' ? 'linear' : 'log'))}
            >
              Scale: {cpuScale === 'log' ? `log ×${monitorLogCoef}` : 'linear'}
            </button>
          </div>
          <div className="text-[11px] text-gray-400 mb-2">Red process: {cpuKeyLabelForUi}</div>
          {cpuChartData.length > 1 ? (
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={cpuChartData} margin={{ top: 6, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" />
                <XAxis dataKey="t" tick={{ fontSize: 10 }} />
                <YAxis
                  domain={[0, 100]}
                  ticks={cpuAxisTicks}
                  tick={{ fontSize: 10 }}
                  tickFormatter={(v: number) => fmtAxisPercent(v, cpuScale, monitorLogCoef)}
                />
                <Tooltip content={<UsageTooltip />} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="app_plot" name="App total (orange)" stroke="#f59e0b" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="key_plot" name="Top important process (red)" stroke="#ef4444" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="other_plot" name="Other system processes (blue)" stroke="#3b82f6" strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-sm text-gray-500">Not enough history points. Enable CPU logging or wait for samples.</div>
          )}
        </div>
      )}

      {/* Active jobs */}
      {activeJobs.length > 0 && (
        <div className="bg-blue-50 rounded border border-blue-200 p-4">
          <div className="text-xs font-semibold text-blue-700 uppercase tracking-wide mb-2">Aktivní joby ({activeJobs.length})</div>
          <div className="space-y-1">
            {activeJobs.map(j => (
              <div key={j.job_id} className="flex items-center gap-3 text-sm">
                <span className="font-mono text-xs text-gray-400">{j.job_id.slice(-8)}</span>
                <span className={`font-medium ${statusColor(j.status)}`}>{j.status}</span>
                <span className="text-gray-600 truncate flex-1">{j.label || j.job_id}</span>
                <span className="text-gray-400 text-xs">{j.progress_percent ?? 0}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent jobs */}
      <div className="bg-white rounded border border-gray-200 p-4">
        <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Nedávné joby</div>
        {recentJobs.length === 0
          ? <div className="text-sm text-gray-400">Žádné joby</div>
          : (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 border-b border-gray-100">
                  <th className="text-left pb-1 font-medium">ID</th>
                  <th className="text-left pb-1 font-medium">Status</th>
                  <th className="text-left pb-1 font-medium">Popis</th>
                  <th className="text-right pb-1 font-medium">Spuštěno</th>
                </tr>
              </thead>
              <tbody>
                {recentJobs.map(j => (
                  <tr key={j.job_id} className="border-b border-gray-50">
                    <td className="py-0.5 font-mono text-gray-400">{j.job_id.slice(-8)}</td>
                    <td className={`py-0.5 ${statusColor(j.status)}`}>{j.status}</td>
                    <td className="py-0.5 text-gray-600 truncate max-w-xs">{j.label || '–'}</td>
                    <td className="py-0.5 text-gray-400 text-right">{j.started_at ? fmtDt(j.started_at) : '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </div>

      {/* Recent transcripts */}
      {transcripts.length > 0 && (
        <div className="bg-white rounded border border-gray-200 p-4">
          <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
            Uložené přepisy ({transcripts.length})
          </div>
          <div className="space-y-1">
            {transcripts.slice(0, 5).map(t => (
              <div key={t.transcript_id} className="flex items-center gap-3 text-xs">
                <span className="text-gray-400">{fmtDt(t.updated_at)}</span>
                <span className="font-medium text-gray-700 truncate flex-1">{t.title}</span>
                {t.model_id && <span className="text-gray-400">{t.model_id}</span>}
              </div>
            ))}
            {transcripts.length > 5 && (
              <div className="text-xs text-gray-400">…a dalších {transcripts.length - 5}</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
