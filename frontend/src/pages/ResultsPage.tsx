import { useEffect, useMemo, useState, useTransition } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { fetchResults, fetchAccuracy } from '../api'
import SummaryPanel from '../components/SummaryPanel'
import StatsTable from '../components/StatsTable'
import AccuracyReport from '../components/AccuracyReport'
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
  // strategies: {label: {day: result}}
  const [strategies, setStrategies] = useState<Record<string, Record<string, any>> | null>(null)
  const [strategyLabels, setStrategyLabels] = useState<string[]>([])
  const [activeStrategy, setActiveStrategy] = useState<string>('')
  const [activeDay, setActiveDay] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [viewTab, setViewTab] = useState<'pnl' | 'accuracy'>('pnl')
  const [accuracy, setAccuracy] = useState<Record<string, any> | null>(null)
  const [accuracyLoading, setAccuracyLoading] = useState(false)
  const [, startTransition] = useTransition()

  useEffect(() => {
    if (!jobId) return
    fetchResults(jobId).then((data) => {
      // New format: {strategies: {label: {day: result}}, strategy_labels: [...]}
      // Legacy format: {days: {day: result}} — still supported
      if (data.strategies) {
        const labels: string[] = data.strategy_labels ?? Object.keys(data.strategies)
        setStrategies(data.strategies)
        setStrategyLabels(labels)
        const firstLabel = labels[0] ?? ''
        setActiveStrategy(firstLabel)
        const days = Object.keys(data.strategies[firstLabel] ?? {}).sort()
        if (days.length > 0) setActiveDay(days[0])
      } else if (data.days) {
        // Legacy single-strategy format
        const label = '__default__'
        setStrategies({ [label]: data.days })
        setStrategyLabels([label])
        setActiveStrategy(label)
        const days = Object.keys(data.days).sort()
        if (days.length > 0) setActiveDay(days[0])
      }
      setLoading(false)
    })
  }, [jobId])

  // When switching strategy tabs, keep the same day if it exists, else pick first
  const handleStrategyTab = (label: string) => {
    startTransition(() => {
      setActiveStrategy(label)
      setViewTab('pnl')
      const days = Object.keys(strategies?.[label] ?? {}).sort()
      if (!days.includes(activeDay) && days.length > 0) setActiveDay(days[0])
    })
  }

  const handleDayTab = (day: string) => {
    startTransition(() => setActiveDay(day))
  }

  const handleViewTab = (tab: 'pnl' | 'accuracy') => {
    setViewTab(tab)
    if (tab === 'accuracy' && accuracy === null && !accuracyLoading) {
      setAccuracyLoading(true)
      fetchAccuracy(jobId!).then((data) => {
        setAccuracy(data.days)
        setAccuracyLoading(false)
      })
    }
  }

  // Derive current data — must happen before any early return so hooks below stay stable
  const currentStrategyResults = strategies?.[activeStrategy] ?? {}
  const sortedDays = Object.keys(currentStrategyResults).sort()
  const dayData = currentStrategyResults[activeDay] ?? null
  const multiStrategy = strategyLabels.length > 1

  // useMemo must be unconditional — placed here, above all early returns
  const chartData = useMemo(() => {
    if (!dayData) return null
    return {
      pnl_curve: downsample(dayData.pnl_curve as any[] ?? [], 400),
      spread_series: downsample(dayData.spread_series as any[] ?? [], 400),
      inventory_curve: downsample(dayData.inventory_curve as any[] ?? [], 400),
      fill_scatter: downsample(dayData.fill_scatter as any[] ?? [], 800),
    }
  }, [dayData])

  // Early returns after all hooks
  if (loading) {
    return (
      <div className="results-loading">
        <div className="spinner" />
        <p>Loading results...</p>
      </div>
    )
  }

  if (!strategies || strategyLabels.length === 0) {
    return (
      <div className="results-loading">
        <p>No results found.</p>
        <button className="btn btn-primary" onClick={() => navigate('/')}>Back to Setup</button>
      </div>
    )
  }

  if (!dayData || !chartData) return null

  return (
    <div className="results-page">
      <div className="results-header">
        <button className="btn btn-sm" onClick={() => navigate('/')}>
          &larr; New Backtest
        </button>

        {/* Strategy tabs — only shown when multiple strategies */}
        {multiStrategy && (
          <div className="tab-bar strategy-tab-bar">
            {strategyLabels.map((label) => (
              <button
                key={label}
                className={`tab strategy-tab ${label === activeStrategy ? 'active' : ''}`}
                onClick={() => handleStrategyTab(label)}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {/* Day tabs */}
        <div className="tab-bar">
          {sortedDays.map((day) => (
            <button
              key={day}
              className={`tab ${day === activeDay ? 'active' : ''}`}
              onClick={() => handleDayTab(day)}
            >
              {formatTabDate(day)}
            </button>
          ))}
        </div>
      </div>

      {/* View tabs */}
      <div className="view-tab-bar">
        <button
          className={`view-tab ${viewTab === 'pnl' ? 'active' : ''}`}
          onClick={() => handleViewTab('pnl')}
        >
          📈 Strategy PnL
        </button>
        <button
          className={`view-tab ${viewTab === 'accuracy' ? 'active' : ''}`}
          onClick={() => handleViewTab('accuracy')}
        >
          🔬 Backtest Accuracy
        </button>
      </div>

      {viewTab === 'pnl' && (
        <>
          <SummaryPanel summary={dayData.summary} />

          <div className="charts-grid">
            <PnLChart data={chartData.pnl_curve} />
            <SpreadChart data={chartData.spread_series} />
            <InventoryChart data={chartData.inventory_curve} />
            <FillScatterChart data={chartData.fill_scatter} />
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
        </>
      )}

      {viewTab === 'accuracy' && (
        <>
          {accuracyLoading ? (
            <div className="results-loading">
              <div className="spinner" />
              <p>Running fill-accuracy diagnostics… this takes a few minutes per day.</p>
            </div>
          ) : accuracy && accuracy[activeDay] ? (
            <AccuracyReport data={accuracy[activeDay]} />
          ) : null}
        </>
      )}
    </div>
  )
}

function formatTabDate(dateStr: string) {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function downsample<T>(arr: T[], maxPoints: number): T[] {
  if (arr.length <= maxPoints) return arr
  const step = arr.length / maxPoints
  const result = Array.from({ length: maxPoints - 1 }, (_, i) => arr[Math.floor(i * step)])
  result.push(arr[arr.length - 1]) // always include the final point (end-of-day value)
  return result
}
