import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('prepare_turnip', ROOT / 'scripts/prepare-turnip.py')
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)

class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = self.root / 'driver_1_aarch64.deb'
        self.archive.write_bytes(b'synthetic archive, never extracted')
        self.lock = {'packages': [{'package': 'driver', 'archive': self.archive.name, 'version': '1',
            'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest()}]}
    def tearDown(self):
        self.temp.cleanup()
    def load(self):
        path = self.root / 'lock.json'
        path.write_text(json.dumps(self.lock))
        return PREPARE.load_lock(path)
    def test_valid_lock_and_archive_hash(self):
        PREPARE.verify_archives(self.load(), self.root)
    def test_changed_archive_is_refused(self):
        self.archive.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            PREPARE.verify_archives(self.load(), self.root)
    def test_traversal_and_duplicate_archive_names_are_refused(self):
        self.lock['packages'][0]['archive'] = '../escape.deb'
        with self.assertRaises(ValueError):
            self.load()
        self.lock['packages'][0]['archive'] = 'driver.deb'
        self.lock['packages'].append(dict(self.lock['packages'][0]))
        with self.assertRaises(ValueError):
            self.load()
    def test_bad_hash_is_refused(self):
        self.lock['packages'][0]['sha256'] = 'not-a-hash'
        with self.assertRaises(ValueError):
            self.load()
    def test_live_endpoint_prevents_driver_replacement(self):
        with patch.object(PREPARE.socket, 'socket') as factory:
            factory.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(ValueError, 'Port 8081 is active'):
                PREPARE.refuse_running_server()

if __name__ == '__main__':
    unittest.main()
