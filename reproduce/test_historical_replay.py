"""Authenticated replay boundary and coordinator tests without science or archives."""
import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from . import public_runner as r, historical_replay as h, s2_contract as s2, s1_lineage as s1
from . import replay_boundary_fixture as f
from .s0_contract import load, write
from .test_portable_resume import canonical_fixture

class ReplayTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name);self.work=self.base/'work';self.assets=self.base/'assets';self.assets.mkdir()
    def fixture(self,**kwargs): f.prepare(self.work,**kwargs)
    def check(self,opt_in=None):
        auth=None if opt_in is None else h.authorization(f.SOURCE,opt_in)
        with patch('tarfile.open',side_effect=AssertionError('payload forbidden')),patch.object(r,'launch',side_effect=AssertionError('science forbidden')):
            return r.boundary('S2',self.work,f.SOURCE,auth)
    def mutate(self,step,name,key,value):
        p=s2.stage_dir(self.work/'S2/project',step)/name;o=load(p);o[key]=value;write(p,o);f.reseal_s2(self.work)
    def test_real_boundary_absent_status_and_diagnostic_unresolved(self):
        self.fixture();pin=self.check(True);ready=pin['readiness']
        self.assertEqual(ready['formal_count'],2);self.assertEqual(ready['diagnostic_UNRESOLVED_count'],1)
        self.assertFalse(ready['diagnostic_membership_authority']);self.assertEqual(ready['R_C'],'NOT_AUTOMATICALLY_ASSIGNED')
        summary=load(s2.stage_dir(self.work/'S2/project','K7')/'K7_SCIENTIFIC_SUMMARY.json')
        self.assertNotIn('status',summary['claim_contract_evidence']);self.assertFalse((self.work/'S3').exists())
    def test_real_no_opt_in_deliberate_authorization_stop(self):
        self.fixture()
        with self.assertRaisesRegex(h.AuthorizationStop,'HISTORICAL_REPLAY_OPT_IN_REQUIRED'):self.check(False)
        self.assertTrue((self.work/'S2/receipts/K7.json').is_file());self.assertFalse((self.work/'S3').exists())
    def test_arbitrary_positive_cohort_and_many_unresolved_are_not_targets(self):
        self.fixture(formal_count=1,unresolved_count=7,fail_count=3)
        self.assertEqual(self.check(True)['readiness']['formal_count'],1)
    def test_zero_operator_pass_blocks(self):
        self.fixture(formal_count=0)
        with self.assertRaises((ValueError,r.ProtocolStop)):self.check(True)
    def test_missing_receipt_blocks(self):
        self.fixture();(self.work/'S2/receipts/K3.json').unlink()
        with self.assertRaises((ValueError,OSError)):self.check(True)
    def test_source_snapshot_drift_blocks(self):
        self.fixture();(self.work/'S1/project/README.md').write_text('drift')
        with self.assertRaisesRegex(ValueError,'snapshot drift'):self.check(True)
    def test_missing_S0_S1_receipts_and_source_commit_mismatch_block(self):
        for stage,step in (('S0','K1'),('S1','PF1')):
            with self.subTest(stage=stage):
                self.work=self.base/stage;f.prepare(self.work);(self.work/stage/'receipts'/(step+'.json')).unlink()
                with self.assertRaises((ValueError,OSError)):self.check(True)
        self.work=self.base/'source';f.prepare(self.work)
        with self.assertRaisesRegex(ValueError,'source commit'):r.boundary('S2',self.work,'other-source',h.authorization('other-source',True))
    def test_asset_lock_drift_blocks(self):
        self.fixture();p=self.work/'S0/execution_lock.json';o=load(p);o['input_transport_verification']['archives'][0]['sha256']='drift';write(p,o)
        with self.assertRaisesRegex(ValueError,'transport lock drift'):self.check(True)
    def test_global_control_and_reference_unresolved_and_fail_block(self):
        cases=[('K4_SCIENTIFIC_SUMMARY.json','identity_decision',s2.RESP[0]),('K4_SCIENTIFIC_SUMMARY.json','frozen_null_decision',s2.RESP[1]),('K4_REFERENCE_CERTIFICATION.json','status','REFERENCE_UNRESOLVED'),('K4_REFERENCE_CERTIFICATION.json','candidate_independent',False),('K4_SCIENTIFIC_SUMMARY.json','reference_unresolved_count',1)]
        for i,(name,key,val) in enumerate(cases):
            with self.subTest(name=name,key=key):
                work=self.base/('case'+str(i));f.prepare(work);self.work=work;self.mutate('K4',name,key,val)
                with self.assertRaises((ValueError,r.ProtocolStop)):self.check(True)
                self.assertFalse((work/'S3').exists())
    def test_incomplete_campaign_rescue_filter_and_global_stop_block(self):
        cases=[('K5','complete_response_cohort',1),('K5','candidate_specific_rescue',True),('K5','top_k_or_proxy_filter',True),('K5','OVERALL_STATUS','UNRESOLVED'),('K7','stop_reason','CERTIFICATION_UNRESOLVED')]
        for i,(step,key,val) in enumerate(cases):
            with self.subTest(step=step,key=key):
                self.work=self.base/('case'+str(i));f.prepare(self.work);self.mutate(step,step+'_SCIENTIFIC_SUMMARY.json',key,val)
                with self.assertRaises((ValueError,r.ProtocolStop)):self.check(True)
                self.assertFalse((self.work/'S3').exists())
    def test_control_result_unresolved_evidence_and_zero_refit_drift_block(self):
        cases=[('K4','K4_CONTROL_FIRST_RESULTS.json','controls',{'identity':{'decision':s2.RESP[1]},'frozen_null':{'decision':s2.RESP[2]}}),('K7','K7_SCIENTIFIC_SUMMARY.json','claim_contract_evidence',{'complete_frozen_protocol_campaign':False})]
        for i,(step,name,key,value) in enumerate(cases):
            with self.subTest(key=key):
                self.work=self.base/('case'+str(i));f.prepare(self.work);self.mutate(step,name,key,value)
                with self.assertRaises((ValueError,r.ProtocolStop)):self.check(True)
        self.work=self.base/'zero-refit';f.prepare(self.work)
        path=s2.stage_dir(self.work/'S2/project','K2')/'K2_III_B_DECISION_MAP.jsonl';rows=s2.rows(path);rows[0]['same_AST_theta_gauge_zero_refit']=False;s2.write_rows(path,rows);f.reseal_s2(self.work)
        with self.assertRaisesRegex(ValueError,'zero-refit'):self.check(True)
    def test_opt_in_cli_rejects_dry_run_and_fresh_non_S0(self):
        with patch('sys.argv',['runner','--dry-run','--authorize-historical-sealed-replay','--work-root',str(self.work),'--staging-root',str(self.assets)]),contextlib.redirect_stderr(io.StringIO()),patch.object(r,'runtime',side_effect=AssertionError('no runtime needed')):
            self.assertEqual(r.main(),3)
        with self.assertRaisesRegex(ValueError,'start S0'):r.orchestrate(self.work,self.assets,('S2',),False,self.identity())
        self.assertFalse(self.work.exists())
    def test_zero_response_pass_and_incompatible_fourth_stratum_block(self):
        self.fixture();p=s2.stage_dir(self.work/'S2/project','K5')/'K5_RESPONSE_DECISION_MAP.jsonl'
        rows=s2.rows(p)
        for row in rows:row['decision']=s2.RESP[2]
        s2.write_rows(p,rows)
        membership=s2.stage_dir(self.work/'S2/project','K5')/'K5_RESPONSE_PASS_MEMBERSHIP.jsonl';s2.write_rows(membership,[])
        counts=dict(zip(s2.RESP,(0,0,len(rows))))
        for step in ('K5','K7'):
            sp=s2.stage_dir(self.work/'S2/project',step)/(step+'_SCIENTIFIC_SUMMARY.json');o=load(sp);o['decision_counts' if step=='K5' else 'response_counts']=counts;write(sp,o)
        from .common import file_sha256
        lp=s2.stage_dir(self.work/'S2/project','K7')/'K7_S3_DECISION_LOCK.json';o=load(lp);o.update(eligible_count_if_authorized=0,eligible_membership_sha256=file_sha256(membership));write(lp,o);f.reseal_s2(self.work)
        with self.assertRaisesRegex(ValueError,'scientific role unresolved'):self.check(True)
    def test_membership_or_mixed_parent_blocks(self):
        self.fixture();p=self.work/'S2/s2_execution.json';o=load(p);o.pop('digest');o['s1']['execution_id']='other';write(p,s1.seal(o))
        with self.assertRaisesRegex(ValueError,'parent pins changed'):self.check(True)
    def test_semantic_marker_drift_blocks_even_receipt_resealed(self):
        self.fixture();p=s2.stage_dir(self.work/'S2/project','K7')/'REPRODUCTION_STAGE_COMPLETE.json';o=load(p);o['execution_id']='other';write(p,o)
        recpath=self.work/'S2/receipts/K7.json';rec=load(recpath);rec.pop('digest');rec['outputs']=[s2.record(row['path']) for row in rec['outputs']];write(recpath,s1.seal(rec))
        with self.assertRaisesRegex(ValueError,'completion marker'):self.check(True)
    def identity(self,opt=True,env=None):return r.execution_identity(self.work,self.assets,env or canonical_fixture(),{'status':'PASS','fixture':True},f.SOURCE,opt)
    def run_chain(self,opt=True,resume=False,env=None,interrupt=False):
        identity=self.identity(opt,env);calls=[]
        def child(cmd):
            stage=next(s for s in r.STAGES if 'run_'+s.lower()+'.sh' in cmd[1]);calls.append(stage)
            self.assertEqual(load(self.work/r.LOCK_NAME)['identity']['historical_replay_authorization'],identity['historical_replay_authorization'])
            if stage=='S0':f.prepare(self.work)
            if stage=='S2' and interrupt:return 124
            if stage=='S3':f.prepare_s3(self.work)
            return 0
        with contextlib.redirect_stdout(io.StringIO()),patch('tarfile.open',side_effect=AssertionError('no archives')):
            result=r.orchestrate(self.work,self.assets,r.STAGES,resume,identity,child,r.boundary,runtime_observation=env or canonical_fixture())
        return result,calls
    def test_complete_authenticated_mock_S0_to_S3_chain(self):
        result,calls=self.run_chain();self.assertEqual(calls,list(r.STAGES));self.assertEqual(result['R_C'],'NOT_AUTOMATICALLY_ASSIGNED')
        state=load(self.work/r.LOCK_NAME);self.assertEqual(len(state['completed']),4)
        self.assertTrue(any(e.get('decision')=='HISTORICAL_SEALED_REPLAY_ENTRY_AUTHORIZED' for e in state['events']))
    def test_no_opt_in_full_coordinator_preserves_S2_without_S3(self):
        with self.assertRaisesRegex(h.AuthorizationStop,'OPT_IN_REQUIRED'):self.run_chain(False)
        state=load(self.work/r.LOCK_NAME);self.assertEqual(set(state['completed']),{'S0','S1','S2'});self.assertFalse((self.work/'S3').exists());self.assertEqual(state['events'][-1]['status'],'AUTHORIZATION_STOP')
    def test_scheduler_resume_across_CPU_architecture_same_consent(self):
        with self.assertRaises(r.ProtocolStop):self.run_chain(interrupt=True)
        other=canonical_fixture();other['threadpools'][0]['architecture']='SkylakeX'
        result,calls=self.run_chain(resume=True,env=other);self.assertEqual(calls,['S2','S3']);self.assertEqual(result['R_C'],'NOT_AUTOMATICALLY_ASSIGNED')
    def test_opt_in_cannot_be_added_removed_or_digest_changed_on_resume(self):
        with self.assertRaises(r.ProtocolStop):self.run_chain(interrupt=True)
        for key,value in [('opt_in',False),('policy_digest','drift'),('public_tag','other')]:
            identity=self.identity();identity['historical_replay_authorization'][key]=value
            with self.assertRaises(ValueError):r.orchestrate(self.work,self.assets,('S2',),True,identity,lambda cmd:self.fail('child invoked'))
        # Also reject adding consent to a root preregistered without consent.
        state=load(self.work/r.LOCK_NAME);state['identity']=self.identity(False);write(self.work/r.LOCK_NAME,state)
        with self.assertRaises(ValueError):r.orchestrate(self.work,self.assets,('S2',),True,self.identity(True),lambda cmd:self.fail('child invoked'))
    def test_legacy_V2_diagnostic_root_never_migrated(self):
        self.work.mkdir();write(self.work/r.LOCK_NAME,{'schema':'P13_PUBLIC_EXECUTION_V2','identity':self.identity()})
        with self.assertRaisesRegex(ValueError,'legacy execution diagnostic'):r.orchestrate(self.work,self.assets,('S0',),True,self.identity())

if __name__=='__main__':unittest.main()
