"""Run inspected pure mathematical/contract tests without launching science."""
import importlib.util
import inspect
import tempfile
import json
import unittest
import io
import sys
from pathlib import Path
from .common import SOURCE_ROOT

# Explicit module allowlist; no formal search, fitter, PDE campaign or real asset opening.
PURE = {
    'test_p13_s0_k0.py': ['test_k0_forbids_data_response_and_search','test_l3_caps_exact_and_executable_complete','test_derivative_nesting_two_allowed_three_rejected','test_gauge_group_action_canonicalizes_identically','test_upstream_sha_lock_has_core_raw_semantics'],
    'test_p13_s1_k0r.py': ['test_family_objective_is_six_field_rms','test_tau_is_ambiguity_not_relaxed_pass','test_continuation_uses_train_progress_and_four_seeds_only','test_scientific_branch_keeps_theta_and_fit_provenance'],
    'test_p13_s2_k0.py': None,'test_p13_s2_k1.py': None,'test_p13_s2_k2.py': None,
    'test_p13_s2_k3.py': None,'test_p13_s2_k6.py': None,
    'test_p13_s3_k1.py': None,'test_p13_s3_k2.py': None,'test_p13_s3_k5.py': None,'test_p13_s3_k6.py': None,
}

def main():
    for folder in ('phases/p13/coefficient_law_raw_xt/src','phases/p11/raw_xt_td/src'):
        sys.path.insert(0,str(SOURCE_ROOT/folder))
    completed=[]
    for name, selected in PURE.items():
        path=SOURCE_ROOT/'phases/p13/coefficient_law_raw_xt/tests'/name
        spec=importlib.util.spec_from_file_location('pure_'+path.stem,path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        if selected is None:
            suite=unittest.defaultTestLoader.loadTestsFromModule(module)
            result=unittest.TextTestRunner(stream=io.StringIO()).run(suite)
            if not result.wasSuccessful():
                raise AssertionError(str(result.failures)+str(result.errors))
            if result.testsRun: completed.extend([name+'::unittest-%d'%n for n in range(result.testsRun)])
        for fn,value in list(vars(module).items()):
            if not fn.startswith('test_') or not callable(value) or (selected is not None and fn not in selected):continue
            with tempfile.TemporaryDirectory(prefix='td-pure-') as tmp:
                params=inspect.signature(value).parameters
                if not params:value()
                elif list(params)==['tmp_path']:value(Path(tmp))
                else:raise AssertionError('uninspected fixture: '+fn)
            completed.append(name+'::'+fn)
    print(json.dumps({'status':'PASS','pure_tests_pass':len(completed),'tests':completed,'heavy_science':False},indent=2))
if __name__=='__main__': main()
