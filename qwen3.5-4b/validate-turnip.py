#!/data/data/com.termux/files/usr/bin/python
"""Validate the isolated Turnip backend without replacing the CPU service.
Uses one temporary localhost server on 18082; always terminates that server.
"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
PORT = 18082
REPORT = ROOT / 'validation-turnip.json'
LOG = ROOT / 'logs/turnip-validation.log'


def post(path, body):
    request = urllib.request.Request(f'http://127.0.0.1:{PORT}/{path}',
        headers={'Content-Type': 'application/json'}, data=json.dumps(body).encode())
    with urllib.request.urlopen(request, timeout=240) as response:
        return json.load(response)


def mem_available_mib():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) // 1024
    raise RuntimeError('Cannot determine available memory')


def stop_on_signal(signum, _frame):
    raise SystemExit(128 + signum)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop_on_signal)
    with socket.socket() as check:
        assert check.connect_ex(('127.0.0.1', PORT)) != 0, 'Experimental port is occupied'
    assert mem_available_mib() >= 4200, 'Not enough memory for safe parallel validation'
    with urllib.request.urlopen('http://127.0.0.1:8081/slots', timeout=5) as response:
        assert not any(slot.get('is_processing') for slot in json.load(response)), 'CPU service is busy; wait before testing'
    env = {**os.environ, 'QWEN_BACKEND': 'turnip', 'QWEN_PORT': str(PORT),
        'QWEN_CONTEXT': '8192', 'QWEN_REASONING': 'off', 'QWEN_THREADS': '6',
        'QWEN_FLASH_ATTN': 'off', 'QWEN_API_URL': f'http://127.0.0.1:{PORT}/v1'}
    report = {'backend': 'Mesa Turnip 26.2.5 / Vulkan', 'context': 8192, 'tests': {}}
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open('w') as log:
        proc = subprocess.Popen([str(ROOT / 'serve.sh'), '--cache-ram', '0', '--verbosity', '4', '--log-colors', 'off'],
            env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            for _ in range(90):
                assert proc.poll() is None, f'Experimental server exited; see {LOG}'
                assert mem_available_mib() >= 800, 'Memory safety threshold reached'
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/health', timeout=1) as response:
                        if json.load(response).get('status') == 'ok':
                            break
                except (OSError, urllib.error.HTTPError):
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Turnip server did not become ready')
            subprocess.run([str(ROOT / 'smoke-test.py'), str(ROOT / 'validation-turnip-api.json')], env=env, check=True, timeout=300)
            report['tests']['api_chat_streamed_tools_and_round_trip'] = 'PASS'

            # A deterministic retrieval task with approximately the same token
            # count as the real compact app's first request, not just arithmetic.
            line = 'Reference note: this line is background material and contains no requested action.\n'
            count = 180
            for _ in range(4):
                text = ('Read the reference notes. Return only the value on the RESULT line.\n'
                    + line * (count // 2) + 'RESULT: TURNIP_LONG_CONTEXT_OK\n'
                    + line * (count - count // 2) + 'What is the value of RESULT? Return only that value.')
                messages = [{'role': 'user', 'content': text}]
                rendered = post('apply-template', {'messages': messages, 'chat_template_kwargs': {'enable_thinking': False}})['prompt']
                tokens = len(post('tokenize', {'content': rendered})['tokens'])
                if 4800 <= tokens <= 5500:
                    break
                count = max(1, int(count * 5100 / tokens))
            assert 4000 <= tokens <= 6500, f'Unexpected long test size: {tokens}'
            started = time.monotonic()
            result = post('v1/chat/completions', {'model': 'qwen3.5-4b', 'messages': messages,
                'chat_template_kwargs': {'enable_thinking': False}, 'temperature': 0, 'seed': 1234,
                'max_tokens': 64, 'presence_penalty': 0, 'repeat_penalty': 1})
            content = result['choices'][0]['message'].get('content', '').strip()
            assert content == 'TURNIP_LONG_CONTEXT_OK', result
            report['tests']['long_context_retrieval'] = {'status': 'PASS', 'seconds': round(time.monotonic() - started, 3),
                'usage': result.get('usage'), 'timings': result.get('timings'), 'reply': content}
            print('PASS ~5K-token retrieval:', json.dumps(report['tests']['long_context_retrieval']), flush=True)

            subprocess.run(['node', str(ROOT / 'verify-pi-voice.mjs')], env={**env,
                'QWEN_TEST_BACKEND': 'Mesa Turnip / Vulkan', 'QWEN_TEST_REPORT': str(ROOT / 'validation-turnip-pi-voice.json')},
                check=True, timeout=360)
            report['tests']['real_pi_voice_host_and_read_tool'] = 'PASS'
            report['status'] = 'PASS'
        except BaseException as error:
            report['status'] = 'FAIL'
            report['error'] = f'{type(error).__name__}: {error}'
            raise
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            REPORT.write_text(json.dumps(report, indent=2) + '\n')
    with urllib.request.urlopen('http://127.0.0.1:8081/health', timeout=5) as response:
        assert json.load(response)['status'] == 'ok'
    print('PASS: experimental server stopped; working CPU service remains healthy', flush=True)
