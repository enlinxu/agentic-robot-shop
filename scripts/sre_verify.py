"""Real app-only incident + raw SRE investigation; always restore catalogue.

Default is provider-free preflight. --ai authorizes one bounded model run.
The fault action is deliberately excluded from the investigator's input.
"""
import argparse
import json
import time
from pathlib import Path
from _paths import REPO_ROOT, EVIDENCE_ROOT
from manage import kube, NS

ROOT = EVIDENCE_ROOT


def request(service, path, data=None, timeout=110):
    source = (
        'import urllib.request,json,urllib.error; '
        f'url="http://{service}:8080{path}"; '
        f'data={repr(json.dumps(data))}.encode() if {data is not None!r} else None; '
        'req=urllib.request.Request(url,data=data,headers={"Content-Type":"application/json"}); '
        '\ntry: r=urllib.request.urlopen(req,timeout=' + str(timeout) + '); print(r.read().decode())'
        '\nexcept urllib.error.HTTPError as e: print(e.read().decode())'
    )
    return json.loads(kube('-n', NS, 'exec', 'deployment/shopping-load', '--', 'python', '-c', source))


def main(ai=False):
    report = {'scenario': 'catalogue has no serving replicas', 'synthetic_shopper': True,
              'shopper_provider_calls': 0, 'ai_requested': ai, 'restored': False}
    original = json.loads(kube('-n', NS, 'get', 'deploy/catalogue', '-o', 'json'))['spec'].get('replicas', 1)
    if original < 1:
        raise RuntimeError('Catalogue is already unavailable; refusing to replace existing fault state')
    if request('shop-mcp', '/healthz')['mode'] != 'healthy':
        raise RuntimeError('Existing MCP fault detected; refusing to change it')
    report['baseline'] = request('shopping-load', '/session', {'profile': 'shop'})
    if not report['baseline'].get('ok'):
        raise RuntimeError('Healthy shopping preflight failed; no fault injected')
    try:
        kube('-n', NS, 'scale', 'deployment/catalogue', '--replicas=0')
        # Endpoint removal can precede kube-proxy updates and process exit.
        # Wait for actual pod deletion so an in-flight healthy request is not
        # misrepresented as a reproduced outage.
        kube('-n', NS, 'wait', '--for=delete', 'pod', '-l', 'app=catalogue', '--timeout=60s')
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            endpoints = json.loads(kube('-n', NS, 'get', 'endpoints/catalogue', '-o', 'json'))
            if not any(s.get('addresses') for s in endpoints.get('subsets', [])):
                break
            time.sleep(1)
        else:
            raise RuntimeError('Catalogue endpoints did not become unavailable')
        report['failed_shopping'] = request('shopping-load', '/session', {'profile': 'shop'})
        if report['failed_shopping'].get('ok'):
            raise RuntimeError('Expected failed shopping request was successful')
        time.sleep(3)  # Allow native batch export; local span evidence is also available.
        run = request('sre-baseline', '/sre/api/runs', {
            'incident': 'Robot Shop shoppers cannot find products using the shopping assistant. Investigate this failed shopping request, identify the likely cause, and recommend recovery with supporting raw evidence.',
            'trace_id': report['failed_shopping']['trace_id'], 'mode': 'ai' if ai else 'preflight'})
        if 'id' not in run:
            raise RuntimeError('SRE investigation was not accepted: ' + str(run))
        report['investigation_id'] = run['id']
        print('Shopping failure captured; investigation ' + run['id'], flush=True)
        deadline = time.monotonic() + 200
        while time.monotonic() < deadline:
            run = request('sre-baseline', '/sre/api/runs/' + run['id'])
            if run['status'] != 'running':
                report['investigation'] = run
                break
            time.sleep(2)
        else:
            raise RuntimeError('Investigation did not finish within its bounded deadline')
    finally:
        kube('-n', NS, 'scale', 'deployment/catalogue', '--replicas=' + str(original))
        kube('-n', NS, 'rollout', 'status', 'deployment/catalogue', '--timeout=120s')
        report['recovery'] = request('shopping-load', '/session', {'profile': 'shop'})
        report['restored'] = report['recovery'].get('ok', False)
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / 'sre-live-verification.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'investigation_id': report.get('investigation_id'), 'investigation_status': report.get('investigation', {}).get('status'),
                          'restored': report['restored'],
                          'report': report.get('investigation', {}).get('report'), 'error': report.get('investigation', {}).get('error')}, indent=2), flush=True)
    if not report['restored']:
        raise RuntimeError('Recovery check failed')
    if report.get('investigation', {}).get('status') != 'completed':
        raise RuntimeError('Investigation failed; see preserved evidence')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ai', action='store_true', help='Run one bounded paid-model investigation instead of free tool preflight')
    main(parser.parse_args().ai)
