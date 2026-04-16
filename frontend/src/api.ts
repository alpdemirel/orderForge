const BASE = '/api';

export async function fetchStrategies() {
  const res = await fetch(`${BASE}/strategies`);
  return res.json();
}

export async function fetchDataDays() {
  const res = await fetch(`${BASE}/data-days`);
  return res.json();
}

export async function launchBacktest(body: {
  strategy_id: string;
  days: string[];
  parameters: Record<string, number>;
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
  onUpdate: (data: { day: string; status: string; error?: string }) => void,
  onDone: () => void,
) {
  const es = new EventSource(`${BASE}/backtest/${jobId}/status`);
  es.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (!data.day) return; // keepalive
    onUpdate(data);
    if (data.status === 'all_done') {
      es.close();
      onDone();
    }
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
