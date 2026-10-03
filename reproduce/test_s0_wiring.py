"""S0 control-flow/metadata fixtures only. No scientific main/fit/solve calls."""
import ast
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from .common import SOURCE_ROOT,file_sha256,digest
from .s0_contract import STEPS,DEPS,P,run_relative,marker_relative,load,write,parent,replay_commitments,repair_parent,resolve_fresh_run
from .s0_launcher import check_execution_root,commitment_metadata,plan,prepare_step,prepare,verify_snapshot
from .s0_schema import validate_summary,validate_artifact,compare,contract
from .resolve_inputs import CAPABILITIES,resolve_input,stage_view
from .verify_baseline import verify

class S0WiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['P13_PREFLIGHT_TMPDIR']);self.base=Path(self.tmp.name)
        self.root=self.base/'execution';self.project=self.root/'project';self.project.mkdir(parents=True)
        replay=commitment_metadata('fixture-id');write(self.project/'s0_commitment_replay.json',replay)
        write(self.root/'execution_lock.json',{'schema':'P13_S0_EXECUTION_LOCK_V1','execution_id':'fixture-id','source_commit':'fixture-source','source_files':{},'commitment_replay_sha256':file_sha256(self.project/'s0_commitment_replay.json')})
        write(self.project/'.s0_context.json',{'schema':'P13_S0_CONTEXT_V1','execution_id':'fixture-id'})

    def tearDown(self):self.tmp.cleanup()

    def fixture(self,step):
        # Synthetic FAIL records for path tests, never frozen expected decisions.
        run=self.project/run_relative(step);run.mkdir(parents=True)
        obj={'OVERALL_STATUS':'FAIL','formal_S1_search_authorized':False}
        write(run/'audit_summary.json',obj);write(run/'no_leakage_guard.json',{'status':'PASS','role':'SYNTHETIC_METADATA_FIXTURE'})
        write(run/'semantic_output_digest.json',{'semantic_output_digest':'fixture-'+step})
        files={p.name:{'sha256':file_sha256(p),'bytes':p.stat().st_size} for p in run.iterdir()}
        r={'execution_id':'fixture-id','step':step,'run_relative':run_relative(step),'semantic_output_digest':'fixture-'+step,'artifacts':files,
           'parent_receipt_digests':{s:load(self.root/'receipts'/f'{s}.json')['receipt_digest'] for s in DEPS[step]}}
        r['receipt_digest']=digest(r);write(self.root/'receipts'/f'{step}.json',r);return run

    def test_replay_assignment_bypasses_random_generation_and_payload_readers(self):
        from p13rawxt import k1_commit
        source=ast.parse(Path(k1_commit.__file__).read_text())
        main=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        assignment=next(n for n in ast.walk(main) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='private_commitments' for t in n.targets))
        from . import s0_contract
        ns={'s0':s0_contract,'root':self.project,'private_run':self.base/'never-create','protocol':{}}
        def forbidden(*a,**k):raise AssertionError('private generation/payload access')
        ns['_make_private_payloads']=forbidden
        with patch.object(k1_commit,'_make_private_payloads',side_effect=forbidden),patch('tarfile.open',side_effect=forbidden),patch('secrets.token_hex',side_effect=forbidden),patch('numpy.load',side_effect=forbidden):
            exec(compile(ast.fix_missing_locations(ast.Module(body=[assignment],type_ignores=[])),'reviewed_K1_assignment','exec'),ns)
        self.assertEqual(len(ns['private_commitments']),4)
        self.assertTrue(all(r['mode']=='HISTORICAL_REPLAY' for r in ns['private_commitments'].values()))
        self.assertFalse((self.base/'never-create').exists())

    def test_commitment_metadata_contains_no_payload_paths_or_seed(self):
        data=commitment_metadata('fixture-id')
        text=json.dumps(data)
        self.assertNotIn('archive_absolute_path',text);self.assertNotIn('staging_root',text);self.assertNotIn('master_seed',text)
        self.assertEqual(len(data['commitments']),4)
        for row in data['commitments'].values():self.assertEqual(row['row_count'],4 if row['kind']=='coefficient' else 32)

    def test_no_s0_stage_resolves_any_private_archive(self):
        for step in STEPS:
            self.assertEqual(stage_view('S0-'+step,self.base/'absent'),{})
            for ident in CAPABILITIES:
                with self.assertRaises(PermissionError):resolve_input(ident,'S0-'+step,self.base/'absent')

    def test_relocation_and_same_execution_chain(self):
        for step in STEPS:self.fixture(step)
        dest=self.base/'relocated';shutil.move(self.root,dest)
        for step in STEPS:self.assertEqual(parent(dest/'project',step),dest/'project'/run_relative(step))

    def test_every_transition_requires_all_allowed_fresh_predecessors(self):
        for step in STEPS:
            if step!='K0':
                for prior in DEPS[step]:
                    receipt=self.root/'receipts'/f'{prior}.json';saved=receipt.read_bytes();receipt.unlink()
                    try:
                        with self.assertRaises(FileNotFoundError):prepare_step(self.project,step)
                    finally:receipt.write_bytes(saved)
            self.fixture(step)
        for step in ['K2R2','K2R3','K2R4']:
            result=repair_parent(self.project,step)
            self.assertTrue(all(self.project in p.parents for p in result if isinstance(p,Path)))

    def test_stale_latest_is_not_a_dependency(self):
        marker=self.project/marker_relative('K1');marker.parent.mkdir(parents=True);marker.write_text('/historical/run\n')
        with self.assertRaises(FileNotFoundError):parent(self.project,'K1')

    def test_mixed_execution_ids_rejected(self):
        self.fixture('K0');p=self.root/'receipts/K0.json';obj=load(p);obj['execution_id']='another-id';write(p,obj)
        with self.assertRaises(ValueError):parent(self.project,'K0')

    def test_tampered_artifact_and_receipt_chain_rejected(self):
        a=self.fixture('K0');self.fixture('K1');(a/'audit_summary.json').write_text('{}')
        with self.assertRaises(ValueError):parent(self.project,'K0')
        p=self.root/'receipts/K1.json';obj=load(p);obj['parent_receipt_digests']['K0']='wrong';obj['receipt_digest']=digest({k:v for k,v in obj.items() if k!='receipt_digest'});write(p,obj)
        with self.assertRaises(ValueError):parent(self.project,'K1')

    def test_traversal_absolute_symlink_cross_stage_run_rejected(self):
        for x in ['/old/run','../old','phases/p13/coefficient_law_raw_xt/runs/historical']:
            with self.assertRaises(ValueError):resolve_fresh_run(self.project,x)
        self.fixture('K0');p=self.project/run_relative('K0')/'audit_summary.json';p.unlink();p.symlink_to(self.base/'outside')
        with self.assertRaises(ValueError):parent(self.project,'K0')

    def test_source_original_and_compatibility_locks(self):
        from .release_integrity import verify
        self.assertEqual(verify()["status"], "PASS")
        self.assertEqual(verify()["scientific_files_changed"], 0)

    def test_root_guards_and_snapshot_integrity(self):
        for p in [SOURCE_ROOT/'execution',self.base/'DEV_FORENSIC/run',self.base/'DEV_ORIGINAL/run',self.base/'own_sMoEs/sMoEs_PDE_SR_solver/run']:
            with self.assertRaises(ValueError):check_execution_root(p)
        (self.base/'alias').symlink_to(self.root)
        with self.assertRaises(ValueError):check_execution_root(self.base/'alias/new')
        lock=load(self.root/'execution_lock.json');lock['source_files']={'static.txt':{'sha256':hashlib.sha256(b'x').hexdigest(),'bytes':1}};write(self.root/'execution_lock.json',lock)
        (self.project/'static.txt').write_text('y')
        with self.assertRaises(ValueError):verify_snapshot(self.root)

    def test_existing_root_refused_without_resume_and_invalid_resume_refused(self):
        with patch('reproduce.s0_launcher.check_source',return_value=('fixture-source',{},{})),patch('reproduce.s0_launcher.audit_inventory',return_value={}):
            with self.assertRaises(FileExistsError):prepare(self.root,self.base)
            bad=self.base/'existing';bad.mkdir()
            with self.assertRaises(FileNotFoundError):prepare(bad,self.base,resume=True)

    def test_real_frozen_s0_schemas_and_rejection_of_unknown_missing_fields(self):
        # Synthetic shape witnesses; no historical payloads or expected outcomes.
        def witness(shape):
            if shape['type']=='object': return {k:witness(v) for k,v in shape['fields'].items()}
            if shape['type']=='array': return []
            return 0
        for step,spec in contract()['stages'].items():
            row=spec['scientific_files']['audit_summary.json']
            obj=witness(row['reference_shape']);obj['OVERALL_STATUS']='FAIL'
            if 'gate_statuses' in obj: obj['gate_statuses']={k:'UNRESOLVED' for k in spec['gate_names']}
            validate_summary(step,obj)
            extra=copy.deepcopy(obj);extra['unknown_scientific_field']=7
            with self.assertRaises(ValueError):validate_summary(step,extra)
            missing=copy.deepcopy(obj);del missing['OVERALL_STATUS']
            with self.assertRaises(ValueError):validate_summary(step,missing)
            changed=copy.deepcopy(obj);changed['OVERALL_STATUS']='UNRESOLVED'
            self.assertFalse(compare(step,'audit_summary.json',changed,obj)['scientific_equal'])

    def test_plan_renders_seven_real_commands_and_stops_before_s1(self):
        result=plan();self.assertEqual([x['step'] for x in result['steps']],['S0-'+s for s in STEPS]);self.assertFalse(result['scientific_stage_started'])
        run=subprocess.run(['bash',str(SOURCE_ROOT/'reproduce/run_s0.sh'),'--dry-run'],capture_output=True,text=True,env=dict(os.environ,P13_PYTHON=sys.executable))
        self.assertEqual(run.returncode,0);self.assertEqual(len(json.loads(run.stdout)['steps']),7)

    def test_actual_child_dispatch_arguments_with_science_runner_mocked(self):
        from . import s0_step
        for step in STEPS:
            with patch.object(sys,'argv',['probe','--project-root',str(self.project),'--step',step]),patch.object(s0_step,'dispatch_module') as science:
                s0_step.main()
                root,module,argv=science.call_args.args
                self.assertEqual(root,self.project)
                self.assertEqual(module,'p13rawxt.'+__import__('reproduce.s0_contract',fromlist=['MODULES']).MODULES[step])
                expected=['--project-root',str(self.project)]
                if step in {'K0','K1'}:expected+=['--run-dir',str(self.project/run_relative(step))]
                self.assertEqual(argv,expected)

    def test_metadata_only_preparation_writes_lock_before_any_science(self):
        from . import s0_launcher
        target=self.base/'new_execution'
        with patch.object(s0_launcher,'check_source',return_value=('fixture-source',{},{})),patch.object(s0_launcher,'audit_inventory',return_value={'fixture':True}),patch.object(s0_launcher,'run_child',side_effect=AssertionError('science invoked')):
            prepare(target,self.base)
        lock=load(target/'execution_lock.json')
        self.assertEqual(lock['mode'],'HISTORICAL_REPLAY');self.assertFalse(lock['advance_to_S1'])
        self.assertEqual(lock['stage_sequence'],list(STEPS));self.assertFalse((target/'project'/P/'runs').exists())

    def test_s1_s2_s3_all_remain_disabled(self):
        for stage in ['s1','s2','s3','all']:
            r=subprocess.run(['bash',str(SOURCE_ROOT/'reproduce'/('run_'+stage+'.sh')),'--execute'],capture_output=True,env=dict(os.environ,P13_PYTHON=sys.executable))
            self.assertNotEqual(r.returncode,0)

    def test_child_io_policy_and_imports_without_science(self):
        # Run only a guard/import probe. Never call s0_step.main or run_module.
        blocked=self.base/'external_private_fixture';blocked.write_bytes(b'opaque')
        program='''import sys\nfrom pathlib import Path\nfrom reproduce.s0_step import install_io_guard\nroot=Path(sys.argv[1]);blocked=Path(sys.argv[2])\nsys.path[:0]=[str(Path(sys.argv[3])/'phases/p13/coefficient_law_raw_xt/src'),str(Path(sys.argv[3])/'phases/p11/raw_xt_td/src')]\ninstall_io_guard(root,python_prefix=sys.argv[3])\ntry:\n blocked.read_bytes()\n raise AssertionError('external read allowed')\nexcept PermissionError: pass\nimport p13rawxt.k0_lock,p13rawxt.k1_commit,p13rawxt.k2_qualification,p13rawxt.k2r2_attainment_repair,p13rawxt.k2r3_capacity_continuation,p13rawxt.k2r4_operational_fitter,p13rawxt.k3_s0_freeze\nprint('GUARD_AND_IMPORT_ONLY_PASS')\n'''
        program+='from concurrent.futures import ProcessPoolExecutor\nwith ProcessPoolExecutor(max_workers=2) as pool:\n assert list(pool.map(abs,[-1,-2]))==[1,2]\nprint("TOY_PROCESS_POOL_PASS")\n'
        r=subprocess.run([sys.executable,'-B','-c',program,str(self.project),str(blocked),str(SOURCE_ROOT)],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);self.assertIn('GUARD_AND_IMPORT_ONLY_PASS',r.stdout)
        self.assertIn('TOY_PROCESS_POOL_PASS',r.stdout)

if __name__=='__main__':unittest.main()
