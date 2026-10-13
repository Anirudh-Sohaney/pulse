import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, Search } from 'lucide-react'
import { apiFetch } from '../api'
import { utcDate } from '../utcDate'

type News = { title: string; url: string; source: string; published_at: string; timestamp_kind: 'gdelt_first_seen' | 'rss_pub_date'; relevance_score: number }
type Projection = { drug_name: string; horizon_days: number; target_end: string; predicted_units_sold: number; trailing_28d_daily_units: number }
type Study = { drug_count: number; snapshot: { as_of_date: string; projections: Projection[] } }
type AnnualDrug = { id: string | null; drug_name: string; demand_state: number; selection_tier: string; observed_claims: number }
type Annual = { drugs: AnnualDrug[]; total: number; filtered_total: number; source_year?: number; target_year?: number; evaluation?: { mean_balanced_accuracy?: number } }

const horizons = [1, 7, 14, 30] as const
const annualPageSize = 25
const annualStates = [
  { label: 'Lowest', color: 'bg-slate-100 text-slate-700', bar: 'bg-slate-300' },
  { label: 'Low', color: 'bg-sky-100 text-sky-900', bar: 'bg-sky-400' },
  { label: 'Middle', color: 'bg-teal-100 text-teal-900', bar: 'bg-teal-500' },
  { label: 'High', color: 'bg-amber-100 text-amber-900', bar: 'bg-amber-500' },
  { label: 'Highest', color: 'bg-rose-100 text-rose-900', bar: 'bg-rose-500' },
] as const

function cleanTitle(title: string, source: string): string {
  const decoded = new DOMParser().parseFromString(title, 'text/html').documentElement.textContent ?? title
  const clean = decoded.replace(/\s+/g, ' ').trim()
  const suffix = [' - ', ' | ', ' – '].find(separator => clean.toLocaleLowerCase().endsWith((separator + source).toLocaleLowerCase()))
  return suffix ? clean.slice(0, -(suffix.length + source.length)).trim() : clean
}

function projectionTone(row: Projection): { className: string; label: string; ratio: number | null } {
  const baseline = row.trailing_28d_daily_units * row.horizon_days
  const ratio = baseline > 0 ? row.predicted_units_sold / baseline : null
  if (ratio === null) return { className: 'bg-slate-100 text-slate-800', label: 'No recent-sales comparison', ratio }
  if (ratio >= 1.25) return { className: 'bg-rose-100 text-rose-900', label: 'Well above recent pace', ratio }
  if (ratio >= 1.05) return { className: 'bg-amber-100 text-amber-900', label: 'Above recent pace', ratio }
  if (ratio <= 0.8) return { className: 'bg-sky-100 text-sky-900', label: 'Below recent pace', ratio }
  return { className: 'bg-teal-100 text-teal-900', label: 'Near recent pace', ratio }
}

