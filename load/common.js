import http from 'k6/http';
import { check } from 'k6';

export const proxyUrl = (__ENV.PROXY_URL || 'http://proxy').replace(/\/$/, '');

export function required(name) {
  if (!__ENV[name]) throw new Error(`Set ${name}; use fresh tokens from the API startup logs.`);
  return __ENV[name];
}

export function body(response) {
  try {
    return response.json();
  } catch (_) {
    return null;
  }
}

export function upstream(response) {
  const key = Object.keys(response.headers).find((name) => name.toLowerCase() === 'x-upstream');
  return key ? response.headers[key] : '';
}

// One VU runs one iteration, so these sets cover the entire run.
export function probeProxy() {
  const seen = new Set();
  for (let i = 0; i < 40; i++) {
    const response = http.get(`${proxyUrl}/health`);
    const address = upstream(response);
    check(response, {
      'proxy health is 200': (r) => r.status === 200,
      'proxy identifies the answering copy': () => address !== '',
    });
    if (address) seen.add(address);
  }
  return seen;
}

export function handleSummary(data) {
  const metrics = {};
  const lines = [];
  for (const [name, metric] of Object.entries(data.metrics)) {
    if (!metric.thresholds) continue;
    metrics[name] = { values: metric.values, thresholds: metric.thresholds };
    for (const [expression, result] of Object.entries(metric.thresholds)) {
      lines.push(`${result.ok ? 'PASS' : 'FAIL'} ${name}: ${expression}`);
    }
  }
  return { stdout: `${lines.join('\n')}\nMETRIC_EVIDENCE ${JSON.stringify(metrics)}\n` };
}
