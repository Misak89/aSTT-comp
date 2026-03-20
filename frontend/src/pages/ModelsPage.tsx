import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ModelStatus, ModelEvent } from '../types'

export function ModelsPage() {
  const [models, setModels] = useState<ModelStatus[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [noteInput, setNoteInput] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')

  useEffect(() => { loadModels() }, [])

  async function loadModels() {
    try {
      const data = await api.models.list()
      setModels(data)
    } catch (e: any) {
      setMsg(`Chyba načítání: ${e.message}`)
    }
  }

  async function recordInstall(id: string) {
    try {
      await api.models.recordInstall(id)
      await loadModels()
    } catch (e: any) { setMsg(`Chyba: ${e.message}`) }
  }

  async function recordUninstall(id: string) {
    try {
      await api.models.recordUninstall(id)
      await loadModels()
    } catch (e: any) { setMsg(`Chyba: ${e.message}`) }
  }

  async function addNote(id: string) {
    const note = noteInput[id]?.trim()
    if (!note) return
    try {
      await api.models.addNote(id, note)
      setNoteInput(prev => ({ ...prev, [id]: '' }))
      await loadModels()
    } catch (e: any) { setMsg(`Chyba: ${e.message}`) }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Modely</h1>
        <button onClick={loadModels} className="text-xs text-blue-600 hover:underline">Obnovit</button>
      </div>

      {msg && <p className="text-sm text-red-600">{msg}</p>}

      <div className="space-y-3">
        {models.map(m => (
          <div key={m.model_id} className="bg-white rounded border border-gray-200">
            {/* Hlavička modelu */}
            <div className="px-4 py-3 flex items-center gap-4 flex-wrap">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">{m.label}</span>
                  <span className="font-mono text-xs text-gray-400">{m.model_id}</span>
                </div>
                <div className="flex items-center gap-3 mt-0.5 text-xs text-gray-500">
                  {m.installed
                    ? <>
                        <span className="text-green-600 font-medium">Nainstalován</span>
                        {m.size_mb != null && <span>{m.size_mb} MB</span>}
                        {m.last_install && <span>od {m.last_install.slice(0, 10)}</span>}
                      </>
                    : <span className="text-gray-400">Nenainstalován</span>}
                </div>
              </div>

              {/* Akce */}
              <div className="flex items-center gap-2 text-xs">
                <button
                  onClick={() => recordInstall(m.model_id)}
                  className="bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 px-3 py-1 rounded"
                >
                  + Zaznamenat install
                </button>
                {m.installed && (
                  <button
                    onClick={() => recordUninstall(m.model_id)}
                    className="bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 px-3 py-1 rounded"
                  >
                    Odinstalovat
                  </button>
                )}
                <button
                  onClick={() => setExpanded(expanded === m.model_id ? null : m.model_id)}
                  className="text-gray-500 hover:text-gray-700 border border-gray-200 px-3 py-1 rounded"
                >
                  {expanded === m.model_id ? '▲ Log' : '▼ Log'}
                  {m.events.length > 0 && (
                    <span className="ml-1 bg-gray-100 text-gray-600 rounded-full px-1.5">{m.events.length}</span>
                  )}
                </button>
              </div>
            </div>

            {/* Rozbalený log */}
            {expanded === m.model_id && (
              <div className="border-t border-gray-100 px-4 py-3 space-y-3">
                {/* Přidat poznámku */}
                <div className="flex gap-2">
                  <input
                    value={noteInput[m.model_id] || ''}
                    onChange={e => setNoteInput(prev => ({ ...prev, [m.model_id]: e.target.value }))}
                    placeholder="Přidat poznámku..."
                    className="flex-1 border rounded px-2 py-1 text-xs"
                    onKeyDown={e => e.key === 'Enter' && addNote(m.model_id)}
                  />
                  <button
                    onClick={() => addNote(m.model_id)}
                    className="text-xs bg-gray-50 hover:bg-gray-100 border border-gray-200 px-3 py-1 rounded"
                  >
                    Přidat
                  </button>
                </div>

                {/* Timeline eventů */}
                {m.events.length === 0 ? (
                  <p className="text-xs text-gray-400">Žádné záznamy.</p>
                ) : (
                  <div className="space-y-1">
                    {[...m.events].reverse().map((ev, i) => (
                      <EventRow key={i} ev={ev} />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

        {models.length === 0 && !msg && (
          <p className="text-gray-400 text-sm">Načítání modelů...</p>
        )}
      </div>

      {/* Nápověda */}
      <div className="bg-gray-50 rounded border border-gray-200 p-4 text-xs text-gray-500 space-y-1">
        <p className="font-medium text-gray-700">Jak nainstalovat model</p>
        <p>Modely se instalují manuálně do <span className="font-mono">runtime/model_store/&#123;model_id&#125;/</span></p>
        <p>Po instalaci klikni <strong>Zaznamenat install</strong> — zaznamená se datum, velikost a verze do logu.</p>
        <p>Log se ukládá do <span className="font-mono">docs/models/&#123;model_id&#125;.json</span></p>
      </div>
    </div>
  )
}

function EventRow({ ev }: { ev: ModelEvent }) {
  const icons: Record<string, string> = {
    install: '⬇',
    uninstall: '✕',
    note: '📝',
  }
  const colors: Record<string, string> = {
    install: 'text-green-700 bg-green-50 border-green-200',
    uninstall: 'text-red-700 bg-red-50 border-red-200',
    note: 'text-gray-700 bg-gray-50 border-gray-200',
  }

  return (
    <div className={`flex items-start gap-2 text-xs rounded border px-2 py-1.5 ${colors[ev.type] || colors.note}`}>
      <span className="mt-0.5">{icons[ev.type] || '•'}</span>
      <div className="flex-1 min-w-0">
        <span className="font-medium capitalize">{ev.type}</span>
        {ev.version && <span className="ml-2 text-gray-500">v{ev.version}</span>}
        {ev.size_mb != null && <span className="ml-2 text-gray-500">{ev.size_mb} MB</span>}
        {ev.reason && <span className="ml-2 text-gray-500">({ev.reason})</span>}
        {ev.note && <span className="ml-2">{ev.note}</span>}
      </div>
      <span className="text-gray-400 whitespace-nowrap">{ev.date.slice(0, 16).replace('T', ' ')}</span>
    </div>
  )
}
