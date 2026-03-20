import { useEffect, useRef, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { api } from '../api/client'
import type { BenchmarkJobStatus, HwSample } from '../types'

interface Props {
  job: BenchmarkJobStatus
}

/**
 * Panel pro live monitoring běžícího benchmarku.
 * Zobrazuje: YouTube embed prvního videa, progress bar, HW grafy (CPU % + RAM MB).
 * Polluje /benchmark/jobs/{id}/live každou sekundu.
 */
export function LiveJobPanel({ job }: Props) {
  const [percent, setPercent] = useState(0)
  const [message, setMessage] = useState(job.progress_message || 'Inicializace...')
  const [hwSeries, setHwSeries] = useState<HwSample[]>([])
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (job.status !== 'running' && job.status !== 'pending') return

    const poll = async () => {
      try {
        const live = await api.benchmark.getLive(job.job_id)
        setPercent(live.percent)
        setMessage(live.message)
        setHwSeries(live.hw_series.slice(-60)) // posledních 60 vzorků
      } catch {
        // backend restart nebo timeout — tiše ignoruj
      }
    }

    poll()
    timerRef.current = setInterval(poll, 1000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [job.job_id, job.status])

  // Extrahuj video_id pro YouTube embed
  const videoId = extractFirstVideoId(job)

  // Připrav data pro grafy (index jako osa X)
  const chartData = hwSeries.map((s, i) => ({
    t: i,
    cpu: s.cpu ?? 0,
    ram: s.ram_mb ? Math.round(s.ram_mb) : 0,
  }))

  return (
    <div className="bg-white border border-blue-200 rounded-lg p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-sm text-blue-800">
          Live benchmark — {job.label || job.job_id.slice(-8)}
        </h2>
        <span className="text-xs text-gray-500 font-mono">{job.job_id.slice(-12)}</span>
      </div>

      {/* Progress bar */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-gray-500">
          <span className="truncate max-w-md">{message}</span>
          <span className="ml-2 font-mono">{percent}%</span>
        </div>
        <div className="w-full bg-gray-100 rounded-full h-2">
          <div
            className="bg-blue-500 h-2 rounded-full transition-all duration-500"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* YouTube embed */}
        {videoId ? (
          <div className="aspect-video rounded overflow-hidden border border-gray-200">
            <iframe
              src={`https://www.youtube.com/embed/${videoId}?autoplay=0&cc_load_policy=1`}
              title="Video benchmark"
              className="w-full h-full"
              allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
            />
          </div>
        ) : (
          <div className="aspect-video rounded bg-gray-50 border border-gray-200 flex items-center justify-center text-gray-400 text-sm">
            Žádné video
          </div>
        )}

        {/* HW grafy */}
        <div className="space-y-3">
          {/* CPU % */}
          <div>
            <p className="text-xs font-medium text-gray-600 mb-1">CPU % (subprocess)</p>
            <ResponsiveContainer width="100%" height={90}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="2 2" stroke="#f0f0f0" />
                <XAxis dataKey="t" hide />
                <YAxis domain={[0, 100]} width={28} tick={{ fontSize: 10 }} unit="%" />
                <Tooltip formatter={(v: number) => [`${v}%`, 'CPU']} labelFormatter={() => ''} />
                <Line type="monotone" dataKey="cpu" stroke="#3b82f6" dot={false} strokeWidth={1.5} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          {/* RAM MB */}
          <div>
            <p className="text-xs font-medium text-gray-600 mb-1">RAM MB (subprocess)</p>
            <ResponsiveContainer width="100%" height={90}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="2 2" stroke="#f0f0f0" />
                <XAxis dataKey="t" hide />
                <YAxis width={40} tick={{ fontSize: 10 }} unit="MB" />
                <Tooltip formatter={(v: number) => [`${v} MB`, 'RAM']} labelFormatter={() => ''} />
                <Line type="monotone" dataKey="ram" stroke="#10b981" dot={false} strokeWidth={1.5} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Podmínky HW */}
      {job.conditions_clean != null && (
        <p className="text-xs text-gray-500">
          HW podmínky:{' '}
          {job.conditions_clean
            ? <span className="text-green-600 font-medium">čisté (CPU &lt; 20% před startem)</span>
            : <span className="text-orange-500 font-medium">zatížený systém — výsledky mohou být zkresleny</span>}
        </p>
      )}
    </div>
  )
}

function extractFirstVideoId(job: BenchmarkJobStatus): string | null {
  return job.video_ids?.[0] ?? null
}
