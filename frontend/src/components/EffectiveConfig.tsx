import './EffectiveConfig.css'

interface Props {
  initialCashUsd: string  // raw form value; empty means unconstrained
  orderSizeBtc: number
}

// Reference price used for back-of-envelope sizing math. Not used for
// trading — only to give the user an idea of how their config will land
// at typical BTC prices. Update as the market drifts.
const REFERENCE_BTC_PRICE = 77_000
const TAKER_FEE_FACTOR = 1.002      // 20bps cushion, mirrors engine lock formula
const MIN_QUANTITY_BTC = 0.0001     // mirrors BaseStrategy.MIN_QUANTITY_BTC
const HEALTHY_RATIO = 5             // balance ≥ 5× clip notional → green

type Status =
  | { kind: 'unconstrained' }
  | { kind: 'inert'; minBalance: number }
  | { kind: 'shrink'; effectiveClip: number; suggestedBalance: number; suggestedClip: number }
  | { kind: 'healthy'; effectiveClip: number; ratio: number }

function computeStatus(cashStr: string, clip: number): Status {
  const trimmed = cashStr.trim()
  if (trimmed === '') return { kind: 'unconstrained' }

  const cash = parseFloat(trimmed)
  if (Number.isNaN(cash) || cash <= 0) return { kind: 'unconstrained' }
  if (!clip || clip <= 0) return { kind: 'unconstrained' }

  const minBalance = MIN_QUANTITY_BTC * REFERENCE_BTC_PRICE * TAKER_FEE_FACTOR
  if (cash < minBalance) {
    return { kind: 'inert', minBalance }
  }

  const targetNotional = clip * REFERENCE_BTC_PRICE * TAKER_FEE_FACTOR
  const capacityBtc = cash / (REFERENCE_BTC_PRICE * TAKER_FEE_FACTOR)
  const effectiveClip = Math.min(clip, capacityBtc)

  if (cash >= HEALTHY_RATIO * targetNotional) {
    return {
      kind: 'healthy',
      effectiveClip,
      ratio: cash / targetNotional,
    }
  }

  // Shrink mode: suggest matched values
  return {
    kind: 'shrink',
    effectiveClip: capacityBtc,
    suggestedBalance: HEALTHY_RATIO * targetNotional,
    suggestedClip: cash / (HEALTHY_RATIO * REFERENCE_BTC_PRICE * TAKER_FEE_FACTOR),
  }
}

function fmtUsd(n: number): string {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
}

function fmtBtc(n: number): string {
  if (n === 0) return '0 BTC'
  if (n >= 0.001) return `${n.toFixed(4)} BTC`
  return `${n.toFixed(6)} BTC`
}

export default function EffectiveConfig({ initialCashUsd, orderSizeBtc }: Props) {
  const status = computeStatus(initialCashUsd, orderSizeBtc)

  if (status.kind === 'unconstrained') {
    return (
      <div className="eff-config eff-unconstrained">
        <span className="section-title">Effective Configuration</span>
        <div className="eff-row eff-info">
          Balance enforcement is off. Strategy will quote at its target size
          ({fmtBtc(orderSizeBtc)}) regardless of capital.
        </div>
      </div>
    )
  }

  const targetNotional = orderSizeBtc * REFERENCE_BTC_PRICE
  const cash = parseFloat(initialCashUsd)

  return (
    <div className={`eff-config eff-${status.kind}`}>
      <span className="section-title">Effective Configuration</span>
      <div className="eff-grid">
        <div className="eff-cell">
          <span className="eff-label">Reference BTC price</span>
          <span className="eff-value">~{fmtUsd(REFERENCE_BTC_PRICE)}</span>
        </div>
        <div className="eff-cell">
          <span className="eff-label">Target clip notional</span>
          <span className="eff-value">{fmtUsd(targetNotional)}</span>
        </div>
        <div className="eff-cell">
          <span className="eff-label">Available cash</span>
          <span className="eff-value">{fmtUsd(cash)}</span>
        </div>
        <div className="eff-cell">
          <span className="eff-label">Effective clip size</span>
          <span className="eff-value">
            {status.kind === 'inert' ? '— (no quoting)' : fmtBtc(status.effectiveClip)}
          </span>
        </div>
      </div>

      {status.kind === 'healthy' && (
        <div className="eff-banner eff-banner-ok">
          ✓ Healthy — balance is {status.ratio.toFixed(1)}× target clip notional.
          Strategy can quote at intended size with room for inventory build-up.
        </div>
      )}

      {status.kind === 'shrink' && (
        <div className="eff-banner eff-banner-warn">
          ⚠ Shrink mode — balance is too small to quote at the target clip.
          Orders will auto-shrink to ~{fmtBtc(status.effectiveClip)}.
          <div className="eff-suggest">
            To match: increase balance to <b>≥ {fmtUsd(status.suggestedBalance)}</b>{' '}
            or reduce order size to <b>≤ {fmtBtc(status.suggestedClip)}</b>.
          </div>
        </div>
      )}

      {status.kind === 'inert' && (
        <div className="eff-banner eff-banner-err">
          ✕ Inert — balance below the minimum lot ({fmtUsd(status.minBalance)}).
          The strategy will post zero orders for the entire run.
        </div>
      )}
    </div>
  )
}
