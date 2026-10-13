"""Standard OTel export plus a small local evidence copy; no prompts or keys."""
import json
import os
from pathlib import Path
from threading import Lock
from contextvars import ContextVar
from urllib.parse import urlsplit
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

LOCK = Lock()
MCP_PEER = ContextVar('mcp_transport_peer', default=None)


class MCPPeerProcessor(SpanProcessor):
    """Attach the configured transport peer to the outer FastMCP client span.

    The inner SDK span describes the same call, so only the wrapper receives
    destination metadata. This supplies standard OTel transport attributes;
    it makes no claim about validation or the caller's outcome.
    """
    def on_start(self, span, parent_context=None):
        peer = MCP_PEER.get()
        if (peer and span.kind == trace.SpanKind.CLIENT
                and span.instrumentation_scope.name == 'fastmcp'):
            if not span.attributes.get('server.address'):
                span.set_attribute('server.address', peer[0])
                span.set_attribute('server.port', peer[1])


def mcp_peer_callback(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('MCP transport must have an HTTP(S) destination')
    peer = (parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))

    async def process(ctx, call_tool, name, args):
        token = MCP_PEER.set(peer)
        try:
            return await call_tool(name, args)
        finally:
            MCP_PEER.reset(token)
    return process
PATH = Path(os.getenv('STATE_DIR', '/state')) / 'spans.jsonl'
ALLOWED = ('gen_ai.operation.name', 'gen_ai.agent.name', 'gen_ai.tool.name', 'gen_ai.request.model', 'gen_ai.provider.name', 'gen_ai.usage.input_tokens', 'gen_ai.usage.output_tokens', 'mcp.method.name', 'server.address', 'server.port', 'net.peer.name', 'net.peer.port', 'http.method', 'http.request.method', 'http.response.status_code', 'error.type')

class EvidenceExporter(SpanExporter):
    def export(self, spans):
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOCK, PATH.open('a') as f:
            for s in spans:
                f.write(json.dumps({'service_name':s.resource.attributes.get('service.name'),'trace_id': format(s.context.trace_id, '032x'), 'span_id': format(s.context.span_id, '016x'), 'parent_span_id': format(s.parent.span_id, '016x') if s.parent else None, 'name': s.name, 'kind': s.kind.name, 'scope': s.instrumentation_scope.name, 'status': s.status.status_code.name, 'attributes': {k: v for k, v in s.attributes.items() if k in ALLOWED}, 'events': [{'name': e.name, 'exception.type': e.attributes.get('exception.type'), 'message_prefix': str(e.attributes.get('exception.message', '')).split('\n')[0][:250]} for e in s.events], 'start_ns': s.start_time, 'end_ns': s.end_time})+'\n')
            # Bounded evidence copy, collector remains the full telemetry destination.
            if PATH.stat().st_size > 10_000_000:
                lines=PATH.read_text().splitlines()[-2000:]
                PATH.write_text('\n'.join(lines)+'\n')
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def setup(name):
    provider = TracerProvider(resource=Resource.create({'service.name': name, 'service.namespace': os.getenv('SHOP_NAMESPACE', 'agentic-robot-shop'), 'k8s.namespace.name': os.getenv('SHOP_NAMESPACE', 'agentic-robot-shop'), 'k8s.cluster.name': os.getenv('CLUSTER_NAME', 'demo'), 'k8s.pod.name': os.getenv('POD_NAME', ''), 'k8s.pod.uid': os.getenv('POD_UID', '')}))
    provider.add_span_processor(MCPPeerProcessor())
    provider.add_span_processor(SimpleSpanProcessor(EvidenceExporter()))
    endpoint=os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT')
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint,insecure=os.getenv('OTEL_EXPORTER_OTLP_INSECURE', 'false' if endpoint.startswith('https://') else 'true').lower() == 'true')))
    trace.set_tracer_provider(provider)
    return provider


def read_spans(trace_id):
    if len(trace_id)!=32 or any(c not in '0123456789abcdef' for c in trace_id):
        raise ValueError('Invalid trace ID')
    with LOCK:
        if not PATH.exists():return []
        lines=PATH.read_text().splitlines()
    return sorted([row for line in lines if (row:=json.loads(line))['trace_id']==trace_id],key=lambda row:row['start_ns'])
