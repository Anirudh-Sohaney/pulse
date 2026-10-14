import { Link, useLocation } from 'react-router-dom'

type TopicId = 'start' | 'catalog' | 'values' | 'forecasts' | 'sources' | 'operations'

const topics: { id: TopicId; number: string; label: string; description: string }[] = [
  { id: 'start', number: '01', label: 'Getting started', description: 'Base URL, access, and your first request' },
  { id: 'catalog', number: '02', label: 'Find signals', description: 'Definitions, search, and identifiers' },
  { id: 'values', number: '03', label: 'Read values', description: 'Latest, history, and usability' },
  { id: 'forecasts', number: '04', label: 'Demand forecasts', description: 'Annual claims and horizon research' },
  { id: 'sources', number: '05', label: 'Source context', description: 'News, surveillance, and public datasets' },
  { id: 'operations', number: '06', label: 'Freshness & limits', description: 'Coverage, timestamps, and API limits' },
]

function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-2xl bg-[#172127] px-5 py-4 text-[12px] leading-6 text-[#e8f1ee] shadow-sm">
      <code>{children}</code>
    </pre>
  )
}

function Endpoint({ method = 'GET', path, children }: { method?: 'GET' | 'POST' | 'DELETE'; path: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-[#e2e6df] bg-white px-5 py-4 sm:px-6">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className={'rounded-md px-2 py-1 text-[10px] font-bold tracking-[0.12em] ' + (method === 'GET' ? 'bg-[#e3f1e8] text-[#27634a]' : method === 'POST' ? 'bg-[#e9ecf7] text-[#3b518a]' : 'bg-[#f7e9e4] text-[#9a4b35]')}>{method}</span>
        <code className="break-all text-[13px] font-semibold text-[#253638]">{path}</code>
      </div>
      <p className="mt-2 text-sm leading-6 text-[#60706d]">{children}</p>
    </div>
  )
}

function Section({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <div>
        <p className="text-[10px] font-bold uppercase tracking-[0.19em] text-[#6a8a75]">{eyebrow}</p>
        <h3 className="mt-1 font-serif text-2xl tracking-tight text-[#20332d]">{title}</h3>
      </div>
      {children}
    </section>
  )
}

function Callout({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-[#dbe7dc] bg-[#f0f6ef] px-5 py-4 text-sm leading-6 text-[#4e6257]">
      <strong className="block text-[#27543b]">{title}</strong>
      <div className="mt-1">{children}</div>
    </div>
  )
}

function StartTopic() {
  return (
    <div className="space-y-10">
      <Section eyebrow="Orientation" title="A public data API">
        <p className="text-sm leading-7 text-[#5b6a64]">Use the same origin as this website and prefix signal routes with <code>/api/v1</code>. Start with the catalog to discover IDs, then request values by ID. The <a className="font-semibold text-[#26704b] underline underline-offset-4" href="/docs">interactive API reference</a> shows the complete request and response schemas.</p>
        <div className="grid gap-3 sm:grid-cols-3">
          {[
            ['1', 'Discover', 'Find a signal definition and its stable ID.'],
            ['2', 'Request', 'Fetch latest values or a dated history.'],
            ['3', 'Interpret', 'Check usability, period, unit, and source.'],
          ].map(([number, title, detail]) => (
            <div key={number} className="rounded-2xl border border-[#dce5dc] bg-[#f8faf6] p-4">
              <span className="text-xs font-bold text-[#487c58]">{number} / 3</span>
              <strong className="mt-2 block text-sm text-[#24382e]">{title}</strong>
              <p className="mt-1 text-xs leading-5 text-[#65736a]">{detail}</p>
            </div>
          ))}
        </div>
      </Section>
      <Section eyebrow="First request" title="Explore the catalog">
        <CodeBlock>{'curl "/api/v1/signals/catalog?search=arkansas&limit=20"'}</CodeBlock>
        <p className="text-sm leading-7 text-[#5b6a64]">Each definition describes what a value means. A catalog entry is not itself evidence that a usable latest value exists. Page through results with <code>limit</code> and <code>offset</code>.</p>
      </Section>
      <Section eyebrow="Access" title="Authentication and API keys">
        <p className="text-sm leading-7 text-[#5b6a64]">Public read routes can be explored without an account. Signed-in users can create a read-only API key for scripted access. The complete key is shown only when created; store it securely.</p>
        <div className="space-y-2">
          <Endpoint method="POST" path="/api/auth/api-keys">Create a read-only key from an authenticated session.</Endpoint>
          <Endpoint path="/api/auth/api-keys">List your keys and their metadata.</Endpoint>
          <Endpoint method="DELETE" path="/api/auth/api-keys/{'{key_id}'}">Revoke a key you no longer use.</Endpoint>
        </div>
        <Callout title="For exploration">You can also issue requests and inspect schemas from <a className="font-semibold underline underline-offset-4" href="/docs">/docs</a>.</Callout>
      </Section>
    </div>
  )
}

function CatalogTopic() {
  return (
    <div className="space-y-10">
      <Section eyebrow="Discovery" title="Choose the right catalog">
        <div className="space-y-2">
          <Endpoint path="/api/v1/signals/catalog">Search the live catalog. Use <code>search</code>, <code>origin</code>, <code>limit</code> (1–1500), and <code>offset</code>. The response includes the page count and <code>filtered_total</code>.</Endpoint>
          <Endpoint path="/api/v1/signals/catalog/seed">Return the fixed set of 1,250 seed definitions. These are definitions, not 1,250 guaranteed current values.</Endpoint>
        </div>
        <CodeBlock>{'curl "/api/v1/signals/catalog?search=flu&limit=25&offset=0"'}</CodeBlock>
      </Section>
      <Section eyebrow="Definitions" title="What a signal ID means">
        <p className="text-sm leading-7 text-[#5b6a64]">A stable <code>sig_</code> ID identifies a measure together with its origin, cadence, geography, and entity. Read the definition before comparing two IDs: values may be observed statistics, scores, or predictions.</p>
        <div className="grid gap-3 sm:grid-cols-2">
          {[
            ['unit', 'The scale of the reported value.'],
            ['state_definition', 'The meaning of an ordinal state or category.'],
            ['forecast_horizon', 'How far ahead a forecast targets.'],
            ['source', 'Where the underlying observation came from.'],
          ].map(([field, meaning]) => (
            <div key={field} className="rounded-2xl border border-[#e2e6df] bg-white p-4">
              <code className="text-xs font-bold text-[#2d6650]">{field}</code>
              <p className="mt-1 text-xs leading-5 text-[#66736e]">{meaning}</p>
            </div>
          ))}
        </div>
        <Callout title="Search syntax">In catalog, drug-ranking, and SDUD search, <code>%</code> and <code>_</code> are treated as literal characters.</Callout>
      </Section>
    </div>
  )
}

function ValuesTopic() {
  return (
    <div className="space-y-10">
      <Section eyebrow="Current data" title="Latest is a quality decision">
        <div className="space-y-2">
          <Endpoint method="POST" path="/api/v1/signals/latest">Request 1–1500 IDs in a JSON <code>ids</code> array. Returns usable <code>values</code>, plus recorded, unusable, missing, and ambiguous results.</Endpoint>
          <Endpoint path="/api/v1/signals/today">Page through the latest snapshot with <code>limit</code> and <code>offset</code>. The snapshot date and each value's observation period can differ from today's date.</Endpoint>
        </div>
        <CodeBlock>{'curl -X POST "/api/v1/signals/latest" \\\n+  -H "Content-Type: application/json" \\\n+  -d \'{"ids":["YOUR_SIGNAL_ID"]}\''}</CodeBlock>
        <Callout title="Usable versus recorded"><code>values</code> contains values admitted for current use. <code>latest_recorded_values</code> includes the most recent stored row even when <code>usable: false</code>; inspect <code>unusable_recorded_values</code> and <code>missing_details</code> before assuming a missing value is zero.</Callout>
      </Section>
      <Section eyebrow="Past data" title="Request a dated history">
        <Endpoint method="POST" path="/api/v1/signals/history">Request up to 100 IDs with either <code>date</code> or <code>start_date</code> and <code>end_date</code>. Set <code>include_revisions</code> to inspect revised rows. Requests exceeding 10,000 source rows return HTTP 413.</Endpoint>
        <CodeBlock>{'curl -X POST "/api/v1/signals/history" \\\n+  -H "Content-Type: application/json" \\\n+  -d \'{"ids":["YOUR_SIGNAL_ID"],"start_date":"2026-01-01","end_date":"2026-03-31"}\''}</CodeBlock>
      </Section>
      <Section eyebrow="Interpretation" title="Read the period before the number">
        <p className="text-sm leading-7 text-[#5b6a64]">Use <code>observation_date</code> for the period the measurement describes, <code>source_timestamp</code> for when the source published or reported it, and <code>ingested_at</code> for when PULSE captured it. A newly ingested row can still describe an older period.</p>
        <p className="text-sm leading-7 text-[#5b6a64]">Ordinal demand states are relative categories. State <strong>4</strong> means the highest defined category; it does not mean four claims or four units to buy.</p>
      </Section>
    </div>
  )
}

function ForecastsTopic() {
  return (
    <div className="space-y-10">
      <Section eyebrow="Annual projections" title="Arkansas Medicare Part D claim states">
        <Endpoint path="/api/v1/signals/demand/drugs">Search and page annual drug projections with <code>search</code>, <code>limit</code> (up to 250), and <code>offset</code>. Results are relative claim-count categories on a 0–4 scale.</Endpoint>
        <CodeBlock>{'curl "/api/v1/signals/demand/drugs?search=metformin&limit=10"'}</CodeBlock>
        <Callout title="What the annual result represents">The published 2026 ranking uses 2024 Arkansas Medicare Part D claims as its source period. It is a two-year projection of relative claim categories, not current dispensing, inventory, or a purchasing quantity. Check the returned source and target years as data refreshes.</Callout>
      </Section>
      <Section eyebrow="Short horizons" title="Understand the horizon study">
        <Endpoint path="/api/v1/signals/demand/horizon-study">Read saved research for 1, 7, 14, and 30 day horizons across 30 selected drugs. The <code>drug_name</code> parameter narrows the drug rows returned.</Endpoint>
        <p className="text-sm leading-7 text-[#5b6a64]">These rows are a historical synthetic benchmark and replay. They are not live 1, 7, 14, or 30 day demand forecasts. Use the study to inspect model behavior, not to infer near-term units to purchase.</p>
      </Section>
      <Section eyebrow="Model status" title="Article-text signals are shadow estimates">
        <p className="text-sm leading-7 text-[#5b6a64]">The 20 article-text outputs use captured titles and summaries and have only ten matched training months. Their recorded estimates are available in history, but they are unvalidated and excluded from usable latest values and purchasing. If the prior month has no text, the shadow run estimates the current month-end from month-to-date articles.</p>
        <p className="text-sm leading-7 text-[#5b6a64]">The 18 legacy ATC outputs were backed only by a zero-filled feature vector and are also excluded from usable latest values. Public source observations remain available separately in the catalog.</p>
      </Section>
    </div>
  )
}

function SourcesTopic() {
  return (
    <div className="space-y-10">
      <Section eyebrow="News" title="Recent context and source links">
        <div className="space-y-2">
          <Endpoint path="/api/v1/signals/news/recent">Return captured articles and <code>source_checks</code> for 1–7 days with <code>days</code>. News relevance is keyword-based context, not evidence that an article changed a forecast.</Endpoint>
          <Endpoint path="/api/v1/signals/recent">Return a paged selection of recent public-source context. Use <code>days</code> and <code>limit</code> (up to 20).</Endpoint>
        </div>
        <Callout title="Publication timestamps">A GDELT timestamp can indicate first seen time, while an FDA RSS timestamp comes from <code>pubDate</code>. Check <code>timestamp_kind</code> before treating either as an event date.</Callout>
      </Section>
      <Section eyebrow="Public datasets" title="Inspect source observations">
        <div className="space-y-2">
          <Endpoint path="/api/v1/signals/sources/shortages/recent">Recent public shortage observations.</Endpoint>
          <Endpoint path="/api/v1/signals/sources/nadac/latest">Dated NADAC acquisition-rate context. It is not a live retail price.</Endpoint>
          <Endpoint path="/api/v1/signals/sources/sdud/latest">Observed Arkansas Medicaid prescription counts with search and paging. Suppressed unknown values remain unknown, not zero.</Endpoint>
        </div>
      </Section>
      <Section eyebrow="Surveillance" title="Compare like with like">
        <p className="text-sm leading-7 text-[#5b6a64]">FluView includes Arkansas and US weighted influenza-like illness measures. NNDSS provisional counts are separate fields; absent or suppressed counts should not be read as zero. CDC wastewater values are site means, not the CDC national median. Check <code>contributing_site_rows</code> when using a wastewater aggregate.</p>
        <p className="text-sm leading-7 text-[#5b6a64]">FDA enforcement counts cover reports dated in a 30 day window. They do not establish active recall status or inventory impact; the legacy <code>recall_active</code> output is unusable.</p>
      </Section>
    </div>
  )
}

function OperationsTopic() {
  return (
    <div className="space-y-10">
      <Section eyebrow="Data health" title="Freshness and coverage">
        <div className="space-y-2">
          <Endpoint path="/api/v1/signals/freshness">Inspect source and adapter refresh status, including the last successful attempt.</Endpoint>
          <Endpoint path="/api/v1/signals/coverage">Inspect coverage across signal definitions and observed values.</Endpoint>
          <Endpoint path="/api/v1/signals/coverage/summary">Get a compact coverage summary for dashboards and operational checks.</Endpoint>
        </div>
        <p className="text-sm leading-7 text-[#5b6a64]">A current target year can still rely on older source data. Check source periods and timestamps in the result. Labor and price context uses monthly BLS series mirrored by FRED; a failed context refresh leaves recorded values intact and does not change the drug projections.</p>
      </Section>
      <Section eyebrow="Traffic" title="Use limits deliberately">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-2xl border border-[#e2e6df] bg-white p-5"><strong className="text-sm text-[#263b30]">Public rate limit</strong><p className="mt-2 text-xs leading-6 text-[#66736e]">A 300-unit burst bucket refills at 5 units per second. Latest, history, and today cost 5 units; catalog and coverage cost 3; other routes cost 1. HTTP 429 includes <code>Retry-After</code>.</p></div>
          <div className="rounded-2xl border border-[#e2e6df] bg-white p-5"><strong className="text-sm text-[#263b30]">API keys</strong><p className="mt-2 text-xs leading-6 text-[#66736e]">Read-only keys have a 10,000-request daily limit and can expire no later than one year after creation. Keep requests bounded and page large catalogs.</p></div>
        </div>
      </Section>
      <Section eyebrow="Compatibility" title="Retired route">
        <Endpoint path="/api/v1/signals/demand/outlook">This route is retired and returns HTTP 410. Use the annual drug route or the saved horizon study according to your question.</Endpoint>
        <p className="text-sm leading-7 text-[#5b6a64]">Private CSV uploads used elsewhere in the site stay in the browser and are not part of the public signal API.</p>
      </Section>
    </div>
  )
}

const topicContent: Record<TopicId, React.ReactNode> = {
  start: <StartTopic />,
  catalog: <CatalogTopic />,
  values: <ValuesTopic />,
  forecasts: <ForecastsTopic />,
  sources: <SourcesTopic />,
  operations: <OperationsTopic />,
}

export default function ApiGuide() {
  const { hash } = useLocation()
  const requestedTopic = hash.slice(1)
  const activeIndex = Math.max(0, topics.findIndex((topic) => topic.id === requestedTopic))
  const active = topics[activeIndex]
  const next = topics[activeIndex + 1]

  return (
    <div className="page-rail pb-20 pt-10 sm:pt-14">
      <div className="mb-9 border-b border-[#dce2d8] pb-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-[#648468]">Developer documentation</p>
          <a href="/docs" className="rounded-full border border-[#b9ccbb] px-4 py-2 text-xs font-semibold text-[#315c3d] transition hover:bg-[#e8f2e7]">Interactive API reference ↗</a>
        </div>
        <h1 className="mt-4 font-serif text-4xl tracking-tight text-[#1b3028] sm:text-5xl">API guide</h1>
        <p className="mt-3 max-w-2xl text-sm leading-7 text-[#5c6c63]">A practical guide to finding signal IDs, reading recorded values, and interpreting demand and public-source data.</p>
      </div>

      <div className="lg:grid lg:grid-cols-[210px_minmax(0,1fr)] lg:items-start lg:gap-8">
        <aside className="mb-8 lg:sticky lg:top-6 lg:mb-0" aria-label="API guide topics">
          <p className="mb-3 hidden px-3 text-[10px] font-bold uppercase tracking-[0.18em] text-[#78887a] lg:block">Topics</p>
          <nav className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:block lg:space-y-1">
            {topics.map((topic) => (
              <Link
                key={topic.id}
                to={'/api-guide#' + topic.id}
                aria-current={topic.id === active.id ? 'page' : undefined}
                className={'group flex items-center gap-2 rounded-xl border px-3 py-2.5 text-left transition lg:w-full lg:items-start ' + (topic.id === active.id ? 'border-[#a9c8b0] bg-[#e8f2e7] text-[#24563a]' : 'border-transparent bg-white/70 text-[#637168] hover:border-[#d6e2d6] hover:text-[#294e38]')}
              >
                <span className="pt-0.5 text-[10px] font-bold opacity-70">{topic.number}</span>
                <span><span className="block whitespace-nowrap text-xs font-semibold lg:whitespace-normal">{topic.label}</span><span className="mt-0.5 hidden text-[10px] leading-4 opacity-75 lg:block">{topic.description}</span></span>
              </Link>
            ))}
          </nav>
          <Link to="/signals" className="mt-6 hidden px-3 text-xs font-semibold text-[#4c7559] underline underline-offset-4 lg:block">Explore signals →</Link>
        </aside>

        <main className="min-w-0" id="guide-content" key={active.id}>
          <div className="mb-8 rounded-[24px] border border-[#dce6da] bg-[#f5f8f1] px-6 py-6 sm:px-8">
            <span className="text-[10px] font-bold uppercase tracking-[0.17em] text-[#60846b]">Guide {active.number} / 06</span>
            <h2 className="mt-2 font-serif text-3xl tracking-tight text-[#20352b]">{active.label}</h2>
            <p className="mt-1 text-sm text-[#637168]">{active.description}</p>
          </div>
          {topicContent[active.id]}
          <div className="mt-12 flex flex-wrap items-center justify-between gap-4 border-t border-[#dce2d8] pt-6">
            <span className="text-xs text-[#7a887d]">{activeIndex + 1} of {topics.length} topics</span>
            {next ? <Link to={'/api-guide#' + next.id} className="rounded-full bg-[#2e6544] px-5 py-2.5 text-xs font-semibold text-white transition hover:bg-[#244f36]">Next: {next.label} →</Link> : <Link to="/signals" className="rounded-full bg-[#2e6544] px-5 py-2.5 text-xs font-semibold text-white transition hover:bg-[#244f36]">Explore signals →</Link>}
          </div>
        </main>
      </div>
    </div>
  )
}
