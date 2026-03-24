import type {
  LibraryItem, LatestResult, BenchmarkJobRequest, BenchmarkJobStatus,
  BenchmarkOptions, Scenario, RunDetail, LiveJobProgress, ModelStatus,
  ModelDescriptor, MicSessionState, AudioDevice, TuningJobStatus,
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

// Library
export const api = {
  library: {
    list: () => get<LibraryItem[]>('/library/items'),
    upsert: (item: Partial<LibraryItem> & { video_id: string; title: string; url: string }) =>
      post<LibraryItem>('/library/items', item),
    downloadSubtitles: (video_id: string, url: string) =>
      post('/library/download-subtitles', { video_id, url }),
    latestResults: (video_id: string) =>
      get<LatestResult[]>(`/library/latest-results/${video_id}`),
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
    openLogsDir: () => post<{ path: string }>('/open-dir/models_log'),
    openStoreDir: (id: string) => post<{ path: string }>(`/open-dir/model_store/${id}`),
  },
  tuning: {
    createJob: (req: unknown) => post<TuningJobStatus>('/tuning/jobs', req),
    listJobs: () => get<TuningJobStatus[]>('/tuning/jobs'),
    getJob: (id: string) => get<TuningJobStatus>(`/tuning/jobs/${id}`),
  },
  mic: {
    devices: () => get<AudioDevice[]>('/mic/devices'),
    createSession: (model_id: string, model_params?: Record<string, unknown>) =>
      post<{ session_id: string; model_id: string; created_at: string }>('/mic/sessions', { model_id, model_params }),
    getSession: (id: string) => get<MicSessionState>(`/mic/sessions/${id}`),
    stopSession: (id: string) => post<MicSessionState>(`/mic/sessions/${id}/stop`),
  },
}
