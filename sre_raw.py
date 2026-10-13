"""Read-only raw observability tools. No fault-control access."""
import json
import os
import re
from pathlib import Path
from typing import Literal

import httpx
from trace_evidence import decode_trace

NS = os.getenv('SHOP_NAMESPACE', 'agentic-robot-shop')
SERVICES = ('shopping-load', 'shopping-agent', 'shop-mcp', 'catalogue', 'cart', 'user', 'mongodb', 'redis', 'web')
Metric = Literal['cpu', 'cpu_throttling', 'memory', 'restarts', 'ready', 'desired_replicas', 'available_replicas']
METRICS = {
    'cpu': 'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="agentic-robot-shop",container!="",container!="POD"}[5m]))',
    'cpu_throttling': 'sum by (pod) (rate(container_cpu_cfs_throttled_periods_total{namespace="agentic-robot-shop",container!=""}[5m])) / clamp_min(sum by (pod) (rate(container_cpu_cfs_periods_total{namespace="agentic-robot-shop",container!=""}[5m])), 0.001)',
    'memory': 'sum by (pod) (container_memory_working_set_bytes{namespace="agentic-robot-shop",container!="",container!="POD"})',
    'restarts': 'kube_pod_container_status_restarts_total{namespace="agentic-robot-shop"}',
    'ready': 'kube_pod_status_ready{namespace="agentic-robot-shop",condition="true"}',
    'desired_replicas': 'kube_deployment_spec_replicas{namespace="agentic-robot-shop"}',
    'available_replicas': 'kube_deployment_status_replicas_available{namespace="agentic-robot-shop"}',
}
METRICS = {k: v.replace('namespace="agentic-robot-shop"', 'namespace="' + NS + '"') for k, v in METRICS.items()}


def redact(value):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = re.sub(r'AIza[\w-]{30,}', '[REDACTED]', text)
    text = re.sub(r'(?i)(bearer\s+)[\w.\-/+=]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|password|token|secret)\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', text)
    return text


def service_name(name):
    if name not in SERVICES:
        raise ValueError('Only Robot Shop application services are allowed')
    return name


