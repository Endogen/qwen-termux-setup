#!/data/data/com.termux/files/usr/bin/python
"""Isolated Vulkan correctness checks. Never touches the running CPU service.
One short-lived localhost-only server at a time; abort on memory pressure.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / 'logs' / 'gpu-investigation'
PORT = 18082
CASES = {
    'cpu-reference': {'device': 'none', 'layers': '0', 'extra': ['--no-repack']},
    'vulkan-1-layer': {'device': 'Vulkan0', 'layers': '1'},
    'vulkan-2-layers': {'device': 'Vulkan0', 'layers': '2'},
    'vulkan-full': {'device': 'Vulkan0', 'layers': 'all'},
    'vulkan-no-fusion': {'device': 'Vulkan0', 'layers': 'all', 'env': {'GGML_VK_DISABLE_FUSION': '1'}},
    'vulkan-no-mmvq': {'device': 'Vulkan0', 'layers': 'all', 'env': {'GGML_VK_DISABLE_MMVQ': '1'}},
    'vulkan-serial': {'device': 'Vulkan0', 'layers': 'all', 'env': {'GGML_VK_DISABLE_ASYNC': '1', 'GGML_VK_SERIALIZE_SUBMISSIONS': '1'}},
    'vulkan-f32': {'device': 'Vulkan0', 'layers': 'all', 'env': {'GGML_VK_DISABLE_F16': '1'}, 'extra': ['--cache-type-k', 'f32', '--cache-type-v', 'f32']},
}
for suffix, flags in {
    'no-fusion': {'GGML_VK_DISABLE_FUSION': '1'},
    'no-mmvq': {'GGML_VK_DISABLE_MMVQ': '1'},
    'f32': {'GGML_VK_DISABLE_F16': '1'},
    'serial': {'GGML_VK_DISABLE_ASYNC': '1', 'GGML_VK_SERIALIZE_SUBMISSIONS': '1'},
    'device-memory': {'GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM': '1'},
    'conservative': {flag: '1' for flag in ['GGML_VK_DISABLE_F16', 'GGML_VK_DISABLE_FUSION',
        'GGML_VK_DISABLE_MMVQ', 'GGML_VK_DISABLE_ASYNC', 'GGML_VK_SERIALIZE_SUBMISSIONS',
        'GGML_VK_DISABLE_DESCRIPTOR_REUSE', 'GGML_VK_DISABLE_GRAPH_OPTIMIZE']},
}.items():
    CASES[f'vulkan-1-layer-{suffix}'] = {'device': 'Vulkan0', 'layers': '1', 'env': flags}

CASES.update({
    'vulkan-2-layers-no-fusion': {'device': 'Vulkan0', 'layers': '2', 'env': {'GGML_VK_DISABLE_FUSION': '1'}},
    'vulkan-32-layers-no-fusion': {'device': 'Vulkan0', 'layers': '32', 'env': {'GGML_VK_DISABLE_FUSION': '1'}},
    'vulkan-no-fusion-no-mmvq': {'device': 'Vulkan0', 'layers': 'all', 'env': {'GGML_VK_DISABLE_FUSION': '1', 'GGML_VK_DISABLE_MMVQ': '1'}},
    'vulkan-no-fusion-f32': {'device': 'Vulkan0', 'layers': 'all', 'env': {'GGML_VK_DISABLE_FUSION': '1', 'GGML_VK_DISABLE_F16': '1'}},
    'vulkan-conservative': {**CASES['vulkan-1-layer-conservative'], 'layers': 'all'},
})


def mem_available_mib():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) // 1024
    raise RuntimeError('Unable to read available memory')


def request(path, body=None, timeout=75):
    req = urllib.request.Request(f'http://127.0.0.1:{PORT}/{path}',
        headers={'Content-Type': 'application/json'},
        data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def run_case(name):
    case = CASES[name]
    minimum_mib = 4200 if case['layers'] in ('all', '32') else 1500
    if mem_available_mib() < minimum_mib:
        raise RuntimeError(f'Less than {minimum_mib} MiB available: refusing to risk the working CPU service')
    with socket.socket() as check:
        assert check.connect_ex(('127.0.0.1', PORT)) != 0, 'Experimental port is already in use'
    args = ['llama-server', '--model', str(ROOT/'Qwen3.5-4B-Q5_K_M.gguf'),
        '--alias', 'qwen-gpu-diagnostic', '--host', '127.0.0.1', '--port', str(PORT),
        '--no-agent', '--ctx-size', '512', '--parallel', '1', '--batch-size', '128',
        '--ubatch-size', '32', '--threads', '4', '--threads-batch', '4',
        '--device', case['device'], '--n-gpu-layers', case['layers'], '--no-op-offload',
        '--fit', 'off', '--flash-attn', 'off', '--no-context-shift', '--jinja',
        '--reasoning', 'off', '--reasoning-format', 'deepseek', '--n-predict', '8',
        '--cache-ram', '0', '--verbosity', '4', '--log-colors', 'off'] + case.get('extra', [])
    env = dict(os.environ)
    for variable in list(env):
        if variable.startswith('GGML_VK_'):
            del env[variable]
    env.update(case.get('env', {}))
    LOGS.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = {'case': name, 'configuration': case, 'availableMiB': mem_available_mib()}
    logpath = LOGS/f'{name}.log'
    with logpath.open('w') as log:
        proc = subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            for _ in range(90):
                if proc.poll() is not None:
                    raise RuntimeError(f'Experimental server exited with code {proc.returncode}; see {logpath}')
                if mem_available_mib() < 800:
                    raise RuntimeError('Memory safety threshold reached; stopping experiment')
                try:
                    if request('health', timeout=1).get('status') == 'ok':
                        break
                except (OSError, urllib.error.HTTPError):
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Experimental server startup timed out')
            result['startupSeconds'] = round(time.monotonic()-started, 3)
            body = {'model': 'qwen-gpu-diagnostic', 'messages': [
                {'role': 'user', 'content': 'What is 17 multiplied by 23? Reply only with the number.'}],
                'chat_template_kwargs': {'enable_thinking': False},
                'temperature': 0, 'seed': 1234, 'max_tokens': 8,
                'presence_penalty': 0, 'frequency_penalty': 0, 'repeat_penalty': 1,
                'logprobs': True, 'top_logprobs': 3}
            request_start = time.monotonic()
            result['request'] = body
            result['response'] = request('v1/chat/completions', body)
            result['requestSeconds'] = round(time.monotonic()-request_start, 3)
            text = result['response']['choices'][0]['message'].get('content', '').strip()
            result['correct'] = text == '391'
            result['text'] = text
        except Exception as error:
            result['error'] = f'{type(error).__name__}: {error}'
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=10)
    result['totalSeconds'] = round(time.monotonic()-started, 3)
    (LOGS/f'{name}.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: result.get(k) for k in ['case', 'correct', 'text', 'requestSeconds', 'error']}), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', nargs='+', choices=CASES)
    options = parser.parse_args()
    for name in options.cases:
        run_case(name)
