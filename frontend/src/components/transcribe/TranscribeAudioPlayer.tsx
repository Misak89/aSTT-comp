/**
 * TranscribeAudioPlayer — wavesurfer.js přehrávač pro stránku Přepis.
 *
 * Funkce:
 * - Waveform vizualizace (wavesurfer.js v7)
 * - Play/pause, ±5s, ±15s, nastavitelný krok
 * - Rychlost: 0.25× - 3.00× (granulární)
 * - Klávesové zkratky: Space, ←→ (±stepS), ↑↓ (±bigStepS)
 * - onTimeUpdate callback pro synchronizaci s editorem
 * - seekTo(t) metoda přes forwardRef
 */
import { useRef, useEffect, useCallback, useState, forwardRef, useImperativeHandle } from 'react'
import { useWavesurfer } from '@wavesurfer/react'

const SPEED_OPTIONS = [
  0.25, 0.50, 0.60, 0.70, 0.75, 0.80, 0.90,
  1.00, 1.10, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00,
]

export interface TranscribeAudioPlayerHandle {
  seekTo: (seconds: number) => void
  getCurrentTime: () => number
}

interface Props {
  audioUrl: string | null
  onTimeUpdate?: (seconds: number) => void
  onReady?: (duration: number) => void
  stepSeconds?: number
  bigStepSeconds?: number
}

export const TranscribeAudioPlayer = forwardRef<TranscribeAudioPlayerHandle, Props>(
  function TranscribeAudioPlayer(
    { audioUrl, onTimeUpdate, onReady, stepSeconds = 5, bigStepSeconds = 15 },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null)
    const [speed, setSpeed] = useState(1.0)
    const [stepS, setStepS] = useState(stepSeconds)
    const [bigStepS, setBigStepS] = useState(bigStepSeconds)
    const [duration, setDuration] = useState(0)

    const { wavesurfer, isPlaying, currentTime } = useWavesurfer({
      container: containerRef,
      url: audioUrl ?? undefined,
      waveColor: '#64748b',
      progressColor: '#af1a1e',
      cursorColor: '#af1a1e',
      height: 70,
      normalize: true,
      interact: true,
    })

    // Notify parent on time change
    useEffect(() => {
      onTimeUpdate?.(currentTime)
    }, [currentTime, onTimeUpdate])

    // Set playback speed when wavesurfer is ready or speed changes
    useEffect(() => {
      if (!wavesurfer) return
      wavesurfer.setPlaybackRate(speed)
    }, [wavesurfer, speed])

    // Ready event
    useEffect(() => {
      if (!wavesurfer) return
      const unsub = wavesurfer.on('ready', (dur) => {
        setDuration(dur)
        onReady?.(dur)
      })
      return () => unsub()
    }, [wavesurfer, onReady])

    // Expose methods to parent
    useImperativeHandle(ref, () => ({
      seekTo: (seconds: number) => {
        if (!wavesurfer || duration === 0) return
        wavesurfer.seekTo(Math.max(0, Math.min(1, seconds / duration)))
      },
      getCurrentTime: () => currentTime,
    }))

    const seek = useCallback((delta: number) => {
      if (!wavesurfer || duration === 0) return
      const next = Math.max(0, Math.min(duration, currentTime + delta))
      wavesurfer.seekTo(next / duration)
    }, [wavesurfer, currentTime, duration])

    // Keyboard shortcuts — listen on container div
    const onKeyDown = useCallback((e: React.KeyboardEvent) => {
      if (e.key === ' ') {
        e.preventDefault()
        wavesurfer?.playPause()
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        seek(-stepS)
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        seek(stepS)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        seek(bigStepS)
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        seek(-bigStepS)
      }
    }, [wavesurfer, seek, stepS, bigStepS])

    function fmt(s: number) {
      const h = Math.floor(s / 3600)
      const m = Math.floor((s % 3600) / 60)
      const sec = Math.floor(s % 60)
      if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
      return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
    }

    if (!audioUrl) {
      return (
        <div className="flex items-center justify-center h-32 bg-gray-900 rounded text-gray-500 text-sm">
          Vyberte zdroj audia pro přehrávač
        </div>
      )
    }

    return (
      <div
        className="bg-gray-900 rounded-lg p-3 select-none outline-none"
        tabIndex={0}
        onKeyDown={onKeyDown}
        title="Klikni sem, pak Space=play/pause, ←/→=±stepS, ↑/↓=±bigStepS"
      >
        {/* Waveform */}
        <div ref={containerRef} className="mb-2 cursor-pointer" />

        {/* Time display */}
        <div className="flex items-center justify-between mb-2">
          <span className="text-gray-300 text-sm font-mono">
            {fmt(currentTime)} / {fmt(duration)}
          </span>
          <span className="text-gray-500 text-xs">
            Klik sem pro klávesnici | Space=play ←/→=±{stepS}s ↑/↓=±{bigStepS}s
          </span>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-2 flex-wrap">
          {/* Seek back big */}
          <button
            onClick={() => seek(-bigStepS)}
            title={`-${bigStepS}s`}
            className="px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded text-sm font-mono"
          >
            -{bigStepS}s
          </button>

          {/* Seek back small */}
          <button
            onClick={() => seek(-stepS)}
            title={`-${stepS}s`}
            className="px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded text-sm font-mono"
          >
            -{stepS}s
          </button>

          {/* Play/Pause */}
          <button
            onClick={() => wavesurfer?.playPause()}
            className="px-4 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-sm font-medium min-w-16"
          >
            {isPlaying ? '⏸ Pauza' : '▶ Play'}
          </button>

          {/* Seek forward small */}
          <button
            onClick={() => seek(stepS)}
            title={`+${stepS}s`}
            className="px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded text-sm font-mono"
          >
            +{stepS}s
          </button>

          {/* Seek forward big */}
          <button
            onClick={() => seek(bigStepS)}
            title={`+${bigStepS}s`}
            className="px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded text-sm font-mono"
          >
            +{bigStepS}s
          </button>

          {/* Speed */}
          <div className="flex items-center gap-1 ml-auto">
            <span className="text-gray-400 text-xs">Rychlost:</span>
            <select
              value={speed}
              onChange={e => setSpeed(Number(e.target.value))}
              className="bg-gray-700 border border-gray-600 text-gray-200 text-sm rounded px-1.5 py-1"
            >
              {SPEED_OPTIONS.map(s => (
                <option key={s} value={s}>{s.toFixed(2)}×</option>
              ))}
            </select>
          </div>

          {/* Custom step settings */}
          <div className="flex items-center gap-1">
            <span className="text-gray-400 text-xs">Krok:</span>
            <input
              type="number" min={1} max={60} value={stepS}
              onChange={e => setStepS(Number(e.target.value))}
              className="bg-gray-700 border border-gray-600 text-gray-200 text-xs rounded px-1 py-1 w-12"
              title="Malý krok (←/→) v sekundách"
            />
            <span className="text-gray-500 text-xs">/</span>
            <input
              type="number" min={1} max={120} value={bigStepS}
              onChange={e => setBigStepS(Number(e.target.value))}
              className="bg-gray-700 border border-gray-600 text-gray-200 text-xs rounded px-1 py-1 w-14"
              title="Velký krok (↑/↓) v sekundách"
            />
            <span className="text-gray-500 text-xs">s</span>
          </div>
        </div>
      </div>
    )
  },
)
