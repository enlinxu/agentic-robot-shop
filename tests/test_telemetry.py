"""Transport metadata is standard, scoped to each concurrent tool call."""
import asyncio
import unittest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from agentic_robot_shop.telemetry import MCPPeerProcessor, MCP_PEER, mcp_peer_callback

class MCPPeerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider=TracerProvider()
        self.exporter=InMemorySpanExporter()
        self.provider.add_span_processor(MCPPeerProcessor())
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))

    def tearDown(self):
        self.provider.shutdown()

    async def test_concurrent_peers_are_isolated_and_credentials_not_exported(self):
        async def run(url):
            async def call(name,args):
                await asyncio.sleep(0)
                with self.provider.get_tracer('fastmcp').start_as_current_span(name,kind=trace.SpanKind.CLIENT):
                    await asyncio.sleep(0)
            await mcp_peer_callback(url)(None,call,'tools/call',{})
            self.assertIsNone(MCP_PEER.get())
        await asyncio.gather(run('http://one:8080/mcp?key=secret'),run('https://user:secret@two/mcp'))
        peers={(s.attributes['server.address'],s.attributes['server.port']) for s in self.exporter.get_finished_spans()}
        self.assertEqual(peers,{('one',8080),('two',443)})
        self.assertNotIn('secret',str(peers))

    async def test_exception_resets_context_and_inner_sdk_is_not_annotated(self):
        async def call(name,args):
            with self.provider.get_tracer('mcp-python-sdk').start_as_current_span(name,kind=trace.SpanKind.CLIENT):
                raise RuntimeError('actual failure')
        with self.assertRaises(RuntimeError):
            await mcp_peer_callback('http://shop-mcp:8080/mcp')(None,call,'tools/call',{})
        self.assertIsNone(MCP_PEER.get())
        self.assertNotIn('server.address',self.exporter.get_finished_spans()[0].attributes)

    async def test_preserve_native_peer_and_ignore_server_spans(self):
        async def call(name,args):
            tracer=self.provider.get_tracer('fastmcp')
            with tracer.start_as_current_span(name,kind=trace.SpanKind.CLIENT,attributes={'server.address':'native','server.port':99}):pass
            with tracer.start_as_current_span(name,kind=trace.SpanKind.SERVER):pass
        await mcp_peer_callback('http://shop-mcp:8080/mcp')(None,call,'tools/call',{})
        client,server=self.exporter.get_finished_spans()
        self.assertEqual(client.attributes['server.address'],'native')
        self.assertEqual(client.attributes['server.port'],99)
        self.assertNotIn('server.address',server.attributes)

    async def test_invalid_transport(self):
        for url in ('stdio://local','not a url','http:///mcp'):
            with self.assertRaises(ValueError):mcp_peer_callback(url)


class SyntheticModelPeerTests(unittest.TestCase):
    def spans(self, scope='pydantic-ai', name='chat m', attrs=None):
        from agentic_robot_shop.telemetry import SyntheticModelPeerProcessor
        provider_=TracerProvider()
        exporter=InMemorySpanExporter()
        provider_.add_span_processor(SyntheticModelPeerProcessor())
        provider_.add_span_processor(SimpleSpanProcessor(exporter))
        a={'gen_ai.request.model':'m'}
        a.update(attrs or {})
        with provider_.get_tracer(scope).start_as_current_span(name,kind=trace.SpanKind.CLIENT,attributes=a):pass
        provider_.shutdown()
        return exporter.get_finished_spans()[0].attributes

    def test_simulated_model_gets_synthetic_address(self):
        a=self.spans()
        self.assertEqual((a['server.address'],a['server.port']),('generativelanguage.googleapis.com',443))

    def test_real_provider_and_existing_address_are_untouched(self):
        self.assertNotIn('server.address',self.spans(name='POST generativelanguage'))
        self.assertEqual(self.spans(attrs={'server.address':'native'})['server.address'],'native')
        self.assertNotIn('server.address',self.spans(scope='other'))
