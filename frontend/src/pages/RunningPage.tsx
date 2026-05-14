import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { subscribeStatus } from '../api'
import './RunningPage.css'

type CellStatus = 'pending' | 'running' | 'completed' | 'error'

interface Cell {
  label: string
  day: string
  status: CellStatus
  error?: string
}

export default function RunningPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  // keyed by "label|||day"
  const [cells, setCells] = useState<Record<string, Cell>>({})
  const [allDone, setAllDone] = useState(false)

  useEffect(() => {
    if (!jobId) return

    const es = subscribeStatus(
      jobId,
      (update) => {
        if (update.day === '__all__') return
        const key = `${update.label}|||${update.day}`
        setCells((prev) => ({
          ...prev,
          [key]: {
            label: update.label,
            day: update.day,
            status: update.status as CellStatus,
            error: update.error,
          },
        }))
      },
      () => setAllDone(true),
    )

    return () => es.close()
  }, [jobId])

  const allCells = Object.values(cells)
  const labels = [...new Set(allCells.map((c) => c.label))].sort()
  const days = [...new Set(allCells.map((c) => c.day))].sort()
  const multiStrategy = labels.length > 1

  const completed = allCells.filter((c) => c.status === 'completed').length
  const total = allCells.length
  const progress = total > 0 ? (completed / total) * 100 : 0

  const getCell = (label: string, day: string) =>
    cells[`${label}|||${day}`]

  return (
    <div className="running-page">
      <div className="running-container">
        <div className="running-title-row">
          <h2>Running Backtests</h2>
          {!allDone && (
            <button
              className="cancel-btn"
              onClick={() => navigate('/')}
              title="Cancel backtest"
            >
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                <path d="M4.5 4.5L13.5 13.5M13.5 4.5L4.5 13.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
              </svg>
              Cancel
            </button>
          )}
        </div>

        <div className="progress-bar-container">
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${progress}%` }} />
          </div>
          <span className="progress-label">{completed} / {total} completed</span>
        </div>

        {multiStrategy ? (
          /* Strategy × Day matrix */
          <div className="status-matrix">
            <table className="matrix-table">
              <thead>
                <tr>
                  <th className="matrix-strategy-col">Strategy</th>
                  {days.map((day) => (
                    <th key={day} className="matrix-day-col">{formatDay(day)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {labels.map((label) => (
                  <tr key={label}>
                    <td className="matrix-label">{label}</td>
                    {days.map((day) => {
                      const cell = getCell(label, day)
                      const status = cell?.status ?? 'pending'
                      return (
                        <td key={day} className={`matrix-cell cell-${status}`}>
                          {status === 'running' && <div className="spinner spinner-sm" />}
                          {status === 'completed' && <span className="icon-done">✓</span>}
                          {status === 'error' && <span className="icon-error" title={cell?.error}>✗</span>}
                          {status === 'pending' && <span className="icon-pending">·</span>}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          /* Single strategy: original flat list */
          <div className="status-list">
            {allCells
              .sort((a, b) => a.day.localeCompare(b.day))
              .map((c) => (
                <div key={`${c.label}|||${c.day}`} className={`status-item status-${c.status}`}>
                  <div className="status-icon">
                    {c.status === 'running' && <div className="spinner" />}
                    {c.status === 'completed' && <span className="icon-done">&#10003;</span>}
                    {c.status === 'error' && <span className="icon-error">&#10007;</span>}
                    {c.status === 'pending' && <span className="icon-pending">&#8226;</span>}
                  </div>
                  <div className="status-text">
                    <span className="status-day">{c.day}</span>
                    <span className="status-message">
                      {c.status === 'running' && 'backtest is running...'}
                      {c.status === 'completed' && 'backtest completed'}
                      {c.status === 'error' && `error: ${c.error}`}
                      {c.status === 'pending' && 'waiting...'}
                    </span>
                  </div>
                </div>
              ))}
          </div>
        )}

        {allDone && (
          <div className="running-actions">
            <button
              className="btn btn-primary btn-run"
              onClick={() => navigate(`/results/${jobId}`)}
            >
              View Results
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function formatDay(dateStr: string) {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