class RawTools:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(timeout=8)
        self.prom = os.getenv('PROMETHEUS_URL', 'http://prometheus:9090')
        self.tempo = os.getenv('TRACE_STORE_URL', 'http://tempo:3200')
        self.kube = 'https://kubernetes.default.svc'

    async def close(self):
        await self.client.aclose()

    async def get(self, url, params=None):
        r = await self.client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def kubernetes(self, path, params=None, text=False):
        token = Path('/var/run/secrets/kubernetes.io/serviceaccount/token').read_text().strip()
        # A dedicated client ensures Kubernetes credentials never reach another API.
        async with httpx.AsyncClient(verify='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt', timeout=8) as client:
            r = await client.get(self.kube + path, params=params, headers={'Authorization': 'Bearer ' + token})
            r.raise_for_status()
            return r.text if text else r.json()

    async def metrics(self, metric: Metric):
        if metric not in METRICS:
            raise ValueError('Unsupported metric')
        data = await self.get(self.prom + '/api/v1/query', {'query': METRICS[metric]})
        if data.get('status') != 'success':
            raise ValueError('Prometheus query failed')
        return {'source': 'prometheus', 'query': METRICS[metric], 'data': data['data'],
                'note': 'Empty series means unavailable telemetry, not a healthy value. CPU uses a five-minute window.'}

    async def workload(self):
        pods = await self.kubernetes(f'/api/v1/namespaces/{NS}/pods')
        deploy = await self.kubernetes(f'/apis/apps/v1/namespaces/{NS}/deployments')
        endpoints = await self.kubernetes(f'/api/v1/namespaces/{NS}/endpoints')
        return {'source': 'kubernetes', 'namespace': NS,
                'deployments': [{'name': d['metadata']['name'], 'desired': d['spec'].get('replicas', 1),
                                 'available': d.get('status', {}).get('availableReplicas', 0)} for d in deploy['items']],
                'endpoints': [{'service': e['metadata']['name'], 'ready_addresses': sum(len(s.get('addresses', [])) for s in e.get('subsets', [])),
                               'not_ready_addresses': sum(len(s.get('notReadyAddresses', [])) for s in e.get('subsets', []))}
                              for e in endpoints['items'] if e['metadata']['name'] in SERVICES],
                'pods': [{'name': p['metadata']['name'], 'app': p['metadata'].get('labels', {}).get('app'),
                          'phase': p.get('status', {}).get('phase'),
                          'conditions': [{'type': c.get('type'), 'status': c.get('status'), 'reason': c.get('reason')}
                                         for c in p.get('status', {}).get('conditions', []) if c.get('type') == 'Ready'],
                          'containers': [{'name': c['name'], 'ready': c.get('ready'), 'restarts': c.get('restartCount'),
                                          'state': c.get('state')} for c in p.get('status', {}).get('containerStatuses', [])],
                          'resources': [{'name': c['name'], 'resources': c.get('resources', {})} for c in p['spec']['containers']]}
                         for p in pods['items'] if p['metadata'].get('labels', {}).get('app') in SERVICES]}

    async def logs(self, service, previous=False):
        service_name(service)
        pods = await self.kubernetes(f'/api/v1/namespaces/{NS}/pods', {'labelSelector': 'app=' + service})
        rows = []
        for p in pods['items'][:2]:
            name = p['metadata']['name']
            try:
                content = await self.kubernetes(f'/api/v1/namespaces/{NS}/pods/{name}/log',
                                               {'tailLines': 40, 'limitBytes': 6000, 'sinceSeconds': 1800, 'timestamps': 'true', 'previous': str(previous).lower()}, text=True)
                rows.append({'pod': name, 'text': redact(content)})
            except httpx.HTTPError as exc:
                rows.append({'pod': name, 'unavailable': type(exc).__name__})
        return {'source': 'kubernetes pod logs', 'service': service, 'logs': rows,
                'note': 'No central log backend configured; empty recent logs do not prove health.'}

    async def events(self):
        data = await self.kubernetes(f'/api/v1/namespaces/{NS}/events')
        rows = sorted(data['items'], key=lambda e: e.get('lastTimestamp') or e.get('metadata', {}).get('creationTimestamp', ''))[-25:]
        return {'source': 'kubernetes events', 'events': [{'time': e.get('lastTimestamp'), 'type': e.get('type'),
                'reason': e.get('reason'), 'object': e.get('involvedObject', {}).get('name'), 'message': redact(e.get('message', ''))[:500]} for e in rows]}

    async def search_traces(self, service='shopping-load'):
        service_name(service)
        data = await self.get(self.tempo + '/api/search', {'q': '{ resource.service.namespace = "' + NS + '" && resource.service.name = "' + service + '" }', 'limit': 8})
        return {'source': 'tempo', 'traces': [{k: t[k] for k in ('traceID', 'rootServiceName', 'rootTraceName', 'startTimeUnixNano', 'durationMs') if k in t}
                                             for t in data.get('traces', [])[:8]],
                'note': 'Recent stored trace headers; empty results may mean ingestion lag. Use read_trace for native spans.'}

    async def trace(self, trace_id):
        if not re.fullmatch(r'[0-9a-f]{32}', trace_id):
            raise ValueError('Expected a 32-character lowercase hex trace ID')
        # Existing filtered export preserves native SDK exception type/message;
        # Tempo adds actual backend spans. Neither endpoint runs shopping actions.
        sources, errors = [], []
        for service in ('shopping-load', 'shopping-agent'):
            try:
                rows = await self.get(f'http://{service}:8080/traces/{trace_id}')
                if isinstance(rows, list):
                    sources.extend(rows)
            except (httpx.HTTPError, ValueError) as exc:
                errors.append({'source': service, 'error': type(exc).__name__})
        try:
            data = await self.get(self.tempo + '/api/traces/' + trace_id)
            sources.extend(decode_trace(data, trace_id))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            errors.append({'source': 'tempo', 'error': type(exc).__name__})
        # Prefer native local exception evidence over Tempo's filtered event types.
        rows = {}
        for row in sources:
            if row.get('trace_id') == trace_id:
                rows.setdefault(row['span_id'], row)
        ordered = sorted(rows.values(), key=lambda r: r['start_ns'])
        origin = min((r['start_ns'] for r in ordered), default=0)
        # Compact raw fields, not a precomputed diagnosis. ASGI send/receive
        # bookkeeping is omitted explicitly; native application/tool/DB spans
        # and error events remain available within the model's bounded context.
        relevant = [r for r in ordered if ' http send' not in r['name'] and ' http receive' not in r['name']]
        spans = [{'id': r['span_id'], 'parent': r.get('parent_span_id'), 'service': r.get('service_name'),
                  'name': r['name'], 'status': r['status'], 'start_ms': round((r['start_ns'] - origin) / 1e6, 3),
                  'duration_ms': round((r['end_ns'] - r['start_ns']) / 1e6, 3),
                  'attributes': r.get('attributes', {}), 'events': r.get('events', [])}
                 for r in relevant[:60]]
        return {'source': 'native OTel spans (Tempo and application evidence copy)', 'trace_id': trace_id,
                'spans': spans, 'total_native_spans': len(ordered), 'omitted_spans': len(ordered) - len(spans),
                'unavailable_sources': errors}
