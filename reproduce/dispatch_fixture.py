"""Inert engineering stage. No P13/P11 imports, evaluations or scientific workers."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import pickle
import sys

def initialize_worker(label):
    global WORKER_LABEL
    WORKER_LABEL=label

def toy_worker(task):
    value,blocked=task
    try:
        Path(blocked).read_bytes()
        denied=False
    except PermissionError:
        denied=True
    return {'value':value*value,'initializer':WORKER_LABEL,'guard_denied':denied,
            'module':toy_worker.__module__,'registered':getattr(sys.modules['__main__'],'toy_worker',None) is toy_worker}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--blocked')
    parser.add_argument('--exit-code',type=int,default=0)
    parser.add_argument('--pickle-only',action='store_true')
    args,extra=parser.parse_known_args()
    if args.pickle_only:
        try:pickle.dumps(toy_worker)
        except pickle.PicklingError as exc:
            print(json.dumps({'old_failure':type(exc).__name__,'message':str(exc)}));return 23
        return 0
    assert sys.modules['__main__'].__dict__ is globals()
    assert not any(n.startswith(('p13rawxt','p11rawxt')) for n in sys.modules)
    with ProcessPoolExecutor(max_workers=2,initializer=initialize_worker,initargs=('fixture-init',)) as pool:
        results=list(pool.map(toy_worker,[(2,args.blocked),(3,args.blocked)]))
    try:
        (Path.cwd()/'reproduce/s0_step.py').open('w')
        immutable_denied=False
    except PermissionError:immutable_denied=True
    print(json.dumps({'registered_main':True,'worker_module':toy_worker.__module__,'argv':sys.argv[1:],
      'cwd':os.getcwd(),'environment':{k:os.environ.get(k) for k in ['NSLOTS','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','P13_S0_PROJECT_ROOT','R5E_ENV_SENTINEL']},
      'results':results,'immutable_write_denied':immutable_denied,'scientific_module_imported':False}),flush=True)
    print('R5E_FIXTURE_STDERR',file=sys.stderr,flush=True)
    return args.exit_code

if __name__=='__main__':raise SystemExit(main())
