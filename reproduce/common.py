import hashlib
import json
from pathlib import Path, PurePosixPath

SOURCE_ROOT = Path(__file__).resolve().parents[1]
STAGES = (
    'S0-K0', 'S0-K1', 'S0-K2', 'S0-K2R2', 'S0-K2R3', 'S0-K2R4', 'S0-K3',
    'S1-K0R', 'S1-K1A', 'S1-K1B', 'S1-K2A', 'S1-K2B', 'S1-K2C', 'S1-K3', 'S1-PF0', 'S1-PF1',
    'S2-K0', 'S2-K1', 'S2-K2', 'S2-K3', 'S2-K4R', 'S2-K5', 'S2-K6', 'S2-K7',
    'S3-K0', 'S3-K1', 'S3-K2', 'S3-K3', 'S3-K4', 'S3-K5', 'S3-K6',
)

def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def canonical_bytes(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def digest(obj):
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()

def relative_path(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or '..' in p.parts or '\\' in value or str(p) != value or value == '.':
        raise ValueError('expected a normalized relative path')
    return p

def contained(root, rel, must_exist=False):
    root = Path(root).resolve(strict=True)
    p = root.joinpath(*relative_path(rel).parts)
    # A path may be safely contained after resolve but still point through an alias.
    # Reject all symlink components rather than silently following any of them.
    cur = root
    for part in relative_path(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            raise ValueError('symlink path rejected')
    resolved = p.resolve(strict=must_exist)
    if root not in resolved.parents:
        raise ValueError('path escapes root')
    return resolved

def write_new_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')
