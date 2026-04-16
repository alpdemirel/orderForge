import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { fetchResults } from '../api'
import SummaryPanel from '../components/SummaryPanel'
import StatsTable from '../components/StatsTable'
import PnLChart from '../components/charts/PnLChart'
import SpreadChart from '../components/charts/SpreadChart'
import InventoryChart from '../components/charts/InventoryChart'
import FillScatterChart from '../components/charts/FillScatterChart'
import VolumeBreakdown from '../components/charts/VolumeBreakdown'
import MakerTakerPie from '../components/charts/MakerTakerPie'
import './ResultsPage.css'

export default function ResultsPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const [results, setResults] = useState<Record<string, any> | null>(null)
  const [activeDay, setActiveDay] = useState<string>('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!jobId) return
    fetchResults(jobId).then((data) => {
      setResults(data.days)
      const days = Object.keys(data.days).sort()
      if (days.length > 0) setActiveDay(days[0])
      setLoading(false)
    })
  }, [jobId])

  if (loading) {
    return (
      <div className="results-loading">
        <div className="spinner" />
        <p>Loading results...</p>
      </div>
    )
  }

  if (!results || Object.keys(results).length === 0) {
    return (
      <div className="results-loading">
        <p>No results found.</p>
        <button className="btn btn-primary" onClick={() => navigate('/')}>Back to Setup</button>
      </div>
    )
  }

  const sortedDays = Object.keys(results).sort()
  const dayData = results[activeDay]

  if (!dayData) return null

  return (
    <div className="results-page">
      <div className="results-header">
        <button className="btn btn-sm" onClick={() => navigate('/')}>
          &larr; New Backtest
        </button>
        <div className="tab-bar">
          {sortedDays.map((day) => (
            <button
              key={day}
              className={`tab ${day === activeDay ? 'active' : ''}`}
              onClick={() => setActiveDay(day)}
            >
              {formatTabDate(day)}
            </button>
          ))}
        </div>
      </div>

      <SummaryPanel summary={dayData.summary} />

      <div className="charts-grid">
        <PnLChart data={dayData.pnl_curve} />
        <SpreadChart data={dayData.spread_series} />
        <InventoryChart data={dayData.inventory_curve} />
        <FillScatterChart data={dayData.fill_scatter} />
      </div>

      <div className="charts-grid-small">
        <VolumeBreakdown
          buyVolume={dayData.summary.buy_volume_btc}
          sellVolume={dayData.summary.sell_volume_btc}
        />
        <MakerTakerPie
          makerFills={dayData.summary.maker_fills}
          takerFills={dayData.summary.taker_fills}
        />
      </div>

      <StatsTable
        summary={dayData.summary}
        parameters={dayData.parameters}
        execution={dayData.execution}
        pnlDistribution={dayData.pnl_distribution}
      />
    </div>
  )
}

function formatTabDate(dateStr: string) {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
