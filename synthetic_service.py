"""Provider-free HTTP workload driver using actual agent/MCP/backend execution.

Run as a separate process: importing synthetic_load prohibits provider requests.
The named scenarios are load inputs, not a natural-language shopping assistant.
"""
import asyncio
import httpx
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from trace_evidence import distributed_spans
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings
from synthetic_load import run_session
from telemetry import setup, read_spans

provider=setup('shopping-load')
Agent.instrument_all(InstrumentationSettings(tracer_provider=provider,include_content=False,include_binary_content=False,include_model_request_parameters=False))
app=FastAPI(title='Synthetic Robot Shop workload driver')
FastAPIInstrumentor.instrument_app(app,tracer_provider=provider)
GATE=asyncio.Semaphore(8)

class Workload(BaseModel):
    profile: Literal['shop','loop','add_known','retry_add']='shop'

@app.get('/healthz')
def health():
    return {'synthetic_model':True,'provider_calls':0,'max_concurrent_sessions':8}

@app.post('/session')
async def session(body:Workload):
    async with GATE:
        result=await run_session(0,body.profile)
    return JSONResponse(result,status_code=200 if result['ok'] else 502)

@app.get('/traces/{trace_id}')
async def traces(trace_id:str):
    try:rows=[dict(row,evidence_source='shopping-load') for row in read_spans(trace_id)]
    except ValueError:raise HTTPException(400,'Invalid trace ID')
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response=await client.get('http://shop-mcp:8080/traces/'+trace_id)
            response.raise_for_status()
            remote=response.json()
            if isinstance(remote,list):rows.extend(row for row in remote if isinstance(row,dict) and row.get('trace_id')==trace_id)
    except (httpx.HTTPError,ValueError):pass
    return await distributed_spans(trace_id,rows)
