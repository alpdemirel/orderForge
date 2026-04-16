import './ParameterForm.css'

interface ParamDef {
  name: string
  label: string
  type: string
  default: number
  min: number
  max: number
  step: number
  description: string
}

interface Props {
  params: ParamDef[]
  values: Record<string, number>
  onChange: (name: string, value: number) => void
}

export default function ParameterForm({ params, values, onChange }: Props) {
  return (
    <div className="param-form">
      <span className="section-title">Strategy Parameters</span>
      <div className="param-list">
        {params.map((p) => (
          <div key={p.name} className="param-row">
            <div className="param-header">
              <label className="param-label">{p.label}</label>
              <input
                type="number"
                className="param-input"
                value={values[p.name] ?? p.default}
                min={p.min}
                max={p.max}
                step={p.step}
                onChange={(e) => onChange(p.name, parseFloat(e.target.value))}
              />
            </div>
            <input
              type="range"
              className="param-slider"
              value={values[p.name] ?? p.default}
              min={p.min}
              max={p.max}
              step={p.step}
              onChange={(e) => onChange(p.name, parseFloat(e.target.value))}
            />
            <div className="param-desc">{p.description}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
