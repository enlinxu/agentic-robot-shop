import unittest
from native_loop_verify import native_outcome


class NativeLoopTests(unittest.TestCase):
    def test_observer_does_not_redirect_shopping_harness_namespace(self):
        import verify
        self.assertEqual(verify.NS,'agentic-robot-shop')

    def test_nested_sdk_spans_do_not_duplicate_tool_observations(self):
        outer={'kind':'CLIENT','status':'UNSET','attributes':{'mcp.method.name':'tools/call','server.address':'shop-mcp'}}
        inner=dict(outer,attributes={'mcp.method.name':'tools/call'})
        agent={'kind':'INTERNAL','status':'ERROR','attributes':{'gen_ai.operation.name':'invoke_agent'},'events':[{'exception.type':'pydantic_ai.exceptions.UsageLimitExceeded'}]}
        http={'kind':'SERVER','status':'ERROR','attributes':{'http.response.status_code':502}}
        for i,row in enumerate((outer,inner,agent,http)):row['span_id']=str(i)
        agent.update(parent_span_id=http['span_id'],service_name='shopping-load')
        http.update(service_name='shopping-load',attributes={'http.method':'POST'})
        result=native_outcome([outer,inner,agent,http])
        self.assertEqual(result,{'tool_calls':1,'tool_errors':0,'native_budget_error':True,'native_http_caller_error':True})

    def test_unrelated_agent_error_does_not_establish_budget_failure(self):
        agent={'kind':'INTERNAL','status':'ERROR','attributes':{'gen_ai.operation.name':'invoke_agent'},'events':[{'exception.type':'RuntimeError'}]}
        tool={'kind':'CLIENT','status':'ERROR','attributes':{'mcp.method.name':'tools/call','server.address':'shop-mcp'}}
        agent['span_id']='agent';tool['span_id']='tool'
        result=native_outcome([agent,tool])
        self.assertFalse(result['native_budget_error'])
        self.assertEqual(result['tool_errors'],1)
