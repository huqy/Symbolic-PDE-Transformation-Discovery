"""Synthetic actual-hook entry/flat-cleanup checks; never invokes diagnostic main."""
import json
import os
import shutil
from pathlib import Path
from .s1_io import ACTIVE_POLICY
from .s1_lineage import K0, RUN, config, verify_membership
from .s1_k2b_interface import k2b_metadata
from .s1_post import adapt


def main():
    project = Path(os.environ['P13_S1_PROJECT']).resolve()
    step = os.environ['P13_S1_STEP']
    assert ACTIVE_POLICY == (str(project), step)
    lock = verify_membership(project)
    before = (project / K0 / '03_k1_open_inputs.json').read_bytes()
    if step == 'K2B':
        from p13rawxt import s1_k2b_post_search_diagnostics as m
        adapt(project, step, m)
        original = m._load_json
        with k2b_metadata(project, m):
            cfg = m._load_json(project / 'phases/p13/coefficient_law_raw_xt/configs/p13_s1_k2b_protocol.json')
            entry = m._verify_entry(project, cfg)[-1]['entry']
            assert entry['cross_family_objects_verified']
            assert all(entry['locks'].values()) and all(entry['gates'].values())
        assert m._load_json is original
    # Frozen remaining work directories contain only flat checkpoint files.
    work = project / RUN / (step + '_work')
    work.mkdir()
    (work / 'restart_lock.json').write_text('{}')
    (work / 'partial.jsonl').write_text('{}\n')
    shutil.rmtree(work)
    assert not work.exists()
    denied = 0
    for path in ('arbitrary-relative-name', 'seed_01'):
        try: Path(path).read_bytes()
        except PermissionError: denied += 1
        else: raise AssertionError('relative read unexpectedly allowed')
    assert (project / K0 / '03_k1_open_inputs.json').read_bytes() == before
    assert verify_membership(project) == lock
    print(json.dumps({'status': 'PASS', 'actual_audit_hook': True, 'stage': step,
                      'frozen_cross_verification': step == 'K2B', 'diagnostic_main_started': False,
                      'flat_cleanup': True, 'relative_reads_denied': denied,
                      'K0_bytes_and_membership_unchanged': True}))


if __name__ == '__main__': main()
