"""Fail-closed bootstrap also inherited by freshly spawned Python workers."""
import os
if os.environ.get('P13_S1_PROJECT'):
    try:
        from reproduce.s1_io import install
        install(os.environ['P13_S1_PROJECT'], os.environ['P13_S1_STEP'])
    except BaseException as exc:
        import sys
        print('S1 I/O bootstrap failed: ' + repr(exc), file=sys.stderr, flush=True)
        os._exit(91)
