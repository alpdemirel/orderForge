import './DaySelector.css'

interface DataDay {
  date: string
  file: string
  size_mb: number
}

interface Props {
  days: DataDay[]
  selected: string[]
  onToggle: (date: string) => void
  onSelectAll: () => void
  onClear: () => void
  onOpenManager: () => void
}

function formatDate(dateStr: string) {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function DaySelector({ days, selected, onToggle, onSelectAll, onClear, onOpenManager }: Props) {
  return (
    <div className="day-selector">
      <div className="day-selector-header">
        <span className="section-title">Select Data Days</span>
        <div className="day-actions">
          <button className="btn btn-sm" onClick={onSelectAll}>Select All</button>
          <button className="btn btn-sm" onClick={onClear}>Clear</button>
        </div>
      </div>
      <div className="day-grid">
        {days.map((day) => {
          const isSelected = selected.includes(day.date)
          return (
            <div
              key={day.date}
              className={`day-tile ${isSelected ? 'selected' : ''}`}
              onClick={() => onToggle(day.date)}
            >
              <div className="day-date">{formatDate(day.date)}</div>
              {isSelected && <div className="day-check">&#10003;</div>}
            </div>
          )
        })}
        <div className="day-tile" onClick={onOpenManager} style={{ backgroundColor: 'var(--bg-card)', border: '1px dashed var(--border-color)' }}>
            <div className="day-date" style={{ fontWeight: 'bold' }}>+ Get data from server</div>
        </div>
      </div>
      {days.length === 0 && (
        <div className="day-empty">No data days found in data/ directory</div>
      )}
    </div>
  )
}
