"""Actual audit-hook cleanup regression; no search or scientific stage launch."""
import json
import os
import shutil
from pathlib import Path
from .s1_io import ACTIVE_POLICY
from .s1_lineage import RUN
from .s1_step import k2a_cleanup


def main():
    project = Path(os.environ['P13_S1_PROJECT']).resolve()
    assert ACTIVE_POLICY == (str(project), 'K2A')
    work = project / RUN / 'K2A_work'
    sibling = project / RUN / 'K2A_work_sibling'
    original = shutil.rmtree; fd_policy = shutil._use_fd_functions
    assert fd_policy, 'fixture requires fd-safe Python 3.10 stdlib'
    for target in (work, sibling):
        (target / 'seed_01/nested').mkdir(parents=True)
        (target / 'seed_01/nested/checkpoint.json').write_text('{}')
    try: original(work)
    except PermissionError as exc:
        assert str(project / 'seed_01') in str(exc), str(exc)
    else: raise AssertionError('old fd-safe traversal should reproduce denial')
    with k2a_cleanup(project):
        try: shutil.rmtree(sibling)
        except PermissionError: pass
        else: raise AssertionError('non-exact target received compatibility')
        assert shutil._use_fd_functions == fd_policy
        shutil.rmtree(work)
        assert not work.exists()
        assert shutil._use_fd_functions == fd_policy
        for path in ('seed_01', 'arbitrary-relative-name'):
            try: Path(path).read_bytes()
            except PermissionError: pass
            else: raise AssertionError('relative read was allowed')
    assert shutil.rmtree is original and shutil._use_fd_functions == fd_policy
    try:
        with k2a_cleanup(project): shutil.rmtree(work)  # absent exact target
    except FileNotFoundError: pass
    else: raise AssertionError('expected cleanup exception')
    assert shutil.rmtree is original and shutil._use_fd_functions == fd_policy
    print(json.dumps({'status': 'PASS', 'actual_audit_hook': True,
                      'old_failure_reproduced': True, 'exact_target_removed': True,
                      'arbitrary_target_unchanged': sibling.is_dir(),
                      'relative_reads_denied': 2, 'success_exception_state_restored': True}))


if __name__ == '__main__': main()
