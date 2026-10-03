"""Install the S0 I/O policy before a normal interpreter -m entry is imported."""
import os
import sys

try:
    from reproduce.s0_contract import context
    from reproduce.s0_step import install_io_guard
    project,_=context(os.environ['P13_S0_PROJECT_ROOT'])
    install_io_guard(project)
except BaseException as exc:
    # Python normally ignores sitecustomize errors; this boundary must fail closed.
    print('S0 child I/O bootstrap failed: '+repr(exc),file=sys.stderr,flush=True)
    os._exit(78)
