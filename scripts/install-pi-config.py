#!/usr/bin/env python3
"""Merge this local model into Pi without exporting/replacing cloud defaults.
Dry-run by default. Writes require --apply; no processes are started or reloaded.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def object_at(parent, key):
    value = parent.setdefault(key, {})
    if not isinstance(value, dict):
        raise ValueError(f'{key} must be an object; merge manually')
    return value


def load_object(path):
    if path.is_symlink():
        raise ValueError(f'Refusing to replace a symlink: {path}')
    if not path.exists():
        return {}
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError(f'Expected a JSON object: {path.name}')
    return result


def merge_models(existing, incoming):
    result = deepcopy(existing)
    providers = object_at(result, 'providers')
    for name, new in incoming['providers'].items():
        old = providers.get(name)
        if old is None:
            providers[name] = deepcopy(new)
            continue
        if not isinstance(old, dict):
            raise ValueError(f'Provider {name} is not an object')
        for key, value in new.items():
            if key == 'models':
                continue
            if key in old and old[key] != value:
                raise ValueError(f'Provider {name} has a conflicting {key}; review manually')
            old[key] = deepcopy(value)
        models = old.setdefault('models', [])
        if not isinstance(models, list) or any(not isinstance(m, dict) or not isinstance(m.get('id'), str) for m in models):
            raise ValueError(f'Invalid models list in {name}')
        ids = [m['id'] for m in models]
        if len(set(ids)) != len(ids):
            raise ValueError(f'Duplicate model ids in {name}')
        for model in new['models']:
            if model['id'] in ids:
                index = ids.index(model['id'])
                models[index] = {**models[index], **deepcopy(model)}
            else:
                models.append(deepcopy(model))
                ids.append(model['id'])
    return result


def merge_profiles(existing, incoming):
    result = deepcopy(existing)
    profiles = result.setdefault('profiles', [])
    if not isinstance(profiles, list):
        raise ValueError('profiles must be an array')
    for profile in incoming['profiles']:
        targets = {(m['provider'], m['id']) for m in profile['models']}
        matched = []
        for index, old in enumerate(profiles):
            if not isinstance(old, dict) or not isinstance(old.get('models'), list):
                raise ValueError('Invalid existing model profile')
            pairs = {(m['provider'], m['id']) for m in old['models']}
            if targets & pairs:
                if pairs != targets or old.get('name') != profile['name']:
                    raise ValueError('A different profile already owns this model; merge manually')
                matched.append(index)
        if len(matched) > 1:
            raise ValueError('Ambiguous duplicate model profiles')
        if matched:
            profiles[matched[0]] = deepcopy(profile)
        else:
            profiles.append(deepcopy(profile))
    return result


def merge_settings(existing, incoming):
    result = deepcopy(existing)
    compaction = object_at(result, 'compaction')
    overrides = object_at(compaction, 'modelOverrides')
    for model, values in incoming['compaction']['modelOverrides'].items():
        object_at(overrides, model).update(deepcopy(values))
    return result


def plan_install(agent, replace_extension=False):
    changes = {}
    for filename, template, merge in [
        ('models.json', 'models.local.json', merge_models),
        ('model-profiles.json', 'model-profiles.json', merge_profiles),
        ('settings.json', 'settings.local.json', merge_settings),
    ]:
        path = agent / filename
        if path.is_symlink():
            raise ValueError(f'Refusing to replace a symlink: {path}')
        original = path.read_bytes() if path.exists() else None
        current = json.loads(original) if original is not None else {}
        if not isinstance(current, dict):
            raise ValueError(f'Expected a JSON object: {filename}')
        merged = merge(current, load_object(ROOT / 'pi' / template))
        if merged != current:
            changes[path] = (original, (json.dumps(merged, indent=2) + '\n').encode())
    for name in ['model-profiles.mjs', 'model-profiles.ts']:
        target = agent / 'extensions' / name
        if target.is_symlink():
            raise ValueError(f'Refusing to replace a symlink: {target}')
        expected = (ROOT / 'pi/extensions' / name).read_bytes()
        current = target.read_bytes() if target.exists() else None
        if current == expected:
            continue
        if current is not None and not replace_extension:
            raise ValueError(f'{name} differs; review it and use --replace-extension explicitly')
        changes[target] = (current, expected)
    return changes


def atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_changes(agent, changes):
    if not changes:
        return None
    # Optimistic transaction: back up every old file before replacing any of it;
    # restore originals if a later write fails. Does not touch live processes.
    originals = {path: values[0] for path, values in changes.items()}
    for path, original in originals.items():
        if (path.read_bytes() if path.exists() else None) != original:
            raise RuntimeError(f'Configuration changed after planning: {path.name}')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    backup = agent / '.local-qwen-backups' / stamp
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    for path, content in originals.items():
        if content is not None:
            atomic_bytes(backup / path.relative_to(agent), content)
    written = []
    try:
        for path, (_original, content) in changes.items():
            # Refuse concurrent edits made after the plan/backup snapshot.
            actual = path.read_bytes() if path.exists() else None
            if actual != originals[path]:
                raise RuntimeError(f'Configuration changed concurrently: {path.name}')
            atomic_bytes(path, content)
            written.append(path)
    except BaseException:
        for path in reversed(written):
            if not path.exists() or path.read_bytes() != changes[path][1]:
                raise RuntimeError(f'Concurrent edit during rollback; inspect backups in {backup}')
            if originals[path] is None:
                path.unlink()
            else:
                atomic_bytes(path, originals[path])
        raise
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent-dir', type=Path, default=Path(os.environ.get('PI_CODING_AGENT_DIR', str(Path.home() / '.pi/agent'))))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    parser.add_argument('--replace-extension', action='store_true')
    args = parser.parse_args()
    agent = args.agent_dir.expanduser().absolute()
    try:
        changes = plan_install(agent, args.replace_extension)
        if not changes:
            print('Already matches the local setup. Nothing to write.')
            return
        print(('Apply' if args.apply else 'Dry-run') + ': ' + ', '.join(str(p.relative_to(agent)) for p in changes))
        if not args.apply:
            print('No files changed. Add --apply after reviewing pi/ and this plan.')
            return
        backup = apply_changes(agent, changes)
        print(f'Backup directory: {backup}')
        print('Installed. Start a fresh Pi worker, or reload only an idle worker.')
        print('No default model, cloud credentials, packages, or running sessions were changed.')
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        parser.exit(1, f'Not installed: {error}\n')


if __name__ == '__main__':
    main()
