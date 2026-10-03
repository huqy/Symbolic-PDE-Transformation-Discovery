"""Fault-injection tests for public authentication, staging and non-authoritative evidence."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from .common import SOURCE_ROOT
from . import release_integrity as r
from . import stage_public_assets as s
from . import public_scan

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
    def export_fixture(self):
        root=self.base/'source'
        shutil.copytree(SOURCE_ROOT,root,ignore=shutil.ignore_patterns('.git','__pycache__','*.pyc'))
        return root
    def synthetic_assets(self):
        src=self.base/'transport';src.mkdir();obj={'assets':[]}
        for n in range(5):
            b=('opaque transport %d'%n).encode();name='synthetic_%d.tar.xz'%n
            (src/name).write_bytes(b);obj['assets'].append({'id':str(n),'filename':name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
        return src,obj
    def test_local_manifest_verifies_without_git(self):
        root=self.export_fixture()
        with patch('subprocess.check_output',side_effect=AssertionError('Git forbidden')):
            self.assertEqual(r.verify(root)['status'],'PASS')
    def test_scientific_byte_drift_refused(self):
        root=self.export_fixture();p=next((root/'phases').rglob('ast_runtime.py'));p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaises(ValueError):r.verify(root)
    def test_missing_source_and_missing_manifest_entry_refused(self):
        root=self.export_fixture();p=root/r.MANIFEST;obj=json.loads(p.read_text());obj['files']=[x for x in obj['files'] if not x['path'].endswith('ast_runtime.py')];p.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):r.verify(root)
    def test_changed_scientific_commitment_refused(self):
        root=self.export_fixture();p=root/r.MANIFEST;obj=json.loads(p.read_text());next(x for x in obj['files'] if x['role']=='scientific_frozen')['sha256']='0'*64;p.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):r.verify(root)
    def test_source_symlink_refused(self):
        root=self.export_fixture();p=root/'reproduce/common.py';p.unlink();p.symlink_to(SOURCE_ROOT/'reproduce/common.py')
        with self.assertRaises(ValueError):r.verify(root)
    def test_reference_results_are_not_runtime_integrity_or_planning_inputs(self):
        from . import public_runner as runner
        from . import s0_launcher,s1_launcher,s2_launcher,s3_launcher
        def denied(event,args):
            if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)) and 'reference_results' in os.fsdecode(args[0]):
                raise AssertionError('reference result read forbidden')
        # Subprocess lifetime bounds the audit hook, including imports and plan generation.
        program='''import sys,os
from pathlib import Path
def denied(event,args):
 if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)) and 'reference_results' in os.fsdecode(args[0]): raise AssertionError('reference result read')
sys.addaudithook(denied)
from reproduce import release_integrity as r,public_runner as p,s0_launcher,s1_launcher,s2_launcher,s3_launcher
r.verify(); s0_launcher.plan()
assert all(m.check_source is r.check_source for m in (s0_launcher,s1_launcher,s2_launcher,s3_launcher))
report=p.dry_plan(Path(sys.argv[1]),Path(sys.argv[2]),p.STAGES,False,{'synthetic':True})
assert not report['scientific_stage_started']
'''
        result=subprocess.run([sys.executable,'-B','-c',program,str(self.base/'work'),str(self.base/'missing-assets')],cwd=SOURCE_ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        root=self.export_fixture();shutil.rmtree(root/'reference_results');self.assertEqual(r.verify(root)['status'],'PASS')
    def test_stage_launchers_share_public_authentication(self):
        from . import s0_launcher,s1_launcher,s2_launcher,s3_launcher
        for module in (s0_launcher,s1_launcher,s2_launcher,s3_launcher):self.assertIs(module.check_source,r.check_source)
    def test_obsolete_module_entry_fails_before_private_git(self):
        from .verify_baseline import verify
        code=compile('def check_source():\n return verify()\n','s0_launcher.py','exec');ns={'verify':verify};exec(code,ns)
        with patch('subprocess.run',side_effect=AssertionError('private Git forbidden')):
            with self.assertRaises(RuntimeError):ns['check_source']()
    def test_staging_is_idempotent_and_never_parses_payloads(self):
        src,obj=self.synthetic_assets();dst=self.base/'staged'
        with patch('tarfile.open',side_effect=AssertionError('archive parsing forbidden')):
            self.assertEqual(s.stage(dst,src,obj)['status'],'PASS');self.assertEqual(s.stage(dst,src,obj)['status'],'PASS')
    def test_corrupt_source_refused_before_creating_destination(self):
        src,obj=self.synthetic_assets();(src/obj['assets'][4]['filename']).write_bytes(b'bad');dst=self.base/'staged'
        with self.assertRaises(ValueError):s.stage(dst,src,obj)
        self.assertFalse(dst.exists())
    def test_mismatched_destination_refused_without_partial_copy(self):
        src,obj=self.synthetic_assets();dst=self.base/'staged';dst.mkdir();(dst/obj['assets'][4]['filename']).write_bytes(b'bad')
        with self.assertRaises(ValueError):s.stage(dst,src,obj)
        self.assertEqual(len(list(dst.iterdir())),1)
    def test_symlink_relative_escape_and_source_overlap_refused(self):
        src,obj=self.synthetic_assets();link=self.base/'alias';link.symlink_to(src,target_is_directory=True)
        for dst in (src,src/'child',link/'child',Path('relative')):
            with self.assertRaises(ValueError):s.stage(dst,src,obj)
        obj['assets'][0]['filename']='../escape'
        with self.assertRaises(ValueError):s.stage(self.base/'dst',src,obj)
    def test_work_overlap_refused(self):
        src,obj=self.synthetic_assets();work=self.base/'work';work.mkdir();(work/'public_execution.json').write_text('{}')
        with self.assertRaises(ValueError):s.stage(work/'inputs',src,obj)
    def test_cjk_and_secret_dependency_scan(self):
        self.assertEqual(public_scan.scan()['status'],'PASS')
    def test_cjk_scanner_covers_supplementary_plane(self):
        self.assertTrue(public_scan.is_cjk(chr(0x20000)));self.assertTrue(public_scan.is_cjk(chr(0x4e00)))
        self.assertFalse(public_scan.is_cjk('A'))

if __name__=='__main__':unittest.main()
