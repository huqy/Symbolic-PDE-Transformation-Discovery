"""Process-level guard regression using the frozen writer, never formal search."""
import json
import os
from pathlib import Path
from .s1_io import ACTIVE_POLICY
from .s1_lineage import RUN, K0
from .s0_contract import P


def main():
    project = Path(os.environ['P13_S1_PROJECT']).resolve()
    assert ACTIVE_POLICY == (str(project), 'K1A'), 'real audit hook must be installed'
    from p13rawxt.s1_k1_formal_search import _write_text
    runs = project / P / 'runs'
    marker = runs / 'LATEST_P13_S1_K1A_RUN.txt'
    temp = marker.with_suffix(marker.suffix + '.tmp')
    text = RUN + '\n'
    _write_text(marker, text)
    assert marker.read_text() == text
    assert not temp.exists()

    denied = [
        runs / 'arbitrary.txt',
        runs / 'arbitrary.tmp',
        runs / 'LATEST_P13_S0_K1_RUN.txt.tmp',
        runs / 'LATEST_P13_S0_K1_RUN.txt',
        runs / 'ACTIVE_P13_S1_K0R_INCOMPLETE_RUN.txt',
        runs / 'ACTIVE_P13_S1_K0R_INCOMPLETE_RUN.txt.tmp',
        runs / 'LATEST_P13_S1_K1A_RUN.tmp',
        runs / 'LATEST_P13_S1_K1A_RUN.txt.tmp.tmp',
        runs / 'unrelated/LATEST_P13_S1_K1A_RUN.txt.tmp',
        project / 'LATEST_P13_S1_K1A_RUN.txt.tmp',
    ]
    for path in denied:
        try:
            path.write_text('must be denied')
        except PermissionError:
            pass
        else:
            raise AssertionError('unauthorized write succeeded: ' + str(path))
        assert not path.exists()

    # Both rename endpoints must be authorized, including when the source is a
    # valid atomic temporary sibling but the destination is an arbitrary file.
    temp.write_text('pending')
    for src, dst in ((temp, runs / 'arbitrary.txt'), (runs / 'arbitrary.tmp', marker)):
        try:
            os.replace(src, dst)
        except PermissionError:
            pass
        else:
            raise AssertionError('unauthorized rename succeeded')
    assert marker.read_text() == text
    temp.unlink()

    for rel in (RUN, K0):
        output = project / rel / 'atomic_guard_regression.txt'
        _write_text(output, 'generated output\n')
        assert output.read_text() == 'generated output\n'
    # K0 always receives an explicit fresh_s1_k0 path. Its legacy ACTIVE
    # marker must not require a second top-level write exception.
    from p13rawxt import s1_k0r_protocol_lock as k0_module
    from .s1_step import k0_marker_writer
    original = k0_module.write_text
    legacy = runs / 'ACTIVE_P13_S1_K0R_INCOMPLETE_RUN.txt'
    try:
        with k0_marker_writer(project, k0_module) as local:
            k0_module.write_text(legacy, K0)
            assert local.read_text() == K0 + '\n'
            raise RuntimeError('synthetic interrupted K0')
    except RuntimeError as exc:
        assert str(exc) == 'synthetic interrupted K0'
    assert local.is_file() and not legacy.exists()
    assert k0_module.write_text is original
    with k0_marker_writer(project, k0_module) as local:
        k0_module.write_text(legacy, K0)
        assert local.read_text() == K0 + '\n'
    assert not local.exists() and not legacy.exists()
    assert k0_module.write_text is original
    print(json.dumps({'status': 'PASS', 'actual_audit_hook': True,
                      'frozen_atomic_writer': True, 'denied_writes': len(denied),
                      'denied_renames': 2, 'generated_trees_preserved': True,
                      'k0_marker_lifecycle': 'PASS'}), flush=True)


if __name__ == '__main__':
    main()
