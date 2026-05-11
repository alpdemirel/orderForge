import './AccuracyReport.css'

interface LatencyData {
  samples: number
  min_ms: number
  p10_ms: number
  median_ms: number
  p90_ms: number
  p99_ms: number
  max_ms: number
  mean_ms: number
  std_ms: number
  cv: number
  high_variability: boolean
}

interface QuoteSurvivalData {
  total_cycles: number
  survived: number
  rate_pct: number
  avg_tick_interval_ms: number
  latency_over_tick: boolean
}

interface AdverseSelectionData {
  fills_analysed: number
  buy_fills: number
  sell_fills: number
  avg_mid_move_usd: number
  pct_adversely_selected: number
  avg_buy_mid_change: number
  pct_buy_dropped: number
  avg_sell_mid_change: number
  pct_sell_rose: number
}

interface FillTimingData {
  interval_median_ms: number
  interval_p10_ms: number
  interval_p90_ms: number
  burst_pct: number
  total_fills: number
  maker_fills: number
  taker_fills: number
  maker_pct: number
  taker_pct: number
}

interface QuoteStalenessData {
  fills: number
  min_usd: number
  p25_usd: number
  median_usd: number
  p75_usd: number
  max_usd: number
  mean_usd: number
}

interface AccuracyData {
  latency?: LatencyData | null
  quote_survival?: QuoteSurvivalData | null
  adverse_selection?: AdverseSelectionData | null
  fill_timing?: FillTimingData | null
  quote_staleness?: QuoteStalenessData | null
  error?: string
}

interface Props {
  data: AccuracyData
}

function Badge({ level }: { level: 'good' | 'warn' | 'bad' }) {
  const labels = { good: '✓ Good', warn: '⚠ Warning', bad: '✗ High Risk' }
  return <span className={`accuracy-badge accuracy-badge--${level}`}>{labels[level]}</span>
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="accuracy-card">
      <div className="accuracy-card-title">{title}</div>
      <div className="accuracy-card-body">{children}</div>
    </div>
  )
}

function Row({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="accuracy-row">
      <span className="accuracy-row-label">{label}</span>
      <span className="accuracy-row-value">{value}</span>
      {sub && <span className="accuracy-row-sub">{sub}</span>}
    </div>
  )
}

