"""Cross-node portability and fail-closed numerical/source/input/receipt identity."""
import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from . import public_runner as r
from .common import SOURCE_ROOT, file_sha256
from . import test_public_release as release_tests

def canonical_fixture():
    lock=r.load(SOURCE_ROOT/'environment/public-release-lock.json');keys=('name','version','build','build_number','subdir','md5','sha256')
    return {'python':lock['python'],'packages':{p['name']:p['version'] for p in lock['packages'] if p['name'] in ('numpy','scipy','threadpoolctl')},'threads':dict.fromkeys(r.THREADS,'1'),'threadpools':[{'user_api':'blas','internal_api':'openblas','prefix':'libopenblas','version':'0.3.30','threading_layer':'pthreads','num_threads':1,'architecture':'Cooperlake','filepath':'/synthetic/env/lib/openblas.so'}],'system':'Linux','machine':'x86_64','builds':[{k:p[k] for k in keys} for p in lock['packages']],'canonical_lock_sha256':file_sha256(SOURCE_ROOT/'environment/public-release-lock.json'),'platform':'synthetic-kernel','executable':'/synthetic/env/bin/python'}

class PortableResumeTests(unittest.TestCase):
    fake_child=release_tests.ReleaseTests.fake_child
    fake_boundary=release_tests.ReleaseTests.fake_boundary
    run_mock=release_tests.ReleaseTests.run_mock
    def setUp(self):
        release_tests.ReleaseTests.setUp(self);self.env=canonical_fixture();self.assets={'status':'PASS','synthetic':True}
        self.identity=r.execution_identity(self.work,self.staging,self.env,self.assets,'mock-commit')
    def invoke(self,env,resume):
        r.require_canonical(env);identity=r.execution_identity(self.work,self.staging,env,self.assets,'mock-commit')
        with contextlib.redirect_stdout(io.StringIO()):return r.orchestrate(self.work,self.staging,('S0',),resume,identity,self.fake_child,self.fake_boundary,runtime_observation=env)
    def test_cross_node_architecture_accepted_full_observations_preserved(self):
        self.invoke(self.env,False);other=copy.deepcopy(self.env);other['threadpools'][0]['architecture']='SkylakeX';other['platform']='different-kernel';other['executable']='/different/env/python';other['threadpools'][0]['filepath']='/different/lib/openblas.so'
        self.invoke(other,True);events=r.load(self.work/r.LOCK_NAME)['events'];self.assertEqual([e['runtime_observation']['threadpools'][0]['architecture'] for e in events],['Cooperlake','SkylakeX']);self.assertEqual(len(self.calls),1)
        with patch.object(r,'source_identity',return_value='mock-commit'),patch.object(r,'verify_assets',return_value=self.assets):self.assertEqual(r.dry_plan(self.work,self.staging,('S0',),True,other)['status'],'PLAN_ONLY')
    def test_python_numpy_scipy_threadpoolctl_build_blas_threads_drift_refused(self):
        self.invoke(self.env,False)
        mutations=[lambda e:e.update(python='0.0'),*[lambda e,k=k:e['packages'].update({k:'0.0'}) for k in ('numpy','scipy','threadpoolctl')],lambda e:e['builds'][0].update(build='drift'),lambda e:e['threadpools'][0].update(version='0.3.31'),lambda e:e['threadpools'][0].update(threading_layer='openmp'),lambda e:e['threadpools'][0].update(num_threads=2),lambda e:e['threads'].update(OPENBLAS_NUM_THREADS='2'),lambda e:e.update(canonical_lock_sha256='changed')]
        for mutate in mutations:
            env=copy.deepcopy(self.env);mutate(env)
            with self.assertRaises(ValueError):self.invoke(env,True)
    def test_source_assets_staging_work_manifest_drift_refused(self):
        self.invoke(self.env,False);original=copy.deepcopy(self.identity)
        for key,value in [('source_commit','drift'),('asset_verification',{'changed':True}),('staging_root','drift'),('work_root','drift'),('asset_manifest_sha256','drift')]:
            self.identity=dict(original,**{key:value})
            with self.assertRaises(ValueError):self.run_mock(('S0',),True)
    def test_receipt_drift_refused_and_legacy_root_not_migrated(self):
        self.invoke(self.env,False);p=self.work/'S0/synthetic_complete.json';p.write_text('{}')
        with self.assertRaises((ValueError,KeyError)):self.invoke(self.env,True)
        lock=r.load(self.work/r.LOCK_NAME);lock['schema']='P13_PUBLIC_EXECUTION_V1';r.write(self.work/r.LOCK_NAME,lock)
        with self.assertRaisesRegex(ValueError,'legacy execution diagnostic'):self.invoke(self.env,True)

if __name__=='__main__':unittest.main()
