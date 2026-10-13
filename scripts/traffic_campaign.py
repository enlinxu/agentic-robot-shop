"""Bounded provider-free HTTP load. Execute inside a shopping-load pod.

The fixed endpoint runs the actual instrumented agent/MCP/backend chain. The
server prohibits provider requests; this client never contacts the paid chat.
"""
import argparse
import asyncio
import json
import math
import time

import httpx


def summarize(rows,elapsed):
    durations=sorted(row['seconds'] for row in rows)
    def percentile(fraction):
        return durations[max(0,math.ceil(len(durations)*fraction)-1)] if durations else None
    return {'sessions':len(rows),'errors':sum(row['status']!=200 for row in rows),
            'elapsed_seconds':round(elapsed,4),'sessions_per_second':round(len(rows)/elapsed,4) if elapsed else 0,
            'p50_seconds':percentile(.5),'p95_seconds':percentile(.95),
            'reported_tool_calls':sum(row.get('tool_calls') or 0 for row in rows),
            'sessions_without_tool_count':sum(row.get('tool_calls') is None for row in rows),
            'provider_calls':0,'synthetic_model':True,'samples':rows}


async def campaign(client,sessions,concurrency,rps):
    rows=[];next_index=0;next_start=time.monotonic();start=next_start
    async def worker():
        nonlocal next_index,next_start
        while next_index<sessions:
            index=next_index;next_index+=1
            # Event-loop assignments are atomic until the first await. Pacing
            # follows actual admissions, so a slow backend creates no catch-up
            # burst and no unbounded list of pending HTTP requests.
            slot=max(next_start,time.monotonic());next_start=slot+1/rps
            await asyncio.sleep(max(0,slot-time.monotonic()))
            began=time.monotonic()
            try:
                response=await client.post('http://shopping-load:8080/session',json={'profile':'shop'},timeout=100)
                body=response.json()
                if body.get('provider_calls')!=0 or body.get('synthetic_model') is not True:
                    raise ValueError('Workload service did not confirm provider-free simulation')
                row={'index':index,'status':response.status_code,'trace_id':body.get('trace_id'),
                     'cart_id':body.get('cart_id'),'tool_calls':body.get('tool_calls')}
                if response.status_code!=200:row['error_type']=body.get('error_type')
            except httpx.HTTPError as exc:
                row={'index':index,'status':0,'error_type':type(exc).__name__}
            row['seconds']=round(time.monotonic()-began,4);rows.append(row)
    workers=[asyncio.create_task(worker()) for _ in range(concurrency)]
    try:
        await asyncio.gather(*workers)
    finally:
        for task in workers:
            if not task.done():task.cancel()
        await asyncio.gather(*workers,return_exceptions=True)
    return summarize(sorted(rows,key=lambda row:row['index']),time.monotonic()-start)


async def main(args):
    async with httpx.AsyncClient() as client:
        result=await campaign(client,args.sessions,args.concurrency,args.rps)
    result['limits']={'sessions':args.sessions,'concurrency':args.concurrency,'rps':args.rps}
    print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--sessions',type=int,default=40)
    parser.add_argument('--concurrency',type=int,default=4)
    parser.add_argument('--rps',type=float,default=4)
    args=parser.parse_args()
    if not 1<=args.sessions<=200 or not 1<=args.concurrency<=8 or not 0<args.rps<=20:
        parser.error('sessions1..200, concurrency1..8, rps>0..20')
    asyncio.run(main(args))
