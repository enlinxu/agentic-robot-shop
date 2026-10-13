"""Fault-harness Kubernetes helpers: explicit context and configurable namespace."""
import argparse
import json
import os
import subprocess
from pathlib import Path
from _paths import REPO_ROOT, EVIDENCE_ROOT

ROOT = REPO_ROOT
NS = os.getenv('SHOP_NAMESPACE', 'agentic-robot-shop')
CONTEXT = os.getenv('KUBE_CONTEXT', '')


def kube(*args, doc=None):
    if not CONTEXT:
        raise ValueError('Set KUBE_CONTEXT explicitly before running cluster commands')
    return subprocess.run(['kubectl', '--context', CONTEXT, *args], input=json.dumps(doc) if doc else None,
                          text=True, capture_output=True, check=True).stdout


def mode(value):
    if value not in ('healthy', 'schema_mismatch', 'missing_output', 'catalogue_timeout'):
        raise ValueError('Unknown fault mode')
    source = 'import urllib.request,json; print(urllib.request.urlopen(urllib.request.Request("http://shop-mcp:8080/control",data=json.dumps({"mode":' + repr(value) + '}).encode(),headers={"Content-Type":"application/json"})).read().decode())'
    print(kube('-n', NS, 'exec', 'deployment/shopping-load', '--', 'python', '-c', source))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['healthy', 'schema_mismatch', 'missing_output', 'catalogue_timeout'])
    mode(p.parse_args().mode)
