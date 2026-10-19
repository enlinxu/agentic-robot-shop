"""Sustained, self-restoring fault runs for the Causely demo.

Each run switches shop-mcp into a fault mode, keeps provider-free shopping sessions flowing so a
monitoring product has real failing traffic to reason about, and always puts the mode back to healthy,
also on Ctrl-C or SIGTERM. Run one scenario at a time, and wait for the previous incident to clear.

    export KUBE_CONTEXT=YOUR_CONTEXT
    python3 scripts/demo_faults.py contract_violation
    python3 scripts/demo_faults.py unhandled_exception --minutes 15
"""
import argparse
import signal
import subprocess
from collections import namedtuple
from manage import CONTEXT, NS, mode

Scenario = namedtuple('Scenario', 'mode gap_seconds minutes expect')
SCENARIOS = {
    'contract_violation': Scenario(
        'schema_mismatch', 30.0, 10,
        'search_products returns price as text while the server reports success; every journey fails client-side. '
        'Expect "MCP Tool Contract Violation" on search_products in about 2 to 4 minutes.'),
    'unhandled_exception': Scenario(
        'unhandled_exception', 1.0, 15,
        'shop-mcp logs a real traceback and answers HTTP 500 to every request. Expect "Faulty Error Handling in '
        'HTTP Path" after about 7 minutes, then "Python Unhandled Exception" on shop-mcp after 10 to 13 minutes.'),
}

# Runs inside the shopping-load pod: one provider-free session every gap seconds until the time is up.
TRAFFIC = '''
import json, sys, time, urllib.error, urllib.request
gap, total = float(sys.argv[1]), float(sys.argv[2])
end, ok, failed = time.time() + total, 0, 0
while time.time() < end:
    started = time.time()
    req = urllib.request.Request('http://shopping-load:8080/session', data=json.dumps({'profile': 'shop'}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        ok += bool(json.loads(urllib.request.urlopen(req, timeout=60).read()).get('ok'))
    except urllib.error.URLError:
        failed += 1
    time.sleep(max(0.0, gap - (time.time() - started)))
print('sessions ok=%d failed=%d' % (ok, failed), flush=True)
'''


def drive_traffic(gap_seconds, minutes):
    """Run the in-pod session loop and wait for it; stop it if this process is interrupted."""
    child = subprocess.Popen(['kubectl', '--context', CONTEXT, '-n', NS, 'exec', 'deployment/shopping-load', '--',
                              'python', '-c', TRAFFIC, str(gap_seconds), str(minutes * 60)])
    try:
        child.wait()
    finally:
        if child.poll() is None:
            child.terminate()


def run(name, minutes=None):
    scenario = SCENARIOS[name]
    minutes = minutes or scenario.minutes
    print(f'{name}: {scenario.expect}')

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        mode(scenario.mode)
        print(f'mode={scenario.mode}; sending sessions every {scenario.gap_seconds:g}s for {minutes} min')
        drive_traffic(scenario.gap_seconds, minutes)
    finally:
        mode('healthy')
        print('restored: mode=healthy. Wait until the incident clears (about 14 minutes) before the next run.')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('scenario', choices=sorted(SCENARIOS))
    p.add_argument('--minutes', type=int, help='how long to keep the fault on (default depends on the scenario)')
    args = p.parse_args()
    run(args.scenario, args.minutes)
