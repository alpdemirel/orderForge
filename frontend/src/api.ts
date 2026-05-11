const BASE = '/api';

export async function fetchStrategies() {
  const res = await fetch(`${BASE}/strategies`);
  return res.json();
}

export async function fetchDataDays() {
  const res = await fetch(`${BASE}/data-days`, { cache: 'no-store' });
  return res.json();
}

export async function launchBacktest(body: {
  strategy_id: string;
  days: string[];
  parameters: Record<string, number>;
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

export async function fetchAccuracy(jobId: string) {
  const res = await fetch(`${BASE}/backtest/${jobId}/accuracy`);
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
