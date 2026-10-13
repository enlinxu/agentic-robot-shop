"""Bounded, auditable AI SRE baseline; raw tools only, no remediation permissions."""
import asyncio
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from importlib.resources import files
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits, RunUsage
from agentic_robot_shop.sre_raw import RawTools, Metric, redact

STATE = Path(os.getenv('STATE_DIR', '/state'))
STATE.mkdir(parents=True, exist_ok=True)
DB = sqlite3.connect(STATE / 'sre.sqlite', check_same_thread=False)
DB.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, day TEXT, mode TEXT, document TEXT)')
DB.commit()
LOCK = asyncio.Lock()
TASKS = set()
app = FastAPI(title='Robot Shop raw-data AI SRE')


class Investigation(BaseModel):
    incident: str = Field(min_length=10, max_length=1500)
    trace_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    mode: Literal['ai', 'preflight'] = 'ai'


class Finding(BaseModel):
    claim: str
    evidence_ids: list[str]


class Report(BaseModel):
    conclusion: str
    confidence: Literal['low', 'medium', 'high']
    findings: list[Finding]
    alternatives_and_gaps: list[str]
    recommended_action: str
    verification: str


def save(run):
    DB.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?,?)', (run['id'], run['day'], run['mode'], json.dumps(run)))
    DB.commit()


