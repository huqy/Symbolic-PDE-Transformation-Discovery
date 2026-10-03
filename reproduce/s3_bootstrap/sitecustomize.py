"""Install the same stage role policy in coordinator children and spawn workers."""
import os
if os.environ.get('P13_S3_PROJECT'):
    try:
        from reproduce.s3_io import install
        install(os.environ['P13_S3_PROJECT'],os.environ['P13_S3_STEP'])
    except BaseException as exc:
        import sys
        print('S3 I/O bootstrap failed: '+repr(exc),file=sys.stderr,flush=True)
        os._exit(91)
