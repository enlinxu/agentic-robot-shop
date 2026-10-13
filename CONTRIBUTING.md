# Contributing

Keep shopping decisions, native telemetry and observable business state distinct.
Do not fabricate spans or claim a failed caller implies a rolled-back write.
Synthetic workloads must remain labeled and provider-free by default.

## Repository layout

- `src/agentic_robot_shop/`: installable Python runtime, with explicit package imports.
- `src/agentic_robot_shop/resources/`: packaged prompts and SRE HTML.
- `scripts/`: build/deploy/credential helpers and operator-run failure experiments.
- `tests/`: Python unit/SDK tests and JavaScript trace-view checks.
- `vendor/robot-shop/`: attributed upstream storefront, Node services and seed data.
- `k8s/`: checked-in deployment resources and overlays.
- `observability/`: collector/Tempo/monitoring configuration.
- `docs/`, `assets/`: walkthroughs, telemetry contract and illustrations.
- `evidence/`: generated operator results; ignored by Git.

## Local development

Use Python 3.13 and Node 22. Install the runtime into a virtual environment:

```sh
python3.13 -m venv .venv
. .venv/bin/activate
pip install -e .
PYTHONPATH=src:scripts python -m unittest discover -s tests -p 'test_*.py'
node tests/test_trace_view.js
```

Operator scripts run directly, for example `python3 scripts/deploy.py render`.
Build/deploy scripts use only the standard library; they do not require installing
the application package just to render manifests or build containers.

The pinned dependency list remains in `requirements.txt`; `pyproject.toml` reads
that same list instead of maintaining conflicting copies. Do not publish generated
runtime evidence, provider credentials or private cluster configuration.

## Validate changes

```sh
docker build -t robot-shop-tests .
docker run --rm -e PYTHONPATH=/app/src:/app/scripts -v "$PWD:/app:ro" \
  robot-shop-tests python -m unittest discover -s /app/tests -p 'test_*.py'
kubectl kustomize k8s
kubectl kustomize k8s/overlays/paid
kubectl kustomize k8s/observability
```

Update both `scripts/deploy.py` and checked-in YAML when changing deployment
behavior. Python container entrypoints are `agentic_robot_shop.<module>:app`.
Package data must include prompts and the SRE page in built wheels, not only a
source checkout. CI verifies tests, package resources, rendering and Kustomize.

Keep changes focused, add tests for new behavior, and preserve upstream license
and attribution. Fault experiments need explicit `KUBE_CONTEXT` and a dedicated
namespace; unit tests must not contact a paid provider or mutate a real cluster.

## Layout migration

Earlier root-level script commands now live under `scripts/`; runtime imports use
`agentic_robot_shop`. Rebuild the agent image before applying the reorganized
manifests, because their qualified entrypoints require the packaged image. Use a
fresh image tag and update the image mappings when upgrading. Existing
running deployments do not change when you update this repository checkout.
