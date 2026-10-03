"""Real interpreter/ProcessPool dispatch regressions using only an inert fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from .common import SOURCE_ROOT
from .s0_contract import write
from .s0_step import dispatch_module

class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['P13_PREFLIGHT_TMPDIR'])
        self.base=Path(self.tmp.name);self.project=self.base/'execution/project'
        self.project.mkdir(parents=True)
        shutil.copytree(SOURCE_ROOT/'reproduce',self.project/'reproduce',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        write(self.project/'.s0_context.json',{'schema':'P13_S0_CONTEXT_V1','execution_id':'toy-execution'})
        write(self.project.parent/'execution_lock.json',{'schema':'P13_S0_EXECUTION_LOCK_V1','execution_id':'toy-execution'})
        self.blocked=self.base/'external_private_fixture';self.blocked.write_text('not a scientific payload')
        self.env=dict(os.environ,PYTHONPATH=str(SOURCE_ROOT),PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',NSLOTS='17',R5E_ENV_SENTINEL='preserve me')
        for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:self.env[k]='1'

    def tearDown(self):self.tmp.cleanup()

    def launch(self,exit_code):
        argv=['--blocked',str(self.blocked),'--exit-code',str(exit_code),'--literal','space value','\u03b1','$literal;not-shell']
        program='from reproduce.s0_step import dispatch_module; import sys; dispatch_module(sys.argv[1], "reproduce.dispatch_fixture", sys.argv[2:])'
        result=subprocess.run([sys.executable,'-B','-c',program,str(self.project),*argv],cwd=self.base,env=self.env,capture_output=True,text=True,timeout=30)
        return result,argv

    def test_real_dispatch_custom_worker_initializer_argv_environment_and_guard(self):
        before=(self.project/'reproduce/s0_step.py').read_bytes()
        result,argv=self.launch(0)
        self.assertEqual(result.returncode,0,result.stderr)
        row=json.loads(result.stdout)
        self.assertTrue(row['registered_main']);self.assertEqual(row['worker_module'],'__main__')
        self.assertEqual(row['argv'],argv);self.assertEqual(row['cwd'],str(self.project))
        self.assertEqual(row['environment']['NSLOTS'],'17')
        self.assertEqual(row['environment']['R5E_ENV_SENTINEL'],'preserve me')
        self.assertEqual(row['environment']['P13_S0_PROJECT_ROOT'],str(self.project))
        for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:self.assertEqual(row['environment'][key],'1')
        self.assertEqual([r['value'] for r in row['results']],[4,9])
        for r in row['results']:
            self.assertEqual(r['initializer'],'fixture-init');self.assertTrue(r['guard_denied']);self.assertTrue(r['registered'])
        self.assertTrue(row['immutable_write_denied']);self.assertFalse(row['scientific_module_imported'])
        self.assertEqual(result.stderr.strip(),'R5E_FIXTURE_STDERR')
        self.assertEqual((self.project/'reproduce/s0_step.py').read_bytes(),before)

    def test_exact_nonzero_exit_code_propagation(self):
        result,_=self.launch(37)
        self.assertEqual(result.returncode,37,result.stderr)
        self.assertTrue(json.loads(result.stdout)['registered_main'])

    def test_old_runpy_semantics_fail_for_the_same_custom_worker(self):
        program='import runpy,sys; sys.argv=["fixture","--pickle-only"]; runpy.run_module("reproduce.dispatch_fixture",run_name="__main__")'
        result=subprocess.run([sys.executable,'-B','-c',program],cwd=self.base,env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,23,result.stderr)
        row=json.loads(result.stdout);self.assertEqual(row['old_failure'],'PicklingError')
        self.assertIn('attribute lookup toy_worker on __main__ failed',row['message'])

    def test_bootstrap_context_failure_exits_before_fixture(self):
        write(self.project/'.s0_context.json',{'schema':'P13_S0_CONTEXT_V1','execution_id':'wrong'})
        env=dict(self.env,PYTHONPATH=os.pathsep.join([str(self.project/'reproduce/s0_bootstrap'),str(self.project)]),P13_S0_PROJECT_ROOT=str(self.project))
        result=subprocess.run([sys.executable,'-B','-m','reproduce.dispatch_fixture','--pickle-only'],cwd=self.project,env=env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,78,result.stderr);self.assertEqual(result.stdout,'')
        self.assertIn('S0 child I/O bootstrap failed',result.stderr)

    def test_missing_bootstrap_is_refused_before_exec(self):
        (self.project/'reproduce/s0_bootstrap/sitecustomize.py').unlink()
        with self.assertRaisesRegex(RuntimeError,'missing S0 child I/O bootstrap'):
            dispatch_module(self.project,'reproduce.dispatch_fixture',[])

if __name__=='__main__':unittest.main()
