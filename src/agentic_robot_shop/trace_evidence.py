"""Filtered native distributed evidence; local spans remain available on outage."""
import base64
import os
import httpx
from agentic_robot_shop.telemetry import ALLOWED

ATTRIBUTES = set(ALLOWED) | {'db.system', 'db.system.name', 'db.operation', 'db.operation.name', 'net.peer.port'}


def identifier(value, size):
    if not value:
        return None
    if len(value) == size * 2 and all(c in '0123456789abcdef' for c in value):
        return value
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) != size:
        raise ValueError('Invalid span identifier')
    return decoded.hex()


def attributes(values):
    result = {}
    for item in values:
        value = item.get('value', {})
        for kind in ('stringValue', 'intValue', 'doubleValue', 'boolValue'):
            if kind in value:
                result[item['key']] = value[kind]
                break
    return result


def decode_trace(document, trace_id):
    rows = []
    if not isinstance(document, dict):
        return rows
    for batch in document.get('batches', document.get('resourceSpans', [])):
        resource = attributes(batch.get('resource', {}).get('attributes', []))
        if resource.get('service.namespace', resource.get('k8s.namespace.name')) != os.getenv('SHOP_NAMESPACE', 'agentic-robot-shop'):
            continue
        for scope in batch.get('scopeSpans', []):
            for span in scope.get('spans', []):
                if identifier(span.get('traceId'), 16) != trace_id:
                    continue
                attrs = attributes(span.get('attributes', []))
                # Exception messages can contain application payloads. Retain type
                # only here; the local exporter supplies bounded SDK evidence.
                events = []
                for event in span.get('events', []):
                    event_attrs = attributes(event.get('attributes', []))
                    events.append({'name': event.get('name'), 'exception.type': event_attrs.get('exception.type'), 'message_prefix': ''})
                status = span.get('status', {}).get('code', 'STATUS_CODE_UNSET')
                rows.append({'trace_id': trace_id, 'span_id': identifier(span.get('spanId'), 8),
                             'parent_span_id': identifier(span.get('parentSpanId'), 8),
                             'service_name': resource.get('service.name'), 'evidence_source': 'otel-trace-store',
                             'name': span.get('name', '')[:250], 'scope': scope.get('scope', {}).get('name', ''),
                             'kind': str(span.get('kind', 'SPAN_KIND_UNSPECIFIED')).removeprefix('SPAN_KIND_'),
                             'status': {0: 'UNSET', 1: 'OK', 2: 'ERROR'}.get(status, str(status).removeprefix('STATUS_CODE_')),
                             'attributes': {k: v for k, v in attrs.items() if k in ATTRIBUTES}, 'events': events,
                             'start_ns': int(span['startTimeUnixNano']), 'end_ns': int(span['endTimeUnixNano'])})
    return rows


async def distributed_spans(trace_id, local_rows):
    if len(trace_id) != 32 or any(c not in '0123456789abcdef' for c in trace_id):
        raise ValueError('Invalid trace ID')
    remote = []
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(os.getenv('TRACE_STORE_URL', 'http://tempo:3200') + '/api/traces/' + trace_id,
                                        headers={'Accept': 'application/json'})
            response.raise_for_status()
            remote = decode_trace(response.json(), trace_id)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    # Same spans can be present in both exports; prefer bounded local SDK events.
    merged = {row['span_id']: row for row in remote}
    merged.update({row['span_id']: row for row in local_rows if row.get('trace_id') == trace_id})
    return sorted(merged.values(), key=lambda row: row['start_ns'])
