import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'

interface Props {
  makerFills: number
  takerFills: number
}

const COLORS = ['#58a6ff', '#d29922']

export default function MakerTakerPie({ makerFills, takerFills }: Props) {
  const data = [
    { name: 'Maker (Passive)', value: makerFills },
    { name: 'Taker (Aggressive)', value: takerFills },
  ]

  if (makerFills === 0 && takerFills === 0) {
    return (
      <div className="chart-box">
        <div className="chart-title">Maker vs Taker Fills</div>
        <div style={{ textAlign: 'center', padding: 40, color: '#6e7681' }}>No fills</div>
      </div>
    )
  }

  return (
    <div className="chart-box">
      <div className="chart-title">Maker vs Taker Fills</div>
      <ResponsiveContainer width="100%" height={250}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={90}
            paddingAngle={3}
            dataKey="value"
            label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}
            labelLine={{ stroke: '#6e7681' }}
          >
            {data.map((_, idx) => (
              <Cell key={idx} fill={COLORS[idx]} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              background: '#1c2128',
              border: '1px solid #30363d',
              borderRadius: 8,
              color: '#e6edf3',
              fontSize: 13,
            }}
          />
          <Legend
            wrapperStyle={{ color: '#8b949e', fontSize: 12 }}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  )
}
