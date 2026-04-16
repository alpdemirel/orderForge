import { useEffect, useRef } from 'react'
import { createChart, ColorType, LineSeries, createSeriesMarkers } from 'lightweight-charts'

interface FillData {
  fill_index: number
  price: number
  side: string
  quantity: number
}

interface Props {
  data: FillData[]
}

export default function FillScatterChart({ data }: Props) {
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

    // Price line
    const priceSeries = chart.addSeries(LineSeries, {
      color: '#58a6ff',
      lineWidth: 1,
      lastValueVisible: false,
      priceLineVisible: false,
    })

    const priceData = data.map((d) => ({
      time: d.fill_index as unknown as import('lightweight-charts').Time,
      value: d.price,
    }))
    priceSeries.setData(priceData)

    // Buy/sell markers
    const markers = data.map((d) => ({
      time: d.fill_index as unknown as import('lightweight-charts').Time,
      position: d.side === 'buy' ? ('belowBar' as const) : ('aboveBar' as const),
      color: d.side === 'buy' ? '#3fb950' : '#f85149',
      shape: d.side === 'buy' ? ('arrowUp' as const) : ('arrowDown' as const),
      text: d.side === 'buy' ? 'B' : 'S',
    }))

    // v5: use createSeriesMarkers plugin
    const markersPlugin = createSeriesMarkers(priceSeries, markers)
    chart.timeScale().fitContent()

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      markersPlugin.detach()
      chart.remove()
    }
  }, [data])

  return (
    <div className="chart-box">
      <div className="chart-title">Fill Distribution</div>
      <div ref={containerRef} />
    </div>
  )
}
