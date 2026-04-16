import './StrategyCard.css'

interface Strategy {
  id: string
  name: string
  description: string
}

interface Props {
  strategy: Strategy
  selected: boolean
  onClick: () => void
}

export default function StrategyCard({ strategy, selected, onClick }: Props) {
  return (
    <div
      className={`strategy-card card ${selected ? 'selected' : ''}`}
      onClick={onClick}
    >
      <div className="strategy-icon">AS</div>
      <div className="strategy-info">
        <h3>{strategy.name}</h3>
        <p>{strategy.description}</p>
      </div>
      {selected && <div className="check-badge">&#10003;</div>}
    </div>
  )
}
