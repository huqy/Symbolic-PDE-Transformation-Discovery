"""Synthetic child/worker role probe; test paths contain only dummy bytes."""
import json
import os
from pathlib import Path

def probe(task):
    path,action=task
    try:
        if action=='read': Path(path).read_bytes()
        else: Path(path).write_text('synthetic')
        return 'ALLOWED'
    except PermissionError: return 'DENIED'

def main():
    if os.environ.get('P13_S2_NATIVE_SYNTHETIC'):
        native_synthetic(os.environ['P13_S2_NATIVE_SYNTHETIC']); return
    task=json.loads(os.environ['P13_S2_SYNTHETIC_PROBE'])
    if os.environ.get('P13_S2_SYNTHETIC_SPAWN'):
        from multiprocessing import get_context
        with get_context('spawn').Pool(1) as p: result=p.map(probe,[task])[0]
    else: result=probe(task)
    print(result)

def native_synthetic(stage):
    """Frozen K4/K5 orchestration with dummy numerical inputs under real guard."""
    from contextlib import ExitStack
    from unittest.mock import patch
    from .test_s2_wiring import Fixture
    from . import s2_step as step
    from .s2_contract import rows
    f=Fixture.__new__(Fixture); f.project=Path(os.environ['P13_S2_PROJECT']); f.events=[]
    f.cohort=rows(step.s1(f.project)/step.active(f.project)['clear_membership']['path'])
    if stage=='K4':
        with ExitStack() as stack:
            for p in f.response_patches(): stack.enter_context(p)
            step.run(f.project,'K4')
    elif stage=='K5':
        m=step.module('K5'); candidates=[f.candidates()[r['membership_index']] for r in step.eligible(f.project)]
        def grid(root,k5,cohort,g,*args):
            out={}
            for r in cohort:
                error=.03 if r['scientific_branch_id']=='b0' else .25
                fields=[{'field_id':f'P13_DEVELOPMENT_COEF_{i:02d}',
                    'records':[{'field_id':f'P13_DEVELOPMENT_COEF_{i:02d}','case_type':case,
                    'relative_energy_error':error} for case in step.config(f.project,'K4')['case_types']]} for i in range(1,5)]
                out[r['scientific_branch_id']]=dict(r,grid=g,fields=fields)
            return out
        with patch.object(step,'candidate_list',return_value=candidates), patch.object(step,'candidate_grid',side_effect=grid), \
             patch.object(m,'load_coefficient_generators',return_value=({}, {'status':'PASS'})):
            step.run(f.project,'K5')
    else: raise ValueError('synthetic native stage must be K4/K5')

if __name__=='__main__':main()
