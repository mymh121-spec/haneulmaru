"""Refresh the delivery manifest from staged Git bytes (independent of CRLF checkout)."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode('utf-8').split('\0')
manifest = {}
for name in sorted(n for n in names if n and n != 'FILES_SHA256.json'):
    content = subprocess.check_output(['git', 'show', ':' + name], cwd=root)
    manifest[name] = hashlib.sha256(content).hexdigest()
(root / 'FILES_SHA256.json').write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
print(f'Updated {len(manifest)} staged file checksums.')
