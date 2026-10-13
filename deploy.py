"""Render/apply portable Kubernetes resources; default installation is provider-free."""
import argparse
import json
import re
import subprocess

SERVICES = ('mongodb', 'redis', 'catalogue', 'cart', 'user', 'shop-mcp', 'shopping-agent', 'shopping-load', 'sre-baseline', 'web')


def options(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', nargs='?', choices=['render', 'install'], default='render')
    p.add_argument('--context', help='Required for install; never uses an implicit context')
    p.add_argument('--namespace', default='agentic-robot-shop')
    p.add_argument('--registry', default='ghcr.io/enlinxu/agentic-robot-shop')
    p.add_argument('--tag', default='v0.1.0')
    p.add_argument('--cluster-name', default='demo')
    p.add_argument('--storage-class', help='Omit to use the cluster default provisioner')
    p.add_argument('--host', help='Optional ingress hostname; port-forward requires no ingress')
    p.add_argument('--ingress-class', default='nginx')
    p.add_argument('--image-pull-secret')
    p.add_argument('--otel-endpoint', default='http://otel-collector:4317')
    p.add_argument('--trace-store-url', default='http://tempo:3200')
    p.add_argument('--prometheus-url', default='http://prometheus:9090')
    p.add_argument('--paid', action='store_true', help='Enable bounded real-model chat/SRE; requires provider Secret')
    p.add_argument('--provider-secret', default='agent-provider')
    p.add_argument('--model', default='google:gemini-2.5-flash-lite')
    p.add_argument('--traffic', action='store_true', help='Enable one provider-free shopping journey per minute')
    a = p.parse_args(argv)
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', a.namespace):
        p.error('namespace must be a DNS label')
    if a.action == 'install' and not a.context:
        p.error('install requires --context')
    return a


def documents(a):
    ns = a.namespace
    def meta(name): return {'name': name, 'namespace': ns}
    yield {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns}}
    for name in ('mongodb', 'redis', 'shop-mcp', 'shopping-agent', 'sre-baseline'):
        spec = {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '3Gi' if name == 'mongodb' else '1Gi'}}}
        if a.storage_class is not None: spec['storageClassName'] = a.storage_class
        yield {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': meta(name), 'spec': spec}
    yield {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': meta('sre-baseline')}
    yield {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': meta('sre-baseline'), 'rules': [
        {'apiGroups': [''], 'resources': ['pods', 'pods/log', 'events', 'endpoints'], 'verbs': ['get', 'list']},
        {'apiGroups': ['apps'], 'resources': ['deployments'], 'verbs': ['get', 'list']}]}
    yield {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': meta('sre-baseline'),
           'subjects': [{'kind': 'ServiceAccount', 'name': 'sre-baseline', 'namespace': ns}],
           'roleRef': {'kind': 'Role', 'name': 'sre-baseline', 'apiGroup': 'rbac.authorization.k8s.io'}}
    for name in SERVICES:
        role = {'shop-mcp': 'shop_mcp', 'shopping-agent': 'agent', 'shopping-load': 'synthetic_service', 'sre-baseline': 'sre_service'}.get(name)
        image = 'redis:7.4-alpine' if name == 'redis' else f'{a.registry.rstrip("/")}/{"agent" if role else name}:{a.tag}'
        port = 27017 if name == 'mongodb' else 6379 if name == 'redis' else 8080
        env = [{'name': 'OTEL_SERVICE_NAME', 'value': name}, {'name': 'SHOP_NAMESPACE', 'value': ns},
               {'name': 'CLUSTER_NAME', 'value': a.cluster_name}, {'name': 'OTEL_EXPORTER_OTLP_ENDPOINT', 'value': a.otel_endpoint},
               {'name': 'OTEL_EXPORTER_OTLP_PROTOCOL', 'value': 'grpc'}, {'name': 'OTEL_TRACES_EXPORTER', 'value': 'otlp'},
               {'name': 'OTEL_METRICS_EXPORTER', 'value': 'none'}, {'name': 'OTEL_LOGS_EXPORTER', 'value': 'none'},
               {'name': 'OTEL_NODE_ENABLED_INSTRUMENTATIONS', 'value': 'http,express,mongodb,redis,redis-4'},
               {'name': 'PYDANTIC_AI_NO_BANNER', 'value': '1'},
               {'name': 'POD_NAME', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.name'}}},
               {'name': 'POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}},
               {'name': 'TRACE_STORE_URL', 'value': a.trace_store_url}, {'name': 'PROMETHEUS_URL', 'value': a.prometheus_url}]
        c = {'name': name, 'image': image, 'ports': [{'containerPort': port}], 'env': env,
             'resources': {'requests': {'cpu': '20m', 'memory': '128Mi'}, 'limits': {'memory': '768Mi'}}}
        pod = {'automountServiceAccountToken': name == 'sre-baseline', 'containers': [c]}
        if a.image_pull_secret: pod['imagePullSecrets'] = [{'name': a.image_pull_secret}]
        if name == 'sre-baseline': pod['serviceAccountName'] = 'sre-baseline'
        if name in ('catalogue', 'cart', 'user'):
            env.append({'name': 'OTEL_RESOURCE_ATTRIBUTES', 'value': f'service.namespace={ns},k8s.namespace.name={ns},k8s.cluster.name={a.cluster_name},k8s.pod.name=$(POD_NAME),k8s.pod.uid=$(POD_UID)'})
        if role:
            c['command'] = ['uvicorn', role + ':app', '--host', '0.0.0.0', '--port', '8080', '--no-access-log']
            env.append({'name': 'STATE_DIR', 'value': '/state' if name in ('shop-mcp', 'shopping-agent', 'sre-baseline') else '/tmp/evidence'})
        if name in ('shopping-agent', 'sre-baseline'):
            env.extend([{'name': 'PAID_MODEL_ENABLED', 'value': str(a.paid).lower()}, {'name': 'AGENT_MODEL', 'value': a.model},
                        {'name': 'SRE_MODEL', 'value': a.model}, {'name': 'DAILY_TURN_LIMIT', 'value': '20'}, {'name': 'SRE_DAILY_LIMIT', 'value': '4'}])
            if a.paid: env.append({'name': 'GEMINI_API_KEY', 'valueFrom': {'secretKeyRef': {'name': a.provider_secret, 'key': 'GEMINI_API_KEY'}}})
        if name in ('mongodb', 'redis', 'shop-mcp', 'shopping-agent', 'sre-baseline'):
            mount = '/data/db' if name == 'mongodb' else '/data' if name == 'redis' else '/state'
            c['volumeMounts'] = [{'name': 'state', 'mountPath': mount}]
            pod['volumes'] = [{'name': 'state', 'persistentVolumeClaim': {'claimName': name}}]
        if name == 'redis': c['args'] = ['redis-server', '--appendonly', 'yes']
        c['readinessProbe'] = ({'tcpSocket': {'port': port}, 'initialDelaySeconds': 10} if name in ('mongodb', 'redis') else
                               {'httpGet': {'path': '/healthz' if role else '/' if name == 'web' else '/health', 'port': port}, 'initialDelaySeconds': 5})
        yield {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': meta(name), 'spec': {'replicas': 1,
               'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': {'app': name}},
               'template': {'metadata': {'labels': {'app': name}}, 'spec': pod}}}
        yield {'apiVersion': 'v1', 'kind': 'Service', 'metadata': meta(name), 'spec': {'selector': {'app': name}, 'ports': [{'name': 'http' if port == 8080 else 'tcp', 'port': port, 'targetPort': port}]}}
    job = {'containers': [{'name': 'traffic', 'image': f'{a.registry.rstrip("/")}/agent:{a.tag}',
            'command': ['python', '-c', 'import urllib.request; urllib.request.urlopen(urllib.request.Request("http://shopping-load:8080/session",data=b\'{"profile":"shop"}\',headers={"Content-Type":"application/json"}),timeout=100).read()']}],
           'restartPolicy': 'Never', 'automountServiceAccountToken': False}
    if a.image_pull_secret: job['imagePullSecrets'] = [{'name': a.image_pull_secret}]
    yield {'apiVersion': 'batch/v1', 'kind': 'CronJob', 'metadata': meta('synthetic-shopping-traffic'), 'spec': {
        'schedule': '* * * * *', 'suspend': not a.traffic, 'concurrencyPolicy': 'Forbid', 'successfulJobsHistoryLimit': 1,
        'failedJobsHistoryLimit': 1, 'jobTemplate': {'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 110, 'template': {'spec': job}}}}}
    if a.host:
        yield {'apiVersion': 'networking.k8s.io/v1', 'kind': 'Ingress', 'metadata': meta('robot-shop'), 'spec': {
            'ingressClassName': a.ingress_class, 'rules': [{'host': a.host, 'http': {'paths': [{'path': '/', 'pathType': 'Prefix',
            'backend': {'service': {'name': 'web', 'port': {'number': 8080}}}}]}}]}}


if __name__ == '__main__':
    args = options()
    doc = {'apiVersion': 'v1', 'kind': 'List', 'items': list(documents(args))}
    if args.action == 'render': print(json.dumps(doc, indent=2))
    else:
        if args.paid:
            subprocess.run(['kubectl', '--context', args.context, '-n', args.namespace, 'get', 'secret', args.provider_secret], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['kubectl', '--context', args.context, 'apply', '-f', '-'], input=json.dumps(doc), text=True, check=True)
