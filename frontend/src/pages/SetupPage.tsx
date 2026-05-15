import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchStrategies, fetchDataDays, launchBacktest } from '../api'
import StrategyCard from '../components/StrategyCard'
import DaySelector from '../components/DaySelector'
import ParameterForm from '../components/ParameterForm'
import DataDownloadModal from '../components/DataDownloadModal'
import EffectiveConfig from '../components/EffectiveConfig'
import './SetupPage.css'

const CATEGORY_ORDER = [
  'Baseline',
  'Stochastic Control',
  'Microstructure / Order Flow',
  'Adverse Selection / Risk',
  'RL / Adaptive',
]

interface StrategyEntry {
  id: string
  strategy_id: string | null
  params: Record<string, number>
  label: string
}

function generateId() {
  return typeof crypto !== 'undefined' && crypto.randomUUID 
    ? crypto.randomUUID() 
    : Date.now().toString(36) + Math.random().toString(36).substring(2);
}

function newEntry(strategy: any | null = null): StrategyEntry {
  const id = generateId()
  if (!strategy) return { id, strategy_id: null, params: {}, label: '' }
  const params: Record<string, number> = {}
  strategy.parameters.forEach((p: any) => { params[p.name] = p.default })
  return { id, strategy_id: strategy.id, params, label: '' }
}

