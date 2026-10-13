# Telemetry contract

## Traces

Python uses standard OTel SDK/OTLP export, Pydantic AI agent/model/tool
instrumentation, native MCP client/server spans and FastAPI/ASGI/HTTPX spans.
Node services preload standard automatic HTTP/Express/MongoDB/Redis instrumentation.
Default transport is OTLP/gRPC to `OTEL_EXPORTER_OTLP_ENDPOINT`; point it at your
collector. Python TLS behavior follows the endpoint scheme, with
`OTEL_EXPORTER_OTLP_INSECURE` available as an explicit override. Enterprise
authentication or mTLS exporters require additional environment/collector setup.

Resource identity includes `service.name`, configurable `service.namespace`,
`k8s.namespace.name`, `k8s.cluster.name`, and actual pod name/UID. Set
`SHOP_NAMESPACE` and `CLUSTER_NAME`; generated manifests do this automatically.

Native fields, where emitted by the producer, include:

| Family | Attributes/events |
| --- | --- |
| Agent/model | `gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.request.model`, `gen_ai.provider.name`, input/output token usage |
| Tools/MCP | `gen_ai.tool.name`, `mcp.method.name`; native SDK validation/transport exception events |
| HTTP | Method, status, path/route/URL and destination attributes, depending on instrumentation version |
| Database | MongoDB/Redis operation and system attributes |
| Errors | Native span ERROR status, exception type and instrumentation-provided messages |

Semantic conventions are evolving; attributes are not guaranteed on every span.
Do not interpret absent evidence as zero errors. The outer client tool span and
inner SDK protocol span may describe the same call: summing both double-counts it.

Synthetic workloads still execute Pydantic AI, MCP, HTTP, databases and real carts.
Their model spans identify a FunctionModel, and the application session span has
`test.synthetic=true`. Their decisions do not demonstrate real-model reasoning,
natural-language understanding or real-model thrashing.

## Browser evidence versus full export

The application writes an allowlisted span copy bounded to approximately 10 MB.
The assistant/lab trace endpoints merge those native copies with namespace-filtered
Tempo results. The viewer preserves real span IDs, parent IDs and start/end timing;
it does not create missing spans or infer a causal diagnosis from timestamps.

Displayed copies omit prompts, tool argument/content bodies, database statements,
remote exception messages and stack traces. Local SDK exception prefixes are
bounded and may contain application values. Agent instrumentation disables prompt
and response content, but backend auto-instrumentation/full OTLP export can carry
URLs or database details outside the display allowlist. Configure collector
redaction for sensitive workloads; do not treat the browser filter as export-wide
redaction. Process crashes can lose spans never ended/exported.

## Metrics

Agent/MCP processes currently export traces, not direct OTel application metrics.
Native model spans carry token usage when available; simulated token counts are
not provider billing. The upstream cart exposes `/metrics`, including its
`items_added` counter. Catalogue/user do not expose a complete application RED
metrics contract.

The optional collector's spanmetrics connector derives calls/errors/duration
histograms from received spans and exposes Prometheus text on port 8889. Inspect
its scrape output for actual normalized metric names. Dimensions include service,
span name/status and configured agent/tool/MCP fields. Keep those labels bounded;
metrics summarize observations, not successful business outcomes.

The optional kube-prometheus-stack supplies kube-state-metrics and kubelet
container CPU/memory/throttling metrics. The SRE tools query namespace-scoped CPU,
throttling, memory, restart, readiness and desired/available replica series. Missing
series or a five-minute CPU window are explicit limitations, not health evidence.

## Logs and persistence

Node services emit structured request/application logs to stdout. Python SDK
errors are visible in native exception spans; process output remains available
through `kubectl logs`. The SRE reads bounded recent Kubernetes pod logs/events,
not a centralized log database. No Loki backend is supplied.

MongoDB, Redis, shopping history/budgets, MCP evidence and SRE runs use PVCs.
Shopping-load evidence is temporary. Disposable carts expire after one hour.
Optional Tempo retention is six hours; old screenshot trace IDs may have expired.
SQLite daily budgets reset by UTC date; their reservations survive pod restart.

## Interpretation boundaries

An HTTP 200 does not guarantee an MCP tool produced usable output. A failed caller
does not prove a cart write rolled back. A successful span does not prove no lost
update occurred. Demonstrating duplicate/lost writes requires actual cart state,
an idempotency outcome or application invariant in addition to ordinary telemetry.
