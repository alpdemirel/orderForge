import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'

interface Props {
  data: { fill_index: number; position_btc: number }[]
}

export default function InventoryChart({ data }: Props) {
  return (
    <div className="chart-box">
      <div className="chart-title">Inventory (BTC Position)</div>
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="invPos" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#3fb950" stopOpacity={0.4} />
              <stop offset="100%" stopColor="#3fb950" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="invNeg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#f85149" stopOpacity={0} />
              <stop offset="100%" stopColor="#f85149" stopOpacity={0.4} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
          <XAxis
            dataKey="fill_index"
            tick={{ fill: '#8b949e', fontSize: 11 }}
            axisLine={{ stroke: '#30363d' }}
            tickLine={{ stroke: '#30363d' }}
          />
          <YAxis
            tick={{ fill: '#8b949e', fontSize: 11 }}
            axisLine={{ stroke: '#30363d' }}
            tickLine={{ stroke: '#30363d' }}
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
            formatter={(v) => [Number(v).toFixed(6) + ' BTC', 'Position']}
            labelFormatter={(l) => `Fill #${l}`}
          />
          <ReferenceLine y={0} stroke="#6e7681" strokeDasharray="3 3" />
          <Area
            type="monotone"
            dataKey="position_btc"
            stroke="#58a6ff"
            strokeWidth={2}
            fill="url(#invPos)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
