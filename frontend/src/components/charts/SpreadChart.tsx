import { useEffect, useRef } from 'react'
import { createChart, ColorType, LineSeries } from 'lightweight-charts'

interface Props {
  data: { timestamp_ms: number; bid_price: number; ask_price: number; mid_price: number }[]
}

export default function SpreadChart({ data }: Props) {
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
      },
    })

    const bidSeries = chart.addSeries(LineSeries, {
      color: '#3fb950',
      lineWidth: 1,
      title: 'Bid',
    })

    const askSeries = chart.addSeries(LineSeries, {
      color: '#f85149',
      lineWidth: 1,
      title: 'Ask',
    })

    const midSeries = chart.addSeries(LineSeries, {
      color: '#58a6ff',
      lineWidth: 1,
      lineStyle: 2,
      title: 'Mid',
    })

    // Use index-based time since timestamps may not be proper UNIX seconds
    const bidData = data.map((d, i) => ({
      time: i as unknown as import('lightweight-charts').Time,
      value: d.bid_price,
    }))
    const askData = data.map((d, i) => ({
      time: i as unknown as import('lightweight-charts').Time,
      value: d.ask_price,
    }))
    const midData = data.map((d, i) => ({
      time: i as unknown as import('lightweight-charts').Time,
      value: d.mid_price,
    }))

    bidSeries.setData(bidData)
    askSeries.setData(askData)
    midSeries.setData(midData)
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
      <div className="chart-title">Bid / Ask / Mid Price</div>
      <div ref={containerRef} />
    </div>
  )
}