export default function AccuracyReport({ data }: Props) {
  if (data.error) {
    return <div className="accuracy-error">Error running diagnostics: {data.error}</div>
  }

  const adversePct = data.adverse_selection?.pct_adversely_selected ?? 0
  const adverseLevel = adversePct < 55 ? 'good' : adversePct < 65 ? 'warn' : 'bad'

  const survivalPct = data.quote_survival?.rate_pct ?? 0
  const survivalLevel = survivalPct >= 85 ? 'good' : survivalPct >= 70 ? 'warn' : 'bad'

  const latencyHigh = data.latency?.high_variability ?? false
  const latencyLevel = latencyHigh ? 'warn' : 'good'

  return (
    <div className="accuracy-report">

      {/* ── 1. Adverse Selection ── */}
      {data.adverse_selection && (
        <Card title="Adverse Selection">
          <div className="accuracy-hero">
            <span className="accuracy-hero-value" data-level={adverseLevel}>
              {data.adverse_selection.pct_adversely_selected}%
            </span>
            <span className="accuracy-hero-label">fills moved against us</span>
            <Badge level={adverseLevel} />
          </div>
          <div className="accuracy-divider" />
          <Row label="Fills analysed" value={data.adverse_selection.fills_analysed.toLocaleString()} />
          <Row
            label="Buy fills"
            value={`${data.adverse_selection.buy_fills.toLocaleString()}`}
            sub={`${data.adverse_selection.pct_buy_dropped}% mid dropped after`}
          />
          <Row
            label="Sell fills"
            value={`${data.adverse_selection.sell_fills.toLocaleString()}`}
            sub={`${data.adverse_selection.pct_sell_rose}% mid rose against`}
          />
          <Row
            label="Avg mid move"
            value={`$${data.adverse_selection.avg_mid_move_usd.toFixed(2)}`}
            sub="after fill (negative = against us)"
          />
        </Card>
      )}

      {/* ── 2. Quote Survival ── */}
      {data.quote_survival && (
        <Card title="Quote Survival Rate">
          <div className="accuracy-hero">
            <span className="accuracy-hero-value" data-level={survivalLevel}>
              {data.quote_survival.rate_pct}%
            </span>
            <span className="accuracy-hero-label">quotes reached the book</span>
            <Badge level={survivalLevel} />
          </div>
          <div className="accuracy-divider" />
          <Row label="Quote cycles" value={data.quote_survival.total_cycles.toLocaleString()} />
          <Row label="Survived" value={data.quote_survival.survived.toLocaleString()} />
          <Row
            label="Avg tick interval"
            value={`${data.quote_survival.avg_tick_interval_ms.toFixed(1)} ms`}
            sub={data.quote_survival.latency_over_tick ? '⚠ Latency > tick — quotes cancelled before landing' : undefined}
          />
        </Card>
      )}

      {/* ── 3. Latency ── */}
      {data.latency && (
        <Card title="Latency Distribution">
          <div className="accuracy-hero">
            <span className="accuracy-hero-value" data-level={latencyLevel}>
              {data.latency.median_ms} ms
            </span>
            <span className="accuracy-hero-label">median latency</span>
            <Badge level={latencyLevel} />
          </div>
          <div className="accuracy-divider" />
          <Row label="Samples" value={data.latency.samples.toLocaleString()} />
          <Row label="P10 / P90" value={`${data.latency.p10_ms} ms / ${data.latency.p90_ms} ms`} />
          <Row label="P99 / Max" value={`${data.latency.p99_ms} ms / ${data.latency.max_ms} ms`} />
          <Row label="Mean ± Std" value={`${data.latency.mean_ms} ± ${data.latency.std_ms} ms`} />
          <Row
            label="Variability (CV)"
            value={data.latency.cv}
            sub={data.latency.high_variability ? '⚠ High — sample from distribution instead of point estimate' : 'Stable'}
          />
        </Card>
      )}

      {/* ── 4. Fill Timing ── */}
      {data.fill_timing && (
        <Card title="Fill Timing & Clustering">
          <Row label="Total fills" value={(data.fill_timing.total_fills ?? 0).toLocaleString()} />
          <Row label="Maker / Taker" value={`${data.fill_timing.maker_pct}% / ${data.fill_timing.taker_pct}%`} />
          <Row label="Median inter-fill" value={`${(data.fill_timing.interval_median_ms ?? 0).toLocaleString()} ms`} />
          <Row label="P10 / P90 interval" value={`${(data.fill_timing.interval_p10_ms ?? 0).toLocaleString()} / ${(data.fill_timing.interval_p90_ms ?? 0).toLocaleString()} ms`} />
          <Row label="Burst fills" value={`${data.fill_timing.burst_pct ?? 0}%`} sub="fills within P10 interval" />
        </Card>
      )}

      {/* ── 5. Quote Staleness ── */}
      {data.quote_staleness && (
        <Card title="Quote Staleness">
          <div className="accuracy-hero">
            <span className="accuracy-hero-value" data-level="warn">
              ${data.quote_staleness.median_usd.toFixed(2)}
            </span>
            <span className="accuracy-hero-label">median fill-to-mid deviation</span>
          </div>
          <div className="accuracy-divider" />
          <Row label="Fills" value={data.quote_staleness.fills.toLocaleString()} />
          <Row label="P25 / Median / P75" value={`$${data.quote_staleness.p25_usd} / $${data.quote_staleness.median_usd} / $${data.quote_staleness.p75_usd}`} />
          <Row label="Mean / Max" value={`$${data.quote_staleness.mean_usd} / $${data.quote_staleness.max_usd}`} />
        </Card>
      )}
    </div>
  )
}
