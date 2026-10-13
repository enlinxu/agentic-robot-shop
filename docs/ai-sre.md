# AI SRE and provider costs

The shopping agent and SRE are separate services using Pydantic AI and the default
model `google:gemini-2.5-flash-lite`. Choose another available Gemini model with
`--model`. Provider access is disabled by default; enable `--paid` with a
namespace-local `agent-provider` Secret. Synthetic shopping has no API credentials
and prohibits provider requests in its process.

## Raw-data investigator

The SRE chooses read-only tools for Prometheus metrics, native distributed traces,
Kubernetes deployments/pods/endpoints, pod logs and events. It sees a customer
incident and optional failed trace ID. Tools are namespace-scoped with fixed API
destinations; the service account cannot read Secrets, exec into pods or write
workloads. Environment values are not returned. API failures, empty series and
truncated evidence are visible rather than converted into health observations.

Each run persists tool arguments/results, evidence IDs, elapsed time, report and
model usage. Findings must cite actual successful tool results. That validation
cannot guarantee a claim is logically correct. Confidence is the model's judgment,
not an externally calibrated probability. The UI exposes the evidence trail,
not private model chain of thought. No automated repair is implemented.

The tool preflight is explicitly **not AI reasoning**. It checks data sources
without contacting a model. Paid AI mode can succeed, remain uncertain, or hit a
guardrail; unsuccessful runs remain recorded. The final model request is reserved
for a report rather than spending the entire budget on investigation tools.

## Enforced limits

| | Shopping chat | AI SRE |
| --- | --- | --- |
| Daily reservations | 20 submitted turns | 4 investigations |
| Model requests per run | 6 | 6 |
| Tool calls per run | 8 | 12 |
| Output tokens per model request | 512 | 1,000 |
| Pre-request input tokens | 6,000 | 14,000 |
| Total usage limit | 12,000 tokens | 35,000 tokens |
| Deadline | 90 seconds | 180 seconds |

SQLite reservations persist across restart and include failed runs; days use UTC.
These are SDK/workload controls, not a provider-side dollar cap. A final response
can cross the total usage threshold. Use the provider's usage/billing account for
actual charges. Bulk synthetic traffic, fault harnesses and free preflight do not
consume paid model requests. Real chat and AI investigations do.

Historical validation used real provider requests. The illustrated completed SRE
run used six model requests with 32,762 input/345 output tokens. Earlier attempts
hit input/request limits while the loop was improved. New model versions and
telemetry content can produce different tool choices, costs and conclusions.

Full content is excluded from agent span instrumentation, but local investigation
records contain the evidence shown to the model. Apply your normal retention and
data-sharing rules before connecting non-demo telemetry to an external provider.
