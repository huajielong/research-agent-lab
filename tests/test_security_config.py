import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


class SecurityConfigTests(unittest.TestCase):
    def configure(self, key, anonymous):
        environment = os.environ.copy()
        environment.update(APP_API_KEY=key, ALLOW_ANONYMOUS=anonymous)
        return subprocess.run([sys.executable, '-c',
            'from src.security import ALLOW_ANONYMOUS; print(ALLOW_ANONYMOUS)'],
            env=environment, cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True)

    def test_missing_key_fails_closed(self):
        self.assertNotEqual(self.configure('', '0').returncode, 0)

    def test_local_anonymous_requires_explicit_opt_in(self):
        result = self.configure('', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'True')

    def test_configured_key_cannot_be_bypassed_by_anonymous_flag(self):
        result = self.configure('test-key', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'False')
