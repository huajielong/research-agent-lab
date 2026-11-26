import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which('bash') if os.name != 'nt' else 'C:/Program Files/Git/bin/bash.exe'


@unittest.skipUnless(BASH and Path(BASH).exists(), 'Bash is required for entrypoint tests')
class DockerSecurityTests(unittest.TestCase):
    def run_entrypoint(self, password):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        commands = {
            'psql': 'if [ "$1" = "-V" ]; then echo "psql (PostgreSQL) 15.0"; exit; fi\nprintf "%s\\n" "$@" >> "$ARGS_LOG"\ncat >> "$SQL_LOG"',
            'pg_ctlcluster': 'exit 0', 'pg_isready': 'exit 0',
            'runuser': 'shift 4; exec "$@"',
            'su': 'while [ "$1" != "-c" ]; do shift; done; shift; bash -c "$1"',
            'uvicorn': 'python -I -c \'import os,pathlib; pathlib.Path(os.environ["DSN_FILE"]).write_text(os.environ.get("DATABASE_URL", ""))\'',
        }
        for name, content in commands.items():
            file = directory / name
            file.write_text('#!/usr/bin/env bash\n' + content + '\n', encoding='utf-8')
            file.chmod(0o755)
        environment = os.environ.copy()
        environment.pop('DATABASE_URL', None)
        environment.pop('POSTGRES_PASSWORD', None)
        environment.update(MOCK_BIN=directory.as_posix(), ENTRYPOINT=(ROOT/'docker/entrypoint.sh').as_posix(),
                           ARGS_LOG=(directory/'args').as_posix(), SQL_LOG=(directory/'sql').as_posix(),
                           DSN_FILE=(directory/'dsn').as_posix(),
                           POSTGRES_USER='role name', POSTGRES_DB='db name',
                           MARKER=(directory/'injected').as_posix())
        if password is not None:
            environment['POSTGRES_PASSWORD'] = password
        setup = 'export PATH="$(cygpath -u "$MOCK_BIN"):$PATH"' if os.name == 'nt' else 'export PATH="$MOCK_BIN:$PATH"'
        result = subprocess.run([BASH, '-c', setup + '; source "$ENTRYPOINT"'],
                                env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
        return result, directory

    def test_missing_password_fails_closed(self):
        result, _ = self.run_entrypoint(None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('POSTGRES_PASSWORD must be set', result.stderr)

    def test_special_credentials_are_data_and_not_logged(self):
        password = 'test-secret\'$(touch "$MARKER") @:/?#'
        result, directory = self.run_entrypoint(password)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((directory/'injected').exists())
        self.assertNotIn('test-secret', result.stdout + result.stderr)
        self.assertNotIn('test-secret', (directory/'args').read_text())
        sql = (directory/'sql').read_text()
        self.assertIn('\\getenv password POSTGRES_PASSWORD', sql)
        self.assertIn("format('CREATE USER %I WITH PASSWORD %L', :'role', :'password')", sql)
        from urllib.parse import urlsplit, unquote
        dsn = (directory/'dsn').read_text()
        parsed = urlsplit(dsn)
        self.assertEqual(unquote(parsed.password), password)
        self.assertEqual(unquote(parsed.username), 'role name')
        self.assertEqual(unquote(parsed.path), '/db name')
