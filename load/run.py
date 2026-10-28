#!/usr/bin/env python3
"""Recreate only this lab stack, collect fresh tokens, and run the k6 checks."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'load' / 'results'


def compose(*args, **kwargs):
    return subprocess.run(['docker', 'compose', *args], cwd=ROOT, text=True, **kwargs)


def credentials(container):
    # Capture the local demo credentials without printing or saving them.
    logs = subprocess.run(['docker', 'logs', container], capture_output=True, text=True, check=True).stdout
    member = re.search(r'u-1 \(member\)\s+(\S+)', logs)
    admin = re.search(r'u-2 \(admin\)\s+(\S+)', logs)
    if not member or not admin:
        raise RuntimeError(f'Startup tokens not found for {container}')
    return member[1], admin[1]


def as_expected(record):
    return (record['exitCode'] == record['expectedExitCode'] and bool(record['evidence'])
            and bool(record['metrics'])
            and record['failedThresholds'] == record['expectedFailedThresholds'])


def print_run(record):
    titles = {'tokens': 'TOKEN PORTABILITY', 'single-copy-control': 'ONE-COPY BOOKING CONTROL',
              'double-booking': 'TWO-COPY BOOKING'}
    expected = as_expected(record)
    verdict = ('PASSED' if record['exitCode'] == 0 else 'FAILED (expected)') if expected else 'UNEXPECTED RESULT'
    print(f"\n{titles.get(record['name'], record['name'])}: {verdict}", flush=True)
    for evidence in record['evidence']:
        if record['name'] == 'tokens':
            responses = evidence['responses']
            print(f'  Traffic reached {len(responses)} API copies.')
            for address, statuses in sorted(responses.items()):
                accepted, rejected = statuses.get('200', 0), statuses.get('401', 0)
                print(f'  {address}: {accepted} accepted (HTTP 200), {rejected} rejected (HTTP 401).')
            rate = record['metrics'].get('token_accepted', {}).get('values', {}).get('rate')
            if rate is not None:
                print(f'  Token accepted on {rate:.0%} of requests; the claim requires 100%.')
            if expected:
                print('  Meaning: the same token works on its issuing copy but fails on the other.')
        else:
            start = datetime.fromisoformat(evidence['start'].replace('Z', '+00:00')).astimezone(timezone.utc)
            end = start + timedelta(minutes=evidence['minutes'])
            print(f"  Room {evidence['roomId']}, {start:%B %d, %Y}, {start:%H:%M}-{end:%H:%M} UTC.")
            print(f"  Proxy coverage: {len(evidence['proxyUpstreams'])} API copies; "
                  f"booking targets: {evidence['expectedTargets']}.")
            for result in evidence['results']:
                print(f"  {result['target']}: HTTP {result['status']} ({result.get('outcome', 'invalid response')}).")
            count = record['metrics'].get('confirmed_bookings', {}).get('values', {}).get('count')
            if count is not None:
                print(f'  Confirmed bookings: {count}; this experiment requires one confirmation and no duplicate.')
            if expected:
                print('  Meaning: the same copy confirmed the first request and waitlisted the second.' if record['name'] == 'single-copy-control'
                      else '  Meaning: two copies independently confirm the same slot: a double booking.')
    guards = {name: metric for name, metric in record['metrics'].items()
              if name not in {'token_accepted', 'confirmed_bookings'}}
    guards_ok = bool(guards) and all(t['ok'] for metric in guards.values() for t in metric['thresholds'].values())
    print(f"  Coverage and response checks: {'PASS' if guards_ok else 'FAIL / missing'}. k6 exit: {record['exitCode']}.")
    if not expected:
        print(f"  Failed thresholds: {record['failedThresholds']}")
    print(f"  Details: load/results/{record['name']}.log", flush=True)


def print_conclusion(report):
    if not all(as_expected(record) for record in report['runs']):
        raise SystemExit('Unexpected result; inspect load/results/*.log. The run is not verified.')
    print('\nVERIFIED: both two-copy claims failed, and the one-copy control passed.')
    print('The runner succeeded because it reproduced the bugs. The platform does not satisfy the two-copy claims.')
    print('Full measurements: load/results/latest.json')


def run_k6(name, script, variables, expected_exit, expected_failures):
    environment = os.environ.copy()
    environment.update(variables)
    args = ['run', '--rm', '--no-deps', '-T']
    for key in variables:
        args += ['-e', key]
    args += ['k6', 'run', '--no-color', f'/scripts/{script}']
    result = compose(*args, env=environment, capture_output=True)
    output = result.stdout + result.stderr
    # k6 should not print headers, but redact credentials before saving any output.
    for key, value in variables.items():
        if 'TOKEN' in key:
            output = output.replace(value, '[REDACTED]')
    (RESULTS / f'{name}.log').write_text(output)
    evidence = []
    metrics = {}
    for line in output.splitlines():
        if ' msg="' in line:
            line = json.JSONDecoder().raw_decode(line.split(' msg=', 1)[1])[0]
        match = re.search(r'(?:TOKEN|BOOKING)_EVIDENCE (\{.*\})', line)
        if match:
            evidence.append(json.loads(match[1]))
        if line.startswith('METRIC_EVIDENCE '):
            metrics = json.loads(line.removeprefix('METRIC_EVIDENCE '))
    failed = sorted(f'{metric_name}:{expression}' for metric_name, metric in metrics.items()
                    for expression, threshold in metric['thresholds'].items() if not threshold['ok'])
    record = {'name': name, 'exitCode': result.returncode, 'expectedExitCode': expected_exit,
              'failedThresholds': failed, 'expectedFailedThresholds': expected_failures,
              'evidence': evidence, 'metrics': metrics}
    print_run(record)
    return record


def main():
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / 'latest.json').unlink(missing_ok=True)
    print('Recreating this lab stack: existing in-memory lab bookings and tokens will reset.', flush=True)
    startup = compose('up', '-d', '--build', '--force-recreate', '--scale', 'api=2', 'api', 'proxy', capture_output=True)
    (RESULTS / 'stack.log').write_text(startup.stdout + startup.stderr)
    if startup.returncode:
        raise SystemExit('Stack startup failed; inspect load/results/stack.log.')
    print('Stack started. Waiting for the proxy and both copies...', flush=True)
    deadline = time.monotonic() + 90
    while True:
        try:
            with urlopen('http://127.0.0.1:8080/health', timeout=2) as response:
                if json.load(response)['status'] == 'ok':
                    break
        except (OSError, ValueError, KeyError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Proxy did not become healthy within 90 seconds.')
        time.sleep(1)
    # nginx re-resolves api every five seconds after scaling.
    time.sleep(6)
    ids = compose('ps', '-q', 'api', capture_output=True, check=True).stdout.split()
    if len(ids) != 2:
        raise RuntimeError(f'Expected exactly two API copies; found {len(ids)}.')
    copies = []
    for container in ids:
        details = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
        member, admin = credentials(container)
        copies.append((details['Name'].lstrip('/'), member, admin))
    copies.sort()
    a, b = copies
    api_a, api_b = f'http://{a[0]}:8080', f'http://{b[0]}:8080'
    print(f'API targets: {api_a}, {api_b}', flush=True)
    control_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%d')
    records = [
        run_k6('tokens', 'tokens.js', {'TOKEN': a[1]}, 99, ['token_accepted:rate==1']),
        run_k6('single-copy-control', 'double-booking.js', {
            'API_A': api_a, 'API_B': api_a, 'TOKEN_A': a[1], 'TOKEN_B': a[2],
            'EXPECTED_TARGETS': '1', 'ROOM_ID': 'r-2', 'START': f'{control_date}T12:00:00Z',
        }, 0, []),
        run_k6('double-booking', 'double-booking.js', {
            'API_A': api_a, 'API_B': api_b, 'TOKEN_A': a[1], 'TOKEN_B': b[2],
        }, 99, ['confirmed_bookings:count<=1']),
    ]
    report = {'recordedAt': datetime.now(timezone.utc).isoformat(), 'runs': records}
    (RESULTS / 'latest.json').write_text(json.dumps(report, indent=2) + '\n')
    print_conclusion(report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-results', action='store_true', help='Explain the saved run without restarting or making requests.')
    args = parser.parse_args()
    if args.show_results:
        report = json.loads((RESULTS / 'latest.json').read_text())
        print(f"Saved run: {report['recordedAt']}")
        for record in report['runs']:
            print_run(record)
        print_conclusion(report)
    else:
        main()
