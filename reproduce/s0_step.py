"""Single scientific S0 child. Never invoked during R4 engineering tests."""
import argparse
import os
import sys
from pathlib import Path
from .s0_contract import STEPS,MODULES,context,run_relative

def install_io_guard(project,python_prefix=None):
    project=Path(project).resolve();execution=project.parent
    prefixes=[Path(p).resolve() for p in {sys.prefix,sys.base_prefix,python_prefix or sys.prefix}]
    system_read=[Path('/usr/lib'),Path('/usr/lib64'),Path('/lib'),Path('/lib64'),Path('/proc'),Path('/sys')]
    protected={project/'.s0_context.json',project/'s0_commitment_replay.json',execution/'execution_lock.json'}
    immutable_dirs=[project/x for x in ['reproduce','inputs','environment','provenance']]
    immutable_dirs+=[project/'phases/p13/coefficient_law_raw_xt'/x for x in ['src','configs','scripts','tests','docs']]
    immutable_dirs+=[project/'phases/p11']
    def within(p,root):return p==root or root in p.parents
    def guard(event,args):
        if event=='open':
            raw,mode,flags=args
            if isinstance(raw,int):return
            p=Path(os.fsdecode(raw)).absolute().resolve()
            writing=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            if writing:
                if not within(p,execution):raise PermissionError('S0 write outside execution root')
                if p in protected or any(within(p,q) for q in immutable_dirs) or within(p,execution/'receipts'):raise PermissionError('S0 cannot write locked source/provenance')
            elif not within(p,execution) and not any(within(p,q) for q in prefixes+system_read) and str(p) not in {'/dev/null','/dev/urandom','/etc/ld.so.cache','/etc/localtime'}:
                raise PermissionError('S0 cannot read external data/private payloads: '+str(p))
        elif event in {'os.symlink','os.link'}:raise PermissionError('S0 symlink/hardlink creation refused')
        elif event in {'os.remove','os.rmdir','os.mkdir','os.rename'}:
            paths=args[:2] if event=='os.rename' else args[:1]
            for raw in paths:
                p=Path(os.fsdecode(raw)).absolute().resolve()
                if not within(p,execution) or p in protected or any(within(p,q) for q in immutable_dirs) or within(p,execution/'receipts'):
                    raise PermissionError('S0 filesystem mutation outside mutable execution namespace')
    sys.addaudithook(guard)
    return guard

def dispatch_module(project,module,argv):
    """Replace the wrapper with normal python -m; reinstall the guard at startup."""
    root,_=context(project)
    bootstrap=root/'reproduce/s0_bootstrap'
    if not (bootstrap/'sitecustomize.py').is_file():
        raise RuntimeError('missing S0 child I/O bootstrap')
    env=os.environ.copy()
    env['P13_S0_PROJECT_ROOT']=str(root)
    paths=[bootstrap,root/'phases/p13/coefficient_law_raw_xt/src',root/'phases/p11/raw_xt_td/src',root]
    env['PYTHONPATH']=os.pathsep.join(map(str,paths))+ (os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    os.chdir(root)
    os.execve(sys.executable,[sys.executable,'-B','-m',module,*argv],env)

def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root',type=Path,required=True);p.add_argument('--step',choices=STEPS,required=True);a=p.parse_args()
    root,lock=context(a.project_root)
    for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
        if os.environ.get(key)!='1':raise RuntimeError('S0 requires one BLAS/OpenMP thread')
    argv=['--project-root',str(root)]
    if a.step in {'K0','K1'}:argv+=['--run-dir',str(root/run_relative(a.step))]
    dispatch_module(root,'p13rawxt.'+MODULES[a.step],argv)

if __name__=='__main__':main()
