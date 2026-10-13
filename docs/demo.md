# Demo and failure walkthrough

Start healthy at `/lab`. Run **Shop and verify**: search within EUR 70, inspect two
products in parallel, add an available robot and read its actual Redis-backed cart.
Inspect the agent/MCP/HTTP/database span tree. The displayed trace is execution
evidence, not a precomputed root-cause story.

In paid mode, `/assistant` supports requests such as “Compare robots under EUR
100,” “Add one Stan,” and “What's in my cart?” The assistant shares the storefront
cart ID and retains conversation history. The model decides its actual tool calls.

## Incident → evidence → recovery

Set explicit operator context before any fault harness:

```sh
export KUBE_CONTEXT=YOUR_CONTEXT
export SHOP_NAMESPACE=agentic-robot-shop
```

| Scenario | Command | What to inspect |
| --- | --- | --- |
| Advertised output contract rejected | `python3 manage.py schema_mismatch`, then `/lab`; restore `python3 manage.py healthy` | Native client rejects text price where a number is required; HTTP transport may still succeed. |
| Missing structured output | `python3 manage.py missing_output`; restore healthy | SDK behavior with content that lacks required structured output. |
| Catalogue unavailable | `python3 chaos.py outage` | Search/add fail; existing cart reads can work; actual service endpoints and failed tool spans. |
| Slow write/ambiguous result | `python3 chaos.py slow_write` | Caller timeout followed by actual cart commit; a failed turn does not imply rollback. |
| Bounded retry duplicates write | `python3 chaos.py retry_write` | Failed caller plus two committed items; inspect actual cart contents. |
| CPU contention | `python3 chaos.py cpu_pressure` | Temporary real CPU quota, cgroup throttling, backend/tool latency; slow cold startup can prevent the load phase. |
| Concurrent lost updates | `python3 chaos.py concurrent_cart` | Successful acknowledgements versus persisted quantity; ordinary error metrics can miss this defect. |
| Redis write refusal | `python3 redis_oom.py` | Actual noeviction OOM, writes failing while reads still work. |
| Database unavailable despite healthy app probe | `python3 mongo_outage.py` | Catalogue health reports OK while the data path fails; measure recovery with a real journey. |
| MCP pod replaced during a write | `python3 mcp_restart.py` | Pod UID changes, transport failure and later cart commit. |
| Admission waiting under slow tools | `python3 queue_pressure.py` | HTTP-to-agent start gap on an eight-session executor, plus actual timeouts. |
| Execution limit | `python3 native_loop_verify.py` or lab loop card | Six successful product calls then actual SDK UsageLimitExceeded; simulated repeated decisions are not evidence of LLM thrashing. |
| Stock changes while planning | `python3 stock_change.py` | Actual product stock changes between search/detail; policy selects another available product and restores stock. |

Except `verify.py` and SRE `--ai`, these harnesses use synthetic decisions or direct
tools and make no provider calls. They create disposable carts and write local
evidence JSON ignored by Git. Run sequentially. The harnesses restore snapshots
in `finally`, but process kill, failed cluster access or failed rollout can prevent
recovery; inspect replicas, environment, resources and Redis controls afterward.
Explicit MCP control modes remain active until you restore `healthy`.

## Raw-data AI SRE baseline

```sh
# Real catalogue incident plus free raw-tool preflight, not AI diagnosis:
python3 sre_verify.py

# Same incident with one bounded paid-model investigation (paid mode required):
python3 sre_verify.py --ai
```

The verifier establishes healthy shopping, removes catalogue serving replicas,
waits for pod deletion, captures a genuinely failed shopping request and gives the
SRE only the customer impact and trace ID. Its tools query raw state/telemetry;
they do not receive the injected-fault action. The verifier restores the original
replica count and checks another actual shopping journey.

Open `/sre` to inspect the model's selected tools, evidence IDs, conclusion,
uncertainty and advice. The SRE cannot execute repairs. A historical run correctly
identified catalogue unavailability but recommended a generic restart; that alone
does not restore a zero-replica deployment. Treat proposed repairs as advisory and
verify them against actual desired/available state.

## Sustained provider-free traffic

Enable `--traffic` during installation for one full shopping journey per minute.
The CronJob forbids overlap, has no retries and is suspended by default. Completed
pods are normal successful jobs. For a bounded paced campaign:

```sh
kubectl --context "$KUBE_CONTEXT" -n "$SHOP_NAMESPACE" exec -i deployment/shopping-load \
  -- python - --sessions 40 --concurrency 4 --rps 4 < traffic_campaign.py
```

Maximum 200 sessions, eight concurrent requests and target 20 arrivals/second;
missed arrivals do not create a catch-up burst. Measure actual achieved throughput,
latency and errors rather than assuming the requested rate was sustained.
