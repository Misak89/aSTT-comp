// Typy zrcadlí Pydantic modely backendu

export interface SubtitleFile {
  filename: string
  size_bytes: number
  ext: string
}

export interface LatestResult {
  model_id: string
  setting_id: string
  wer: number | null
  cer: number | null
  run_id: string
  timestamp: string
  transcript_snippet: string | null
}

export interface LibraryItem {
  video_id: string
  title: string
  url: string
  duration_seconds: number | null
  language: string
  genre: string | null
  subtitles_local: boolean
  subtitle_files: SubtitleFile[]
  added_at: string | null
  latest_results?: LatestResult[]
}

export interface ParamSpec {
  name: string
  label: string
  type: 'int' | 'float' | 'str' | 'bool' | 'select'
  default: unknown
  description: string
  min: number | null
  max: number | null
  options: string[]
}

export interface ModelDescriptor {
  model_id: string
  label: string
  adapter: string
  languages: string[]
  supports_streaming: boolean
  supports_microphone: boolean
  notes: string
  params: ParamSpec[]
}

export interface BenchmarkJobRequest {
  video_ids?: string[]
  sources?: string[]
  model_ids?: string[]
  setting_ids?: string[]
  sample_seconds?: number
  chunk_seconds?: number
  evaluation_mode?: 'real' | 'synthetic' | 'streaming'
  clip_strategy?: 'random' | 'uniform'
  clip_seed?: number
  label?: string
  scenario_id?: string
  model_params?: Record<string, Record<string, unknown>>
}

// Mic session
export interface MicSessionState {
  session_id: string
  model_id: string
  status: 'idle' | 'recording' | 'stopped'
  transcript: string
  first_word_latency_ms: number | null
  elapsed_s: number
  rtf: number
  total_audio_s: number
  error: string | null
}

export interface AudioDevice {
  index: number
  name: string
  max_input_channels: number
  default_samplerate: number
}

export interface BenchmarkJobStatus {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  label: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  progress_message: string | null
  progress_percent: number
  error: string | null
  run_id: string | null
  result_url: string | null
  conditions_clean: boolean | null
  pre_cpu: number | null
  pre_ram_mb: number | null
  video_ids: string[] | null
  evaluation_mode: string | null
}

export interface Scenario {
  scenario_id: string
  description?: string
  clip_seconds: number
  clip_seed?: number
  model_ids: string[]
  setting_ids: string[]
  video_ids: string[]
  created_at?: string
}

export interface ModelOption {
  id: string
  label: string
}

export interface BenchmarkOptions {
  models: ModelOption[]
  settings: ModelOption[]
}

export interface AggregateMetrics {
  score: number | null
  wer: number | null
  cer: number | null
  latency_ms: number | null
  rtf: number | null
  cpu_percent: number | null
  ram_mb: number | null
}

export interface SourceMetric {
  video_id: string | null
  canonical_url: string | null
  clip_start_seconds: number | null
  clip_seconds: number | null
  transcript: string | null
  reference_text: string | null
  wer: number | null
  cer: number | null
  latency_ms: number | null
  rtf: number | null
  engine_elapsed_seconds: number | null
  chunk_metrics: ChunkMetric[] | null
}

export interface ChunkMetric {
  chunk_start_s: number
  chunk_end_s: number
  chunk_duration_s: number
  processing_s: number
  rtf: number
  total_elapsed_s: number
  words: number
}

export interface RunResult {
  model_id: string
  model_label: string
  setting_id: string
  setting_label: string
  aggregate: AggregateMetrics
  source_metrics: SourceMetric[]
}

export interface RunDetail {
  run_id: string
  created_at_utc: string
  evaluation_mode: string
  sample_seconds: number
  sources: string[]
  results: RunResult[]
}

// Live progress z running jobu
export interface HwSample {
  cpu: number | null
  ram_mb: number | null
}

export interface LiveJobProgress {
  job_id: string
  status: string
  percent: number
  message: string
  message_log: string[]
  updated_at: string | null
  hw_series: HwSample[]
  transcript: string
  transcript_ts?: string
  pre_cpu: number | null
  pre_ram_mb: number | null
  model_params_used: Record<string, unknown>
}

// Modely — install/uninstall log
export interface ModelEvent {
  type: 'install' | 'uninstall' | 'note'
  date: string
  version: string | null
  size_mb: number | null
  reason: string | null
  note: string | null
}

export interface ModelStatus {
  model_id: string
  label: string
  installed: boolean
  last_install: string | null
  last_uninstall: string | null
  size_mb: number | null
  events: ModelEvent[]
}
