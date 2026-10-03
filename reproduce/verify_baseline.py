"""Publication replacement for internal baseline authentication."""
import json
import sys
from .common import SOURCE_ROOT
from .release_integrity import verify as verify_release

def verify(root=SOURCE_ROOT):
    caller = sys._getframe(1).f_code
    if caller.co_name == 'check_source' and caller.co_filename.endswith('s0_launcher.py'):
        raise RuntimeError('Use reproduce/run_s0.sh or the public coordinator; private ancestry authentication is disabled')
    return verify_release(root)

if __name__ == '__main__':
    print(json.dumps(verify(),indent=2))
