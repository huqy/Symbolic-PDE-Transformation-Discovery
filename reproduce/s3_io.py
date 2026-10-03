"""S3 source/input/output allowlist; stage-specific SEALED capabilities; historical S3 reads denied."""
import os
import sys
from pathlib import Path
from .s3_contract import state, RUN, STEPS, DIRS
from .s1_lineage import RUN as S1_RUN

ACTIVE_POLICY = None

# Frozen K1 opener, s3_k1_sealed_operator_transfer.py:345-348. Other
# frozen run markers are either projected into stage-owned native_status
# (K3/K4) or not called by the fresh reproduction wrappers (K0/K2/K5/K6).
COEFFICIENT_MARKER = 'LATEST_P13_S3_SEALED_FINAL_COEF_INPUT.txt'

def marker_write_allowed(project, step, path):
    runs = Path(project) / Path(RUN).parent
    return step == 'K1' and path.parent == runs and path.name in (
        COEFFICIENT_MARKER, COEFFICIENT_MARKER + '.tmp')

def archive_allowed(input_id, step):
    return step in {'sealed_coefficient':('K1','K3','K4'), 'sealed_response':('K3',)}.get(input_id,())

def install(project, step):
    global ACTIVE_POLICY
    if step not in STEPS: raise PermissionError('unknown S3 stage')
    project=Path(project).resolve(); ctx=state(project); work=project.parent
    s2=Path(ctx['s2']['root']); s1=Path(ctx['s2']['S1']['root']); s1project=s1/'project'; s1run=s1project/S1_RUN
    source={project/p for p in ctx['source_files']}
    pins={Path(r['path']) for r in ctx['s2']['pins']+ctx['s2']['S1']['pins']+ctx['s2']['S1'].get('descriptive_pins',[])}
    from .s0_contract import load
    active=load(s1run/'PF1_final_freeze/PF1_S2_ACTIVE_INPUT_MANIFEST.json')
    for key in ('instrument_lock','pair_builder_source'): pins.add(s1project/active['null_control'][key]['path'])
    from .resolve_inputs import load_manifest
    archives={Path(ctx['staging_root'])/r['staged_basename'] for r in load_manifest()['historical_random_realizations'] if archive_allowed(r['id'],step)}
    libs=[Path(x).resolve() for x in (sys.prefix,sys.base_prefix)]+[Path(x) for x in ('/usr/lib','/usr/lib64','/lib','/lib64')]
    parent_source={s2/'project'/p for p in load(s2/'s2_execution.json')['source_files']}
    s1ctx=load(s1/'s1_execution.json')
    train=s1project/'phases/p13/coefficient_law_raw_xt/runs/fresh_s0_k1'
    mutable=project/RUN
    def within(p,q): return p==q or q in p.parents
    def can_write(p):
        current=mutable/DIRS[STEPS.index(step)]
        return p not in source and (p==mutable or within(p,current) or
               marker_write_allowed(project,step,p))
    def can_read(p):
        if p in source or p in parent_source or p in pins or p in archives: return True
        if p==work/'s3_execution.json' or within(p,work/'receipts'): return True
        # Incoming TRAIN registries and descriptive S1 evidence only. There is
        # no historical S3 directory, private cache or ambient LATEST capability.
        if within(p,s1run): return True
        from .s2_contract import RUN as S2_RUN
        if within(p,s2/'project'/S2_RUN): return True
        if p==s1/'s1_execution.json' or within(p,s1/'receipts'): return True
        if within(p,s1project/'phases/p13/coefficient_law_raw_xt/runs/fresh_s0_k2r3') or within(p,s1project/'phases/p13/coefficient_law_raw_xt/runs/fresh_s0_k2r4'): return True
        if step=='K0' and within(p,train): return True
        if within(p,mutable):
            relative=p.relative_to(mutable)
            if not relative.parts: return True
            if relative.parts[0] in DIRS:
                return DIRS.index(relative.parts[0])<=STEPS.index(step)
            return False  # no ambient LATEST read capability
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
                raise PermissionError('S3 role I/O denied: '+str(p))
        elif event in ('os.symlink','os.link','subprocess.Popen','os.system','socket.connect'):
            raise PermissionError('S3 external mutation/process/network denied')
        elif event in ('os.remove','os.rmdir','os.mkdir','os.rename'):
            paths=args[:2] if event=='os.rename' else args[:1]
            fds=args[2:4] if event=='os.rename' else ((args[2] if event=='os.mkdir' else args[1]),)
            resolved=[path(raw,fd) for raw,fd in zip(paths,fds)]
            if any(marker_write_allowed(project,step,p) for p in resolved):
                final=project/Path(RUN).parent/COEFFICIENT_MARKER
                if event!='os.rename' or resolved!=[final.with_suffix('.txt.tmp'),final]:
                    raise PermissionError('S3 marker mutation outside exact atomic replace denied')
            for p in resolved:
                if not can_write(p): raise PermissionError('S3 mutation denied')
    sys.addaudithook(guard); ACTIVE_POLICY=(str(project),step)
    return guard
