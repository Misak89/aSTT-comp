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
import { useEffect, useState, useRef } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { api } from '../api/client'
import { listTranscripts } from '../components/transcribe/useTranscribeStorage'
import type { BenchmarkJobStatus, LibraryItem } from '../types'

interface HealthSample {
  t: string          // HH:MM:SS
  ram_pct: number
  ram_used_mb: number
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

export function DashboardPage() {
  const [health, setHealth] = useState<{
    startedAt: string; ramUsed: number; ramTotal: number; ramPct: number; utc: string
  } | null>(null)
  const [ramHistory, setRamHistory] = useState<HealthSample[]>([])
  const [jobs, setJobs] = useState<BenchmarkJobStatus[]>([])
  const [library, setLibrary] = useState<LibraryItem[]>([])
  const [uptime, setUptime] = useState('')
  const historyRef = useRef<HealthSample[]>([])

  // Poll health every 5s
  useEffect(() => {
    const fetchHealth = () => {
      fetch('/api/health').then(r => r.json()).then((d: Record<string, unknown>) => {
        const sample: HealthSample = {
          t: new Date().toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          ram_pct: (d.ram_percent as number) ?? 0,
          ram_used_mb: (d.ram_used_mb as number) ?? 0,
        }
        historyRef.current = [...historyRef.current.slice(-29), sample]
        setRamHistory([...historyRef.current])
        setHealth({
          startedAt: (d.started_at as string) ?? '',
          ramUsed: (d.ram_used_mb as number) ?? 0,
          ramTotal: (d.ram_total_mb as number) ?? 0,
          ramPct: (d.ram_percent as number) ?? 0,
          utc: (d.utc as string) ?? '',
        })
      }).catch(() => {})
    }
    fetchHealth()
    const t = setInterval(fetchHealth, 5_000)
    return () => clearInterval(t)
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

      {/* RAM sparkline */}
      {ramHistory.length > 1 && (
        <div className="bg-white rounded border border-gray-200 p-4">
          <div className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">RAM % (posledních 30 vzorků, interval 5s)</div>
          <ResponsiveContainer width="100%" height={80}>
            <AreaChart data={ramHistory} margin={{ top: 2, right: 4, left: -30, bottom: 0 }}>
              <XAxis dataKey="t" hide />
              <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} />
              <Tooltip
                formatter={(v: number) => [`${v.toFixed(1)}%`, 'RAM']}
                labelFormatter={l => `čas: ${l}`}
                contentStyle={{ fontSize: 11 }}
              />
              <Area type="monotone" dataKey="ram_pct" stroke="#3b82f6" fill="#bfdbfe" strokeWidth={1.5} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
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
