# Demo deployment boundaries

This application keeps upstream Robot Shop behavior and dependencies for demonstrations.
It has no application authentication boundary around lab/SRE APIs. Paid model access
is disabled by default; use an authenticated ingress for shared paid deployments.
The raw-data SRE has only namespace-scoped read permissions and no repair/exec tools.
Fault harnesses require operator permissions and must target a dedicated demo namespace.

Build-time npm audits reported vulnerabilities in retained cart/user dependencies,
and some upstream libraries are deprecated. This release does not claim those
services are hardened for production. Review/upgrade dependencies before broader
exposure; concurrency and idempotency defects are intentionally documented demo
subjects rather than silently fixed here. No real payment/checkout route is deployed.

Keep provider keys in Kubernetes Secrets, avoid committing generated runtime evidence,
and redact exported telemetry according to the connected workload's data policy.
