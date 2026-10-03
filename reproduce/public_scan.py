"""Publication language, credential and dependency census; opaque archives exempt."""
import json
import re
from pathlib import Path
from .common import SOURCE_ROOT

# Frozen protocol provenance fields are retained for byte identity, not dereferenced.
LOCATOR_FILES = {
    'phases/p13/coefficient_law_raw_xt/configs/p13_s1_k0r_protocol.json',
    'phases/p13/coefficient_law_raw_xt/configs/p13_s2_k0_protocol.json',
    'phases/p13/coefficient_law_raw_xt/configs/p13_s3_k1_protocol.json',
}

def is_cjk(c):
    n=ord(c)
    return any(a <= n <= b for a,b in ((0x3400,0x4dbf),(0x4e00,0x9fff),(0xf900,0xfaff),(0x20000,0x323af),(0x3040,0x30ff),(0x3100,0x312f),(0xac00,0xd7af)))

SECRET_PATTERNS = [r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                   r'gh[pousr]_[A-Za-z0-9]{30,}', r'github_pat_[A-Za-z0-9_]{40,}',
                   r'AKIA[A-Z0-9]{16}', r'sk-[A-Za-z0-9]{40,}']

def scan(root=SOURCE_ROOT):
    root=Path(root); assets=json.loads((root/'reproduce/PUBLIC_RELEASE_ASSETS.json').read_text())
    binary={'public_assets/'+r['filename'] for r in assets['assets']}
    findings={k:[] for k in ('cjk','secrets','forbidden_files','symlinks','unexpected_binary','private_runtime_paths','large_files','caches')}
    retained=[]; text_files=0
    for p in sorted(root.rglob('*')):
        rel=p.relative_to(root).as_posix()
        if '.git' in p.relative_to(root).parts: continue
        if p.is_symlink():findings['symlinks'].append(rel);continue
        if not p.is_file():continue
        if rel.startswith(('CODEX_ANALYSIS/','TD_Discovery_Context/','CAPSULE_META/')) or '/prompts/' in rel or '_VIEW/' in rel:
            findings['forbidden_files'].append(rel)
        if any(x in p.parts for x in ('__pycache__','.pytest_cache','.envs','checkpoints')): findings['caches'].append(rel)
        if rel in binary: continue
        raw=p.read_bytes()
        if len(raw)>1000000: findings['large_files'].append(rel)
        try: s=raw.decode('utf-8')
        except UnicodeDecodeError:findings['unexpected_binary'].append(rel);continue
        text_files+=1
        if any(is_cjk(c) for c in s):findings['cjk'].append(rel)
        if any(re.search(pattern,s) for pattern in SECRET_PATTERNS):findings['secrets'].append(rel)
        # Private absolute home/scratch locations are forbidden except exact frozen provenance.
        if re.search(r'/u/(?:home|scratch)/',s):
            if rel in LOCATOR_FILES: retained.append(rel)
            else: findings['private_runtime_paths'].append(rel)
        if re.search(r'https?://github[.]com/huqy/(?:TD_Discovery_Context|TD_Discovery_symlearn|Symbolic_Learn_PDE_TD_Paper)',s):
            findings['private_runtime_paths'].append(rel)
    return {'status':'PASS' if not any(findings.values()) else 'FAIL','text_files':text_files,
            'findings':findings,'frozen_inert_locator_files':retained,'opaque_binary_exemptions':sorted(binary)}

if __name__=='__main__':
    report=scan();print(json.dumps(report,indent=2));raise SystemExit(report['status']!='PASS')
