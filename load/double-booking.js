import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
import { body, probeProxy, required } from './common.js';
export { handleSummary } from './common.js';

// Claim: the same room slot is confirmed at most once across the tested copies.
const confirmedBookings = new Counter('confirmed_bookings');
const proxyUpstreams = new Counter('proxy_upstreams');
const bookingTargets = new Counter('booking_targets');
const expectedTargets = Number(__ENV.EXPECTED_TARGETS || 2);
if (![1, 2].includes(expectedTargets)) throw new Error('EXPECTED_TARGETS must be 1 (control) or 2.');

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: {
    confirmed_bookings: ['count>=1', 'count<=1'],
    proxy_upstreams: ['count==2'],
    booking_targets: [`count==${expectedTargets}`],
    checks: ['rate==1'],
  },
};

export default function () {
  const targets = [required('API_A'), required('API_B')];
  const tokens = [required('TOKEN_A'), required('TOKEN_B')];
  const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const start = __ENV.START || `${tomorrow}T10:00:00Z`;
  const roomId = __ENV.ROOM_ID || 'r-1';
  const seen = probeProxy();
  proxyUpstreams.add(seen.size);
  const directAddresses = new Set();
  const results = [];
  confirmedBookings.add(0);

  // Sequential requests suffice: this checks shared state, not a timing race.
  for (let i = 0; i < targets.length; i++) {
    const base = targets[i].replace(/\/$/, '');
    const headers = { Authorization: `Bearer ${tokens[i]}` };
    const auth = http.get(`${base}/bookings`, { headers });
    const preflightOk = auth.status === 200 && Array.isArray(body(auth)?.bookings);
    check(auth, { 'each direct target accepts its own token': () => preflightOk });
    if (!preflightOk) throw new Error(`Authentication precondition failed for target ${i + 1}.`);

    const response = http.post(`${base}/bookings`, { roomId, start, minutes: '60', attendees: '1' }, { headers });
    const data = body(response);
    const address = response.remote_ip ? `${response.remote_ip}:${response.remote_port}` : '';
    if (address) directAddresses.add(address);
    const confirmed = response.status === 201 && data?.outcome === 'confirmed';
    const waitlisted = response.status === 202 && data?.outcome === 'waitlisted';
    const record = confirmed ? data.booking : data?.waitlistEntry;
    check(response, {
      'booking reaches a copy observed through the proxy': () => seen.has(address),
      'request is confirmed or waitlisted for the requested slot': () =>
        (confirmed || waitlisted) && record?.roomId === roomId && record?.start === start,
    });
    if (confirmed) {
      confirmedBookings.add(1);
      const readback = http.get(`${base}/bookings/${encodeURIComponent(data.booking.id)}`, { headers });
      const stored = body(readback);
      check(readback, {
        'confirmed booking is readable and still holds the requested slot': (r) =>
          r.status === 200 && stored?.id === data.booking.id && stored?.status === 'CONFIRMED'
          && stored?.roomId === roomId && stored?.start === start,
      });
    }
    results.push({ target: base, address, status: response.status, outcome: data?.outcome, bookingId: data?.booking?.id });
  }
  bookingTargets.add(directAddresses.size);
  console.log(`BOOKING_EVIDENCE ${JSON.stringify({ claim: 'a room slot is booked at most once', roomId, start,
    minutes: 60, proxyUpstreams: [...seen].sort(), expectedTargets, results })}`);
}
