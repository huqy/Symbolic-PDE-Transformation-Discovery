"""Exercise real handoffs with small authenticated, outcome-independent fixtures."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from .common import SOURCE_ROOT, file_sha256
from . import runtime_dependencies as dep, s2_contract as s2, s3_contract as s3
from . import s2_step, s3_step
from .boundary_fixture import s1_fixture, s2_fixture

class RuntimeClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
    def export(self):
        root=self.base/'source';shutil.copytree(SOURCE_ROOT,root,ignore=shutil.ignore_patterns('.git','__pycache__'));return root
    def test_complete_runtime_closure(self):
        self.assertEqual(dep.audit()['status'],'PASS')
    def test_every_runtime_dependency_missing_fails(self):
        root=self.export()
        for row in dep.audit()['dependencies']:
            if row['required_path'] in (dep.CATALOG,'reproduce/RELEASE_SOURCE_MANIFEST.json'):continue
            p=root/row['required_path'];data=p.read_bytes();p.unlink()
            try:self.assertEqual(dep.audit(root)['status'],'FAIL',str(p))
            finally:p.write_bytes(data)
    def test_every_dependency_manifest_omission_fails(self):
        root=self.export();p=root/'reproduce/RELEASE_SOURCE_MANIFEST.json';original=json.loads(p.read_text())
        for row in dep.audit()['dependencies']:
            rel=row['required_path']
            if rel=='reproduce/RELEASE_SOURCE_MANIFEST.json':continue
            obj=dict(original,files=[r for r in original['files'] if r['path']!=rel]);p.write_text(json.dumps(obj))
            self.assertEqual(dep.audit(root)['status'],'FAIL',rel)
        p.write_text(json.dumps(original))
    def test_real_s1_s2_handoff_and_shell_plan_without_archive_opening(self):
        for count in (1,3):
            base=self.base/str(count);root=s1_fixture(base,count)
            before={str(p):file_sha256(p) for p in base.rglob('*') if p.is_file()}
            with patch('tarfile.open',side_effect=AssertionError('archives forbidden')):
                header,active,clear,lock=s2.handoff(root)
                self.assertEqual(header['clear_count'],count)
            result=subprocess.run(['bash',str(SOURCE_ROOT/'reproduce/run_s2.sh'),'--dry-run','--s1-execution',str(root),'--workdir',str(base/'future-S2'),'--staging-root',str(base/'assets')],cwd=SOURCE_ROOT,capture_output=True,text=True,env=dict(__import__('os').environ,P13_PYTHON=sys.executable))
            self.assertEqual(result.returncode,0,result.stderr);plan=json.loads(result.stdout)
            self.assertFalse(plan['DEVELOPMENT_OPENED']);self.assertFalse(plan['scientific_stage_started']);self.assertFalse((base/'future-S2').exists())
            self.assertEqual(before,{str(p):file_sha256(p) for p in base.rglob('*') if p.is_file()})
            project=base/'projection';(project/'reproduce').mkdir(parents=True)
            shutil.copyfile(SOURCE_ROOT/'reproduce/s2_public_commitments.json',project/'reproduce/s2_public_commitments.json')
            with patch.object(s2_step,'active',return_value=active),patch.object(s2_step,'state',return_value={'staging_root':str(base/'assets'),'s1':{'PF1_receipt_digest':header['PF1_receipt_digest']}}),patch('tarfile.open',side_effect=AssertionError('archives forbidden')):
                projected=s2_step.projection(project);self.assertEqual(projected['DEVELOPMENT_response_commitment']['row_count'],32)
    def test_real_s2_s3_handoff_shell_plan_and_sidecar_projection(self):
        from .s0_contract import write
        for count in (1,3):
            base=self.base/str(count);root=s2_fixture(base,count)
            with patch('tarfile.open',side_effect=AssertionError('SEALED forbidden')):
                header,groups=s3.handoff(root);self.assertEqual(header['formal_count'],count)
            result=subprocess.run(['bash',str(SOURCE_ROOT/'reproduce/run_s3.sh'),'--dry-run','--s2-execution',str(root),'--workdir',str(base/'future-S3'),'--staging-root',str(base/'assets')],cwd=SOURCE_ROOT,capture_output=True,text=True,env=dict(__import__('os').environ,P13_PYTHON=sys.executable))
            self.assertEqual(result.returncode,0,result.stderr);self.assertFalse(json.loads(result.stdout)['scientific_stage_started']);self.assertFalse((base/'future-S3').exists())
            project=base/'projection';(project/'reproduce').mkdir(parents=True);src=SOURCE_ROOT/'reproduce/s3_public_commitments.json';shutil.copyfile(src,project/'reproduce'/src.name)
            metadata=json.loads(src.read_text())['commitments']; active={}
            for key,label,ident in [('coefficient:SEALED_FINAL_COEF','SEALED_coefficient_guard','sealed_coefficient'),('response:SEALED_FINAL_COEF','SEALED_response_guard','sealed_response')]:active[label]=dict(metadata[key],input_id=ident)
            write(base/'parent'/s3_step.S1_RUN/'PF1_final_freeze/PF1_S2_ACTIVE_INPUT_MANIFEST.json',active)
            with patch.object(s3_step,'s1',return_value=base/'parent'),patch.object(s3_step,'state',return_value={'staging_root':str(base/'assets')}),patch('tarfile.open',side_effect=AssertionError('SEALED forbidden')):
                projected=s3_step.projection(project);self.assertTrue(projected['SEALED_response_guard']['public_row_commitments'])
    def test_sidecars_match_public_input_commitment_headers(self):
        inputs=json.loads((SOURCE_ROOT/'inputs/P13_REPRO_INPUT_MANIFEST.json').read_text())['historical_random_realizations']
        for stage in ('s2','s3'):
            obj=json.loads((SOURCE_ROOT/'reproduce'/(stage+'_public_commitments.json')).read_text())
            for c in obj['commitments'].values():
                row=next(r for r in inputs if r.get('commitment_kind')==c['kind'] and r.get('commitment_role')==c['role'])
                for side,manifest in [('archive_bytes','bytes'),('archive_sha256','sha256'),('kind','commitment_kind'),('role','commitment_role'),('row_count','row_count'),('payload_semantic_digest','payload_semantic_digest')]:self.assertEqual(c[side],row[manifest])
                self.assertNotIn('archive_absolute_path',c)

if __name__=='__main__':unittest.main()
