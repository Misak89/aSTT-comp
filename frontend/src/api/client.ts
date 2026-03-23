import type {
  LibraryItem, LatestResult, BenchmarkJobRequest, BenchmarkJobStatus,
  BenchmarkOptions, Scenario, RunDetail, LiveJobProgress, ModelStatus,
  ModelDescriptor, MicSessionState, AudioDevice,
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
    openJobDir: (id: string) => post<{ path: string }>(`/benchmark/jobs/${id}/open-dir`),
  },
  runs: {
    get: (id: string) => get<RunDetail>(`/runs/${id}`),
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
    openLogsDir: () => post<{ path: string }>('/models/open-logs-dir'),
    openStoreDir: (id: string) => post<{ path: string }>(`/models/${id}/open-store-dir`),
  },
  mic: {
    devices: () => get<AudioDevice[]>('/mic/devices'),
    createSession: (model_id: string, model_params?: Record<string, unknown>) =>
      post<{ session_id: string; model_id: string; created_at: string }>('/mic/sessions', { model_id, model_params }),
    getSession: (id: string) => get<MicSessionState>(`/mic/sessions/${id}`),
    stopSession: (id: string) => post<MicSessionState>(`/mic/sessions/${id}/stop`),
  },
}
