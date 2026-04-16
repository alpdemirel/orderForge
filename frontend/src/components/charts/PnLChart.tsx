import { useEffect, useRef } from 'react'
import { createChart, ColorType, AreaSeries } from 'lightweight-charts'

interface Props {
  data: { fill_index: number; pnl: number }[]
}

export default function PnLChart({ data }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#1c2128' },
        textColor: '#8b949e',
      },
      grid: {
        vertLines: { color: '#30363d' },
        horzLines: { color: '#30363d' },
      },
      width: containerRef.current.clientWidth,
      height: 300,
      crosshair: {
        horzLine: { color: '#58a6ff44' },
        vertLine: { color: '#58a6ff44' },
      },
      rightPriceScale: {
        borderColor: '#30363d',
      },
      timeScale: {
        borderColor: '#30363d',
        visible: true,
      },
    })

    const finalPnl = data[data.length - 1]?.pnl ?? 0
    const lineColor = finalPnl >= 0 ? '#3fb950' : '#f85149'
    const topColor = finalPnl >= 0 ? 'rgba(63,185,80,0.3)' : 'rgba(248,81,73,0.3)'
    const bottomColor = finalPnl >= 0 ? 'rgba(63,185,80,0.02)' : 'rgba(248,81,73,0.02)'

    const series = chart.addSeries(AreaSeries, {
      lineColor,
      topColor,
      bottomColor,
      lineWidth: 2,
    })

    // Use fill_index as time (Lightweight Charts needs ascending numbers)
    const chartData = data.map((d) => ({
      time: d.fill_index as unknown as import('lightweight-charts').Time,
      value: d.pnl,
    }))

    series.setData(chartData)
    chart.timeScale().fitContent()

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
    }
  }, [data])

  return (
    <div className="chart-box">
      <div className="chart-title">Equity Curve (PnL)</div>
      <div ref={containerRef} />
    </div>
  )
}
