"""Optional namespace-local Tempo/OTel assets; emits a Kubernetes List."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def documents(namespace, storage_class=None):
    def meta(name): return {'name': name, 'namespace': namespace}
    for name, file in [('tempo', 'tempo.yaml'), ('otel-collector', 'collector.yaml')]:
        yield {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': meta(name), 'data': {file: (ROOT / file).read_text()}}
        ports = [3200, 4317, 4318] if name == 'tempo' else [4317, 4318, 8889]
        container = {'name': name, 'image': 'grafana/tempo:3.0.0' if name == 'tempo' else 'otel/opentelemetry-collector-contrib:0.161.0',
                     'args': ['-config.file=/etc/config/tempo.yaml', '-target=all'] if name == 'tempo' else ['--config=/etc/config/collector.yaml'],
                     'ports': [{'containerPort': port} for port in ports],
                     'resources': {'requests': {'cpu': '50m', 'memory': '256Mi'}, 'limits': {'memory': '1Gi' if name == 'tempo' else '512Mi'}},
                     'volumeMounts': [{'name': 'config', 'mountPath': '/etc/config', 'readOnly': True}]}
        pod = {'automountServiceAccountToken': False, 'containers': [container], 'volumes': [{'name': 'config', 'configMap': {'name': name}}]}
        if name == 'tempo':
            spec = {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '3Gi'}}}
            if storage_class is not None: spec['storageClassName'] = storage_class
            yield {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': meta('tempo'), 'spec': spec}
            pod['securityContext'] = {'runAsNonRoot': True, 'runAsUser': 10001, 'fsGroup': 10001}
            pod['volumes'].extend([{'name': 'data', 'persistentVolumeClaim': {'claimName': 'tempo'}}, {'name': 'work', 'emptyDir': {}}])
            container['volumeMounts'].extend([{'name': 'data', 'mountPath': '/data/tempo'}, {'name': 'work', 'mountPath': '/var/tempo'}])
            container['readinessProbe'] = {'httpGet': {'path': '/ready', 'port': 3200}, 'initialDelaySeconds': 10}
        yield {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': meta(name), 'spec': {'replicas': 1, 'strategy': {'type': 'Recreate'},
               'selector': {'matchLabels': {'app': name}}, 'template': {'metadata': {'labels': {'app': name}}, 'spec': pod}}}
        yield {'apiVersion': 'v1', 'kind': 'Service', 'metadata': meta(name), 'spec': {'selector': {'app': name},
               'ports': [{'name': 'http' if port == 3200 else 'metrics' if port == 8889 else 'otlp-' + ('grpc' if port == 4317 else 'http'), 'port': port, 'targetPort': port} for port in ports]}}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--namespace', default='agentic-robot-shop')
    p.add_argument('--storage-class')
    a = p.parse_args()
    print(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': list(documents(a.namespace, a.storage_class))}, indent=2))
