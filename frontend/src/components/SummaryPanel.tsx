import './SummaryPanel.css'

interface Props {
  summary: Record<string, any>
}

function formatUsd(v: number) {
  const sign = v >= 0 ? '+' : ''
  return `${sign}$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export default function SummaryPanel({ summary }: Props) {
  const cards = [
    {
      label: 'Total PnL',
      value: formatUsd(summary.total_pnl_usd),
      className: summary.total_pnl_usd >= 0 ? 'positive' : 'negative',
    },
    {
      label: 'Total Fills',
      value: `${summary.total_fills}`,
      sub: `${summary.buy_fills} buy / ${summary.sell_fills} sell`,
    },
    {
      label: 'Volume',
      value: `${summary.total_volume_btc.toFixed(4)} BTC`,
      sub: `~$${summary.total_volume_usd.toLocaleString()}`,
    },
    {
      label: 'Max Drawdown',
      value: `$${summary.max_drawdown_usd.toFixed(2)}`,
      className: 'negative',
    },
    {
      label: 'Fees Paid',
      value: `$${summary.fees_paid_usd.toFixed(2)}`,
    },
    {
      label: 'Calmar Ratio',
      value: summary.calmar_ratio > 0 ? `${summary.calmar_ratio.toFixed(2)}x` : 'N/A',
    },
  ]

  return (
    <div className="summary-panel">
      {cards.map((c) => (
        <div key={c.label} className={`summary-card ${c.className || ''}`}>
          <div className="summary-label">{c.label}</div>
          <div className="summary-value">{c.value}</div>
          {c.sub && <div className="summary-sub">{c.sub}</div>}
        </div>
      ))}
    </div>
  )
}
