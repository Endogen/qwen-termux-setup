#!/usr/bin/env python3
"""Fail closed on obvious secrets/raw runtime data before publishing Git files.
This is a bounded safety check, not a general-purpose secret scanner.
"""
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|hf_[A-Za-z0-9]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
UUID = re.compile(rb'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I)
FORBIDDEN_SUFFIXES = {'.gguf', '.deb', '.so', '.apk', '.jks', '.keystore', '.pem', '.key', '.jsonl', '.log', '.pyc'}


def check_json(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'apiKey', 'token', 'password', 'deviceId'} and child not in ('local-only', None, ''):
                raise ValueError(f'Non-placeholder private field: {key}')
            check_json(child)
    elif isinstance(value, list):
        for child in value:
            check_json(child)


def main():
    files = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'], cwd=ROOT).split(b'\0')
    failures = []
    total = 0
    for raw in files:
        if not raw:
            continue
        name = raw.decode()
        path = Path(name)
        try:
            if path.suffix in FORBIDDEN_SUFFIXES or {'logs', 'extracted', 'backups', 'runtime-packages', 'debs'} & set(path.parts):
                raise ValueError('Runtime/binary/private path is staged')
            if path.name in {'auth.json', 'token', 'hosts.yml', '.env'} or path.name.startswith(('validation-', 'runtime-turnip-', 'models-before-')):
                # Distilled evidence is explicitly allowlisted, raw reports are not.
                if name != 'evidence/validation-summary.json':
                    raise ValueError('Raw runtime/configuration file is staged')
            blob = subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
            total += len(blob)
            if len(blob) > 512 * 1024 or b'\0' in blob:
                raise ValueError('Unexpected large/binary Git blob')
            if TOKEN.search(blob):
                raise ValueError('Credential-like token/private key found')
            if path.suffix == '.json':
                if UUID.search(blob):
                    raise ValueError('Session/device identity found in JSON data')
                check_json(json.loads(blob))
        except (ValueError, subprocess.CalledProcessError) as error:
            failures.append(f'{name}: {error}')
    if failures:
        sys.exit('\n'.join(failures))
    print(f'PASS staged audit: {sum(bool(f) for f in files)} files, {total} bytes; no raw runtime data or obvious secrets')


if __name__ == '__main__':
    main()
