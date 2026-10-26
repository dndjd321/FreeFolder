from pathlib import Path
import os
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_deployment_app_starts_and_shuts_down(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c',
            'import asyncio,server_deploy\n'
            'async def main():\n'
            '    async with server_deploy.app.router.lifespan_context(server_deploy.app):\n'
            '        pass\n'
            'asyncio.run(main())'],
            cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_local_import_does_not_install_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bootstrap, 'ROOT', Path(tmp)), patch.dict(os.environ, {}, clear=True), patch.object(bootstrap.subprocess, 'run') as run:
            bootstrap.ensure_node_dependencies()
            run.assert_not_called()

    def test_render_installs_exact_lock_without_package_scripts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bootstrap, 'ROOT', Path(tmp)), patch.dict(os.environ, {'RENDER': 'true'}, clear=True), patch.object(bootstrap.shutil, 'which', return_value='/usr/bin/npm'), patch.object(bootstrap.subprocess, 'run') as run:
            bootstrap.ensure_node_dependencies()
            self.assertEqual(['ci', '--ignore-scripts', '--no-audit', '--no-fund'], run.call_args.args[0][1:])
            self.assertTrue(run.call_args.kwargs['check'])

    def test_installed_runtime_does_not_install_again(self):
        with patch.dict(os.environ, {'RENDER': 'true'}, clear=True), patch.object(bootstrap.subprocess, 'run') as run:
            bootstrap.ensure_node_dependencies()
            run.assert_not_called()

    def test_missing_npm_fails_with_explicit_configuration_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bootstrap, 'ROOT', Path(tmp)), patch.dict(os.environ, {'RENDER': 'true'}, clear=True), patch.object(bootstrap.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'Node.js'):
                bootstrap.ensure_node_dependencies()


if __name__ == '__main__':
    unittest.main()
