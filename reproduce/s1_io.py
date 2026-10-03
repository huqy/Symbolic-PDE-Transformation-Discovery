"""Scientific child/worker I/O allowlist. Installed by sitecustomize before imports."""
import os
import sys
from pathlib import Path

ACTIVE_POLICY = None

def install(project, step):
    global ACTIVE_POLICY
    from .s1_lineage import RUN, K0, K1, POST, STEPS, context, verify_membership
    if step not in STEPS: raise PermissionError('unknown S1 stage')
    project = Path(project).resolve(); work = project.parent
    ctx = context(project)
    if step in POST: verify_membership(project)
    source_files = {project / p for p in ctx['source_files']
                    if p.startswith(('reproduce/', 'phases/p11/', 'phases/p13/')) and '/runs/' not in p}
    # Exact projected files, never a broad S0-root permission.
    projected = {project / p for p in ctx['input_files']}
    denied = {project / p for p in ctx.get('diagnostic_files', [])} if step != 'K2B' else set()
    denied |= {project / p for p in ctx.get('control_files', [])} if step not in ('K3', 'PF1') else set()
    forbidden_source = {project / 'reproduce/s1_reference.json', project / 'reproduce/s1_compare.py'}
    archive = Path(ctx['diagnostic_archive']) if step == 'K2B' else None
    libs = [Path(x).resolve() for x in {sys.prefix, sys.base_prefix}] + [Path('/usr/lib'), Path('/usr/lib64'), Path('/lib'), Path('/lib64')]
    mutable = [project / RUN, project / K0, project / 'phases/p13/coefficient_law_raw_xt/runs']
    # Only the selected stage may write stage outputs; post-membership registries are locked.
    locked = set()
    if step in POST:
        m = verify_membership(project)
        locked = {project / r['path'] for r in list(m['membership'].values()) + m['registries']}
        locked |= {project / RUN / 'FINAL_TRAIN_membership.json'}
    def within(p, q): return p == q or q in p.parents
    def s1_marker(p):
        # Frozen writers create a temporary sibling and atomically replace it.
        return (p.parent == mutable[2] and p.name.startswith('LATEST_P13_S1_')
                and (p.name.endswith('.txt') or p.name.endswith('.txt.tmp')))
    def can_write(p):
        if p in locked or p in projected or p in source_files: return False
        return p == mutable[2] or within(p, project / RUN) or within(p, project / K0) or s1_marker(p)
    def guard(event, args):
        if event == 'open':
            raw, mode, flags = args
            if isinstance(raw, int): return
            p = Path(os.fsdecode(raw)).absolute().resolve()
            writing = (isinstance(mode, str) and any(x in mode for x in 'wax+')) or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing:
                if not can_write(p): raise PermissionError('S1 write denied: ' + str(p))
            else:
                if p in denied or p in forbidden_source: raise PermissionError('S1 role denied: ' + str(p))
                allowed = (p in source_files or p in projected or within(p, project / RUN) or within(p, project / K0)
                           or p.parent == mutable[2] and p.suffix == '.txt'
                           or p == work / 's1_execution.json' or p == work / 'membership_lock.json'
                           or within(p, work / 'receipts') or p == archive
                           or any(within(p, q) for q in libs)
                           or str(p) in ('/dev/null', '/dev/urandom', '/etc/localtime', '/etc/ld.so.cache')
                           or str(p).startswith('/proc/') and p.name in ('stat', 'status', 'maps', 'meminfo', 'cpuinfo'))
                if not allowed: raise PermissionError('S1 read denied: ' + str(p))
        elif event in ('os.symlink', 'os.link'):
            raise PermissionError('S1 links forbidden')
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.rename'):
            raw_paths = args[:2] if event == 'os.rename' else args[:1]
            fds = args[2:4] if event == 'os.rename' else (args[2] if event == 'os.mkdir' else args[1],)
            for raw, fd in zip(raw_paths, fds):
                p = Path(os.fsdecode(raw))
                if not p.is_absolute() and fd not in (None, -1):
                    p = Path(os.readlink('/proc/self/fd/' + str(fd))) / p
                p = p.absolute().resolve()
                if not can_write(p): raise PermissionError('S1 mutation denied: ' + str(p))
        elif event in ('subprocess.Popen', 'os.system', 'os.exec', 'socket.connect'):
            raise PermissionError('scientific S1 child cannot launch external commands/network')
    sys.addaudithook(guard)
    ACTIVE_POLICY = (str(project), step)
    return guard
