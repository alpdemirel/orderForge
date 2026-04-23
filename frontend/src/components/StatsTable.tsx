import { useState } from 'react'
import './StatsTable.css'

interface StatSection {
  title: string
  rows: { label: string; value: string }[]
}

interface Props {
  summary: Record<string, any>
  parameters: Record<string, any>
  execution: Record<string, any>
  pnlDistribution: Record<string, any>
}

function fmt(v: number, decimals = 2) {
  return v.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

// Human-friendly labels + units for every known strategy parameter key.
// Any key present in the `parameters` dict but not in this map falls back to
// its raw key name, so new strategies keep rendering without a code change.
const PARAM_LABELS: Record<string, { label: string; suffix?: string }> = {
  // Avellaneda-Stoikov
  risk_aversion: { label: 'Risk aversion (gamma)' },
  liquidity_param: { label: 'Liquidity param (kappa)' },
  final_sigma: { label: 'Final sigma', suffix: ' $/tick' },
  // Glosten-Milgrom
  informed_fraction: { label: 'Informed fraction (alpha)' },
  value_half_range_bps: { label: 'Value half-range', suffix: ' bps' },
  belief_decay: { label: 'Belief decay' },
  final_belief: { label: 'Final belief (V = V_H)' },
  belief_updates: { label: 'Belief updates (count)' },
  // Shared
  order_size: { label: 'Order size', suffix: ' BTC' },
  session_duration: { label: 'Session duration', suffix: 's' },
}

// Preferred display order. Unknown keys are appended alphabetically at the end.
const PARAM_ORDER = [
  'risk_aversion',
  'informed_fraction',
  'value_half_range_bps',
  'belief_decay',
  'order_size',
  'session_duration',
  'liquidity_param',
  'final_sigma',
  'final_belief',
  'belief_updates',
]

function formatParamValue(key: string, raw: any): string {
  const meta = PARAM_LABELS[key]
  const suffix = meta?.suffix ?? ''
  if (raw === null || raw === undefined) return 'N/A'
  if (typeof raw === 'number') {
    // Use more decimals for small floats, fewer for integers/large values.
    const abs = Math.abs(raw)
    let decimals = 2
    if (Number.isInteger(raw)) decimals = 0
    else if (abs < 1) decimals = 4
    return `${fmt(raw, decimals)}${suffix}`
  }
  return `${raw}${suffix}`
}

function buildParamRows(parameters: Record<string, any>): { label: string; value: string }[] {
  const keys = Object.keys(parameters)
  const ordered = [
    ...PARAM_ORDER.filter((k) => keys.includes(k)),
    ...keys.filter((k) => !PARAM_ORDER.includes(k)).sort(),
  ]
  return ordered.map((k) => ({
    label: PARAM_LABELS[k]?.label ?? k,
    value: formatParamValue(k, parameters[k]),
  }))
}

export default function StatsTable({ summary, parameters, execution, pnlDistribution }: Props) {
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({})

  const toggle = (title: string) => {
    setOpenSections((prev) => ({ ...prev, [title]: !prev[title] }))
  }

  const sections: StatSection[] = [
    {
      title: 'Execution',
      rows: [
        { label: 'Replay time', value: `${execution.replay_time_s}s` },
      ],
    },
    {
      title: 'Strategy Parameters',
      rows: buildParamRows(parameters),
    },
    {
      title: 'Fill Summary',
      rows: [
        { label: 'Total fills', value: `${summary.total_fills}` },
        { label: 'Buy fills', value: `${summary.buy_fills}` },
        { label: 'Sell fills', value: `${summary.sell_fills}` },
        { label: 'Maker fills (passive)', value: `${summary.maker_fills}` },
        { label: 'Taker fills (aggressive)', value: `${summary.taker_fills}` },
      ],
    },
    {
      title: 'Volume',
      rows: [
        { label: 'Buy volume', value: `${fmt(summary.buy_volume_btc, 6)} BTC` },
        { label: 'Sell volume', value: `${fmt(summary.sell_volume_btc, 6)} BTC` },
        { label: 'Total volume', value: `${fmt(summary.total_volume_btc, 6)} BTC` },
        { label: 'Total volume (USD est.)', value: `$${fmt(summary.total_volume_usd)}` },
        { label: 'Net volume (buy - sell)', value: `${fmt(summary.net_volume_btc, 6)} BTC` },
      ],
    },
    {
      title: 'Price Analysis',
      rows: [
        { label: 'Final mid price', value: `$${fmt(summary.final_mid_price)}` },
        { label: 'Buy VWAP', value: `$${fmt(summary.buy_vwap)}` },
        { label: 'Sell VWAP', value: `$${fmt(summary.sell_vwap)}` },
        { label: 'VWAP spread', value: `$${fmt(summary.vwap_spread)}` },
        { label: 'Fill price range', value: `$${fmt(summary.fill_price_min)} - $${fmt(summary.fill_price_max)}` },
        { label: 'Mean fill price', value: `$${fmt(summary.fill_price_mean)}` },
      ],
    },
    {
      title: 'P&L Breakdown',
      rows: [
        { label: 'Realised cash', value: `$${fmt(summary.realised_cash_usd)}` },
        { label: 'Open position', value: `${fmt(summary.open_position_btc, 6)} BTC` },
        { label: 'Unrealised PnL', value: `$${fmt(summary.unrealised_pnl_usd)}` },
        { label: 'Fees paid', value: `$${fmt(summary.fees_paid_usd)}` },
        { label: 'Total PnL (MtM)', value: `$${fmt(summary.total_pnl_usd)}` },
        { label: 'PnL per fill', value: `$${fmt(summary.pnl_per_fill, 4)}` },
        { label: 'PnL per BTC traded', value: `$${fmt(summary.pnl_per_btc_traded)}` },
      ],
    },
    {
      title: 'Risk',
      rows: [
        { label: 'Max drawdown', value: `$${fmt(summary.max_drawdown_usd)}` },
        { label: 'Calmar ratio', value: summary.calmar_ratio > 0 ? `${summary.calmar_ratio}x` : 'N/A' },
        { label: 'Final inventory', value: `${fmt(summary.open_position_btc, 6)} BTC` },
        { label: 'Inventory value at mid', value: `$${fmt(summary.inventory_value_usd)}` },
      ],
    },
    {
      title: 'Market Making Efficiency',
      rows: [
        { label: 'Gross edge (VWAP spread)', value: `$${fmt(summary.edge_per_btc)} /BTC` },
        { label: 'Gross edge (matched vol)', value: `$${fmt(summary.edge_total)}` },
        { label: 'Fees as % of edge', value: `${summary.fees_pct_of_edge}%` },
        { label: 'Completed round-trips', value: `${summary.roundtrips}` },
      ],
    },
    {
      title: 'P&L Distribution',
      rows: [
        { label: 'Min', value: `$${fmt(pnlDistribution.min)}` },
        { label: '25th percentile', value: `$${fmt(pnlDistribution.p25)}` },
        { label: 'Median', value: `$${fmt(pnlDistribution.median)}` },
        { label: '75th percentile', value: `$${fmt(pnlDistribution.p75)}` },
        { label: 'Max', value: `$${fmt(pnlDistribution.max)}` },
        { label: 'Final', value: `$${fmt(pnlDistribution.final)}` },
        { label: 'Time in profit', value: `${pnlDistribution.time_in_profit_pct}%` },
      ],
    },
  ]

  return (
    <div className="stats-table">
      {sections.map((section) => {
        const isOpen = openSections[section.title] ?? false
        return (
          <div key={section.title} className="stats-section">
            <div className="stats-section-header" onClick={() => toggle(section.title)}>
              <span className="stats-section-title">{section.title}</span>
              <span className={`stats-chevron ${isOpen ? 'open' : ''}`}>&#9660;</span>
            </div>
            {isOpen && (
              <div className="stats-section-body">
                {section.rows.map((row) => (
                  <div key={row.label} className="stats-row">
                    <span className="stats-label">{row.label}</span>
                    <span className="stats-value">{row.value}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