export default function SetupPage() {
  const navigate = useNavigate()
  const [strategies, setStrategies] = useState<any[]>([])
  const [days, setDays] = useState<any[]>([])
  const [entries, setEntries] = useState<StrategyEntry[]>([])
  const [activeId, setActiveId] = useState<string>('')
  const [selectedDays, setSelectedDays] = useState<string[]>([])
  const [launching, setLaunching] = useState(false)
  const [showDataManager, setShowDataManager] = useState(false)
  const [openCategories, setOpenCategories] = useState<Record<string, boolean>>({})
  const [initialCashUsd, setInitialCashUsd] = useState<string>('')

  useEffect(() => {
    fetchStrategies().then((data) => {
      setStrategies(data)
      if (data.length > 0) {
        const first = newEntry(data[0])
        setEntries([first])
        setActiveId(first.id)
        const defaultCat = data[0].category || 'Other'
        setOpenCategories({ [defaultCat]: true })
      }
    })
    fetchDataDays().then(setDays)
  }, [])

  const activeEntry = entries.find((e) => e.id === activeId) ?? null
  const activeStrategy = strategies.find((s) => s.id === activeEntry?.strategy_id) ?? null

  const updateActive = (patch: Partial<StrategyEntry>) => {
    setEntries((prev) => prev.map((e) => e.id === activeId ? { ...e, ...patch } : e))
  }

  const handleSelectStrategy = (s: any) => {
    const params: Record<string, number> = {}
    s.parameters.forEach((p: any) => { params[p.name] = p.default })
    updateActive({ strategy_id: s.id, params })
  }

  const handleAddEntry = () => {
    const defaultStrat = strategies[0] ?? null
    const entry = newEntry(defaultStrat)
    setEntries((prev) => [...prev, entry])
    setActiveId(entry.id)
  }

  const handleRemoveEntry = (id: string) => {
    setEntries((prev) => prev.filter((e) => e.id !== id))
    if (activeId === id) {
      const next = entries.filter((e) => e.id !== id)
      if (next.length > 0) setActiveId(next[next.length - 1].id)
    }
  }

  const toggleDay = (date: string) => {
    setSelectedDays((prev) =>
      prev.includes(date) ? prev.filter((d) => d !== date) : [...prev, date]
    )
  }

  const toggleCategory = (cat: string) => {
    setOpenCategories((prev) => ({ ...prev, [cat]: !prev[cat] }))
  }

  // Capital validation — two severity levels:
  //   blocked: cash below minimum lot → cannot open any order at all
  //   warned:  cash below strategy requirement → fit_quote will shrink clips, but runs
  const _REF_PRICE = 77_000
  const _FEE_FACTOR = 1.002
  const _MIN_QTY = 0.0001
  const _cashValue = initialCashUsd.trim() === '' ? null : parseFloat(initialCashUsd)

  const isEntryBlocked = (_entry: StrategyEntry): boolean => {
    if (_cashValue === null || Number.isNaN(_cashValue) || _cashValue <= 0) return false
    return _cashValue < _MIN_QTY * _REF_PRICE * _FEE_FACTOR
  }

  const isEntryWarned = (entry: StrategyEntry): boolean => {
    if (_cashValue === null || Number.isNaN(_cashValue) || _cashValue <= 0) return false
    if (isEntryBlocked(entry)) return false
    const strat = strategies.find((s) => s.id === entry.strategy_id)
    const cc = strat?.capital_check
    const orderSize = entry.params.order_size ?? 0
    if (cc && orderSize > 0) {
      if (cc.mode === 'inventory') {
        const maxInv = entry.params[cc.param] ?? 0.5
        return _cashValue < maxInv * _REF_PRICE
      }
      return _cashValue < cc.multiplier * orderSize * _REF_PRICE * _FEE_FACTOR
    }
    return false
  }

  const configBlocked = entries.some(isEntryBlocked)
  const warnedEntries = entries.filter(isEntryWarned)
  const hasValidEntries = entries.some((e) => e.strategy_id !== null)

  const handleRun = async () => {
    if (!hasValidEntries || selectedDays.length === 0) return
    setLaunching(true)
    try {
      const cashTrimmed = initialCashUsd.trim()
      const cashValue = cashTrimmed === '' ? null : parseFloat(cashTrimmed)
      const strategyConfigs = entries
        .filter((e) => e.strategy_id !== null)
        .map((e) => ({
          strategy_id: e.strategy_id!,
          parameters: e.params,
          ...(e.label.trim() ? { label: e.label.trim() } : {}),
        }))
      const { job_id } = await launchBacktest({
        strategies: strategyConfigs,
        days: selectedDays,
        initial_cash_usd: cashValue,
      })
      navigate(`/running/${job_id}`)
    } catch {
      setLaunching(false)
    }
  }

  const strategiesByCategory = strategies.reduce((acc: Record<string, any[]>, s) => {
    const cat = s.category || 'Other'
    if (!acc[cat]) acc[cat] = []
    acc[cat].push(s)
    return acc
  }, {})

  const sortedCategories = Object.keys(strategiesByCategory).sort((a, b) => {
    const ai = CATEGORY_ORDER.indexOf(a)
    const bi = CATEGORY_ORDER.indexOf(b)
    if (ai === -1 && bi === -1) return a.localeCompare(b)
    if (ai === -1) return 1
    if (bi === -1) return -1
    return ai - bi
  })

  return (
    <div className="setup-page">
      {/* Strategy slot tabs */}
      <div className="setup-section">
        <span className="section-title">Select Strategy</span>

        <div className="strategy-slots">
          {entries.map((entry, idx) => {
            const strat = strategies.find((s) => s.id === entry.strategy_id)
            return (
              <div
                key={entry.id}
                className={`strategy-slot ${entry.id === activeId ? 'active' : ''}`}
                onClick={() => setActiveId(entry.id)}
              >
                <span className="slot-label">
                  {entry.label.trim() || strat?.name || `Strategy ${idx + 1}`}
                </span>
                {entries.length > 1 && (
                  <button
                    className="slot-remove"
                    onClick={(ev) => { ev.stopPropagation(); handleRemoveEntry(entry.id) }}
                    title="Remove this strategy"
                  >
                    ×
                  </button>
                )}
              </div>
            )
          })}
          <button className="strategy-slot slot-add" onClick={handleAddEntry} title="Add another strategy">
            + Add
          </button>
        </div>

        {/* Optional label for active entry */}
        {activeEntry && (
          <div className="slot-label-row">
            <input
              type="text"
              className="slot-label-input"
              placeholder={activeStrategy?.name ?? 'Strategy name…'}
              value={activeEntry.label}
              maxLength={40}
              onChange={(e) => updateActive({ label: e.target.value })}
            />
          </div>
        )}

        {/* Strategy selector accordion — applies to active entry */}
        {sortedCategories.map((category) => {
          const group = strategiesByCategory[category]
          const isOpen = !!openCategories[category]
          const hasSelected = group.some((s) => s.id === activeEntry?.strategy_id)
          return (
            <div key={category} className="strategy-group">
              <button
                className={`strategy-group-header ${isOpen ? 'open' : ''} ${hasSelected ? 'has-selected' : ''}`}
                onClick={() => toggleCategory(category)}
              >
                <span className="strategy-group-label">{category}</span>
                <span className="strategy-group-meta">
                  {hasSelected && <span className="strategy-group-selected-dot" />}
                  <span className="strategy-group-count">{group.length}</span>
                  <span className="strategy-group-chevron">{isOpen ? '▲' : '▼'}</span>
                </span>
              </button>
              {isOpen && (
                <div className="strategy-list">
                  {group.map((s) => (
                    <StrategyCard
                      key={s.id}
                      strategy={s}
                      selected={activeEntry?.strategy_id === s.id}
                      onClick={() => handleSelectStrategy(s)}
                    />
                  ))}
                </div>
              )}
            </div>
          )
        })}
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

      {activeEntry && activeStrategy?.parameters?.some((p: any) => p.name === 'order_size') && (
        <div className="setup-section">
          <EffectiveConfig
            initialCashUsd={initialCashUsd}
            orderSizeBtc={activeEntry.params.order_size ?? 0}
            capitalCheck={activeStrategy?.capital_check}
            strategyParams={activeEntry.params}
          />
        </div>
      )}

      {activeEntry && activeStrategy && (
        <div className="setup-section">
          <ParameterForm
            params={activeStrategy.parameters}
            values={activeEntry.params}
            onChange={(name, value) => updateActive({ params: { ...activeEntry.params, [name]: value } })}
          />
        </div>
      )}

      <div className="setup-actions">
        {warnedEntries.length > 0 && !configBlocked && (
          <div className="capital-warning">
            ⚠ {warnedEntries.length === 1
              ? `"${strategies.find(s => s.id === warnedEntries[0].strategy_id)?.name ?? 'Strategy'}" will run with a reduced order size to fit available cash.`
              : `${warnedEntries.length} strategies will run with reduced order sizes to fit available cash.`}
          </div>
        )}
        <button
          className="btn btn-primary btn-run"
          disabled={!hasValidEntries || selectedDays.length === 0 || launching || configBlocked}
          onClick={handleRun}
        >
          {launching
            ? 'Launching...'
            : `Run Backtest (${entries.filter(e => e.strategy_id).length} strateg${entries.filter(e => e.strategy_id).length !== 1 ? 'ies' : 'y'}, ${selectedDays.length} day${selectedDays.length !== 1 ? 's' : ''})`}
        </button>
      </div>
    </div>
  )
}
