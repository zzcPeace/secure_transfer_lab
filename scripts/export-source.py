"""Export only source/docs: no runtime credentials, user files or virtualenvs."""

import argparse
import hashlib
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=Path("dist/SecureTransfer-source.zip"))
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
files = [root / name for name in ("README.md", "LICENSE", "pyproject.toml", "requirements.txt", ".gitignore")]
for directory in ("sft", "scripts", "docs"):
    files += [
        p for p in (root / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts
    ]
output = args.output.absolute()
output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        relative = path.relative_to(root)
        if path.is_symlink() or path.name.endswith((".pyc", ".key.pem", ".sqlite3")):
            raise ValueError(f"Unexpected export input: {relative}")
        data = path.read_bytes()
        if path.suffix in (".cmd", ".ps1"):
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        archive.writestr("secure_transfer_lab/" + relative.as_posix(), data)
digest = hashlib.sha256(output.read_bytes()).hexdigest()
output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n")
print(output)
print(f"SHA256: {digest}")
