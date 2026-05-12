import './EffectiveConfig.css'

type CapitalCheck =
  | { mode: 'clip'; multiplier: number }
  | { mode: 'inventory'; param: string }

interface Props {
  initialCashUsd: string  // raw form value; empty means unconstrained
  orderSizeBtc: number
  capitalCheck?: CapitalCheck
  strategyParams?: Record<string, number>
}

// Reference price used for back-of-envelope sizing math. Not used for
// trading — only to give the user an idea of how their config will land
// at typical BTC prices. Update as the market drifts.
const REFERENCE_BTC_PRICE = 77_000
const TAKER_FEE_FACTOR = 1.002      // 20bps cushion, mirrors engine lock formula
const MIN_QUANTITY_BTC = 0.0001     // mirrors BaseStrategy.MIN_QUANTITY_BTC

type Status =
  | { kind: 'unconstrained' }
  | { kind: 'inert'; minBalance: number }
  | { kind: 'shrink'; effectiveClip: number; requiredBalance: number; hint: string }
  | { kind: 'healthy'; effectiveClip: number; ratio: number; hint: string }

function computeStatus(
  cashStr: string,
  clip: number,
  capitalCheck: CapitalCheck,
  strategyParams: Record<string, number>,
): Status {
  const trimmed = cashStr.trim()
  if (trimmed === '') return { kind: 'unconstrained' }

  const cash = parseFloat(trimmed)
  if (Number.isNaN(cash) || cash <= 0) return { kind: 'unconstrained' }
  if (!clip || clip <= 0) return { kind: 'unconstrained' }

  const minBalance = MIN_QUANTITY_BTC * REFERENCE_BTC_PRICE * TAKER_FEE_FACTOR
  if (cash < minBalance) {
    return { kind: 'inert', minBalance }
  }

  const capacityBtc = cash / (REFERENCE_BTC_PRICE * TAKER_FEE_FACTOR)
  const effectiveClip = Math.min(clip, capacityBtc)

  let requiredBalance: number
  let hint: string

  if (capitalCheck.mode === 'inventory') {
    const maxInv = strategyParams[capitalCheck.param] ?? 0.5
    requiredBalance = maxInv * REFERENCE_BTC_PRICE
    hint = `Needs cash ≥ max inventory (${maxInv} BTC × ~$${REFERENCE_BTC_PRICE.toLocaleString()})`
  } else {
    const multiplier = capitalCheck.multiplier
    requiredBalance = multiplier * clip * REFERENCE_BTC_PRICE * TAKER_FEE_FACTOR
    hint = `Needs cash ≥ ${multiplier}× clip notional`
  }

  if (cash >= requiredBalance) {
    return {
      kind: 'healthy',
      effectiveClip,
      ratio: cash / (clip * REFERENCE_BTC_PRICE),
      hint,
    }
  }

  return {
    kind: 'shrink',
    effectiveClip,
    requiredBalance,
    hint,
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

export default function EffectiveConfig({ initialCashUsd, orderSizeBtc, capitalCheck, strategyParams }: Props) {
  const check: CapitalCheck = capitalCheck ?? { mode: 'clip', multiplier: 5 }
  const sparams: Record<string, number> = strategyParams ?? {}
  const status = computeStatus(initialCashUsd, orderSizeBtc, check, sparams)

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
          ✓ Healthy — balance is {status.ratio.toFixed(1)}× target clip notional.{' '}
          {status.hint}.
        </div>
      )}

      {status.kind === 'shrink' && (
        <div className="eff-banner eff-banner-warn">
          ⚠ Insufficient balance — {status.hint}.
          <div className="eff-suggest">
            Increase balance to <b>≥ {fmtUsd(status.requiredBalance)}</b>{' '}
            or reduce order size / max inventory to fit available funds.
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
