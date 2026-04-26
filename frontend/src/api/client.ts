import type {
  LibraryItem, LatestResult, BenchmarkJobRequest, BenchmarkJobStatus,
  BenchmarkOptions, Scenario, RunDetail, LiveJobProgress, ModelStatus,
  ModelDescriptor, MicSessionState, AudioDevice, TuningJobStatus, YTSearchResult,
  TuningDecisionReport,
  TuningEventsResponse,
  TuningMicCalibrationCheckResponse,
  WebAppAutostartStatus,
  SpecstoryLiveStatus,
  AppProcessSnapshot,
  MicOrchestratorV7Health,
  MicManualRecordRequest,
  MicManualRecordResponse,
  MicManualRecordListResponse,
  MicManualRecordDeleteResponse,
  MicManualRecordBulkDeleteResponse,
  MicMobileLoopPackageRequest,
  MicMobileLoopPackageResponse,
  MicMobileLoopPackageListResponse,
  MicMobileLoopPackageDeleteResponse,
  MicClientSequenceEventRequest,
  MicClientSequenceEventResponse,
  MicSequenceReport,
  MicSequenceReadinessResponse,
  LocalFileEntry,
  SegmentBundle,
  SegmentBundlePreviewRequest,
} from '../types'

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`)
  return r.json()
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body != null ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    const detail = await r.text()
    throw new Error(`POST ${path} → ${r.status}: ${detail}`)
  }
  return r.json()
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`PUT ${path} → ${r.status}`)
  return r.json()
}

async function del(path: string): Promise<void> {
  const r = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  if (!r.ok && r.status !== 204) throw new Error(`DELETE ${path} → ${r.status}`)
}

async function delJson<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  if (!r.ok) {
    const detail = await r.text()
    throw new Error(`DELETE ${path} → ${r.status}: ${detail}`)
  }
  return r.json()
}

// Library
export const api = {
  health: {
    specstory: () => get<SpecstoryLiveStatus>('/health/specstory'),
    processes: (mode?: 'fast' | 'slow' | 'full') =>
      get<AppProcessSnapshot>(`/health/processes${mode ? `?mode=${encodeURIComponent(mode)}` : ''}`),
    micOrchestratorV7: (opts?: { max_reports?: number; max_events?: number }) => {
      const q = new URLSearchParams()
      if (opts?.max_reports != null) q.set('max_reports', String(opts.max_reports))
      if (opts?.max_events != null) q.set('max_events', String(opts.max_events))
      const suffix = q.toString() ? `?${q.toString()}` : ''
      return get<MicOrchestratorV7Health>(`/health/mic-orchestrator-v7${suffix}`)
    },
    cleanupStalePids: () =>
      post<{ status: string; removed_count: number; kept_count: number; error_count: number; removed: string[]; kept: string[]; errors: string[] }>(
        '/health/processes/cleanup-stale-pids'
      ),
  },
  library: {
    list: () => get<LibraryItem[]>('/library/items'),
    upsert: (item: Partial<LibraryItem> & { video_id: string; title: string; url: string }) =>
      post<LibraryItem>('/library/items', item),
    setVisibility: (video_id: string, visible_in_menus: boolean) =>
      post<LibraryItem>(`/library/items/${video_id}/visibility`, { visible_in_menus }),
    downloadSubtitles: (video_id: string, url: string) =>
      post('/library/download-subtitles', { video_id, url }),
    latestResults: (video_id: string) =>
      get<LatestResult[]>(`/library/latest-results/${video_id}`),
    search: (params: {
      q: string; max_results?: number; min_duration?: number; max_duration?: number;
      min_views?: number; uploaded_after?: string; audio_langs?: string[];
      subtitle_langs?: string[]; subtitle_type?: string; content_type?: string; categories?: string[];
    }) => post<YTSearchResult[]>('/library/search', params),
    fetchVideoInfo: (url: string) =>
      get<{ title: string; language: string; duration_seconds: number | null; uploader: string }>(
        `/library/video-info?url=${encodeURIComponent(url)}`
      ),
    scanDirectory: (path: string) =>
      post<LocalFileEntry[]>('/library/scan-directory', { path }),
    importLocalFile: (path: string, title: string, language: string) =>
      post<LibraryItem>('/library/import-local-file', { path, title, language }),
    previewSegmentBundle: (req: SegmentBundlePreviewRequest) =>
      post<SegmentBundle>('/library/segment-bundles/preview', req),
    upsertSegmentBundle: (sourceId: string, req: SegmentBundlePreviewRequest) =>
      put<SegmentBundle>(`/library/segment-bundles/${encodeURIComponent(sourceId)}`, req),
    getSegmentBundle: (sourceId: string) =>
      get<SegmentBundle>(`/library/segment-bundles/${encodeURIComponent(sourceId)}`),
  },
  benchmark: {
    options: () => get<BenchmarkOptions>('/benchmark/options'),
    createJob: (req: BenchmarkJobRequest) =>
      post<BenchmarkJobStatus>('/benchmark/jobs', req),
    listJobs: () => get<{ jobs: BenchmarkJobStatus[] }>('/benchmark/jobs'),
    getJob: (id: string) => get<BenchmarkJobStatus>(`/benchmark/jobs/${id}`),
    cancelJob: (id: string) => post<BenchmarkJobStatus>(`/benchmark/jobs/${id}/cancel`),
    listScenarios: () => get<Scenario[]>('/benchmark/scenarios'),
    saveScenario: (sc: Scenario) => post<Scenario>('/benchmark/scenarios', sc),
    deleteScenario: (id: string) => del(`/benchmark/scenarios/${id}`),
    getLive: (id: string) => get<LiveJobProgress>(`/benchmark/jobs/${id}/live`),
    openJobDir: (id: string) => post<{ path: string }>(`/open-dir/jobs/${id}`),
  },
  runs: {
    get: (id: string) => get<RunDetail>(`/runs/${id}`),
    openDir: (id: string) => post<{ path: string }>(`/open-dir/runs/${id}`),
    wordDiff: (runId: string, resultIdx: number, sourceIdx: number) =>
      get<{ run_id: string; model_id: string; setting_id: string; diff: {op: string; ref: string|null; hyp: string|null}[]; stats: {total: number; correct: number; substitutions: number; deletions: number; insertions: number} }>(`/runs/${runId}/results/${resultIdx}/sources/${sourceIdx}/diff`),
  },
  openDir: {
    jobs: () => post<{ path: string }>('/open-dir/jobs'),
    runs: () => post<{ path: string }>('/open-dir/runs'),
    modelStore: () => post<{ path: string }>('/open-dir/model_store'),
    modelsLog: () => post<{ path: string }>('/open-dir/models_log'),
    loggerLogs: () => post<{ path: string }>('/open-dir/logger_logs'),
    subtitles: () => post<{ path: string }>('/open-dir/subtitles'),
    audioCache: () => post<{ path: string }>('/open-dir/audio_cache'),
    subtitlesVideo: (videoId: string) => post<{ path: string }>(`/open-dir/subtitles/${videoId}`),
  },
  models: {
    list: () => get<ModelStatus[]>('/models'),
    get: (id: string) => get<ModelStatus>(`/models/${id}`),
    registry: () => get<ModelDescriptor[]>('/models/registry'),
    params: (id: string) => get<{ model_id: string; supports_streaming: boolean; supports_microphone: boolean; params: ModelDescriptor['params'] }>(`/models/${id}/params`),
    recordInstall: (id: string, version?: string, size_mb?: number) =>
      post<ModelStatus>(`/models/${id}/install`, { version, size_mb }),
    recordUninstall: (id: string, reason?: string) =>
      del(`/models/${id}`),
    addNote: (id: string, note: string) =>
      post<ModelStatus>(`/models/${id}/note`, { note }),
    getWebAppAutostart: () => get<WebAppAutostartStatus>('/models/webapp-autostart'),
    enableWebAppAutostart: () => post<WebAppAutostartStatus>('/models/webapp-autostart/enable'),
    disableWebAppAutostart: () => post<WebAppAutostartStatus>('/models/webapp-autostart/disable'),
    openWebAppStartupDir: () => post<{ path: string }>('/models/webapp-autostart/open-startup-dir'),
    openLogsDir: () => post<{ path: string }>('/open-dir/models_log'),
    openStoreDir: (id: string) => post<{ path: string }>(`/open-dir/model_store/${id}`),
  },
  tuning: {
    createJob: (req: unknown) => post<TuningJobStatus>('/tuning/jobs', req),
    listJobs: () => get<TuningJobStatus[]>('/tuning/jobs'),
    getJob: (id: string) => get<TuningJobStatus>(`/tuning/jobs/${id}`),
    checkMicCalibration: (metrics: { rms_dbfs: number; clipping_rate_pct: number; noise_floor_dbfs: number }) =>
      post<TuningMicCalibrationCheckResponse>('/tuning/mic-calibration/check', metrics),
    decisionReport: (id: string, opts?: { min_success_rate?: number; max_rtf?: number; allow_proxy?: boolean; require_repro_n?: number; top?: number }) => {
      const q = new URLSearchParams()
      if (opts?.min_success_rate != null) q.set('min_success_rate', String(opts.min_success_rate))
      if (opts?.max_rtf != null) q.set('max_rtf', String(opts.max_rtf))
      if (opts?.allow_proxy != null) q.set('allow_proxy', String(opts.allow_proxy))
      if (opts?.require_repro_n != null) q.set('require_repro_n', String(opts.require_repro_n))
      if (opts?.top != null) q.set('top', String(opts.top))
      const suffix = q.toString() ? `?${q.toString()}` : ''
      return get<TuningDecisionReport>(`/tuning/jobs/${id}/decision${suffix}`)
    },
    events: (id: string, opts?: { after_seq?: number; limit?: number; event_type?: string }) => {
      const q = new URLSearchParams()
      if (opts?.after_seq != null) q.set('after_seq', String(opts.after_seq))
      if (opts?.limit != null) q.set('limit', String(opts.limit))
      if (opts?.event_type) q.set('event_type', opts.event_type)
      const suffix = q.toString() ? `?${q.toString()}` : ''
      return get<TuningEventsResponse>(`/tuning/jobs/${id}/events${suffix}`)
    },
    cancelJob: (id: string) => post<TuningJobStatus>(`/tuning/jobs/${id}/cancel`),
    openJobDir: (id: string) => post<{ path: string }>(`/tuning/jobs/${id}/open-dir`),
  },
  transcribe: {
    upload: (file: File) => {
      const fd = new FormData(); fd.append('file', file)
      return fetch('/api/transcribe/upload', { method: 'POST', body: fd }).then(r => { if (!r.ok) throw new Error(r.statusText); return r.json() })
    },
    listTranscripts: () => get<{ transcript_id: string; title: string; created_at: string; updated_at: string; source_label?: string; model_id?: string; range_from?: string; range_to?: string; plain_text_preview?: string }[]>('/transcribe/transcripts'),
    getTranscript: (id: string) => get<{ transcript_id: string; title: string; html: string; created_at: string; updated_at: string; source_label?: string; model_id?: string }>(`/transcribe/transcripts/${id}`),
    saveTranscript: (req: { title: string; html: string; plain_text: string; transcript_id?: string; source_label?: string; model_id?: string; range_from?: string; range_to?: string }) =>
      post<{ transcript_id: string; updated_at: string }>('/transcribe/transcripts', req),
    deleteTranscript: (id: string) => del(`/transcribe/transcripts/${id}`),
    getSettings: () => get<Record<string, unknown>>('/transcribe/settings'),
    saveSettings: (settings: Record<string, unknown>) => post<{ ok: boolean }>('/transcribe/settings', settings),
  },
  mic: {
    devices: () => get<AudioDevice[]>('/mic/devices'),
    createSession: (model_id: string, model_params?: Record<string, unknown>) =>
      post<{
        session_id: string
        model_id: string
        created_at: string
        orchestrator_mode?: string | null
        run_id?: string | null
        sequence_id?: string | null
        sequence_index?: number | null
        sequence_total?: number | null
        event_contract_version?: string | null
        preflight_ok?: boolean
        preflight_errors?: string[]
        preflight_warnings?: string[]
      }>('/mic/sessions', { model_id, model_params }),
    getSession: (id: string) => get<MicSessionState>(`/mic/sessions/${id}`),
    stopSession: (id: string) => post<MicSessionState>(`/mic/sessions/${id}/stop`),
    listManualRecords: (opts?: { limit?: number; model_id?: string }) => {
      const q = new URLSearchParams()
      if (opts?.limit != null) q.set('limit', String(opts.limit))
      if (opts?.model_id) q.set('model_id', opts.model_id)
      const suffix = q.toString() ? `?${q.toString()}` : ''
      return get<MicManualRecordListResponse>(`/mic/manual-records${suffix}`)
    },
    saveManualRecord: (req: MicManualRecordRequest) =>
      post<MicManualRecordResponse>('/mic/manual-records', req),
    deleteManualRecord: (recordId: string) =>
      delJson<MicManualRecordDeleteResponse>(`/mic/manual-records/${encodeURIComponent(recordId)}`),
    clearManualRecords: (opts?: { model_id?: string; mic_test_mode?: 'free_speech' | 'reference_video' | 'unknown' }) => {
      const q = new URLSearchParams()
      if (opts?.model_id) q.set('model_id', opts.model_id)
      if (opts?.mic_test_mode) q.set('mic_test_mode', opts.mic_test_mode)
      const suffix = q.toString() ? `?${q.toString()}` : ''
      return delJson<MicManualRecordBulkDeleteResponse>(`/mic/manual-records${suffix}`)
    },
    createMobileLoopPackage: (req: MicMobileLoopPackageRequest) =>
      post<MicMobileLoopPackageResponse>('/mic/mobile-loop-packages', req),
    listMobileLoopPackages: (opts?: { limit?: number; video_id?: string }) => {
      const q = new URLSearchParams()
      if (opts?.limit != null) q.set('limit', String(opts.limit))
      if (opts?.video_id) q.set('video_id', opts.video_id)
      const suffix = q.toString() ? `?${q.toString()}` : ''
      return get<MicMobileLoopPackageListResponse>(`/mic/mobile-loop-packages${suffix}`)
    },
    deleteMobileLoopPackage: (packageId: string) =>
      delJson<MicMobileLoopPackageDeleteResponse>(`/mic/mobile-loop-packages/${encodeURIComponent(packageId)}`),
    logSequenceEvent: (req: MicClientSequenceEventRequest) =>
      post<MicClientSequenceEventResponse>('/mic/sequence-events', req),
    getSequenceReport: (token: string) =>
      get<MicSequenceReport>(`/mic/sequences/${encodeURIComponent(token)}`),
    getSequenceReadiness: (token: string, min_models = 3) =>
      get<MicSequenceReadinessResponse>(
        `/mic/sequences/${encodeURIComponent(token)}/readiness?min_models=${encodeURIComponent(String(min_models))}`
      ),
    getContract: () =>
      get<{ schema: string; version: string; required_fields_v7: string[]; reason_codes: string[] }>('/mic/contract'),
  },
}
