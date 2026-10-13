"""Provider-free budget failure: verify tools and caller as separate outcomes."""
import json
import time
from pathlib import Path

from verify import request

ROOT=Path(__file__).resolve().parent
FIELDS=['MCPCallsObserved','MCPCallErrors','OutputValidationErrors']


def native_outcome(rows):
    # One outer native client span per actual call; unannotated inner SDK spans
    # represent the same calls and must not double the count.
    tools=[s for s in rows if s['kind']=='CLIENT'
           and s['attributes'].get('mcp.method.name')=='tools/call'
           and s['attributes'].get('server.address')=='shop-mcp']
    agents=[s for s in rows if s['attributes'].get('gen_ai.operation.name')=='invoke_agent'
            and s['kind']=='INTERNAL' and s['status']=='ERROR']
    budget_error=any(e.get('exception.type','').rsplit('.',1)[-1]=='UsageLimitExceeded'
                     for s in agents for e in s.get('events',[]))
    byid={s['span_id']:s for s in rows}
    caller_error=False
    for agent in agents:
        parent=byid.get(agent.get('parent_span_id'));seen=set()
        for _ in range(64):
            if not parent or parent['span_id'] in seen or parent.get('service_name')!=agent.get('service_name'):break
            seen.add(parent['span_id'])
            if parent['kind']=='SERVER' and (parent['attributes'].get('http.request.method') or parent['attributes'].get('http.method')):
                caller_error=parent['status']=='ERROR'
                break
            parent=byid.get(parent.get('parent_span_id'))
    return {'tool_calls':len(tools),'tool_errors':sum(s['status']=='ERROR' for s in tools),
            'native_budget_error':budget_error,'native_http_caller_error':caller_error}


def run():
    result=request('session',{'profile':'loop'},service='shopping-load')
    assert result['status']==502 and result['body']['provider_calls']==0,result
    rows=request('traces/'+result['body']['trace_id'],service='shopping-load')['body']
    native=native_outcome(rows)
    assert native=={'tool_calls':6,'tool_errors':0,'native_budget_error':True,'native_http_caller_error':True},native
    (ROOT/'native-loop-result.json').write_text(json.dumps({'result':result,'spans':rows,'native':native},indent=2)+'\n')
    print(json.dumps(native))

if __name__=='__main__':run()
