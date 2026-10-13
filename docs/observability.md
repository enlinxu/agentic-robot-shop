# Optional observability stack

You can use an existing stack. Set the app renderer's OTLP, Tempo and Prometheus
URLs, and configure your collector to accept traces from the app namespace. No
application install changes an existing monitoring release or collector.

After installing the application namespace, for an isolated demo, the supplied assets install namespace-local OTel collector
and Tempo; the official kube-prometheus-stack chart provides Prometheus, Grafana,
kube-state-metrics and kubelet metrics. Helm installation creates its own cluster
RBAC/CRDs and may require administrator permissions. Skip it if you already have
monitoring or cannot grant those permissions.

```sh
# Omit --storage-class to use your default provisioner.
python3 observability/render.py --namespace agentic-robot-shop > rendered-observability.json
kubectl --context YOUR_CONTEXT apply -f rendered-observability.json

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --version 91.9.0 --kube-context YOUR_CONTEXT --namespace agentic-robot-shop \
  -f observability/monitoring-values.yaml
```

Chart values add scrapes for the collector's spanmetrics and upstream cart metrics,
and add Tempo as a Grafana data source. The namespace-local endpoints are:

| Service | Endpoint |
| --- | --- |
| OTel collector | `http://otel-collector:4317` (gRPC), port 4318 for HTTP |
| Tempo | `http://tempo:3200` |
| Prometheus | `http://monitoring-prometheus:9090` |
| Grafana | `grafana:80` |

Pass `--prometheus-url http://monitoring-prometheus:9090` to `deploy.py install`
alongside your registry/tag. Verify actual Services with `kubectl get svc` if your
chart naming differs. Forward Grafana with `kubectl ... port-forward svc/grafana
3000:80`, obtain its generated administrator password using the chart's release
notes, and use Explore to query Prometheus or an actual Tempo trace ID.

Collector image is pinned to 0.161.0 and Tempo to 3.0.0. The stack is a local
single-instance demo layout, not a distributed production observability platform.
Tempo uses a 3 Gi PVC and six-hour trace retention. The collector derives span
metrics; it does not fabricate failures or business invariants. No log backend is
installed. Under network policies, allow app → collector, collector → Tempo,
Prometheus → metrics endpoints and SRE → telemetry/Kubernetes APIs. Paid mode also
needs provider egress. Reuse your normal TLS/auth configuration for external APIs;
additional bearer-header support is not built into the SRE raw-tool clients.

Confirm data rather than assuming installation proves ingestion: run `/lab`,
inspect its native trace, search that ID in Tempo, check scrape targets, and run
the SRE free tool preflight. Missing sources are reported as unavailable.
