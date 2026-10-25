#!/usr/bin/env python3
"""Portable launcher regressions. No real GGUF, driver, GPU, or Pi required."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parent / 'serve.sh'

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qwen-launcher-test-')
        base = Path(self.temp.name)
        self.root = base / 'model'
        self.root.mkdir()
        shutil.copyfile(SOURCE, self.root / 'serve.sh')
        # Sparse size-only fixture: no real weights are created or read.
        with (self.root / 'Qwen3.5-4B-Q5_K_M.gguf').open('wb') as fixture:
            fixture.truncate(3143656608)
        driver = self.root / 'gpu-driver-experiment'
        library = driver / 'extracted/test-prefix/lib'
        library.mkdir(parents=True)
        (driver / 'turnip.json').write_text('{}')
        for name in ['libvulkan_freedreno.so', 'libvulkan.so']:
            (library / name).touch()
        binary = base / 'bin'
        binary.mkdir()
        fake = binary / 'llama-server'
        fake.write_text('#!' + sys.executable + '\n'
            'import sys,os,json\n'
            'print(json.dumps({"args":sys.argv[1:],"env":{k:os.environ.get(k) for k in '
            '["LD_LIBRARY_PATH","VK_DRIVER_FILES","VK_ICD_FILENAMES","GGML_VK_DISABLE_FUSION"]}}))\n')
        fake.chmod(0o700)
        self.env = {k:v for k,v in os.environ.items() if not k.startswith(('QWEN_', 'GGML_VK_'))
            and k not in ('VK_DRIVER_FILES', 'VK_ICD_FILENAMES')}
        self.env.update(PATH=str(binary) + ':' + self.env['PATH'], PREFIX='/test-prefix')
    def tearDown(self):
        self.temp.cleanup()
    def run_launcher(self, **env):
        return subprocess.run(['bash', str(self.root / 'serve.sh')], env={**self.env, **env}, capture_output=True, text=True, timeout=10)
    def successful(self, **env):
        result = self.run_launcher(**env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.splitlines()[-1])
    def option(self, result, key):
        return result['args'][result['args'].index(key) + 1]
    def test_default_private_turnip_is_conservative_and_memory_bounded(self):
        result = self.successful()
        for flag, expected in [('--device', 'Vulkan0'), ('--n-gpu-layers', 'all'), ('--flash-attn', 'off'),
                ('--cache-ram', '0'), ('--ctx-checkpoints', '4'), ('--port', '8081'), ('--ctx-size', '8192')]:
            self.assertEqual(self.option(result, flag), expected)
        self.assertEqual(result['env']['GGML_VK_DISABLE_FUSION'], '1')
        self.assertEqual(result['env']['VK_DRIVER_FILES'], str(self.root / 'gpu-driver-experiment/turnip.json'))
        self.assertEqual(result['env']['VK_ICD_FILENAMES'], result['env']['VK_DRIVER_FILES'])
        self.assertIn('gpu-driver-experiment/extracted', result['env']['LD_LIBRARY_PATH'])
    def test_explicit_cpu_preserves_the_validated_fallback(self):
        result = self.successful(QWEN_BACKEND='cpu')
        self.assertEqual(self.option(result, '--device'), 'none')
        self.assertEqual(self.option(result, '--n-gpu-layers'), '0')
        self.assertEqual(self.option(result, '--flash-attn'), 'auto')
        self.assertIn('--no-op-offload', result['args'])
        self.assertNotIn('--cache-ram', result['args'])
        self.assertIsNone(result['env']['VK_DRIVER_FILES'])
        self.assertIsNone(result['env']['GGML_VK_DISABLE_FUSION'])
    def test_raw_stock_vulkan_is_still_blocked(self):
        result = self.run_launcher(QWEN_BACKEND='vulkan')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('failed correctness tests', result.stderr)
        self.assertNotIn('"args"', result.stdout)
    def test_bad_backend_is_rejected(self):
        self.assertNotEqual(self.run_launcher(QWEN_BACKEND='bad').returncode, 0)
    def test_missing_turnip_is_an_error_not_a_silent_backend_change(self):
        result = self.run_launcher(PREFIX='/missing-termux-prefix')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('QWEN_BACKEND=cpu', result.stderr)
        self.assertNotIn('"args"', result.stdout)
    def test_missing_or_wrong_size_model_is_rejected(self):
        (self.root / 'Qwen3.5-4B-Q5_K_M.gguf').write_bytes(b'incomplete')
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing or incomplete', result.stderr)

if __name__ == '__main__':
    unittest.main(verbosity=2)
