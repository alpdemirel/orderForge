import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchStrategies, fetchDataDays, launchBacktest } from '../api'
import StrategyCard from '../components/StrategyCard'
import DaySelector from '../components/DaySelector'
import ParameterForm from '../components/ParameterForm'
import './SetupPage.css'

export default function SetupPage() {
  const navigate = useNavigate()
  const [strategies, setStrategies] = useState<any[]>([])
  const [days, setDays] = useState<any[]>([])
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(null)
  const [selectedDays, setSelectedDays] = useState<string[]>([])
  const [params, setParams] = useState<Record<string, number>>({})
  const [launching, setLaunching] = useState(false)

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
      const { job_id } = await launchBacktest({
        strategy_id: selectedStrategy,
        days: selectedDays,
        parameters: params,
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
        />
      </div>

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