def load(run_id):
    row = DB.execute('SELECT document FROM runs WHERE id=?', (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Investigation not found')
    return json.loads(row[0])


def validate_citations(report, evidence):
    valid = {e['id'] for e in evidence if e['status'] == 'ok'}
    if not report.findings or any(not f.evidence_ids or not set(f.evidence_ids) <= valid for f in report.findings):
        raise ValueError('Report must cite actual successful tool results for every finding')


async def reserve_report_request(ctx, tool_definition):
    """Finish with available evidence instead of spending the last call on tools."""
    if ctx.usage.requests >= 5 or ctx.usage.input_tokens >= 22000:
        return None
    return tool_definition


async def investigate(run):
    raw = RawTools()
    usage = RunUsage()
    start = time.monotonic()
    async def call(name, args, operation):
        entry = {'id': f'E{len(run["evidence"]) + 1}', 'tool': name, 'arguments': args,
                 'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running'}
        run['evidence'].append(entry)
        save(run)
        t = time.monotonic()
        try:
            result = await operation()
            content = redact(result)
            # Exact bounded content shown to the model is retained for review.
            entry['content'] = content[:5000]
            entry['truncated'] = len(content) > 5000
            entry['status'] = 'ok'
        except Exception as exc:
            entry['status'] = 'unavailable'
            entry['content'] = type(exc).__name__ + ': ' + redact(str(exc))[:350]
        entry['duration_seconds'] = round(time.monotonic() - t, 3)
        save(run)
        return {'evidence_id': entry['id'], 'status': entry['status'], 'content': entry['content'], 'truncated': entry.get('truncated', False)}

    try:
        if run['mode'] == 'preflight':
            await call('workload_state', {}, raw.workload)
            await call('query_metrics', {'metric': 'available_replicas'}, lambda: raw.metrics('available_replicas'))
            await call('search_traces', {'service': 'shopping-load'}, raw.search_traces)
            await call('pod_logs', {'service': 'shop-mcp'}, lambda: raw.logs('shop-mcp'))
            if run.get('trace_id'):
                await call('read_trace', {'trace_id': run['trace_id']}, lambda: raw.trace(run['trace_id']))
            run['status'] = 'completed'
            run['note'] = 'Provider-free tool preflight. No AI reasoning or diagnosis performed.'
        else:
            agent = Agent(os.getenv('SRE_MODEL', 'google:gemini-2.5-flash-lite'), output_type=Report,
                          name='robot-shop-raw-sre', instructions=files('agentic_robot_shop').joinpath('resources/sre_instructions.txt').read_text(encoding='utf-8'),
                          retries=0, model_settings={'temperature': 0.1, 'max_tokens': 1000})

            @agent.instructions
            def remaining_budget(ctx):
                remaining = 6 - ctx.usage.requests
                return f'{remaining} model requests remain. If investigation tools are unavailable, return your report now, with any uncertainty. Prioritize the supplied failed trace and workload state before browsing other traces.'

            @agent.tool_plain(prepare=reserve_report_request)
            async def query_metrics(metric: Metric) -> dict:
                """Query namespace-scoped Prometheus CPU, throttle, memory, restart, readiness or replica metrics."""
                return await call('query_metrics', {'metric': metric}, lambda: raw.metrics(metric))

            @agent.tool_plain(prepare=reserve_report_request)
            async def workload_state() -> dict:
                """Inspect current Robot Shop deployments, pods, resource limits and service endpoints."""
                return await call('workload_state', {}, raw.workload)

            @agent.tool_plain(prepare=reserve_report_request)
            async def pod_logs(service: str, previous: bool = False) -> dict:
                """Read bounded recent logs for a Robot Shop service, optionally from a previous container."""
                return await call('pod_logs', {'service': service, 'previous': previous}, lambda: raw.logs(service, previous))

            @agent.tool_plain(prepare=reserve_report_request)
            async def kubernetes_events() -> dict:
                """Read recent Robot Shop Kubernetes events; historical events may be unrelated."""
                return await call('kubernetes_events', {}, raw.events)

            @agent.tool_plain(prepare=reserve_report_request)
            async def search_traces(service: str = 'shopping-load') -> dict:
                """Find recent trace IDs from Tempo for one Robot Shop service."""
                return await call('search_traces', {'service': service}, lambda: raw.search_traces(service))

            @agent.tool_plain(prepare=reserve_report_request)
            async def read_trace(trace_id: str) -> dict:
                """Read native distributed spans including timings, parentage, errors and SDK exceptions."""
                return await call('read_trace', {'trace_id': trace_id}, lambda: raw.trace(trace_id))

            prompt = run['incident'] + ('\nReported failed request trace_id: ' + run['trace_id'] if run.get('trace_id') else '')
            result = await asyncio.wait_for(agent.run(prompt, usage=usage, usage_limits=UsageLimits(
                request_limit=6, tool_calls_limit=12, total_tokens_limit=35000,
                per_request_input_tokens_limit=14000, count_tokens_before_request=True)), timeout=180)
            validate_citations(result.output, run['evidence'])
            run['report'] = result.output.model_dump()
            run['usage'] = {'model_requests': result.usage.requests, 'tool_calls': result.usage.tool_calls,
                            'input_tokens': result.usage.input_tokens, 'output_tokens': result.usage.output_tokens}
            run['status'] = 'completed'
    except Exception as exc:
        run['status'] = 'failed'
        run['error'] = type(exc).__name__ + ': ' + redact(str(exc))[:500]
        # Failure may follow paid requests; never present it as provider-free.
    finally:
        await raw.close()
        if run['mode'] == 'ai':
            run['usage'] = {'model_requests': usage.requests, 'tool_calls': usage.tool_calls,
                            'input_tokens': usage.input_tokens, 'output_tokens': usage.output_tokens}
        run['duration_seconds'] = round(time.monotonic() - start, 3)
        save(run)


@app.get('/healthz')
def health():
    day = datetime.now(timezone.utc).date().isoformat()
    used = DB.execute("SELECT count(*) FROM runs WHERE day=? AND mode='ai'", (day,)).fetchone()[0]
    return {'status': 'ok', 'paid_model_enabled': os.getenv('PAID_MODEL_ENABLED','false').lower()=='true', 'data_access': 'read-only Robot Shop raw telemetry', 'mode': 'raw-observability',
            'model': os.getenv('SRE_MODEL', 'google:gemini-2.5-flash-lite'), 'ai_runs_used_today': used,
            'ai_daily_limit': int(os.getenv('SRE_DAILY_LIMIT', '4'))}


@app.get('/sre')
@app.get('/sre/')
def page():
    return HTMLResponse(files('agentic_robot_shop').joinpath('resources/sre.html').read_text(encoding='utf-8'))


@app.get('/sre/api/runs')
def recent_runs():
    return [json.loads(row[0]) for row in DB.execute('SELECT document FROM runs ORDER BY rowid DESC LIMIT 12')]


@app.get('/sre/api/runs/{run_id}')
def get_run(run_id: str):
    return load(run_id)


@app.post('/sre/api/runs', status_code=202)
async def start_run(body: Investigation):
    async with LOCK:
        if any(not t.done() for t in TASKS):
            raise HTTPException(409, 'An investigation is already running')
        day = datetime.now(timezone.utc).date().isoformat()
        if body.mode == 'ai':
            if os.getenv('PAID_MODEL_ENABLED', 'false').lower() != 'true':
                raise HTTPException(503, 'Paid AI investigation is disabled; use preflight or configure a provider Secret')
            used = DB.execute("SELECT count(*) FROM runs WHERE day=? AND mode='ai'", (day,)).fetchone()[0]
            if used >= int(os.getenv('SRE_DAILY_LIMIT', '4')):
                raise HTTPException(429, 'Daily AI investigation limit reached; preflight remains free')
        run = {'id': str(uuid.uuid4()), 'day': day, 'started_at': datetime.now(timezone.utc).isoformat(),
               **body.model_dump(), 'status': 'running', 'evidence': [],
               'provider_free': body.mode == 'preflight'}
        save(run)  # Reserve budget before scheduling, including failures.
        task = asyncio.create_task(investigate(run))
        TASKS.add(task)
        task.add_done_callback(TASKS.discard)
        return run


@app.on_event('startup')
def reconcile():
    for row in DB.execute('SELECT document FROM runs').fetchall():
        run = json.loads(row[0])
        if run['status'] == 'running':
            run.update(status='failed', error='Process restarted during investigation; evidence retained, budget remains reserved.')
            save(run)
