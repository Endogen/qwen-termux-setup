#!/usr/bin/env python3
"""Verify/download pinned driver archives; extract privately only with --apply.
Never installs a .deb, replaces Android's driver, or starts an inference server.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'qwen3.5-4b/gpu-driver-experiment'


def load_lock(path):
    lock = json.loads(path.read_text())
    seen = set()
    for package in lock['packages']:
        name = package['archive']
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.deb') or name in seen:
            raise ValueError('Invalid/duplicate archive filename in lock')
        if not re.fullmatch(r'[0-9a-f]{64}', package['sha256']):
            raise ValueError('Invalid SHA-256 in lock')
        if not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', package['package']):
            raise ValueError('Invalid package name in lock')
        seen.add(name)
    if not seen:
        raise ValueError('Empty package lock')
    return lock


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def verify_archives(lock, archives):
    for package in lock['packages']:
        path = archives / package['archive']
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing archive: {path.name}; download the private release assets or use --download')
        if digest(path) != package['sha256']:
            raise ValueError(f'SHA-256 mismatch: {path.name}; refusing extraction')


def refuse_running_server():
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(('127.0.0.1', 8081)) == 0:
            raise ValueError('Port 8081 is active. Stop only the idle Qwen server before preparing its driver files')


def prepare(lock, archives):
    refuse_running_server()
    prefix = os.environ.get('PREFIX', '')
    if prefix != lock['termuxPrefix']:
        raise ValueError('These aarch64 archives require the standard com.termux PREFIX')
    TARGET.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.prepare-', dir=TARGET) as temp:
        staging = Path(temp) / 'extracted'
        staging.mkdir()
        for package in lock['packages']:
            subprocess.run(['dpkg-deb', '-x', str(archives / package['archive']), str(staging)], check=True)
        private_lib = staging / prefix.lstrip('/') / 'lib'
        for library in ['libvulkan.so', 'libvulkan_freedreno.so', 'libdrm.so', 'libxshmfence.so']:
            if not (private_lib / library).is_file():
                raise ValueError(f'Extraction did not produce {library}')
        # Recheck immediately before replacing any previous private tree.
        refuse_running_server()
        final = TARGET / 'extracted'
        previous = None
        if final.exists():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            previous = TARGET / ('extracted.previous.' + stamp)
            final.rename(previous)
        try:
            staging.rename(final)
            icd = {'file_format_version': '1.0.0', 'ICD': {
                'library_path': str(final / prefix.lstrip('/') / 'lib/libvulkan_freedreno.so'), 'api_version': '1.4.0'}}
            temporary_icd = Path(temp) / 'turnip.json'
            temporary_icd.write_text(json.dumps(icd, indent=2) + '\n')
            temporary_icd.replace(TARGET / 'turnip.json')
        except BaseException:
            if final.exists():
                shutil.rmtree(final)
            if previous is not None:
                previous.rename(final)
            raise
        if previous:
            print(f'Previous private extraction retained: {previous.name}')
    print('Private Turnip driver prepared. No global package/loader was replaced.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archives', type=Path, default=TARGET / 'debs')
    parser.add_argument('--download', action='store_true', help='Download missing exact versions from configured Termux APT; do not install')
    parser.add_argument('--apply', action='store_true', help='Extract verified archives privately while Qwen is stopped')
    args = parser.parse_args()
    try:
        lock = load_lock(ROOT / 'turnip-packages.lock.json')
        archives = args.archives.expanduser().absolute()
        if args.apply:
            refuse_running_server()
        if args.download:
            archives.mkdir(parents=True, exist_ok=True)
            for package in lock['packages']:
                if not (archives / package['archive']).exists():
                    subprocess.run(['apt-get', 'download', f"{package['package']}={package['version']}"], cwd=archives, check=True)
        verify_archives(lock, archives)
        print(f"Verified {len(lock['packages'])} pinned package archives (SHA-256).")
        if args.apply:
            prepare(lock, archives)
        else:
            print('Check only: no driver extraction, package installation, or server changes. Use --apply explicitly.')
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Not prepared: {error}\n')


if __name__ == '__main__':
    main()
