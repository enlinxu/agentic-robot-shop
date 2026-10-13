# Kubernetes manifests

These are checked-in YAML manifests, not just a Python installer. They deploy the
Robot Shop storefront, catalogue/cart/user services, MongoDB, Redis, MCP facade,
shopping agent, synthetic workload service and AI SRE. Default model access and
the background traffic CronJob are disabled.

## Install from scratch

You need a Linux Kubernetes cluster with a dynamic default StorageClass, or edit
the PVCs in `base/storage.yaml` to specify your provisioner. Build/publish the six
application images first, or run the repository's **Publish images** workflow.
The manifests do not imply those registry images have already been published.

```sh
git clone https://github.com/enlinxu/agentic-robot-shop.git
cd agentic-robot-shop

python3 build.py --registry YOUR_REGISTRY/robot-shop --tag v0.1.0 --push
```

Edit the six `newName` entries in **`k8s/base/kustomization.yaml`** to use your
registry paths. Set `newTag` to the tag you built. For private registries, provide
`imagePullSecrets` in the relevant pod templates (including the traffic job).

```sh
# Review the complete namespace, storage, RBAC, deployments, services and job.
kubectl kustomize k8s

kubectl --context YOUR_CONTEXT apply -k k8s
kubectl --context YOUR_CONTEXT -n agentic-robot-shop \
  wait --for=condition=Available deployment --all --timeout=300s
kubectl --context YOUR_CONTEXT -n agentic-robot-shop get pods,pvc
kubectl --context YOUR_CONTEXT -n agentic-robot-shop port-forward svc/web 8080:8080
```

Open http://localhost:8080 for Robot Shop; `/lab` runs real shopping with free
synthetic decisions. `/assistant` and SRE AI mode require explicit paid enablement.
Port-forwarding needs no Ingress controller. The application namespace is
`agentic-robot-shop`; for another namespace use `deploy.py render --namespace ...`
so runtime telemetry identity and SRE query filters change with it, rather than
only rewriting Kubernetes metadata.

## File map

| Files | Resources |
| --- | --- |
| `base/namespace.yaml` | Application namespace |
| `base/storage.yaml` | Database, cart, session, MCP evidence and SRE PVCs |
| `base/rbac.yaml` | SRE service account and namespace-scoped read permissions |
| `base/catalogue.yaml`, `cart.yaml`, `user.yaml`, `web.yaml` | Original shopping-path Deployments and Services |
| `base/mongodb.yaml`, `redis.yaml` | Real databases |
| `base/shop-mcp.yaml`, `shopping-agent.yaml`, `shopping-load.yaml` | Agent/MCP/workload Deployments and Services |
| `base/sre-baseline.yaml` | Separate raw-data investigator |
| `base/traffic.yaml` | Suspended provider-free shopping CronJob |
| `base/kustomization.yaml` | Resource list and editable image registry/tag mappings |
| `overlays/paid/` | Explicit provider Secret references and paid-mode enablement |
| `observability/` | Optional namespace-local collector and Tempo assets |

## Optional telemetry stack

```sh
kubectl --context YOUR_CONTEXT apply -k k8s/observability
```

This installs only the supplied collector and Tempo. For Prometheus/Grafana and
infrastructure metrics, follow [the observability guide](../docs/observability.md).
If using its monitoring chart, set the SRE container's `PROMETHEUS_URL` in
`base/sre-baseline.yaml` to `http://monitoring-prometheus:9090`. Use your existing
stack's addresses instead when integrating with an existing installation. Agent
and workload `TRACE_STORE_URL` and `OTEL_EXPORTER_OTLP_ENDPOINT` are configurable
in their Deployment YAML.

## Optional paid chat and AI SRE

After the namespace exists:

```sh
python3 provider_secret.py --context YOUR_CONTEXT
kubectl --context YOUR_CONTEXT apply -k k8s/overlays/paid
```

The overlay inherits the same images and resources from `base`; it enables paid
mode only for shopping chat/SRE and references `agent-provider/GEMINI_API_KEY`.
It does not grant additional SRE Kubernetes permissions. Reapplying `-k k8s`
disables paid mode again. Your Secret remains until explicitly removed.

## Removal

```sh
kubectl --context YOUR_CONTEXT delete -k k8s
```

The base contains PVCs and the namespace: removal deletes demo state, including
any other resources placed in that namespace. Use it only for a dedicated demo
namespace. No teardown command is run automatically.
