#!/usr/bin/env bash
set -euo pipefail
COMMITMENTS="${1:?usage: verify_p13_s0_k1_private_commitments.sh PRIVATE_COMMITMENTS_JSON}"
python - "$COMMITMENTS" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
p=Path(sys.argv[1]).resolve()
obj=json.loads(p.read_text())
failed=[]
for key,row in sorted(obj.items()):
    path=Path(row['archive_absolute_path'])
    if not path.is_file():
        failed.append(f'{key}: missing {path}')
        print(f'{key}: MISSING {path}')
        continue
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''): h.update(b)
    observed=h.hexdigest(); expected=row['archive_sha256']
    mode=oct(path.stat().st_mode & 0o777)
    ok=observed==expected
    print(f'{key}: {"PASS" if ok else "FAIL"} sha={observed} mode={mode} path={path}')
    if not ok: failed.append(f'{key}: SHA mismatch')
if failed:
    print('\n'.join(f'FATAL: {x}' for x in failed), file=sys.stderr)
    raise SystemExit(2)
PY
