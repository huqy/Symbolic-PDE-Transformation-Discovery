"""Synthetic S1 wiring checks. No formal fit, solver, or heavy stage launch."""
import copy
import ast
import io
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stdout
from types import SimpleNamespace
from .common import SOURCE_ROOT, file_sha256
from .s0_contract import P, write, load
from . import s1_lineage as l
from .s1_launcher import validate_entry, plan

for rel in (P+'src', 'phases/p11/raw_xt_td/src'):
    sys.path.insert(0, str(SOURCE_ROOT / rel))

class S1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='s1_test_', dir=os.environ.get('P13_PREFLIGHT_TMPDIR', '/work/tmp'))
        self.base = Path(self.tmp.name); self.project = self.base / 'execution/project'; self.project.mkdir(parents=True)
        cfg = self.project / P / 'configs'; cfg.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SOURCE_ROOT / P / 'configs', cfg)
        self.run = self.project / l.RUN; self.run.mkdir(parents=True)
        self.ctx = {'schema':'P13_S1_EXECUTION_V1','execution_id':'fresh-fixture','source_files':{},'input_files':{},'diagnostic_files':[], 'control_files':[], 'diagnostic_archive':str(self.base/'diagnostic.tar.xz')}
        write(self.project.parent/'s1_execution.json', self.ctx)
    def tearDown(self): self.tmp.cleanup()

    def fixture_units(self, continuation=False, clear_count=1):
        from p13rawxt.s1_search_primitives import continuation_decision, operator_qualification
        dec = continuation_decision([(1., .5)]*4, not continuation, True)
        dec.update(integrity_pass=True, clear_FULL_operator_qualified_exists=not continuation)
        write(self.run/'K2A_continuation_decision.json', dec)
        write(self.run/'K2A_scientific_summary.json', {'OVERALL_STATUS':'PASS','first_rung':True})
        write(self.run/'K2A_authoritative_input_manifest.json', {'objects':[]})
        l.complete(self.project,'K2A',[self.run/'K2A_continuation_decision.json',self.run/'K2A_scientific_summary.json'])
        hs = l.horizons(self.project, dec)
        # Tiny registries with production horizon labels, not production proposals.
        for seed in l.config(self.project,'k1')['paired_seeds']:
            for arm,total in hs.items():
                unit=self.run/'units'/f'seed_{seed:02d}'/arm;unit.mkdir(parents=True)
                write(unit/'unit_summary.json',{'status':'PASS','processed':total,'total':total,
                      'counters':{'F4_scientific_branches':0,'F4_branch_emissions':0},'identity_baseline':{'J_i':[1.]*6}})
                for name in ('proposal_ledger','branch_registry','skeleton_registry','equivalence_map'):
                    (unit/(name+'.jsonl')).write_text('')
        meta={'frozen':True,'clear_membership_index':l.RUN+'/K2A_FROZEN_FULL_CLEAR_MEMBERSHIP.jsonl',
              'unresolved_membership_index':l.RUN+'/K2A_FROZEN_FULL_UNRESOLVED_MEMBERSHIP.jsonl',
              'clear_FULL_branch_count':clear_count,'unresolved_FULL_branch_count':0}
        (self.project/meta['clear_membership_index']).write_text('{"scientific_branch_id":"fresh-not-historical"}\n' if clear_count else '')
        (self.project/meta['unresolved_membership_index']).write_text('')
        write(self.run/'K2A_membership_lock.json',meta)
        return meta

    def freeze(self, continuation=False, clear_count=1):
        meta=self.fixture_units(continuation, clear_count)
        write(self.run/'FINAL_TRAIN_membership.json',meta)
        l.complete(self.project,'FINAL',[self.run/'FINAL_TRAIN_membership.json'])
        return l.freeze_membership(self.project)

    def test_frozen_baseline_compile_import(self):
        from .verify_baseline import verify
        self.assertEqual(verify()['status'],'PASS')
        for p in (SOURCE_ROOT/P/'src/p13rawxt').glob('s1_*.py'):
            compile(p.read_bytes(),str(p),'exec');importlib.import_module('p13rawxt.'+p.stem)

    def test_s0_authorization_positive_negative(self):
        cfg=l.config(SOURCE_ROOT,'k1')
        e={'status':'AUTHORIZED','authorized_action':'P13-S1-K0_FORMAL_SEARCH_IMPLEMENTATION_REGRESSION',
           'formal_S1_K1_search_authorized':False,'active_inputs':{'K1_run':l.K1},
           'first_formal_rung_after_S1_K0_PASS':{'arms':cfg['arms'],'paired_seeds':len(cfg['paired_seeds']),
                                              'structural_proposals_per_seed_per_arm':cfg['budget']['proposals_per_seed_per_arm']}}
        a={'status':'PASS','S1_K0_authorized':True,'S1_K1_formal_search_authorized':False}
        validate_entry(e,a,cfg)
        for key,value in [('status','DENIED'),('authorized_action','K1'),('formal_S1_K1_search_authorized',True)]:
            bad=copy.deepcopy(e);bad[key]=value
            with self.assertRaises(ValueError):validate_entry(bad,a,cfg)
        with self.assertRaises(ValueError):validate_entry(e,dict(a,S1_K0_authorized=False),cfg)

    def test_fresh_membership_and_mutation(self):
        m=self.freeze();self.assertEqual(m['membership']['clear']['count'],1)
        self.assertEqual(l.verify_membership(self.project),m)
        p=self.project/m['membership']['clear']['path'];p.write_text(p.read_text()+'{}\n')
        with self.assertRaises(ValueError):l.verify_membership(self.project)

    def test_dynamic_receipt_chain(self):
        self.freeze()
        for step in ('K2B','K2C','K3','PF0','PF1'):
            p=self.run/(step+'_synthetic.json');write(p,{'fresh_stage':step,'semantic_digest':'fresh-'+step})
            r=l.complete(self.project,step,[p],semantic='fresh-'+step)
            self.assertEqual(l.verify_receipt(self.project,step),r)
        r=l.verify_receipt(self.project,'PF1');self.assertIn('K2C',r['parents']);self.assertIn('K3',r['parents']);self.assertIn('PF0',r['parents'])
        p=l.receipt_path(self.project,'K3');v=load(p);v['semantic_digest']='tamper';write(p,v)
        with self.assertRaises(ValueError):l.verify_receipt(self.project,'PF1')


    def test_false_continuation(self):
        from .s1_continuation import extend,final_adjudication
        self.fixture_units(False)
        with patch('p13rawxt.s1_k1_formal_search.run_arm',side_effect=AssertionError('K1C called')):
            self.assertEqual(extend(self.project),[]);final_adjudication(self.project)
        l.complete(self.project,'FINAL',[self.run/'FINAL_TRAIN_membership.json'])
        lock=l.freeze_membership(self.project)
        first=l.config(self.project,'k1')['budget']['proposals_per_seed_per_arm']
        self.assertEqual(set(lock['horizons'].values()),{first})

    def test_true_continuation_only_eligible_complete_before_freeze(self):
        from .s1_continuation import extend,final_adjudication
        self.fixture_units(True);calls=[]
        def runner(root,run,k1,base,cfg,seed,arm,workers,total):
            calls.append((seed,arm,total));return {'status':'PASS','processed':total}
        extend(self.project,runner)
        rule=l.config(self.project,'k2a')['continuation_rule']
        self.assertEqual({a for _,a,_ in calls},set(rule['eligible_arms']));self.assertEqual(len(calls),8)
        self.assertFalse(l.membership_path(self.project).exists())
        l.complete(self.project,'K1C',[self.run/'K1C_summary.json'])
        from p13rawxt import s1_k2a_train_adjudication as a
        from p13rawxt.s1_search_primitives import operator_qualification
        unit=self.run/'units/seed_01/FULL-V2';rows=[]
        first=l.config(self.project,'k1')['budget']['proposals_per_seed_per_arm']
        for j,value in enumerate((.4,.4,.5,.9),1):
            rows.append({'arm':'FULL-V2','paired_seed':1,'proposal_index':first+j,'scientific_branch_id':'new-'+str(j),
                         'exact_equivalence_class':'same-execution','structural_hash':'skeleton','J_i':[value]*6,
                         'J_family':value,'coefficient_dependent_syntax':True,
                         'operator_qualification':operator_qualification([value]*6,[1.]*6,True,True,l.config(self.project,'k2a')['tau_num'])})
        (unit/'branch_registry.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        (unit/'skeleton_registry.jsonl').write_text(''.join(json.dumps({'proposal_index':r['proposal_index']})+'\n' for r in rows))
        us=load(unit/'unit_summary.json');us['counters'].update(F4_scientific_branches=len(rows),F4_branch_emissions=len(rows));write(unit/'unit_summary.json',us)
        # Scan plumbing is exercised with tiny fixtures; full proposal budgets are never executed.
        def proposals(path,checkpoints):return {'row_count':max(checkpoints),'prefix':{},'total_worker_cpu_seconds':.000123456789,'total_evaluator_calls':3}
        with patch.object(a,'_scan_proposal_ledger',proposals),patch('p13rawxt.s1_search_primitives.continuation_decision',side_effect=AssertionError('second decision')):
            final_adjudication(self.project)
        meta=load(self.run/'FINAL_TRAIN_membership.json');self.assertEqual(meta['clear_FULL_branch_count'],2)
        self.assertEqual(meta['unresolved_FULL_branch_count'],1)
        self.assertEqual(meta['clear_FULL_exact_execution_class_count'],1)
        l.complete(self.project,'FINAL',[self.run/'FINAL_TRAIN_membership.json',self.run/'FINAL_TRAIN_adjudication.json'])
        lock=l.freeze_membership(self.project)
        self.assertEqual(lock['horizons']['FULL-V1'],l.config(self.project,'k1')['budget']['proposals_per_seed_per_arm'])
        self.assertEqual(lock['horizons']['FULL-V2'],rule['continuation_total_horizon'])

    def test_diagnostic_gate_and_all_dev_sealed_denied(self):
        from .resolve_inputs import resolve_input
        with self.assertRaises(FileNotFoundError):l.diagnostic_capability(self.project,'K2B')
        self.freeze();l.diagnostic_capability(self.project,'K2B')
        for step in l.STEPS:
            if step!='K2B':
                with self.assertRaises(PermissionError):l.diagnostic_capability(self.project,step)
            for ident in ('development_coefficient','development_response','sealed_coefficient','sealed_response'):
                with self.assertRaises(PermissionError):resolve_input(ident,'S1-'+step,self.base/'absent')

    def test_frozen_restart_and_cpu_loop(self):
        from .s1_regression import restart_regression
        self.assertEqual(restart_regression(self.run/'fixture',l.config(self.project,'k1'))['status'],'PASS')

    def test_atomic_s1_marker_actual_audit_hook(self):
        for rel in ('reproduce',P+'src','phases/p11/raw_xt_td/src'):
            shutil.copytree(SOURCE_ROOT/rel,self.project/rel,ignore=shutil.ignore_patterns('__pycache__'))
        (self.project/P/'runs/unrelated').mkdir()
        ctx=load(self.project.parent/'s1_execution.json')
        ctx['source_files']={p.relative_to(self.project).as_posix():{} for p in self.project.rglob('*') if p.is_file()}
        write(self.project.parent/'s1_execution.json',ctx)
        env=os.environ.copy();env.update(dict.fromkeys(('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'),'1'))
        env.update(P13_S1_PROJECT=str(self.project),P13_S1_STEP='K1A',PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',
            PYTHONPATH=os.pathsep.join(map(str,[self.project/'reproduce/s1_bootstrap',self.project,self.project/P/'src',self.project/'phases/p11/raw_xt_td/src'])))
        result=subprocess.run([sys.executable,'-B','-m','reproduce.s1_atomic_marker_fixture'],env=env,cwd=self.project,
                              capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout);self.assertEqual(report['status'],'PASS')
        self.assertTrue(report['actual_audit_hook']);self.assertTrue(report['frozen_atomic_writer'])
        self.assertEqual(report['denied_writes'],10);self.assertEqual(report['denied_renames'],2)
        self.assertTrue(report['generated_trees_preserved'])
        self.assertEqual(report['k0_marker_lifecycle'],'PASS')

    def test_child_fork_spawn_policy(self):
        self.freeze()
        shutil.copytree(SOURCE_ROOT/'reproduce',self.project/'reproduce',ignore=shutil.ignore_patterns('__pycache__'))
        files={p.relative_to(self.project).as_posix():{} for p in self.project.rglob('*') if p.is_file()}
        ctx=load(self.project.parent/'s1_execution.json');ctx['source_files']=files;write(self.project.parent/'s1_execution.json',ctx)
        denied=self.base/'DEVELOPMENT_payload';denied.write_text('SYNTHETIC ONLY')
        sealed=self.base/'SEALED_payload';sealed.write_text('SYNTHETIC ONLY')
        diagnostic=Path(ctx['diagnostic_archive']);diagnostic.write_text('SYNTHETIC ONLY')
        cross=self.project/l.K1/'OPENED_TRANSFER_DIAGNOSTIC.npz';cross.parent.mkdir(parents=True,exist_ok=True);cross.write_text('SYNTHETIC ONLY')
        rel=cross.relative_to(self.project).as_posix();ctx['diagnostic_files']=[rel];ctx['input_files'][rel]=l.record(self.project,cross)
        write(self.project.parent/'s1_execution.json',ctx)
        env=os.environ.copy();env.update(P13_S1_PROJECT=str(self.project),P13_S1_STEP='K0',P13_DENY_PROBE=str(denied),
            PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join(map(str,[self.project/'reproduce/s1_bootstrap',self.project])))
        for step in l.STEPS:
            env['P13_S1_STEP']=step
            expected='READ' if step=='K2B' else 'DENIED'
            env['P13_IO_PROBES']=json.dumps([[str(denied),'DENIED'],[str(sealed),'DENIED'],[str(diagnostic),expected],[str(cross),expected]])
            result=subprocess.run([sys.executable,'-B','-m','reproduce.s1_io_fixture'],env=env,cwd=self.project,capture_output=True,text=True,timeout=40)
            self.assertEqual(result.returncode,0,step+result.stdout+result.stderr)

    def test_dry_run_paths_and_no_frozen_edits(self):
        p=plan(self.base/'s0',self.base/'s1',self.base/'private',True)
        self.assertEqual([x['step'] for x in p['steps']],list(l.STEPS))
        self.assertFalse(p['scientific_stage_started'])
        from .release_integrity import verify
        self.assertEqual(verify()['scientific_files_changed'],0)

    def test_isolated_prepare_fresh_s0_and_guarded_k0(self):
        from . import s0_contract as s0
        from . import s1_launcher as launcher
        from .common import digest
        root=self.base/'s0';p=root/'project';p.mkdir(parents=True)
        write(root/'execution_lock.json',{'schema':'P13_S0_EXECUTION_LOCK_V1','execution_id':'s0-fixture','source_commit':'s0-source','source_files':{}})
        write(p/'.s0_context.json',{'schema':'P13_S0_CONTEXT_V1','execution_id':'s0-fixture'})
        cfg=l.config(SOURCE_ROOT,'k1'); hashes={}
        for step in s0.STEPS:
            r=p/s0.run_relative(step);r.mkdir(parents=True)
            write(r/'semantic_output_digest.json',{'semantic_output_digest':'fresh-'+step})
            write(r/'audit_summary.json',{'OVERALL_STATUS':'PASS','S1_K1_formal_search_authorized':False})
            (r/'OVERALL_STATUS.txt').write_text('PASS\n')
            if step=='K1':
                objects=[]
                for role,n in [('TRAIN_OPERATOR',6),('OPENED_TRANSFER_DIAGNOSTIC',2)]:
                    for i in range(n):
                        for grid in (17,33,65):
                            path=f'open_search_objects/{role}/{i}_G{grid}.npz';q=r/path;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(b'SYNTHETIC_K0_HASH_ONLY')
                            objects.append({'role':role,'field_id':role+str(i),'grid':grid,'path':path,'sha256':file_sha256(q),'semantic_digest':digest({'synthetic':True})})
                write(r/'open_search_object_manifest.json',{'objects':objects})
                write(r/'private_payload_commitments.json',{})
            if step=='K3':
                entry={'status':'AUTHORIZED','authorized_action':'P13-S1-K0_FORMAL_SEARCH_IMPLEMENTATION_REGRESSION',
                       'formal_S1_K1_search_authorized':False,'active_inputs':{'K1_run':l.K1},
                       'first_formal_rung_after_S1_K0_PASS':{'arms':cfg['arms'],'paired_seeds':len(cfg['paired_seeds']),
                       'structural_proposals_per_seed_per_arm':cfg['budget']['proposals_per_seed_per_arm']}}
                write(r/'s1_entry_manifest.json',entry)
                write(r/'s0_gate_adjudication.json',{'status':'PASS','S1_K0_authorized':True,'S1_K1_formal_search_authorized':False})
            rec={'execution_id':'s0-fixture','step':step,'run_relative':s0.run_relative(step),'semantic_output_digest':'fresh-'+step,
                 'parent_receipt_digests':dict(hashes),'artifacts':{q.relative_to(r).as_posix():{'sha256':file_sha256(q),'bytes':q.stat().st_size} for q in r.rglob('*') if q.is_file()}}
            rec['receipt_digest']=digest(rec);hashes[step]=rec['receipt_digest'];write(root/'receipts'/f'{step}.json',rec)
        files={}
        for subtree in ('reproduce','phases','inputs','provenance','environment'):
            for q in (SOURCE_ROOT/subtree).rglob('*'):
                if q.is_file() and '__pycache__' not in q.parts:
                    files[q.relative_to(SOURCE_ROOT).as_posix()]={'sha256':file_sha256(q),'bytes':q.stat().st_size,'mode':q.stat().st_mode&0o777}
        staging=self.base/'staging';staging.mkdir();work=self.base/'fresh'
        native_open = Path.open
        def forbid_diagnostic_read(path, *args, **kwargs):
            if '/OPENED_TRANSFER_DIAGNOSTIC/' in str(path):
                raise AssertionError('pre-freeze diagnostic payload read, including hashing')
            return native_open(path, *args, **kwargs)
        with patch.object(launcher,'check_source',return_value=('fixture-source',files,{'status':'PASS'})), patch.object(Path,'open',forbid_diagnostic_read):
            project=launcher.prepare(work,root,staging)
            self.assertEqual(launcher.prepare(work,root,staging,True),project)
            with self.assertRaises(FileExistsError):launcher.prepare(work,root,staging)
        ctx=l.context(project)
        self.assertTrue(all(not (project/x).exists() for x in ctx['diagnostic_files']))
        env=os.environ.copy();env.update(dict.fromkeys(launcher.THREADS,'1'))
        env.update(P13_S1_PROJECT=str(project),P13_S1_STEP='K0',PYTHONDONTWRITEBYTECODE='1',
                   PYTHONPATH=os.pathsep.join(map(str,[project/'reproduce/s1_bootstrap',project,project/P/'src',project/'phases/p11/raw_xt_td/src'])))
        result=subprocess.run([sys.executable,'-B','-m','reproduce.s1_step','--project-root',str(project),'--step','K0'],
                              cwd=project,env=env,capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(load(project/l.K0/'audit_summary.json')['formal_K1A_authorized'],True)
        diag=[r for r in load(project/l.K1/'open_search_object_manifest.json')['objects'] if r['role']=='OPENED_TRANSFER_DIAGNOSTIC']
        projection=[{k:r.get(k) for k in ('field_id','grid','path','sha256','semantic_digest')} for r in diag]
        self.assertEqual(load(project/l.K0/'03_k1_open_inputs.json')['existing_transfer_commitments_metadata_only'],projection)
        self.assertFalse((project/P/'runs/ACTIVE_P13_S1_K0R_INCOMPLETE_RUN.txt').exists())
        self.assertFalse((project/l.K0/'ACTIVE_P13_S1_K0R_INCOMPLETE_RUN.txt').exists())
        self.assertFalse((project/l.RUN/'units').exists())
        from p13rawxt.s1_k1_formal_search import _verify_entry
        _verify_entry(project,cfg)
        summary=load(project/l.K0/'audit_summary.json');summary['formal_K1A_authorized']=False
        write(project/l.K0/'audit_summary.json',summary)
        with self.assertRaises(RuntimeError):_verify_entry(project,cfg)

    def test_actual_post_adapters_and_fresh_freeze_lineage(self):
        self.post_fixture(1)

    def test_empty_cohort_post_freeze(self):
        self.post_fixture(0)

    def scope_lineage(self, continuation):
        """Tiny completed lineage through K2C; coordinator must freeze real K3."""
        rel=P+'src/p13rawxt/calibration_instruments.py'
        dst=self.project/rel;dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(SOURCE_ROOT/rel,dst)
        ctx=load(self.project.parent/'s1_execution.json');ctx['source_commit']='scope-fixture'
        write(self.project.parent/'s1_execution.json',ctx)
        for step in ('K0','K1A','K1B'):
            p=self.run/(step+'_fixture.json');write(p,{'synthetic_only':True})
            l.complete(self.project,step,[p])
        meta=self.fixture_units(continuation)
        if continuation:
            p=self.run/'K1C_summary.json';write(p,{'status':'PASS','synthetic_only':True})
            l.complete(self.project,'K1C',[p])
        write(self.run/'FINAL_TRAIN_membership.json',meta)
        l.complete(self.project,'FINAL',[self.run/'FINAL_TRAIN_membership.json'])
        lock=l.freeze_membership(self.project)
        from .s0_contract import run_relative
        write(self.project/l.K1/'private_payload_commitments.json',{
            'coefficient:DEVELOPMENT_COEF':{},'coefficient:SEALED_FINAL_COEF':{},
            'response:DEVELOPMENT_COEF':{},'response:SEALED_FINAL_COEF':{}})
        write(self.project/run_relative('K2R4')/'capacity_reuse_lock.json',{'source_run':run_relative('K2R3')})
        write(self.project/run_relative('K2R3')/'calibration_only/capacity_instrument_lock_k2r3.json',
              {'role':'CALIBRATION_ONLY','forbidden_from_search':True,'instrument_hashes':{'null_capacity':'fixture-null'},'theta':{'null_capacity':[.1]}})
        from .s1_post import chain_semantic
        for step,sub in [('K2B','K2B_diagnostics'),('K2C','K2C_theory_bridge')]:
            d=self.run/sub;d.mkdir()
            write(d/(step+'_semantic_output_digest.json'),{'semantic_output_digest':'fresh-'+step})
            write(d/(step+'_scientific_summary.json'),{'OVERALL_STATUS':'PASS','semantic_output_digest':'fresh-'+step})
            chain_semantic(self.project,step);l.complete(self.project,step,list(d.iterdir()))
        return lock

    def scope_execute(self, resume, calls):
        from . import s1_launcher as launcher
        from .s1_post import freeze_k3
        from .s1_scope import require_pf0_scope
        def child(cmd, **kwargs):
            step=cmd[-1];calls.append(step)
            if step=='K3':freeze_k3(self.project)
            elif step in ('PF0','PF1'):
                require_pf0_scope(self.project)
                sub='PF0_postfreeze' if step=='PF0' else 'PF1_final_freeze'
                write(self.run/sub/'synthetic_gate.json',{'synthetic_only':True,'stage':step})
            else:raise AssertionError('unexpected scientific child: '+step)
        with patch.object(launcher,'prepare',return_value=self.project), \
             patch.object(launcher,'project_controls'), \
             patch.object(launcher.subprocess,'run',side_effect=child):
            return launcher.execute(self.project.parent,self.base/'s0',self.base/'staging',resume)

    def test_pf0_scope_false_launcher_and_resume(self):
        self.scope_lineage(False);calls=[]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.scope_execute(False,calls),0)
        self.assertEqual(calls,['K3','PF0','PF1'])
        for step in ('K3','PF0','PF1'):l.verify_receipt(self.project,step)
        self.assertFalse((self.project.parent/'pf0_scope_guard.json').exists())
        calls.clear()
        with redirect_stdout(io.StringIO()):self.assertEqual(self.scope_execute(True,calls),0)
        self.assertEqual(calls,[])

    def test_pf0_scope_true_launcher_and_resume(self):
        from .s1_scope import STATUS,EXIT_CODE
        lock=self.scope_lineage(True);calls=[]
        protected=[self.project/r['path'] for r in list(lock['membership'].values())+lock['registries']]
        protected += [l.membership_path(self.project),self.run/'FINAL_TRAIN_membership.json']
        before={p:p.read_bytes() for p in protected}
        native_open=Path.open;opened=[]
        def metadata_only(path,*args,**kwargs):
            # The actual coordinator/K3/guard may read metadata, never any payload.
            if path.suffix in ('.npy','.npz','.xz','.h5'):
                raise AssertionError('scope stop attempted payload read: '+str(path))
            opened.append(path);return native_open(path,*args,**kwargs)
        stdout=io.StringIO()
        with redirect_stdout(stdout),patch.object(Path,'open',metadata_only):
            self.assertEqual(self.scope_execute(False,calls),EXIT_CODE)
        self.assertEqual(calls,['K3']);self.assertIn(STATUS,stdout.getvalue())
        self.assertEqual({p:p.read_bytes() for p in protected},before)
        k3=l.verify_receipt(self.project,'K3')
        k3bytes={self.project/r['path']:(self.project/r['path']).read_bytes() for r in k3['outputs']}
        path=self.project.parent/'pf0_scope_guard.json';stop=l.unseal(load(path))
        self.assertEqual(stop['status'],STATUS);self.assertEqual(stop['parent_K3_receipt_digest'],k3['digest'])
        self.assertEqual(stop['membership_lock_digest'],lock['digest'])
        self.assertTrue(stop['continuation']['authorized']);self.assertFalse(stop['PF0_PF1_authorized'])
        stop_bytes=path.read_bytes();stop_mtime=path.stat().st_mtime_ns
        calls.clear();stdout=io.StringIO()
        with redirect_stdout(stdout),patch.object(Path,'open',metadata_only):
            self.assertEqual(self.scope_execute(True,calls),EXIT_CODE)
        self.assertEqual(calls,[]);self.assertIn(STATUS,stdout.getvalue())
        self.assertEqual(path.read_bytes(),stop_bytes);self.assertEqual(path.stat().st_mtime_ns,stop_mtime)
        self.assertEqual({p:p.read_bytes() for p in protected},before)
        self.assertEqual({p:p.read_bytes() for p in k3bytes},k3bytes)
        self.assertEqual(l.verify_membership(self.project),lock)
        for step,sub in [('PF0','PF0_postfreeze'),('PF1','PF1_final_freeze')]:
            self.assertFalse(l.receipt_path(self.project,step).exists());self.assertFalse((self.run/sub).exists())
            self.assertEqual(list(self.run.glob(step+'_*')),[])

    def test_pf0_scope_child_and_adapter_fail_closed(self):
        from .s1_scope import STATUS,PF0ScopeBlocked
        from .s1_post import adapt
        from .s1_step import run
        self.scope_lineage(True)
        for step in ('PF0','PF1'):
            with patch('reproduce.s1_step.importlib.import_module',side_effect=AssertionError('frozen addendum imported')):
                with self.assertRaisesRegex(PF0ScopeBlocked,STATUS):run(self.project,step)
            module=SimpleNamespace()
            with self.assertRaisesRegex(PF0ScopeBlocked,STATUS):adapt(self.project,step,module)
        self.assertFalse((self.run/'PF0_postfreeze/PF0_data_boundary_guard.json').exists())

    def test_pf0_scope_dry_run_and_no_boundary_rewrite(self):
        from .s1_scope import STATUS,EXIT_CODE
        env=os.environ.copy();env['P13_PYTHON']=sys.executable
        result=subprocess.run(['bash',str(SOURCE_ROOT/'reproduce/run_s1.sh'),'--dry-run',
            '--s0-execution',str(self.base/'s0'),'--workdir',str(self.project.parent),
            '--staging-root',str(self.base/'staging'),'--resume'],cwd=SOURCE_ROOT,env=env,capture_output=True,text=True,check=True)
        dry=json.loads(result.stdout);self.assertEqual(dry['pf0_pf1_scope']['status'],STATUS)
        self.assertEqual(dry['pf0_pf1_scope']['exit_code'],EXIT_CODE)
        self.assertIn('never invoke PF0/PF1',dry['pf0_pf1_scope']['true'])
        self.assertIn('continuation=false',dry['steps'][-2]['gate'])
        self.assertIn('continuation=false',dry['steps'][-1]['gate'])
        self.assertFalse((self.project.parent/'pf0_scope_guard.json').exists())
        # Frozen false verification remains intact; production adapters must never
        # assign/write that boundary field to disguise a real continuation.
        for p in (SOURCE_ROOT/'reproduce').glob('s1_*.py'):
            for node in ast.walk(ast.parse(p.read_text())):
                if isinstance(node,ast.Subscript) and isinstance(node.ctx,ast.Store):
                    self.assertFalse(isinstance(node.slice,ast.Constant) and node.slice.value=='continuation_32768_run',str(p))
                if isinstance(node,ast.Dict):
                    self.assertFalse(any(isinstance(k,ast.Constant) and k.value=='continuation_32768_run' for k in node.keys),str(p))

    def post_fixture(self, clear_count):
        from .s1_post import adapt,freeze_k3,chain_semantic
        from p13rawxt import s1_pf0_postfreeze_diagnostics as pf0
        from p13rawxt import s1_pf1_final_freeze as pf1
        m=self.freeze(clear_count=clear_count)
        for rel in ('src/p13rawxt/calibration_instruments.py','src/p13rawxt/s1_k2c_theory_bridge.py'):
            dst=self.project/P/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(SOURCE_ROOT/P/rel,dst)
        private={'coefficient:DEVELOPMENT_COEF':{},'coefficient:SEALED_FINAL_COEF':{},'response:DEVELOPMENT_COEF':{},'response:SEALED_FINAL_COEF':{}}
        write(self.project/l.K1/'private_payload_commitments.json',private)
        from .s0_contract import run_relative
        write(self.project/run_relative('K2R4')/'capacity_reuse_lock.json',{'source_run':run_relative('K2R3')})
        write(self.project/run_relative('K2R3')/'calibration_only/capacity_instrument_lock_k2r3.json',
              {'role':'CALIBRATION_ONLY','forbidden_from_search':True,'instrument_hashes':{'null_capacity':'fixture-null'},'theta':{'null_capacity':[.1]}})
        for step,sub in [('K2B','K2B_diagnostics'),('K2C','K2C_theory_bridge')]:
            d=self.run/sub;d.mkdir()
            write(d/(step+'_semantic_output_digest.json'),{'semantic_output_digest':'new-'+step})
            write(d/(step+'_scientific_summary.json'),{'OVERALL_STATUS':'PASS','semantic_output_digest':'new-'+step})
            (d/('K2B_diagnostic_branch_results.jsonl' if step=='K2B' else 'K2C_branch_results.jsonl')).write_text('{}\n')
            if step=='K2B':write(d/'K2B_diagnostic_identity_baselines.json',{})
            chain_semantic(self.project,step)
            l.complete(self.project,step,list(d.iterdir()))
        freeze_k3(self.project);l.complete(self.project,'K3',list((self.run/'K3_freeze').iterdir()))
        markers=self.project/P/'runs'
        for stage,target in [('S1_FORMAL',l.RUN),('S1_K3',l.RUN),('S0_K1',l.K1),('S1_PF0',l.RUN)]:
            (markers/('LATEST_P13_'+stage+'_RUN.txt')).write_text(target+'\n')
        try:
            adapt(self.project,'PF0',pf0)
            cfg=pf0._load_json(self.project/P/'configs/p13_s1_pf0_protocol.json')
            _,_,entry=pf0._verify_entry(self.project,cfg)
            self.assertTrue(all(entry['gates'].values()))
            d=self.run/'PF0_postfreeze';d.mkdir()
            (self.run/'PF0_OVERALL_STATUS.txt').write_text('PASS\n')
            nxt='P13-S1-PF1_FINAL_STAGE_FREEZE_AND_S2_HANDOFF';(self.run/'PF0_NEXT_ACTION.txt').write_text(nxt+'\n')
            k3=load(self.run/'K3_freeze/K3_SEMANTIC_OUTPUT_DIGEST.json')['semantic_output_digest']
            boundary=dict.fromkeys(['K3_base_freeze_mutated','clear_membership_changed','unresolved_membership_changed','DEVELOPMENT_read','SEALED_read','response_outcomes_read','historical_response_read','new_formal_search','PLCP_run','continuation_32768_run','candidate_refit','ASP_or_theory_or_witness_has_membership_authority','audit_archive_used_as_runtime_input'],False);boundary['status']='PASS'
            write(d/'PF0_data_boundary_guard.json',boundary)
            write(d/'PF0_membership_immutability.json',{'clear_sha256':m['membership']['clear']['sha256'],'clear_count':clear_count,'unresolved_sha256':m['membership']['unresolved']['sha256'],'unresolved_count':0})
            write(d/'PF0_ASP_aggregate.json',{'rows':clear_count,'lambda_1_integrity_error':{'n':clear_count,'max':0. if clear_count else None}})
            write(d/'PF0_PRE_DEV_PREDICTIONS.json',{'frozen_before_DEVELOPMENT':True,'membership_unchanged':True,'predictions_are_descriptive_not_gates':True})
            write(d/'PF0_source_manifest.json',{'files':[]})
            write(d/'PF0_semantic_output_digest.json',{'semantic_output_digest':'new-PF0','outputs':[]})
            summary={'OVERALL_STATUS':'PASS','NEXT_ACTION':nxt,'parent_K3_semantic_digest':k3,'membership_clear_count':clear_count,'membership_unresolved_count':0,'membership_unchanged':True,'DEVELOPMENT_or_SEALED_opened':False,'response_outcomes_opened':False,'new_search_or_refit':False,'K3_freeze_remains_immutable':True,'PF0_semantic_output_digest':'new-PF0'}
            write(d/'PF0_scientific_summary.json',summary);chain_semantic(self.project,'PF0');l.complete(self.project,'PF0',list(d.iterdir()))
            adapt(self.project,'PF1',pf1);self.assertEqual(pf1.run(self.project),0)
            out=load(self.run/'PF1_final_freeze/PF1_FINAL_S1_SCIENTIFIC_FREEZE.json')
            self.assertEqual(out['cohorts']['S2_ACTIVE_clear'],clear_count)
            self.assertEqual(out['route_interpretation'],'UNRESOLVED_REPRO_INTERPRETATION')
            self.assertEqual(out['fresh_parent_receipts']['PF0'],l.verify_receipt(self.project,'PF0')['digest'])
        finally:
            importlib.reload(pf0);importlib.reload(pf1)

if __name__ == '__main__':unittest.main()
