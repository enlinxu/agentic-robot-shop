"""Create a namespace-local provider Secret from a hidden prompt, never argv/files."""
import argparse
import getpass
import json
import subprocess

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--context', required=True)
    p.add_argument('--namespace', default='agentic-robot-shop')
    p.add_argument('--name', default='agent-provider')
    a = p.parse_args()
    key = getpass.getpass('Gemini API key (hidden): ').strip()
    if not key: p.error('Key cannot be empty')
    doc = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': a.name, 'namespace': a.namespace},
           'type': 'Opaque', 'stringData': {'GEMINI_API_KEY': key}}
    subprocess.run(['kubectl', '--context', a.context, 'create', '-f', '-'], input=json.dumps(doc), text=True, check=True)
