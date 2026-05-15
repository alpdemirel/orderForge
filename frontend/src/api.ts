const BASE = '/api';

export async function fetchStrategies() {
  const res = await fetch(`${BASE}/strategies`);
  return res.json();
}

export async function fetchDataDays() {
  const res = await fetch(`${BASE}/data-days`, { cache: 'no-store' });
  return res.json();
}

export interface StrategyConfig {
  strategy_id: string;
  parameters: Record<string, number>;
  label?: string;
}

export async function launchBacktest(body: {
  strategies: StrategyConfig[];
  days: string[];
  initial_cash_usd?: number | null;
}) {
  const res = await fetch(`${BASE}/backtest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

export function subscribeStatus(
  jobId: string,
  onUpdate: (data: { label: string; day: string; status: string; error?: string }) => void,
  onDone: () => void,
) {
  const es = new EventSource(`${BASE}/backtest/${jobId}/status`);
  es.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (!data.day) return; // keepalive
    if (data.status === 'all_done') {
      es.close();
      onDone();
      return;
    }
    onUpdate(data);
  };
  es.onerror = () => {
    es.close();
  };
  return es;
}

export async function fetchResults(jobId: string) {
  const res = await fetch(`${BASE}/backtest/${jobId}/results`);
  return res.json();
}

export async function fetchAccuracy(jobId: string) {
  const res = await fetch(`${BASE}/backtest/${jobId}/accuracy`);
  return res.json();
}

export interface HistoryEntry {
  job_id: string;
  timestamp: number;
  strategy_labels: string[];
  strategy_configs: { strategy_id: string; label: string }[];
  days: string[];
  initial_cash_usd: number | null;
  summary: Record<string, Record<string, {
    total_pnl: number;
    total_fills: number;
    max_drawdown: number;
    total_volume_btc: number;
  }>>;
  errors: Record<string, string[]>;
}

export async function fetchHistory(): Promise<HistoryEntry[]> {
  const res = await fetch(`${BASE}/history`, { cache: 'no-store' });
  return res.json();
}

export async function deleteHistoryEntry(jobId: string) {
  const res = await fetch(`${BASE}/history/${jobId}`, { method: 'DELETE' });
  return res.json();
}

export async function fetchS3Days() {
  const res = await fetch(`${BASE}/s3-days`);
  return res.json();
}

export async function downloadS3Day(day: string) {
  const res = await fetch(`${BASE}/s3-download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ day }),
  });
  return res.json();
}

export async function deleteDataDay(day: string) {
  const res = await fetch(`${BASE}/data-days/${day}`, {
    method: 'DELETE',
  });
  return res.json();
}

export function subscribeDownloadStatus(
  day: string,
  onUpdate: (data: { status: string; progress?: number; error?: string }) => void,
) {
  const es = new EventSource(`${BASE}/s3-download/${day}/status`);
  es.onmessage = (e) => {
    const data = JSON.parse(e.data);
    onUpdate(data);
    if (data.status === 'completed' || data.status === 'error') {
      es.close();
    }
  };
  es.onerror = () => {
    es.close();
  };
  return es;
}
