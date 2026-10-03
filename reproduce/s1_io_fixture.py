"""Worker-boundary fixture, never used by scientific orchestration."""
import multiprocessing
import os
import json
from pathlib import Path

def probe(path):
    try: Path(path).read_bytes()
    except PermissionError: return 'DENIED'
    return 'READ'

def main():
    probes = json.loads(os.environ['P13_IO_PROBES']) if 'P13_IO_PROBES' in os.environ else [[os.environ['P13_DENY_PROBE'],'DENIED']]
    for path,expected in probes: assert probe(path) == expected,(path,expected)
    for method in ('fork', 'spawn'):
        with multiprocessing.get_context(method).Pool(1) as pool:
            for path,expected in probes: assert pool.apply(probe, (path,)) == expected,(method,path,expected)
    print('COORDINATOR_FORK_SPAWN_DENY_PASS', flush=True)

if __name__ == '__main__': main()
