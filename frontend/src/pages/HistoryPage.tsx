import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchHistory, deleteHistoryEntry, type HistoryEntry } from '../api'
import './HistoryPage.css'

function formatTimestamp(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleString()
}

function formatPnl(value: number): string {
  const sign = value >= 0 ? '+' : '-'
  return `${sign}$${Math.abs(value).toFixed(2)}`
}

function aggregatePnl(entry: HistoryEntry, label: string): { pnl: number; fills: number } {
  let pnl = 0
  let fills = 0
  const labelData = entry.summary[label] || {}
  for (const day of Object.keys(labelData)) {
    pnl += labelData[day].total_pnl ?? 0
    fills += labelData[day].total_fills ?? 0
  }
  return { pnl, fills }
}

export default function HistoryPage() {
  const navigate = useNavigate()
  const [entries, setEntries] = useState<HistoryEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchHistory().then((data) => {
      setEntries(data)
      setLoading(false)
    })
  }, [])

  const handleOpen = (jobId: string) => {
    navigate(`/results/${jobId}`)
  }

  const handleDelete = async (e: React.MouseEvent, jobId: string) => {
    e.stopPropagation()
    if (!confirm('Delete this backtest from history?')) return
    await deleteHistoryEntry(jobId)
    setEntries((prev) => prev.filter((e) => e.job_id !== jobId))
  }

  if (loading) {
    return (
      <div className="history-loading">
        <div className="spinner" />
        <p>Loading history...</p>
      </div>
    )
  }

  if (entries.length === 0) {
    return (
      <div className="history-empty">
        <p>No past backtests yet.</p>
        <button className="btn btn-primary" onClick={() => navigate('/')}>
          Run a Backtest
        </button>
      </div>
    )
  }

  return (
    <div className="history-page">
      <div className="history-header">
        <span className="section-title">Backtest History</span>
        <span className="history-count">{entries.length} run{entries.length !== 1 ? 's' : ''}</span>
      </div>

      <div className="history-list">
        {entries.map((entry) => (
          <div
            key={entry.job_id}
            className="history-card"
            onClick={() => handleOpen(entry.job_id)}
          >
            <div className="history-card-header">
              <div className="history-card-meta">
                <span className="history-timestamp">{formatTimestamp(entry.timestamp)}</span>
                <span className="history-job-id">#{entry.job_id.slice(0, 8)}</span>
              </div>
              <button
                className="history-delete"
                onClick={(e) => handleDelete(e, entry.job_id)}
                title="Delete from history"
              >
                ×
              </button>
            </div>

            <div className="history-card-body">
              <div className="history-section">
                <div className="history-section-label">Strategies</div>
                <div className="history-strategy-list">
                  {entry.strategy_labels.map((label) => {
                    const agg = aggregatePnl(entry, label)
                    return (
                      <div key={label} className="history-strategy">
                        <span className="history-strategy-name">{label}</span>
                        <span className={`history-pnl ${agg.pnl >= 0 ? 'positive' : 'negative'}`}>
                          {formatPnl(agg.pnl)}
                        </span>
                        <span className="history-fills">{agg.fills} fills</span>
                      </div>
                    )
                  })}
                </div>
              </div>

              <div className="history-section">
                <div className="history-section-label">Days</div>
                <div className="history-days">{entry.days.join(', ')}</div>
              </div>

              {entry.initial_cash_usd !== null && (
                <div className="history-section">
                  <div className="history-section-label">Initial Cash</div>
                  <div className="history-cash">${entry.initial_cash_usd.toLocaleString()}</div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
