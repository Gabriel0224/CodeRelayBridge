"""Default native release build: both clients plus one universal server."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import shutil
import hashlib
import json
import time
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from desktop_client import VERSION
from release_tools.release_matrix import PLANS, policy

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--core-source', default=str(ROOT))
    args = parser.parse_args()
    policy()
    for role in ('client', 'server'):
        command = [sys.executable, '-m', 'desktop_client.native_build', role, '--edition', 'standard']
        if role == 'client':
            command += ['--core-source', args.core_source]
        subprocess.run(command, cwd=ROOT, check=True)
if __name__ == '__main__':
    main()
