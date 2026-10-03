"""Release engineering tests. All execution children and asset bytes are synthetic."""
import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from . import public_runner as r, verify_public_assets as v
from .common import SOURCE_ROOT

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name); self.work=self.base/'work'; self.staging=self.base/'assets'; self.staging.mkdir()
        self.assets={'assets':[]}
        for i in range(5):
            data=('opaque synthetic transport '+str(i)).encode(); name='synthetic_'+str(i)+'.tar.xz'
            (self.staging/name).write_bytes(data)
            self.assets['assets'].append({'id':str(i),'filename':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        self.identity={'source_commit':'mock-commit','environment':{'synthetic':True},'staging_root':str(self.staging)}
        self.calls=[]
    def fake_child(self,cmd):
        stage=next(s for s in r.STAGES if 'run_'+s.lower()+'.sh' in cmd[1]); self.calls.append((stage,cmd))
        p=self.work/stage; p.mkdir(exist_ok=True)
        (p/'synthetic_complete.json').write_text(json.dumps({'stage':stage,'source':'mock-commit','arbitrary_PASS_count':7}))
        return 0
    def fake_boundary(self,stage,work,source):
        obj=json.loads((work/stage/'synthetic_complete.json').read_text()); self.assertEqual(obj['source'],source)
        return {'execution_id':'synthetic-'+stage,'receipt':str(work/stage/'synthetic_complete.json'),
                'sha256':hashlib.sha256((work/stage/'synthetic_complete.json').read_bytes()).hexdigest()}
    def run_mock(self,stages=r.STAGES,resume=False,child=None,boundary=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return r.orchestrate(self.work,self.staging,stages,resume,self.identity,child or self.fake_child,boundary or self.fake_boundary)
    def test_exact_five_verify_without_payload_parser(self):
        with patch('tarfile.open',side_effect=AssertionError('archive parsing forbidden')):
            report=v.verify_assets(self.staging,self.assets)
        self.assertEqual(report['status'],'PASS'); self.assertFalse(report['payloads_parsed'])
    def test_missing_all_reports_five_without_fallback(self):
        report=v.verify_assets(self.base/'missing',self.assets)
        self.assertEqual([a['status'] for a in report['assets']],['MISSING']*5)
    def test_wrong_name_is_missing(self):
        row=self.assets['assets'][0]; (self.staging/row['filename']).rename(self.staging/'renamed.tar.xz')
        self.assertEqual(v.verify_assets(self.staging,self.assets)['assets'][0]['status'],'MISSING')
    def test_wrong_bytes(self):
        (self.staging/self.assets['assets'][0]['filename']).write_bytes(b'bad')
        self.assertEqual(v.verify_assets(self.staging,self.assets)['assets'][0]['status'],'BYTES_MISMATCH')
    def test_equal_length_wrong_hash(self):
        row=self.assets['assets'][0]; (self.staging/row['filename']).write_bytes(b'x'*row['bytes'])
        self.assertEqual(v.verify_assets(self.staging,self.assets)['assets'][0]['status'],'SHA256_MISMATCH')
    def test_asset_symlink_refused(self):
        row=self.assets['assets'][0]; p=self.staging/row['filename']; p.unlink(); p.symlink_to(self.staging/self.assets['assets'][1]['filename'])
        self.assertEqual(v.verify_assets(self.staging,self.assets)['assets'][0]['status'],'SYMLINK_REFUSED')
    def test_exact_five_and_unique(self):
        for rows in (self.assets['assets'][:4],self.assets['assets']+[self.assets['assets'][0]],self.assets['assets'][:4]+[self.assets['assets'][0]]):
            with self.assertRaises(ValueError): v.verify_assets(self.staging,{'assets':rows})
    def test_no_path_escape(self):
        self.assets['assets'][0]['filename']='../escape'
        with self.assertRaises(ValueError): v.verify_assets(self.staging,self.assets)
    def test_manifest_matches_frozen_commitments(self):
        self.assertEqual(len(v.load_assets()['assets']),5)
    def test_integrated_dry_run_has_no_children_or_writes(self):
        with patch.object(r,'verify_assets',return_value=v.verify_assets(self.staging,self.assets)),patch.object(r,'launch',side_effect=AssertionError('no child')):
            result=r.dry_plan(self.work,self.staging,r.STAGES,False,{'synthetic':True})
        self.assertEqual([p['stage'] for p in result['stage_plans']],list(r.STAGES)); self.assertFalse(self.work.exists())
        self.assertFalse(result['scientific_stage_started']); self.assertFalse(result['count_targets_used'])
    def test_commands_forward_only_existing_stage_interfaces(self):
        for s in r.STAGES:
            cmd=r.command(s,self.work,self.staging,True,True)
            self.assertIn('--execute',cmd); self.assertIn('--resume',cmd); self.assertIn(str(self.work/s),cmd)
            if s!='S0': self.assertIn(str(self.work/r.STAGES[r.STAGES.index(s)-1]),cmd)
    def test_mock_chain_all_stages(self):
        result=self.run_mock(); self.assertEqual([s for s,c in self.calls],list(r.STAGES))
        self.assertEqual(result['R_C'],'NOT_AUTOMATICALLY_ASSIGNED')
    def test_stage_failure_preserves_and_resumes_same_root(self):
        def broken(cmd):
            if 'run_s1.sh' in cmd[1]: (self.work/'S1').mkdir(); return 124
            return self.fake_child(cmd)
        with self.assertRaises(r.ProtocolStop): self.run_mock(child=broken)
        self.assertEqual(list(r.load(self.work/r.LOCK_NAME)['completed']),['S0'])
        self.calls=[]; self.run_mock(resume=True)
        self.assertEqual([s for s,c in self.calls],['S1','S2','S3']); self.assertIn('--resume',self.calls[0][1])
    def test_complete_resume_skips_every_child(self):
        self.run_mock(); self.calls=[]; self.run_mock(resume=True); self.assertEqual(self.calls,[])
    def test_separate_stages_use_same_fresh_parents(self):
        self.run_mock(('S0',)); self.run_mock(('S1',),True); self.run_mock(('S2',),True); self.run_mock(('S3',),True)
        self.assertEqual([s for s,c in self.calls],list(r.STAGES))
    def test_later_stage_cannot_start_fresh(self):
        with self.assertRaises(ValueError): self.run_mock(('S2',))
        self.assertFalse(self.work.exists())
    def test_missing_parent_stops_before_child(self):
        self.run_mock(('S0',)); self.calls=[]
        with self.assertRaises(ValueError): self.run_mock(('S2',),True)
        self.assertEqual(self.calls,[])
    def test_historical_root_without_integrated_lock_refused(self):
        self.work.mkdir(); (self.work/'historical_outputs.json').write_text('{}')
        with self.assertRaises(ValueError): self.run_mock(resume=True)
        self.assertEqual(self.calls,[])
    def test_existing_root_without_resume_refused(self):
        self.run_mock(('S0',))
        with self.assertRaises(FileExistsError): self.run_mock(('S1',))
    def test_resume_source_environment_or_staging_drift_refused(self):
        self.run_mock(('S0',)); original=self.identity.copy()
        for k,value in [('source_commit','changed'),('environment',{'changed':True}),('staging_root','changed')]:
            self.identity=dict(original,**{k:value})
            with self.assertRaises(ValueError): self.run_mock(resume=True)
    def test_parent_receipt_changed_refused(self):
        self.run_mock(('S0',)); self.calls=[]; (self.work/'S0/synthetic_complete.json').write_text('{"source":"mock-commit","stage":"S0","changed":true}')
        with self.assertRaises(ValueError): self.run_mock(('S1',),True)
        self.assertEqual(self.calls,[])
    def test_protocol_stop_prevents_later_stages(self):
        def stop(s,w,c):
            if s=='S1': raise r.ProtocolStop('scope guard UNRESOLVED')
            return self.fake_boundary(s,w,c)
        with self.assertRaises(r.ProtocolStop): self.run_mock(boundary=stop)
        self.assertEqual([s for s,c in self.calls],['S0','S1'])
    def test_arbitrary_fresh_counts_have_no_execution_target(self):
        def child(cmd):
            rc=self.fake_child(cmd)
            s=self.calls[-1][0]; p=self.work/s/'synthetic_complete.json'
            obj=json.loads(p.read_text()); obj['arbitrary_PASS_count']=1; obj['arbitrary_UNRESOLVED_count']=99; p.write_text(json.dumps(obj)); return rc
        self.run_mock(child=child); self.assertEqual(len(self.calls),4)
    def test_boundaries_preserve_candidate_unresolved_but_stop_protocol_unresolved(self):
        from . import s3_contract as c
        summaries={}
        for step in c.STEPS:
            p=self.base/step; p.mkdir(); obj={'OVERALL_STATUS':'PASS','operator_UNRESOLVED_count':26}
            if step=='K6': obj['claim_contract_evidence']={'status':'CLAIM_COMPATIBLE_CANDIDATE'}
            (p/(step+'_SCIENTIFIC_SUMMARY.json')).write_text(json.dumps(obj)); summaries[step]=p
        self.work.mkdir(); (self.work/'S3/receipts').mkdir(parents=True); (self.work/'S3/receipts/K6.json').write_text('{}')
        ctx={'source_commit':'mock-commit','execution_id':'synthetic','s2':{'root':str(self.work/'S2')}}
        with patch.object(c,'state',return_value=ctx),patch.object(c,'receipt',return_value={'digest':'synthetic'}),patch.object(c,'stage_dir',side_effect=lambda p,s:summaries[s]):
            self.assertEqual(r.boundary('S3',self.work,'mock-commit')['execution_id'],'synthetic')
            p=summaries['K3']/'K3_SCIENTIFIC_SUMMARY.json'; p.write_text(json.dumps({'OVERALL_STATUS':'PASS','stop_reason':'REFERENCE_UNRESOLVED'}))
            with self.assertRaises(r.ProtocolStop): r.boundary('S3',self.work,'mock-commit')
    def test_protected_and_overlapping_paths_refused(self):
        for a,b in ((SOURCE_ROOT/'work',self.staging),(self.work,self.work/'assets'),(self.base/'DEV_FORENSIC/work',self.staging),(Path('relative'),self.staging)):
            with self.assertRaises(ValueError): r.roots(a,b)
    def test_resume_unknown_root_dry_run_refuses_historical_outputs(self):
        self.work.mkdir()
        with patch.object(r,'verify_assets',return_value=v.verify_assets(self.staging,self.assets)):
            with self.assertRaises(ValueError): r.dry_plan(self.work,self.staging,r.STAGES,True,{'synthetic':True})
    def test_resume_lock_symlink_refused_before_read(self):
        self.work.mkdir(); other=self.base/'other.json'; other.write_text('{}'); (self.work/r.LOCK_NAME).symlink_to(other)
        with self.assertRaises(ValueError): self.run_mock(resume=True)
    def test_dry_run_resume_requires_existing_root(self):
        with patch.object(r,'verify_assets',return_value=v.verify_assets(self.staging,self.assets)):
            with self.assertRaises(ValueError): r.dry_plan(self.work,self.staging,r.STAGES,True,{'synthetic':True})
    def test_canonical_environment_rejects_numeric_drift_and_oversubscription(self):
        env={'python':'3.10.19','packages':{'numpy':'2.2.6','scipy':'1.15.2','threadpoolctl':'3.6.0'},
             'threads':dict.fromkeys(r.THREADS,'1'),'threadpools':[{'internal_api':'openblas','version':'0.3.30','num_threads':1}]}
        r.require_canonical(env)
        env['threadpools'][0]['num_threads']=16
        with self.assertRaises(ValueError): r.require_canonical(env)
        env['threadpools'][0]['num_threads']=1; env['packages']['numpy']='unvalidated'
        with self.assertRaises(ValueError): r.require_canonical(env)
    def test_interrupt_terminates_only_launched_stage_group(self):
        child=MagicMock(); child.pid=987654; child.wait.side_effect=[KeyboardInterrupt(),0]
        with patch.object(r.subprocess,'Popen',return_value=child),patch.object(r.os,'killpg') as terminate:
            with self.assertRaises(KeyboardInterrupt): r.launch(['synthetic-child'])
        terminate.assert_called_once_with(child.pid,r.signal.SIGTERM)
    def test_final_claim_unresolved_stops_full_chain(self):
        def stop(stage,work,source):
            if stage=='S3': raise r.ProtocolStop('UNRESOLVED external claim evidence')
            return self.fake_boundary(stage,work,source)
        with self.assertRaises(r.ProtocolStop): self.run_mock(boundary=stop)
        self.assertNotIn('S3',r.load(self.work/r.LOCK_NAME)['completed'])
    def test_frozen_scientific_tree_and_launchers_byte_identical(self):
        from .release_integrity import verify
        self.assertEqual(verify()["status"], "PASS")
        self.assertEqual(verify()["scientific_files_changed"], 0)

if __name__=='__main__': unittest.main()
