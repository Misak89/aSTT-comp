// Typy zrcadlí Pydantic modely backendu

export interface YTSearchResult {
  video_id: string
  title: string
  url: string
  duration_seconds: number
  view_count: number
  upload_date: string
  channel: string
  thumbnail: string
  audio_language: string
  subtitle_manual: string[]
  subtitle_auto: string[]
  categories: string[]
  in_library: boolean
}

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
  visible_in_menus: boolean
  subtitles_local: boolean
  subtitle_files: SubtitleFile[]
  subtitle_manual: string[]
  subtitle_auto: string[]
  subtitle_languages: string[]
  added_at: string | null
  upload_date: string | null
  view_count: number | null
  metadata_fetched_at: string | null
  audio_cached: boolean
  audio_size_bytes: number | null
  audio_duration_seconds: number | null
  latest_results?: LatestResult[]
}

export interface SegmentItem {
  idx: number
  start_s: number
  end_s: number
  duration_s: number
  snapped: boolean
  snapped_from_s: number | null
  snap_delta_ms: number | null
  snap_reason: string | null
}

export interface SegmentBundle {
  source_id: string
  source_type: 'library_item' | 'upload'
  mode: 'preset' | 'manual'
  audio_duration_seconds: number
  tolerance_seconds: number
  preset_minutes: number | null
  points_seconds: number[]
  segments: SegmentItem[]
  created_at: string
  updated_at: string
}

