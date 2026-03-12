const BASE = "http://localhost:8000";

export async function fetchSymbols(): Promise<{ symbols: string[]; count: number }> {
  const res = await fetch(`${BASE}/api/symbols`);
  return res.json();
}

export async function fetchConfig() {
  const res = await fetch(`${BASE}/api/config`);
  return res.json();
}

export async function updateConfig(body: object) {
  const res = await fetch(`${BASE}/api/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export async function startBot(symbols: string[]) {
  const res = await fetch(`${BASE}/api/bot/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols }),
  });
  return res.json();
}

export async function stopBot() {
  const res = await fetch(`${BASE}/api/bot/stop`, { method: "POST" });
  return res.json();
}

export async function fetchStatus() {
  const res = await fetch(`${BASE}/api/status`);
  return res.json();
}
