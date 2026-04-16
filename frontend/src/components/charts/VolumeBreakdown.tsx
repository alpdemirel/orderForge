import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from 'recharts'

interface Props {
  buyVolume: number
  sellVolume: number
}

export default function VolumeBreakdown({ buyVolume, sellVolume }: Props) {
  const data = [
    { name: 'Buy', volume: buyVolume, color: '#3fb950' },
    { name: 'Sell', volume: sellVolume, color: '#f85149' },
  ]

  return (
    <div className="chart-box">
      <div className="chart-title">Volume Breakdown (BTC)</div>
      <ResponsiveContainer width="100%" height={250}>
        <BarChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
          <XAxis
            dataKey="name"
            tick={{ fill: '#8b949e', fontSize: 12 }}
            axisLine={{ stroke: '#30363d' }}
          />
          <YAxis
            tick={{ fill: '#8b949e', fontSize: 11 }}
            axisLine={{ stroke: '#30363d' }}
            tickFormatter={(v) => v.toFixed(4)}
          />
          <Tooltip
            contentStyle={{
              background: '#1c2128',
              border: '1px solid #30363d',
              borderRadius: 8,
              color: '#e6edf3',
              fontSize: 13,
            }}
            formatter={(v) => [Number(v).toFixed(6) + ' BTC', 'Volume']}
          />
          <Bar dataKey="volume" radius={[6, 6, 0, 0]}>
            {data.map((entry, idx) => (
              <Cell key={idx} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
