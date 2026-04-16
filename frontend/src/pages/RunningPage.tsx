import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { subscribeStatus } from '../api'
import './RunningPage.css'

interface DayStatus {
  day: string
  status: 'pending' | 'running' | 'completed' | 'error'
  error?: string
}

export default function RunningPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const [statuses, setStatuses] = useState<Record<string, DayStatus>>({})
  const [allDone, setAllDone] = useState(false)

  useEffect(() => {
    if (!jobId) return

    const es = subscribeStatus(
      jobId,
      (update) => {
        if (update.day === '__all__') return
        setStatuses((prev) => ({
          ...prev,
          [update.day]: {
            day: update.day,
            status: update.status as DayStatus['status'],
            error: update.error,
          },
        }))
      },
      () => setAllDone(true),
    )

    return () => es.close()
  }, [jobId])

  const entries = Object.values(statuses)
  const completed = entries.filter((s) => s.status === 'completed').length
  const total = entries.length
  const progress = total > 0 ? (completed / total) * 100 : 0

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

        <div className="status-list">
          {entries
            .sort((a, b) => a.day.localeCompare(b.day))
            .map((s) => (
            <div key={s.day} className={`status-item status-${s.status}`}>
              <div className="status-icon">
                {s.status === 'running' && <div className="spinner" />}
                {s.status === 'completed' && <span className="icon-done">&#10003;</span>}
                {s.status === 'error' && <span className="icon-error">&#10007;</span>}
                {s.status === 'pending' && <span className="icon-pending">&#8226;</span>}
              </div>
              <div className="status-text">
                <span className="status-day">{s.day}</span>
                <span className="status-message">
                  {s.status === 'running' && 'backtest is running...'}
                  {s.status === 'completed' && 'backtest completed'}
                  {s.status === 'error' && `error: ${s.error}`}
                  {s.status === 'pending' && 'waiting...'}
                </span>
              </div>
            </div>
          ))}
        </div>

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
