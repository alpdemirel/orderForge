import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchStrategies, fetchDataDays, launchBacktest } from '../api'
import StrategyCard from '../components/StrategyCard'
import DaySelector from '../components/DaySelector'
import ParameterForm from '../components/ParameterForm'
import DataDownloadModal from '../components/DataDownloadModal'
import EffectiveConfig from '../components/EffectiveConfig'
import './SetupPage.css'

export default function SetupPage() {
  const navigate = useNavigate()
  const [strategies, setStrategies] = useState<any[]>([])
  const [days, setDays] = useState<any[]>([])
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null)
  const [selectedDays, setSelectedDays] = useState<string[]>([])
  const [params, setParams] = useState<Record<string, number>>({})
  const [launching, setLaunching] = useState(false)
  const [showDataManager, setShowDataManager] = useState(false)
  // Optional account seeding. Empty cash field = no enforcement (legacy
  // behavior); any numeric value enables balance-constrained quoting.
  // Base inventory is intentionally not exposed — realistic backtests
  // start with zero BTC and let the strategy build inventory itself.
  const [initialCashUsd, setInitialCashUsd] = useState<string>('')

  useEffect(() => {
    fetchStrategies().then((data) => {
      setStrategies(data)
      if (data.length > 0) {
        setSelectedStrategy(data[0].id)
        const defaults: Record<string, number> = {}
        data[0].parameters.forEach((p: any) => { defaults[p.name] = p.default })
        setParams(defaults)
      }
    })
    fetchDataDays().then(setDays)
  }, [])

  const toggleDay = (date: string) => {
    setSelectedDays((prev) =>
      prev.includes(date) ? prev.filter((d) => d !== date) : [...prev, date]
    )
  }

  const handleRun = async () => {
    if (!selectedStrategy || selectedDays.length === 0) return
    setLaunching(true)
    try {
      const cashTrimmed = initialCashUsd.trim()
      const cashValue = cashTrimmed === '' ? null : parseFloat(cashTrimmed)
      const { job_id } = await launchBacktest({
        strategy_id: selectedStrategy,
        days: selectedDays,
        parameters: params,
        initial_cash_usd: cashValue,
      })
      navigate(`/running/${job_id}`)
    } catch {
      setLaunching(false)
    }
  }

  const currentStrategy = strategies.find((s) => s.id === selectedStrategy)

  return (
    <div className="setup-page">
      <div className="setup-section">
        <span className="section-title">Select Strategy</span>
        <div className="strategy-list">
          {strategies.map((s) => (
            <StrategyCard
              key={s.id}
              strategy={s}
              selected={selectedStrategy === s.id}
              onClick={() => {
                setSelectedStrategy(s.id)
                const defaults: Record<string, number> = {}
                s.parameters.forEach((p: any) => { defaults[p.name] = p.default })
                setParams(defaults)
              }}
            />
          ))}
        </div>
      </div>

      <div className="setup-section">
        <DaySelector
          days={days}
          selected={selectedDays}
          onToggle={toggleDay}
          onSelectAll={() => setSelectedDays(days.map((d: any) => d.date))}
          onClear={() => setSelectedDays([])}
          onOpenManager={() => setShowDataManager(true)}
        />
      </div>

      {showDataManager && (
        <DataDownloadModal 
            onClose={() => setShowDataManager(false)} 
            onDataChanged={() => fetchDataDays().then(setDays)} 
        />
      )}

      <div className="setup-section">
        <span className="section-title">Account Balance (optional)</span>
        <div className="param-list">
          <div className="param-row">
            <div className="param-header">
              <label className="param-label">Initial Cash (USD)</label>
              <input
                type="number"
                className="param-input"
                value={initialCashUsd}
                min={0}
                step={100}
                onChange={(e) => setInitialCashUsd(e.target.value)}
              />
            </div>
            <div className="param-desc">
              Starting cash balance. Leave empty to disable balance enforcement
              (strategy quotes any size). Any value enables capacity-aware
              sizing — orders auto-shrink to fit available funds. Base
              inventory always starts at 0 BTC; the strategy builds inventory
              from its own bid fills.
            </div>
          </div>
        </div>
      </div>

      {/* The EffectiveConfig panel is convention-coupled to a strategy
          parameter named `order_size` (per-quote BTC clip). Hide it for
          strategies that don't expose that parameter so future strategies
          aren't forced into a name they don't have. */}
      {currentStrategy?.parameters?.some((p: any) => p.name === 'order_size') && (
        <div className="setup-section">
          <EffectiveConfig
            initialCashUsd={initialCashUsd}
            orderSizeBtc={params.order_size ?? 0}
          />
        </div>
      )}

      {currentStrategy && (
        <div className="setup-section">
          <ParameterForm
            params={currentStrategy.parameters}
            values={params}
            onChange={(name, value) => setParams((prev) => ({ ...prev, [name]: value }))}
          />
        </div>
      )}

      <div className="setup-actions">
        <button
          className="btn btn-primary btn-run"
          disabled={!selectedStrategy || selectedDays.length === 0 || launching}
          onClick={handleRun}
        >
          {launching ? 'Launching...' : `Run Backtest (${selectedDays.length} day${selectedDays.length !== 1 ? 's' : ''})`}
        </button>
      </div>
    </div>
  )
}
