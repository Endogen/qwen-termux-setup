import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('install_config', ROOT / 'scripts/install-pi-config.py')
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)

class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = Path(self.temp.name) / 'agent'
    def tearDown(self):
        self.temp.cleanup()
    def put(self, filename, data):
        path = self.agent / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path.read_bytes()
    def seed_cloud(self):
        self.models = {'providers': {'cloud-test': {'baseUrl': 'https://cloud.invalid/v1', 'apiKey': 'test-only-not-a-secret', 'models': [{'id': 'large'}]}}}
        self.settings = {'defaultProvider': 'cloud-test', 'defaultModel': 'large', 'defaultThinkingLevel': 'xhigh',
            'packages': ['npm:existing-package'], 'compaction': {'enabled': True, 'reserveTokens': 6000,
                'modelOverrides': {'cloud-test/large': {'keepRecentTokens': 12000}}}}
        self.put('models.json', self.models)
        self.put('settings.json', self.settings)
        self.put('model-profiles.json', {'profiles': [{'name': 'Other', 'models': [{'provider': 'other', 'id': 'model'}], 'tools': ['read']}]})
    def test_cli_is_dry_run_by_default_and_does_not_create_agent_directory(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/install-pi-config.py'), '--agent-dir', str(self.agent)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Dry-run', result.stdout)
        self.assertFalse(self.agent.exists())
    def test_merge_preserves_cloud_credentials_defaults_packages_and_other_profiles(self):
        self.seed_cloud()
        original = (self.agent / 'settings.json').read_bytes()
        backup = INSTALL.apply_changes(self.agent, INSTALL.plan_install(self.agent))
        models = json.loads((self.agent / 'models.json').read_text())
        self.assertEqual(models['providers']['cloud-test'], self.models['providers']['cloud-test'])
        settings = json.loads((self.agent / 'settings.json').read_text())
        for key in ['defaultProvider', 'defaultModel', 'defaultThinkingLevel', 'packages']:
            self.assertEqual(settings[key], self.settings[key])
        self.assertEqual(settings['compaction']['reserveTokens'], 6000)
        self.assertEqual(settings['compaction']['modelOverrides']['cloud-test/large'], {'keepRecentTokens': 12000})
        self.assertEqual(settings['compaction']['modelOverrides']['local-llama/qwen3.5-4b']['reserveTokens'], 2048)
        self.assertEqual(json.loads((self.agent / 'model-profiles.json').read_text())['profiles'][0]['name'], 'Other')
        self.assertEqual((backup / 'settings.json').read_bytes(), original)
        self.assertEqual((self.agent / 'settings.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
    def test_second_install_is_a_true_noop(self):
        INSTALL.apply_changes(self.agent, INSTALL.plan_install(self.agent))
        self.assertEqual(INSTALL.plan_install(self.agent), {})
    def test_conflicting_provider_is_rejected_without_writing(self):
        original = self.put('models.json', {'providers': {'local-llama': {'baseUrl': 'https://different.invalid/v1'}}})
        with self.assertRaisesRegex(ValueError, 'conflicting baseUrl'):
            INSTALL.plan_install(self.agent)
        self.assertEqual((self.agent / 'models.json').read_bytes(), original)
    def test_existing_extension_needs_explicit_replacement_and_is_backed_up(self):
        target = self.agent / 'extensions/model-profiles.mjs'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'previous custom implementation')
        with self.assertRaisesRegex(ValueError, 'replace-extension'):
            INSTALL.plan_install(self.agent)
        backup = INSTALL.apply_changes(self.agent, INSTALL.plan_install(self.agent, replace_extension=True))
        self.assertEqual((backup / 'extensions/model-profiles.mjs').read_bytes(), b'previous custom implementation')
        self.assertEqual(target.read_bytes(), (ROOT / 'pi/extensions/model-profiles.mjs').read_bytes())
    def test_overlapping_profile_is_not_silently_stolen(self):
        self.put('model-profiles.json', {'profiles': [{'name': 'Different policy', 'models': [{'provider': 'local-llama', 'id': 'qwen3.5-4b'}], 'tools': ['bash']}]})
        with self.assertRaisesRegex(ValueError, 'already owns'):
            INSTALL.plan_install(self.agent)
    def test_invalid_json_and_bad_settings_structure_fail_before_writes(self):
        self.agent.mkdir()
        (self.agent / 'models.json').write_text('{invalid')
        with self.assertRaises(ValueError):
            INSTALL.plan_install(self.agent)
        (self.agent / 'models.json').unlink()
        self.put('settings.json', {'compaction': None})
        with self.assertRaises(ValueError):
            INSTALL.plan_install(self.agent)
        self.assertFalse((self.agent / 'extensions').exists())
    def test_concurrent_edit_after_plan_is_not_overwritten(self):
        self.seed_cloud()
        plan = INSTALL.plan_install(self.agent)
        edited = self.put('settings.json', {'defaultProvider': 'changed-while-planning'})
        with self.assertRaisesRegex(RuntimeError, 'changed after planning'):
            INSTALL.apply_changes(self.agent, plan)
        self.assertEqual((self.agent / 'settings.json').read_bytes(), edited)
    def test_write_failure_rolls_back_previous_files(self):
        self.seed_cloud()
        original = (self.agent / 'models.json').read_bytes()
        settings = (self.agent / 'settings.json').read_bytes()
        real_write = INSTALL.atomic_bytes
        def fail_profile(path, content):
            if path == self.agent / 'model-profiles.json':
                raise OSError('simulated write error')
            return real_write(path, content)
        with patch.object(INSTALL, 'atomic_bytes', side_effect=fail_profile):
            with self.assertRaises(OSError):
                INSTALL.apply_changes(self.agent, INSTALL.plan_install(self.agent))
        self.assertEqual((self.agent / 'models.json').read_bytes(), original)
        self.assertEqual((self.agent / 'settings.json').read_bytes(), settings)
        self.assertFalse((self.agent / 'extensions').exists())
    def test_symlinked_configuration_is_not_replaced(self):
        self.agent.mkdir()
        source = Path(self.temp.name) / 'elsewhere.json'
        source.write_text('{}')
        (self.agent / 'models.json').symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            INSTALL.plan_install(self.agent)
        self.assertTrue((self.agent / 'models.json').is_symlink())

if __name__ == '__main__':
    unittest.main()
