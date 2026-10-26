"""Install the locked Node runtime dependencies on Render before serving games."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def ensure_node_dependencies():
    package = ROOT / 'battle_service/node_modules/pokemon-showdown/package.json'
    if package.exists():
        return
    enabled = os.environ.get('FREEFOLDER_AUTO_INSTALL_ENGINE', os.environ.get('RENDER', '')).lower() in ('1', 'true', 'yes')
    if not enabled:
        return
    npm = shutil.which('npm.cmd') or shutil.which('npm')
    if not npm:
        raise RuntimeError('Node.js and npm are required. Install the battle service before starting this server.')
    subprocess.run([npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
                   cwd=ROOT / 'battle_service', check=True, timeout=180,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