export default function SignalsPage() {
  const [news, setNews] = useState<News[]>([])
  const [newsError, setNewsError] = useState('')
  const [feedIncomplete, setFeedIncomplete] = useState(false)
  const [study, setStudy] = useState<Study | null>(null)
  const [studyError, setStudyError] = useState('')
  const [studySearch, setStudySearch] = useState('')
  const [annualSearch, setAnnualSearch] = useState('')
  const [annualQuery, setAnnualQuery] = useState('')
  const [annualPage, setAnnualPage] = useState(0)
  const [annual, setAnnual] = useState<Annual | null>(null)
  const [annualError, setAnnualError] = useState('')

  useEffect(() => {
    let active = true
    const refresh = () => {
      apiFetch('/v1/signals/news/recent?days=7').then((result: { articles: News[]; source_checks?: Record<string, { last_status: string }> }) => {
        if (!active) return
        setNews(result.articles.filter(article => /^https?:\/\//i.test(article.url))
          .sort((a, b) => b.relevance_score - a.relevance_score || Date.parse(b.published_at) - Date.parse(a.published_at)))
        setFeedIncomplete(Object.values(result.source_checks ?? {}).some(check => check.last_status === 'failed'))
        setNewsError('')
      }).catch(() => { if (active) setNewsError('News could not be loaded right now.') })
      apiFetch('/v1/signals/demand/horizon-study').then((result: Study) => {
        if (active) { setStudy(result); setStudyError('') }
      }).catch(() => { if (active) setStudyError('The synthetic projection study could not be loaded.') })
    }
    refresh()
    const timer = window.setInterval(refresh, 5 * 60_000)
    const onVisible = () => { if (!document.hidden) refresh() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { active = false; window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => { setAnnualPage(0); setAnnualQuery(annualSearch.trim()) }, 300)
    return () => window.clearTimeout(timer)
  }, [annualSearch])

  useEffect(() => {
    let active = true
    const refresh = () => apiFetch(`/v1/signals/demand/drugs?search=${encodeURIComponent(annualQuery)}&limit=${annualPageSize}&offset=${annualPage * annualPageSize}`)
      .then((result: Annual) => { if (active) { setAnnual(result); setAnnualError('') } })
      .catch(() => { if (active) setAnnualError('Annual predictions could not be loaded right now.') })
    refresh()
    const timer = window.setInterval(refresh, 5 * 60_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [annualQuery, annualPage])

  const projectionRows = useMemo(() => {
    const byDrug = new Map<string, Map<number, Projection>>()
    for (const row of study?.snapshot?.projections ?? []) {
      if (!byDrug.has(row.drug_name)) byDrug.set(row.drug_name, new Map())
      byDrug.get(row.drug_name)?.set(row.horizon_days, row)
    }
    return [...byDrug].sort(([a], [b]) => a.localeCompare(b))
      .filter(([name]) => name.toLocaleLowerCase().includes(studySearch.trim().toLocaleLowerCase()))
  }, [study, studySearch])

  return <div className="space-y-8">
    <header className="max-w-4xl">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Evidence and projections</p>
      <h1 className="mt-3 font-serif text-5xl text-ink">Drug demand signals</h1>
      <p className="mt-4 text-sm leading-relaxed text-slate-600">Recent source-linked news, a historical 30-drug synthetic forecast study, and annual Arkansas Medicare Part D claim categories. Each section shows its own data source and timeframe.</p>
    </header>

    <section className="paper-card overflow-hidden rounded-2xl" aria-labelledby="news-heading">
      <div className="border-b border-slate-200 p-6 sm:p-8"><p className="text-xs font-semibold uppercase tracking-[0.15em] text-slate-500">Last seven days</p><h2 id="news-heading" className="mt-2 font-serif text-3xl text-ink">Relevant news</h2><p className="mt-2 text-sm text-slate-600">Pharmacy and public-health headlines ranked by keyword relevance. Articles provide context and do not establish a change in drug demand.</p></div>
      {feedIncomplete && <p className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-xs text-amber-900">One news feed did not refresh on its latest attempt. Headlines from available feeds remain shown.</p>}
      {newsError && <p role="alert" className="px-6 py-5 text-sm text-rose-800">{newsError}</p>}
      {!newsError && (news.length ? <div className="grid gap-px bg-slate-200 md:grid-cols-2 xl:grid-cols-3">{news.slice(0, 9).map(article => <a key={article.url} href={article.url} target="_blank" rel="noopener noreferrer" className="group flex min-h-40 flex-col justify-between bg-white p-6 transition hover:bg-slate-50"><div><p className="text-xs font-medium text-slate-500">{article.source} · {article.timestamp_kind === 'gdelt_first_seen' ? 'First seen' : 'Published'} {utcDate(article.published_at)}</p><h3 className="mt-3 text-base font-semibold leading-snug text-ink group-hover:text-primary-800">{cleanTitle(article.title, article.source)}</h3></div><span className="mt-4 inline-flex items-center gap-1 text-xs font-semibold text-primary-800">Read source <ArrowUpRight className="h-3.5 w-3.5" /></span></a>)}</div> : <p className="px-6 py-8 text-sm text-slate-600">No matching source-linked headlines were captured in the last seven days.</p>)}
    </section>

    <section className="paper-card overflow-hidden rounded-2xl" aria-labelledby="short-heading">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 p-6 sm:p-8"><div className="max-w-3xl"><p className="text-xs font-semibold uppercase tracking-[0.15em] text-slate-500">Historical synthetic pharmacy · as of {study?.snapshot?.as_of_date ?? '—'}</p><h2 id="short-heading" className="mt-2 font-serif text-3xl text-ink">30 drugs across four timeframes</h2><p className="mt-2 text-sm leading-relaxed text-slate-600">Each cell is projected cumulative units sold over the next 1, 7, 14, or 30 days after the snapshot date. These are saved model replays on synthetic data, not current Arkansas pharmacy forecasts.</p></div><label className="flex w-full max-w-xs items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm"><Search className="h-4 w-4 text-slate-400" /><input value={studySearch} onChange={event => setStudySearch(event.target.value)} placeholder="Find a drug" aria-label="Search 30-drug study" className="w-full bg-transparent outline-none" /></label></div>
      <div className="flex flex-wrap gap-x-5 gap-y-2 border-b border-slate-200 px-6 py-4 text-xs text-slate-600">{[
        ['bg-sky-100', 'Below recent pace'], ['bg-teal-100', 'Near recent pace'], ['bg-amber-100', 'Above recent pace'], ['bg-rose-100', 'Well above recent pace'], ['bg-slate-100', 'No comparison'],
      ].map(([color, label]) => <span key={label} className="inline-flex items-center gap-1.5"><span className={`h-3 w-3 rounded-sm ${color}`} />{label}</span>)}</div>
      {studyError && <p role="alert" className="p-6 text-sm text-rose-800">{studyError}</p>}
      {!studyError && !study && <p className="p-6 text-sm text-slate-600">Loading projection study…</p>}
      {study && <><div className="overflow-x-auto"><table className="w-full min-w-[720px] text-sm"><thead className="bg-slate-50 text-slate-600"><tr><th className="px-6 py-3 text-left font-medium">Drug</th>{horizons.map(days => <th key={days} className="px-3 py-3 text-center font-medium">Next {days === 1 ? 'day' : `${days} days`}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{projectionRows.map(([name, rows]) => <tr key={name}><th scope="row" className="px-6 py-3 text-left font-medium text-ink">{name}</th>{horizons.map(days => { const row = rows.get(days); const tone = row && projectionTone(row); return <td key={days} className="px-2 py-2 text-center">{row && tone ? <div className={`rounded-xl px-2 py-2.5 ${tone.className}`} title={`${tone.label}; target through ${row.target_end}`}><span className="block font-semibold tabular-nums">{Math.round(row.predicted_units_sold).toLocaleString()} units</span><span className="block text-xs opacity-80">{tone.ratio === null ? 'No baseline' : `${Math.round(tone.ratio * 100)}% of recent pace`}</span></div> : '—'}</td> })}</tr>)}</tbody></table>{projectionRows.length === 0 && <p className="p-6 text-sm text-slate-600">No drugs match this search.</p>}</div><div className="border-t border-slate-200 px-6 py-4 text-xs leading-relaxed text-slate-600">Color compares each projection with that drug’s average daily sales over the preceding 28 synthetic days. Longer windows are cumulative; cells should not be added together. The archived signals lack complete historical publication times, so the study assumes availability after source period end. <a href="/dashboard" className="font-semibold text-primary-800 underline">Use your own sales and inventory for a private forecast</a>.</div></>}
    </section>

    <section className="paper-card overflow-hidden rounded-2xl" aria-labelledby="annual-heading">
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 p-6 sm:p-8"><div className="max-w-3xl"><p className="text-xs font-semibold uppercase tracking-[0.15em] text-slate-500">Arkansas Medicare Part D · {annual?.source_year ?? '—'} claims → {annual?.target_year ?? '—'} projection</p><h2 id="annual-heading" className="mt-2 font-serif text-3xl text-ink">Annual predictions</h2><p className="mt-2 text-sm leading-relaxed text-slate-600">Relative claim-volume categories for {annual?.total?.toLocaleString() ?? '—'} drugs. A category describes projected annual Part D claims in Arkansas; it is not current dispensing or a count of units to buy.</p></div><label className="flex w-full max-w-xs items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm"><Search className="h-4 w-4 text-slate-400" /><input value={annualSearch} onChange={event => setAnnualSearch(event.target.value)} placeholder="Search annual predictions" aria-label="Search annual drug predictions" className="w-full bg-transparent outline-none" /></label></div>
      <div className="flex flex-wrap gap-2 border-b border-slate-200 px-6 py-4">{annualStates.map((state, index) => <span key={index} className={`rounded-full px-3 py-1 text-xs font-medium ${state.color}`}>{index} · {state.label}</span>)}</div>
      {annualError && <p role="alert" className="p-6 text-sm text-rose-800">{annualError}</p>}
      {!annualError && !annual && <p className="p-6 text-sm text-slate-600">Loading annual predictions…</p>}
      {annual && <><div className="divide-y divide-slate-100">{annual.drugs.map(drug => { const state = annualStates[drug.demand_state] ?? annualStates[0]; return <div key={drug.id ?? drug.drug_name} className="grid gap-2 px-6 py-3.5 sm:grid-cols-[minmax(0,1fr)_minmax(240px,0.8fr)] sm:items-center"><div><p className="font-medium text-ink">{drug.drug_name}</p><p className="mt-1 text-xs text-slate-500">{drug.observed_claims.toLocaleString()} claims in {annual.source_year} · {drug.selection_tier === 'high_history' ? 'Higher-history tier' : 'Stable-range tier'}</p></div><div className="flex items-center gap-3"><div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100" aria-hidden="true"><div className={`h-full rounded-full ${state.bar}`} style={{ width: `${(drug.demand_state + 1) * 20}%` }} /></div><span className={`w-28 shrink-0 rounded-full px-3 py-1 text-center text-xs font-semibold ${state.color}`}>{state.label} · {drug.demand_state}</span></div></div> })}{annual.drugs.length === 0 && <p className="p-6 text-sm text-slate-600">No annual predictions match this search.</p>}</div><div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-6 py-4 text-xs text-slate-600"><p>Showing {annual.drugs.length ? annualPage * annualPageSize + 1 : 0}–{annualPage * annualPageSize + annual.drugs.length} of {annual.filtered_total.toLocaleString()} · {annual.evaluation?.mean_balanced_accuracy ? `${(annual.evaluation.mean_balanced_accuracy * 100).toFixed(1)}% balanced accuracy across chronological holdouts` : 'Historical evaluation unavailable'}</p><div className="flex gap-2"><button disabled={annualPage === 0} onClick={() => setAnnualPage(page => page - 1)} className="rounded-full border border-slate-200 px-3 py-1.5 disabled:opacity-40">Previous</button><button disabled={(annualPage + 1) * annualPageSize >= annual.filtered_total} onClick={() => setAnnualPage(page => page + 1)} className="rounded-full border border-slate-200 px-3 py-1.5 disabled:opacity-40">Next</button></div></div></>}
    </section>
  </div>
}
