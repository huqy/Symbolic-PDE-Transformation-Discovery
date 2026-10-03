"""S2 source/input/output allowlist; SEALED and historical S2 reads always denied."""
import os
import sys
from pathlib import Path
from .s2_contract import state, RUN, STEPS, DIRS
from .s1_lineage import RUN as S1_RUN

ACTIVE_POLICY = None

def archive_allowed(input_id, step):
    return step in {'development_coefficient':('K0','K4','K5'), 'development_response':('K4',)}.get(input_id,())

def install(project, step):
    global ACTIVE_POLICY
    if step not in STEPS: raise PermissionError('unknown S2 stage')
    project=Path(project).resolve(); ctx=state(project); work=project.parent
    s1=Path(ctx['s1']['root']); s1project=s1/'project'; s1run=s1project/S1_RUN
    source={project/p for p in ctx['source_files']}
    pins={Path(r['path']) for r in ctx['s1']['pins']}
    from .s0_contract import load
    active=load(s1run/'PF1_final_freeze/PF1_S2_ACTIVE_INPUT_MANIFEST.json')
    for key in ('instrument_lock','pair_builder_source'): pins.add(s1project/active['null_control'][key]['path'])
    from .resolve_inputs import load_manifest
    archives={Path(ctx['staging_root'])/r['staged_basename'] for r in load_manifest()['historical_random_realizations'] if archive_allowed(r['id'],step)}
    libs=[Path(x).resolve() for x in (sys.prefix,sys.base_prefix)]+[Path(x) for x in ('/usr/lib','/usr/lib64','/lib','/lib64')]
    mutable=project/RUN
    def within(p,q): return p==q or q in p.parents
    def can_write(p):
        current=mutable/DIRS[STEPS.index(step)]
        return p not in source and (p==mutable or within(p,current) or
               p.parent==mutable and p.name.startswith('LATEST_P13_S2_') and p.suffix=='.txt')
    def can_read(p):
        if p in source or p in pins or p in archives: return True
        if p==work/'s2_execution.json' or within(p,work/'receipts'): return True
        # Incoming TRAIN registries and descriptive S1 evidence only. There is
        # no historical S2 directory, private cache or ambient LATEST capability.
        if within(p,s1run): return True
        if within(p,mutable):
            relative=p.relative_to(mutable)
            if not relative.parts: return True
            if relative.parts[0] in DIRS:
                return DIRS.index(relative.parts[0])<=STEPS.index(step)
            return p.suffix=='.txt'  # locally generated markers only
        if any(within(p,q) for q in libs): return True
        return str(p) in ('/dev/null','/dev/urandom','/etc/localtime','/etc/ld.so.cache') or str(p).startswith('/proc/')
    def path(raw,fd=None):
        p=Path(os.fsdecode(raw))
        if not p.is_absolute() and fd not in (None,-1): p=Path(os.readlink('/proc/self/fd/'+str(fd)))/p
        return p.absolute().resolve()
    def guard(event,args):
        if event=='open':
            raw,mode,flags=args
            if isinstance(raw,int): return
            p=path(raw); writing=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
            if not (can_write(p) if writing else can_read(p)):
                raise PermissionError('S2 role I/O denied: '+str(p))
        elif event in ('os.symlink','os.link','subprocess.Popen','os.system','socket.connect'):
            raise PermissionError('S2 external mutation/process/network denied')
        elif event in ('os.remove','os.rmdir','os.mkdir','os.rename'):
            paths=args[:2] if event=='os.rename' else args[:1]
            fds=args[2:4] if event=='os.rename' else ((args[2] if event=='os.mkdir' else args[1]),)
            for raw,fd in zip(paths,fds):
                if not can_write(path(raw,fd)): raise PermissionError('S2 mutation denied')
    sys.addaudithook(guard); ACTIVE_POLICY=(str(project),step)
    return guard
