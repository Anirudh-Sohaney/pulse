import { useEffect, useState } from 'react'
import { ArrowUpRight, Search } from 'lucide-react'
import { apiFetch } from '../api'
import { utcDateTime } from '../utcDate'

const PAGE_SIZE = 25
const REFRESH_MS = 5 * 60_000

type Definition = {
  id: string
  signal_id: string
  unit: string | null
  source_name: string | null
  source_url: string | null
  geography_id: string
  entity_key: string
}
type CatalogPage = { signals: Definition[]; count: number; filtered_total: number }
type LatestValue = {
  id: string
  value: number
  observation_date: string
  source_timestamp: string
  ingested_at: string
  source_url: string | null
  data_quality: string | null
}

function displayValue(value: number, unit: string | null): string {
  if (unit === 'share') return `${(value * 100).toFixed(1)}%`
  if (unit === 'percent_outpatient_visits') return `${value.toFixed(2)}%`
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)} ${unit?.replace(/_/g, ' ') ?? ''}`.trim()
}

export default function SourceSignalTable() {
  const [search, setSearch] = useState('')
  const [committedSearch, setCommittedSearch] = useState('')
  const [page, setPage] = useState(0)
  const [catalog, setCatalog] = useState<CatalogPage | null>(null)
  const [latest, setLatest] = useState<Record<string, LatestValue>>({})
  const [error, setError] = useState('')

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(0)
      setCommittedSearch(search.trim())
    }, 300)
    return () => window.clearTimeout(timer)
  }, [search])

  useEffect(() => {
    let active = true
    let requestNumber = 0
    const refresh = async () => {
      const currentRequest = ++requestNumber
      try {
        const result: CatalogPage = await apiFetch(
          `/v1/signals/catalog?origin=model_external_state_feature&search=${encodeURIComponent(committedSearch)}&limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`)
        const response: { values: LatestValue[] } = result.signals.length
          ? await apiFetch('/v1/signals/latest', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ids: result.signals.map(row => row.id) }),
            })
          : { values: [] }
        if (!active || currentRequest !== requestNumber) return
        setCatalog(result)
        setLatest(Object.fromEntries(response.values.map(row => [row.id, row])))
        setError('')
      } catch (cause) {
        if (!active || currentRequest !== requestNumber) return
        setCatalog(null)
        setLatest({})
        setError(cause instanceof Error ? cause.message : 'Could not load source signals')
      }
    }
    refresh()
    const timer = window.setInterval(refresh, REFRESH_MS)
    const onVisible = () => { if (!document.hidden) refresh() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      active = false
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [committedSearch, page])

  return <section className="paper-card overflow-hidden rounded-2xl">
    <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200 p-6">
      <div><h2 className="font-serif text-3xl text-ink">Public source signals</h2><p className="mt-1 text-sm text-slate-500">{catalog?.filtered_total ?? '—'} matching source observations · source periods are shown separately from capture times</p></div>
      <label className="flex w-full max-w-xs items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm"><Search className="h-4 w-4 text-slate-400" /><input value={search} maxLength={120} onChange={event => setSearch(event.target.value)} placeholder="Search source signals" className="w-full bg-transparent outline-none" aria-label="Search source signals" /></label>
    </div>
    {error && <p role="alert" className="border-b border-red-200 bg-red-50 px-6 py-4 text-sm text-red-700">{error}</p>}
    <div className="overflow-x-auto"><table className="w-full text-sm"><thead className="bg-slate-50 text-slate-600"><tr><th className="px-6 py-3 text-left">Signal</th><th className="px-6 py-3 text-left">Latest value</th><th className="px-6 py-3 text-left">Source period</th><th className="px-6 py-3 text-left">Source or retrieval time</th><th className="px-6 py-3 text-left">Recorded</th></tr></thead><tbody className="divide-y divide-slate-100">
      {catalog?.signals.map(definition => {
        const value = latest[definition.id]
        return <tr key={definition.id}>
          <td className="px-6 py-3"><p className="font-medium text-ink">{definition.signal_id.replace(/_/g, ' ')}</p><p className="mt-1 text-xs text-slate-500">{definition.geography_id} · {definition.source_name ?? 'Public source'}</p><p className="mt-1 font-mono text-xs text-slate-500">{definition.id}</p>{(value?.source_url || definition.source_url) && <a className="mt-1 inline-flex items-center gap-1 text-xs text-primary-800 underline" href={value?.source_url || definition.source_url || undefined} target="_blank" rel="noopener noreferrer">Source <ArrowUpRight className="h-3 w-3" /></a>}</td>
          <td className="px-6 py-3 font-medium text-ink">{value ? displayValue(value.value, definition.unit) : 'No usable value'}{value?.data_quality?.includes('publication_time_unknown') && <p className="mt-1 text-xs font-normal text-amber-800">Publication time unknown</p>}</td>
          <td className="px-6 py-3 text-slate-700">{value?.observation_date ?? '—'}</td>
          <td className="px-6 py-3 text-slate-600">{value?.source_timestamp ? utcDateTime(value.source_timestamp) : 'Unknown'}</td>
          <td className="px-6 py-3 text-slate-600">{value?.ingested_at ? utcDateTime(value.ingested_at) : '—'}</td>
        </tr>
      })}
    </tbody></table>{catalog && !catalog.count && <p className="p-8 text-center text-sm text-slate-500">No public source signals match this search.</p>}</div>
    <div className="flex items-center justify-between border-t border-slate-200 px-6 py-4 text-sm"><span className="text-slate-500">Page {page + 1} of {Math.max(1, Math.ceil((catalog?.filtered_total ?? 0) / PAGE_SIZE))}</span><div className="flex gap-2"><button disabled={page === 0} onClick={() => setPage(page - 1)} className="rounded-full border border-slate-200 px-4 py-2 disabled:opacity-40">Previous</button><button disabled={!catalog || (page + 1) * PAGE_SIZE >= catalog.filtered_total} onClick={() => setPage(page + 1)} className="rounded-full border border-slate-200 px-4 py-2 disabled:opacity-40">Next</button></div></div>
  </section>
}
