import { useEffect } from 'react'
import type { Dispatch, MutableRefObject, SetStateAction } from 'react'

import { api } from '../../api/client'
import type { AppProcessSnapshot } from '../../types'

type ProcessPanelMode = 'show_log' | 'hide_log' | 'hide_no_log'

interface UseProcessScanCadenceArgs {
  processPanelMode: ProcessPanelMode
  processFastMult: number
  processSlowMult: number
  processLogMult: number
  setProcessPhaseLabel: Dispatch<SetStateAction<string>>
  setNextFastScanAtMs: Dispatch<SetStateAction<number | null>>
  setNextSlowScanAtMs: Dispatch<SetStateAction<number | null>>
  setNextLogAtMs: Dispatch<SetStateAction<number | null>>
  setProcessScanLogCount: Dispatch<SetStateAction<number>>
  setProcesses: Dispatch<SetStateAction<AppProcessSnapshot | null>>
  lastFastRunAtRef: MutableRefObject<number>
  lastSlowRunAtRef: MutableRefObject<number>
  lastLogWriteAtRef: MutableRefObject<number>
  processFastBaseMs: number
  processSlowBaseMs: number
  processLogBaseMs: number
  processPhaseOffsetMs: number
  processCollisionGuardMs: number
  clampMult: (value: number) => number
  appendScanLogEntry: (entry: {
    ts_utc: string
    phase: string
    running: number
    total: number
    zombies: number
    warnings: number
  }) => number
  mergeProcessSnapshots: (previous: AppProcessSnapshot | null, incoming: AppProcessSnapshot) => AppProcessSnapshot
}

export function useProcessScanCadence(args: UseProcessScanCadenceArgs): void {
  const {
    processPanelMode,
    processFastMult,
    processSlowMult,
    processLogMult,
    setProcessPhaseLabel,
    setNextFastScanAtMs,
    setNextSlowScanAtMs,
    setNextLogAtMs,
    setProcessScanLogCount,
    setProcesses,
    lastFastRunAtRef,
    lastSlowRunAtRef,
    lastLogWriteAtRef,
    processFastBaseMs,
    processSlowBaseMs,
    processLogBaseMs,
    processPhaseOffsetMs,
    processCollisionGuardMs,
    clampMult,
    appendScanLogEntry,
    mergeProcessSnapshots,
  } = args

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

    const fastEveryMs = processFastBaseMs * clampMult(processFastMult)
    const slowEveryMs = processSlowBaseMs * clampMult(processSlowMult)
    const logEveryMs = processLogBaseMs * clampMult(processLogMult)
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
      const total = Math.max(running, (snapshot.profiles ?? []).filter((p) => p.expected).length)
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
          setProcesses((prev) => mergeProcessSnapshots(prev, snapshot))
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
      const nearFast = Math.abs(now - lastFastRunAtRef.current) < processCollisionGuardMs
      if (nearFast) {
        scheduleSlow(processPhaseOffsetMs)
        return
      }
      lastSlowRunAtRef.current = now
      setProcessPhaseLabel('slow')
      try {
        const snapshot = await api.health.processes('slow')
        if (!cancelled) {
          setProcessPhaseLabel(snapshot.scan_phase ?? 'slow')
          setProcesses((prev) => mergeProcessSnapshots(prev, snapshot))
          maybeWriteScanLog(snapshot, snapshot.scan_phase ?? 'slow')
        }
      } catch {
        // ignore polling errors
      } finally {
        if (!cancelled) scheduleSlow(slowEveryMs)
      }
    }

    scheduleFast(0)
    scheduleSlow(processPhaseOffsetMs)
    if (shouldLog) {
      setNextLogAtMs(lastLogWriteAtRef.current > 0 ? lastLogWriteAtRef.current + logEveryMs : Date.now() + logEveryMs)
    }

    return () => {
      cancelled = true
      if (fastTimer != null) window.clearTimeout(fastTimer)
      if (slowTimer != null) window.clearTimeout(slowTimer)
    }
  }, [
    processPanelMode,
    processFastMult,
    processSlowMult,
    processLogMult,
    setProcessPhaseLabel,
    setNextFastScanAtMs,
    setNextSlowScanAtMs,
    setNextLogAtMs,
    setProcessScanLogCount,
    setProcesses,
    lastFastRunAtRef,
    lastSlowRunAtRef,
    lastLogWriteAtRef,
    processFastBaseMs,
    processSlowBaseMs,
    processLogBaseMs,
    processPhaseOffsetMs,
    processCollisionGuardMs,
    clampMult,
    appendScanLogEntry,
    mergeProcessSnapshots,
  ])
}
