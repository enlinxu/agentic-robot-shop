"""Build the six application images; optionally push to your chosen registry."""
import argparse
import subprocess
from pathlib import Path

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--registry', required=True)
    p.add_argument('--tag', default='v0.1.0')
    p.add_argument('--push', action='store_true')
    a = p.parse_args()
    root = Path(__file__).resolve().parent
    for name in ('agent', 'catalogue', 'cart', 'user', 'mongodb', 'web'):
        directory = root if name == 'agent' else root / 'vendor/robot-shop' / ('mongo' if name == 'mongodb' else name)
        image = f'{a.registry.rstrip("/")}/{name}:{a.tag}'
        subprocess.run(['docker', 'build', '-t', image, str(directory)], check=True)
        if a.push: subprocess.run(['docker', 'push', image], check=True)
