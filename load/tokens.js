import http from 'k6/http';
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';
import { body, proxyUrl, required, upstream } from './common.js';
export { handleSummary } from './common.js';

// Claim: one valid token works whichever API copy answers the request.
const tokenAccepted = new Rate('token_accepted');
const proxyUpstreams = new Counter('proxy_upstreams');

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: {
    token_accepted: ['rate==1'],
    proxy_upstreams: ['count==2'],
    checks: ['rate==1'],
  },
};

export default function () {
  const token = required('TOKEN');
  const results = {};
  for (let i = 0; i < 40; i++) {
    const response = http.get(`${proxyUrl}/bookings`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const address = upstream(response);
    const data = body(response);
    check(response, {
      'proxy identifies the answering copy': () => address !== '',
      'response is an authenticated list or a credential rejection': (r) =>
        (r.status === 200 && Array.isArray(data?.bookings)) || r.status === 401,
    });
    tokenAccepted.add(response.status === 200 && Array.isArray(data?.bookings));
    if (address) {
      results[address] ||= {};
      results[address][response.status] = (results[address][response.status] || 0) + 1;
    }
  }
  proxyUpstreams.add(Object.keys(results).length);
  console.log(`TOKEN_EVIDENCE ${JSON.stringify({ claim: 'a token works on any copy', responses: results })}`);
}
