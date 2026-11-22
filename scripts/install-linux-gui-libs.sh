#!/usr/bin/env bash
# Ubuntu/Debian fallback: extract the missing cursor library inside the project.
# APT selects the package and trusted checksum; curl uses the current shell's
# proxy (the system's APT-specific proxy may belong to another login session).
# No system package installation or sudo is needed.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v apt-get >/dev/null
command -v dpkg-deb >/dev/null
command -v curl >/dev/null
mkdir -p .local/qt-packages .local/qt
.venv/bin/python - <<'PY'
import hashlib
import shlex
import subprocess
from pathlib import Path

listing = subprocess.run(['apt-get', '--print-uris', 'download', 'libxcb-cursor0'],
                         check=True, capture_output=True, text=True).stdout
packages = [shlex.split(line) for line in listing.splitlines() if line.startswith("'")]
if len(packages) != 1 or len(packages[0]) != 4:
    raise SystemExit('Cannot determine the package URI/checksum from APT.')
url, filename, size, checksum = packages[0]
algorithm, expected = checksum.split(':', 1)
if algorithm.upper() not in ('SHA256', 'SHA512') or Path(filename).name != filename:
    raise SystemExit('Unsupported package checksum or filename.')
package = Path('.local/qt-packages') / filename

def verified(path):
    if not path.is_file() or path.stat().st_size != int(size):
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm.lower()).hexdigest() == expected.lower()

if not verified(package):
    partial = package.with_suffix('.deb.partial')
    subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
                    '--connect-timeout', '15', '--max-time', '120', '--output', str(partial), url], check=True)
    if not verified(partial):
        raise SystemExit('Package checksum mismatch; extraction cancelled.')
    partial.replace(package)
subprocess.run(['dpkg-deb', '--extract', str(package), '.local/qt'], check=True)
print(f'已核验 {algorithm}：{filename}')
PY
printf '%s\n' 'libxcb-cursor0 已解压到项目 .local/qt，GUI 启动时自动加载。' \
  '显示窗口仍需要图形桌面或正确配置的 SSH X11 转发。'