export interface SegmentBundlePreviewRequest {
  source_id: string
  source_type: 'library_item' | 'upload'
  mode: 'preset' | 'manual'
  audio_duration_seconds: number
  tolerance_seconds?: number
  pause_aware?: boolean
  pause_silence_dbfs?: number
  pause_min_silence_ms?: number
  preset_minutes?: number | null
  manual_points_seconds?: number[]
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
  stream_suitability: 'green' | 'amber' | 'red'
  transcript_suitability: 'green' | 'amber' | 'red'
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
  segment_start_seconds?: number
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
  first_word_wall_ms?: number | null
  first_word_audio_ms?: number | null
  first_token_ms_p50?: number | null
  first_token_ms_p95?: number | null
  segment_finalize_ms_p50?: number | null
  segment_finalize_ms_p95?: number | null
  processing_ms_p50?: number | null
  processing_ms_p95?: number | null
  capture_jitter_ms_p50?: number | null
  capture_jitter_ms_p95?: number | null
  capture_lag_ms_p50?: number | null
  capture_lag_ms_p95?: number | null
  sample_rate?: number | null
  input_gain_db?: number | null
  queue_high_watermark_s?: number | null
  queue_low_watermark_s?: number | null
  queue_depth_s?: number | null
  queue_depth_peak_s?: number | null
  backpressure_events?: number | null
  backpressure_active?: boolean | null
  drop_rate?: number | null
  session_resets?: number | null
  worker_rss_peak_mb?: number | null
  chunk_count?: number | null
  dropped_chunks?: number | null
  sequence_timing?: Record<string, unknown> | null
  orchestrator_mode?: 'legacy_sequence' | 'v7_cs_online' | null
  run_id?: string | null
  sequence_id?: string | null
  sequence_index?: number | null
  sequence_total?: number | null
  global_timeline_ms?: number | null
  reason_code?: string | null
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

export interface MicManualRecordRequest {
  model_id: string
  metrics?: Record<string, unknown>
  note?: string
  quality_assessment?: string
  transcript?: string
  source?: string
}

export interface MicManualRecordResponse {
  record_id: string
  history_path: string
  latest_model_path: string
  latest_path: string
  saved_at: string
}

export interface MicManualRecordDeleteResponse {
  record_id: string
  deleted: boolean
  deleted_count: number
}

export interface MicManualRecordBulkDeleteResponse {
  deleted: number
  remaining: number
  model_id?: string | null
  mic_test_mode?: 'free_speech' | 'reference_video' | 'unknown' | null
}

export interface MicManualRecordListItem {
  record_id: string
  saved_at: string
  model_id: string
  note: string
  quality_assessment: string
  source: string
  transcript: string
  metrics: Record<string, unknown>
}

export interface MicManualRecordListResponse {
  records: MicManualRecordListItem[]
}

export interface MicMobileLoopPackageRequest {
  video_id: string
  clip_from_s: number
  clip_to_s: number
  pause_s: number
  repeat_count: number
  include_sync_round: boolean
}

export interface MicMobileLoopPackageResponse {
  package_id: string
  created_at: string
  video_id: string
  video_title: string
  clip_from_s: number
  clip_to_s: number
  clip_duration_s: number
  pause_s: number
  measured_rounds: number
  sync_rounds: number
  total_rounds: number
  total_duration_s: number
  wav_url: string
  download_url: string
  instructions: string
  reference_excerpt?: string | null
}

export interface MicMobileLoopPackageListItem {
  package_id: string
  created_at: string
  video_id: string
  video_title: string
  clip_from_s: number
  clip_to_s: number
  clip_duration_s: number
  pause_s: number
  measured_rounds: number
  sync_rounds: number
  total_rounds: number
  total_duration_s: number
  wav_url: string
  download_url: string
  instructions: string
  reference_excerpt?: string | null
  wav_exists: boolean
  zip_exists: boolean
}

export interface MicMobileLoopPackageListResponse {
  packages: MicMobileLoopPackageListItem[]
}

export interface MicMobileLoopPackageDeleteResponse {
  package_id: string
  deleted: boolean
}

export type MicTrialStatus = 'ok' | 'borderline' | 'too_slow_for_slot' | 'fail'

export interface MicSequenceTrial {
  seq_index: number | null
  seq_total: number | null
  session_id: string
  model_id: string
  phase?: string | null
  status?: string | null
  created_at?: string | null
  started_at?: string | null
  stopped_at: string | null
  updated_at?: string | null
  trial_status: MicTrialStatus
  rtf: number | null
  elapsed_s: number | null
  total_audio_s: number | null
  drop_rate: number | null
  first_word_latency_ms: number | null
  first_word_wall_ms: number | null
  first_word_audio_ms: number | null
  segment_finalize_ms_p50: number | null
  segment_finalize_ms_p95: number | null
  queue_depth_peak_s: number | null
  backpressure_events: number | null
  worker_rss_peak_mb: number | null
  reason_code: string | null
  error: string | null
}

export interface MicSequenceSummary {
  counts?: {
    ok?: number
    borderline?: number
    too_slow_for_slot?: number
    fail?: number
  }
  running?: number
  finalized?: number
  reasons?: Record<string, number>
  avg_rtf?: number | null
  avg_drop_rate?: number | null
}

export interface MicSequenceReport {
  sequence_token: string
  updated_at: string
  sequence_total: number | null
  trials_count: number
  summary?: MicSequenceSummary
  trials: MicSequenceTrial[]
}

export interface LocalFileEntry {
  path: string
  filename: string
  size_bytes: number
  duration_seconds: number | null
  ext: string
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
  mer: number | null
  wil: number | null
  wer_normalized: number | null
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
  wer_normalized: number | null
  mer: number | null
  wil: number | null
  segment_metrics: { from_ms: number; to_ms: number; hyp_text: string; ref_text: string | null; wer: number | null }[] | null
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

export interface WebAppAutostartStatus {
  supported: boolean
  enabled: boolean
  startup_dir: string
  entry_path: string
  script_path: string
  launch_url: string
}

export interface SpecstoryLiveStatus {
  status: 'ok' | 'stale' | 'missing' | 'error'
  source: string
  generated_at_utc: string
  loop_started_utc?: string | null
  loop_pid?: number | null
  interval_seconds?: number | null
  last_run_utc?: string | null
  last_success_utc?: string | null
  last_exit_code?: number | null
  last_error?: string | null
  history_files_count?: number | null
  runs_total?: number | null
  runs_failed?: number | null
  stale_seconds_threshold?: number | null
  message?: string | null
}

export interface AppProcessInfo {
  pid: number
  name: string
  status: string
  started_at_utc: string | null
  running_for_seconds: number | null
  exe: string | null
  cmdline_preview: string
  is_root?: boolean
  profile_id?: string | null
  profile_label?: string | null
  cpu_percent?: number | null
  ram_mb?: number | null
  gpu_memory_mb?: number | null
  zombie_candidate?: boolean
}

export interface AppProcessProfile {
  id: string
  label: string
  expected: boolean
}

export interface AppProcessGpuSummary {
  provider?: string | null
  total_util_percent?: number | null
}

export interface AppProcessSnapshot {
  status: 'ok' | 'unavailable' | 'error'
  updated_at_utc: string
  scan_phase?: 'fast' | 'slow' | 'full'
  count: number
  root_pids?: number[]
  processes: AppProcessInfo[]
  profiles?: AppProcessProfile[]
  zombie_count?: number
  warnings?: string[]
  gpu?: AppProcessGpuSummary
  message?: string | null
}

// Tuning
export interface TuningTrialResult {
  trial_idx: number
  model_id?: string
  params: Record<string, unknown>
  chunk_seconds: number
  wer: number | null
  cer: number | null
  wer_normalized: number | null
  wer_soft: number | null   // WER ignorující drobné záměny (char edit dist < 0.40)
  wer_llm: number | null    // LLM hodnocení — zatím vždy null
  mer: number | null
  wil: number | null
  rtf: number | null
  rtf_p50?: number | null
  rtf_p95?: number | null
  latency_ms: number | null
  latency_p50_ms?: number | null
  latency_p95_ms?: number | null
  latency_quality?: 'measured_live' | 'probe_online' | 'proxy_offline' | 'mixed' | 'unknown' | null
  latency_lane?: 'strict_live' | 'probe_online' | 'batch_proxy' | 'mixed' | 'unknown' | null
  first_token_ms_p50?: number | null
  first_token_ms_p95?: number | null
  segment_finalize_ms_p50?: number | null
  segment_finalize_ms_p95?: number | null
  processing_ms_p50?: number | null
  processing_ms_p95?: number | null
  capture_jitter_ms_p50?: number | null
  capture_jitter_ms_p95?: number | null
  capture_lag_ms_p50?: number | null
  capture_lag_ms_p95?: number | null
  queue_depth_peak_s?: number | null
  backpressure_events?: number | null
  drop_rate?: number | null
  session_resets?: number | null
  reason_code?: string | null
  ram_mb?: number | null
  ram_peak_mb?: number | null
  ram_p95_mb?: number | null
  worker_rss_before_mb?: number | null
  worker_rss_after_mb?: number | null
  worker_rss_peak_mb?: number | null
  load_profile?: string | null
  load_cpu_target_pct?: number | null
  load_ram_target_pct?: number | null
  constraints_profile?: string | null
  constraints_cpu_cores?: number | null
  constraints_ram_limit_mb?: number | null
  constraints_priority?: string | null
  constraints_applied?: boolean | null
  constraints_warnings?: string[] | null
  constraints_ram_mode?: 'hard' | 'soft' | 'none' | null
  constraints_cpu_applied?: boolean | null
  constraints_priority_applied?: boolean | null
  constraints_ram_hard_cap_applied?: boolean | null
  constraints_ram_hard_cap_error?: string | null
  load_cpu_actual_avg_pct?: number | null
  load_cpu_actual_p95_pct?: number | null
  load_ram_actual_avg_pct?: number | null
  load_ram_actual_p95_pct?: number | null
  load_samples?: number | null
  load_control_ok?: boolean | null
  perceived_delay_s: number | null
  perceived_delay_method?: string | null
  perceived_delay_quality?: 'high' | 'medium' | 'low' | 'unknown' | null
  source_success_count?: number | null
  source_error_count?: number | null
  success_rate?: number | null
  resource_metrics_available?: boolean | null
  is_repeat?: boolean
  repeat_of_trial_idx?: number | null
  repeat_no?: number
  repeat_group_key?: string | null
  trial_finished_at?: string | null
  error: string | null
  is_pareto: boolean
  rtf_viable: boolean  // RTF < 1.0 = použitelné pro live mikrofon
  source_metrics?: { video_id: string; wer: number | null; cer: number | null; rtf: number | null; error?: string | null }[]
  transcript: string | null
  reference_text: string | null
  elapsed_s: number | null
  total_audio_s: number | null
  word_count: number | null
  word_diff: { op: string; ref: string | null; hyp: string | null; is_soft: boolean }[] | null
  chunk_metrics: { chunk_start_s: number; chunk_end_s: number; rtf: number; processing_s: number }[] | null
}

export interface TuningMetricStats {
  n: number
  mean: number
  std: number
  ci95_low: number
  ci95_high: number
}

export interface TuningReproducibilityItem {
  seed_trial_idx: number
  model_id: string
  params: Record<string, unknown>
  chunk_seconds: number
  runs_total: number
  runs_ok: number
  runs_error: number
  rtf_viable_rate: number | null
  wer?: TuningMetricStats | null
  rtf?: TuningMetricStats | null
  latency_ms?: TuningMetricStats | null
  perceived_delay_s?: TuningMetricStats | null
  ram_mb?: TuningMetricStats | null
  repeat_trial_idxs?: number[]
}

export interface TuningHardwareInfo {
  hostname?: string | null
  os?: string | null
  python?: string | null
  cpu_model?: string | null
  logical_cores?: number | null
  physical_cores?: number | null
  ram_total_mb?: number | null
}

export interface TuningDecisionCandidate {
  trial_idx: number
  model_id: string
  params: Record<string, unknown>
  wer: number
  wer_soft: number | null
  rtf: number
  perceived_delay_s: number | null
  latency_ms: number | null
  latency_quality: string | null
  latency_lane?: 'strict_live' | 'probe_online' | 'batch_proxy' | 'mixed' | 'unknown' | null
  success_rate: number | null
  repro_runs_ok: number
  repro_wer_ci_width: number | null
  score: number
  score_breakdown: Record<string, number>
}

export interface TuningDecisionReport {
  job_id: string
  status: string
  hardware_profile: string | null
  hardware_note: string | null
  load_profile?: string | null
  load_cpu_target_pct?: number | null
  load_ram_target_pct?: number | null
  constraints_profile?: string | null
  constraints_cpu_cores?: number | null
  constraints_ram_limit_mb?: number | null
  constraints_priority?: string | null
  constraints_ram_mode?: 'hard' | 'soft' | 'none' | null
  require_repro_n?: number
  repro_validation?: {
    required_n?: number
    checked_top_k?: number
    passed?: boolean
    missing_trial_idxs?: number[]
    reason?: string
  }
  selected_lane?: 'strict_live' | 'probe_online' | 'batch_proxy' | 'mixed' | 'unknown' | null
  selected_pool:
    | 'strict_live'
    | 'probe_online_fallback'
    | 'batch_proxy_fallback'
    | 'mixed_fallback'
    | 'unknown_fallback'
    | 'fallback_all'
  lane_counts?: Record<string, number>
  best: TuningDecisionCandidate | null
  top: TuningDecisionCandidate[]
  error: string | null
}

export interface TuningEventItem {
  seq: number
  ts_utc: string
  event_type: string
  payload: Record<string, unknown>
}

export interface TuningEventStats {
  exists: boolean
  count: number
  max_seq: number
  event_types: Record<string, number>
}

export interface TuningEventValidation {
  ok: boolean
  issues: string[]
  count: number
  first_seq: number
  last_seq: number
  terminal_event: string | null
  terminal_seq: number | null
  post_terminal_events: number
  event_type_counts: Record<string, number>
  status_values: string[]
}

export interface TuningEventsResponse {
  job_id: string
  event_db: string
  after_seq: number
  next_after_seq: number
  count: number
  events: TuningEventItem[]
  stats: TuningEventStats
  validation?: TuningEventValidation
}

export type TuningInputMode = 'replay' | 'real_mic'

export interface TuningMicProtocol {
  distance_cm: number
  phone_volume_pct: number
  input_gain_pct: number
  environment: 'quiet' | 'office_noise'
  device_note?: string | null
}

export interface TuningMicCalibration {
  rms_dbfs: number
  clipping_rate_pct: number
  noise_floor_dbfs: number
  passed: boolean
  checked_at?: string | null
  reasons?: string[]
}

export interface TuningMicCalibrationCheckResponse {
  passed: boolean
  reasons: string[]
  thresholds: Record<string, number>
  metrics: Record<string, number>
}

export interface TuningJobStatus {
  job_id: string
  status: string
  model_id: string
  model_ids: string[]
  input_mode?: TuningInputMode
  strategy?: string | null
  label: string | null
  hardware_profile?: string | null
  hardware_note?: string | null
  hardware_info?: TuningHardwareInfo
  constraints_profile?: string | null
  constraints_cpu_cores?: number | null
  constraints_ram_limit_mb?: number | null
  constraints_priority?: string | null
  constraints_applied?: boolean | null
  constraints_warnings?: string[]
  constraints_ram_mode?: 'hard' | 'soft' | 'none' | null
  constraints_cpu_applied?: boolean | null
  constraints_priority_applied?: boolean | null
  constraints_ram_hard_cap_applied?: boolean | null
  constraints_ram_hard_cap_error?: string | null
  load_profile?: string | null
  load_cpu_target_pct?: number | null
  load_ram_target_pct?: number | null
  validate_beam_preflight?: boolean
  mic_protocol?: TuningMicProtocol | null
  mic_calibration?: TuningMicCalibration | null
  mic_device?: number | string | null
  mic_chunk_seconds?: number | null
  mic_prepare_seconds?: number | null
  created_at: string
  total_trials: number
  completed_trials: number
  results: TuningTrialResult[]
  error: string | null
  best_trial_idx: number | null
  progress_message: string | null
  audio_ready: string[]
  updated_ts?: string  // timestamp poslední aktualizace workeru (pro detekci zaseknutí)
  reproducibility?: TuningReproducibilityItem[]
  repro_validation?: {
    required_n?: number
    checked_top_k?: number
    passed?: boolean
    missing_trial_idxs?: number[]
    reason?: string
  }
}
