"""Actual model-driven shopping agent with native Pydantic AI instrumentation."""
import asyncio
import json
import os
import sqlite3
import uuid
import httpx
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.usage import UsageLimits
from pydantic_ai.messages import ModelMessagesTypeAdapter
from telemetry import mcp_peer_callback, setup, PATH,read_spans
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from trace_evidence import distributed_spans

provider=setup('shopping-agent')
Agent.instrument_all(InstrumentationSettings(tracer_provider=provider,include_content=False,include_binary_content=False,include_model_request_parameters=False))
app=FastAPI()
FastAPIInstrumentor.instrument_app(app,excluded_urls='healthz')
LOCK=asyncio.Lock()
STATE=Path(os.getenv('STATE_DIR','/state'));STATE.mkdir(parents=True,exist_ok=True)
DB=sqlite3.connect(STATE/'agent.sqlite',check_same_thread=False)
DB.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, history BLOB)')
DB.execute('CREATE TABLE IF NOT EXISTS budget(day TEXT PRIMARY KEY, turns INTEGER NOT NULL)');DB.commit()
INSTRUCTIONS=Path(__file__).with_name('instructions.txt').read_text()

class Chat(BaseModel):
    message:str=Field(min_length=1,max_length=1500)
    cart_id:uuid.UUID

@app.get('/healthz')
def health():
    day=datetime.now(timezone.utc).date().isoformat()
    row=DB.execute('SELECT turns FROM budget WHERE day=?',(day,)).fetchone()
    return {'status':'ok','paid_model_enabled':os.getenv('PAID_MODEL_ENABLED','false').lower()=='true','model':os.getenv('AGENT_MODEL','google:gemini-2.5-flash-lite'),'turns_used':row[0] if row else 0,'daily_turn_limit':int(os.getenv('DAILY_TURN_LIMIT','20'))}

@app.get('/traces/{trace_id}')
async def spans(trace_id:str):
    try:rows=[dict(row,evidence_source='shopping-agent') for row in read_spans(trace_id)]
    except ValueError:raise HTTPException(400,'Invalid trace ID')
    # Add the MCP service's filtered native evidence. No provider request or
    # fabricated span is involved; unavailable remote evidence leaves local rows.
    origin=os.getenv('MCP_URL','http://shop-mcp:8080/mcp').rsplit('/mcp',1)[0]
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            result=await client.get(origin+'/traces/'+trace_id)
            result.raise_for_status()
            remote=result.json()
            if isinstance(remote,list):rows.extend(remote)
    except (httpx.HTTPError,ValueError):pass
    return await distributed_spans(trace_id,rows)

@app.post('/chat')
async def chat(req:Chat):
    if os.getenv('PAID_MODEL_ENABLED', 'false').lower() != 'true':
        raise HTTPException(503, 'Paid shopping chat is disabled; use /lab or enable it with a provider Secret')
    async with LOCK:
        day=datetime.now(timezone.utc).date().isoformat()
        DB.execute('INSERT OR IGNORE INTO budget VALUES (?,0)',(day,))
        used=DB.execute('SELECT turns FROM budget WHERE day=?',(day,)).fetchone()[0]
        if used>=int(os.getenv('DAILY_TURN_LIMIT','20')):raise HTTPException(429,'Daily demo usage limit reached')
        # Reserve before calling provider, including failed turns; survives pod restart.
        DB.execute('UPDATE budget SET turns=turns+1 WHERE day=?',(day,));DB.commit()
        cart_id=str(req.cart_id)
        row=DB.execute('SELECT history FROM sessions WHERE id=?',(cart_id,)).fetchone()
        history=ModelMessagesTypeAdapter.validate_json(row[0]) if row else []
        mcp_url=os.getenv('MCP_URL','http://shop-mcp:8080/mcp')
        toolset=MCPToolset(mcp_url,process_tool_call=mcp_peer_callback(mcp_url),tool_error_behavior='error',max_retries=0,read_timeout=15)
        agent=Agent(os.getenv('AGENT_MODEL','google:gemini-2.5-flash-lite'),name='stan-shopping-agent',instructions=INSTRUCTIONS+'\nApplication cart_id: '+cart_id,toolsets=[toolset],retries=0,model_settings={'max_tokens':512,'temperature':0.1})
        # Native instrumentation supplies agent/tool/model spans and exceptions.
        with trace.get_tracer('robot-shop.application').start_as_current_span('shopping_turn') as span:
            trace_id=format(span.get_span_context().trace_id,'032x')
            try:
                result=await asyncio.wait_for(agent.run(req.message,message_history=history,usage_limits=UsageLimits(request_limit=6,tool_calls_limit=8,total_tokens_limit=12000,per_request_input_tokens_limit=6000,count_tokens_before_request=True)),timeout=90)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR)
                return __import__('fastapi').responses.JSONResponse({'completed':False,'answer':'The shopping task failed while contacting a model or tool. Please try again after the service recovers.','error_type':type(exc).__name__,'trace_id':trace_id,'cart_id':cart_id},status_code=502)
            DB.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)',(cart_id,ModelMessagesTypeAdapter.dump_json(result.all_messages()[-30:])));DB.commit()
            return {'completed':True,'answer':result.output,'trace_id':trace_id,'cart_id':cart_id,'usage':{'requests':result.usage.requests,'input_tokens':result.usage.input_tokens,'output_tokens':result.usage.output_tokens}}
