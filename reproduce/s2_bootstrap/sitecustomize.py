"""Install the same stage role policy in coordinator children and spawn workers."""
import os
if os.environ.get('P13_S2_PROJECT'):
    try:
        from reproduce.s2_io import install
        install(os.environ['P13_S2_PROJECT'],os.environ['P13_S2_STEP'])
    except BaseException as exc:
        import sys
        print('S2 I/O bootstrap failed: '+repr(exc),file=sys.stderr,flush=True)
        os._exit(91)
