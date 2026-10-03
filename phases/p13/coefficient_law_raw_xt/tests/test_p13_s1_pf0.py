from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from p13rawxt.ast_runtime import FieldJetInterpolator
from p13rawxt.coefficients import make_search_object
from p13rawxt.family_evaluator import FieldView
from p13rawxt.s1_pf0_postfreeze_diagnostics import _combine_raw, _gram_geometry


def _gen(fid: str, phase: float):
    return {
        'schema':'P13_COEFFICIENT_GENERATOR_V1','field_id':fid,'role':'TRAIN_OPERATOR','family':'oblique_plane_wave_3mode','seed':1,'epsilon':0.2,'primitive_terms':[],
        'expanded_plane_wave_components':[
            {'amplitude':0.4,'kx':0.25,'kt':0.30,'phase':phase},
            {'amplitude':-0.3,'kx':-0.20,'kt':0.35,'phase':phase+0.7},
            {'amplitude':0.2,'kx':0.40,'kt':-0.15,'phase':phase+1.1},
        ]
    }


def _views():
    out=[]
    for i in range(6):
        g=_gen(f'f{i}',0.37*i); arr=make_search_object(g,65,4)
        out.append(FieldView(g['field_id'],'TRAIN_OPERATOR',65,arr,FieldJetInterpolator(arr,4)))
    return out


def test_radial_angular_decomposition_is_branchwise_exact():
    s=0.73; c=0.974
    r=math.sqrt(1+s*s-2*s*c)
    assert abs(r*r - ((1-c*c)+(s-c)*(s-c))) < 1e-14


def test_combine_raw_scales_about_candidate_neutral_map_not_identity():
    raw0={'X':np.array([2.0,3.0]),'T':np.array([-1.0,4.0])}
    raw1={'X':np.array([4.0,7.0]),'T':np.array([3.0,10.0])}
    got=_combine_raw(raw0,raw1,0.5)
    assert np.allclose(got['X'],[3.0,5.0])
    assert np.allclose(got['T'],[1.0,7.0])
    one=_combine_raw(raw0,raw1,1.0)
    assert np.array_equal(one['X'],raw1['X'])


def test_gram_geometry_uses_actual_branchwise_s_not_median_root_guess():
    views=_views()
    s=1.31; c=0.97; r=math.sqrt(1+s*s-2*s*c)
    bid='b1'
    k2={bid:{'functional_alignment':{
        'status':'RESOLVED','candidate_tangent_norm':s,'theory_tangent_norm':1.0,
        'theory_cosine':c,'theory_tangent_relative_residual':r,
        'projection_relative_residual':1e-5,
        'fitted_basis_coefficients':[0.25,-1/6,0.5,-0.25,1/12,1/12],
    }}}
    clear=[{'scientific_branch_id':bid,'arm':'FULL-V1','paired_seed':1}]
    out, rows=_gram_geometry(views,k2,clear)
    rr=rows[0]
    assert abs(rr['s_norm_ratio']-s)<1e-12
    assert abs(rr['radial_fraction_of_r_squared']-((s-c)**2/r**2))<1e-12
    assert out['aggregate']['resolved_count']==1
    assert len(out['gram_lock']['Gram_matrix'])==6


def test_pf0_protocol_is_postmembership_and_forbids_dev_search_and_refit():
    root=Path(__file__).resolve().parents[1]
    cfg=json.loads((root/'configs/p13_s1_pf0_protocol.json').read_text())
    assert cfg['role']=='REFERENCE_DESCRIPTIVE_POST_MEMBERSHIP'
    assert cfg['parent_freeze']['K3_freeze_remains_immutable'] is True
    assert cfg['PF0_C_matched_TRAIN_witness']['candidate_seed_or_initialization'] is False
    assert cfg['PF0_C_matched_TRAIN_witness']['membership_authority'] is False
    assert cfg['PF0_D_witness_epsilon_comparator']['refit_by_epsilon'] is False
    assert cfg['PF0_E_amplitude_sensitivity_probe']['candidate_refit'] is False
    assert 1.0 in cfg['PF0_E_amplitude_sensitivity_probe']['lambda_grid']
    assert 1.6 in cfg['PF0_E_amplitude_sensitivity_probe']['lambda_grid']
    forbidden=' '.join(cfg['data_boundary']['forbidden'])
    assert 'DEVELOPMENT' in forbidden
    assert 'SEALED' in forbidden
    assert '32768' in forbidden
    assert 'PLCP' in forbidden


def test_pf0_next_action_is_final_s1_freeze_not_direct_dev_opening():
    root=Path(__file__).resolve().parents[1]
    cfg=json.loads((root/'configs/p13_s1_pf0_protocol.json').read_text())
    assert cfg['next_on_pass']=='P13-S1-PF1_FINAL_STAGE_FREEZE_AND_S2_HANDOFF'


def test_asp_neutral_domain_failure_is_unresolved_but_lambda1_remains_evaluable(monkeypatch):
    import p13rawxt.s1_pf0_postfreeze_diagnostics as pf0

    class Grid:
        mesh=(np.zeros((2,2)),np.zeros((2,2)))
    class Probe:
        def __init__(self, kind): self.kind=kind
        def derivatives(self, x, t): return {'kind':self.kind}
    class F:
        field_id='f'
        arrays={'kind':'train','a_d0_0':np.ones((2,2))}
        probe_interpolator=Probe('train')
    class N:
        arrays={'kind':'neutral','a_d0_0':np.ones((2,2))}
        probe_interpolator=Probe('neutral')

    monkeypatch.setattr(pf0, '_grid_from_arrays', lambda arrays: Grid())
    monkeypatch.setattr(pf0, '_probe_source', lambda *a, **k: np.zeros((1,2)))
    monkeypatch.setattr(pf0, 'grid_coefficient_derivatives', lambda arrays, order: arrays)
    def fake_eval(rx, rt, theta, x, t, cd, order):
        if cd.get('kind') == 'neutral':
            raise FloatingPointError('inverse domain failure')
        return {'X':np.zeros_like(x,dtype=float), 'T':np.zeros_like(t,dtype=float)}
    monkeypatch.setattr(pf0, 'evaluate_pair_jet', fake_eval)
    monkeypatch.setattr(pf0, 'evaluate_validity_variable', lambda *a, **k: {'overall_valid':True,'stage_index':5,'highest_feasibility_level':'F4','rejection_codes':[]})
    monkeypatch.setattr(pf0, '_p11_imports', lambda: {'gauge_second_jet': lambda raw: (raw,{})})
    monkeypatch.setattr(pf0, 'operator_on_variable', lambda *a, **k: (None, {'J_princ':1.25}))
    base={'validity':{'inverse_probe_grid_per_axis':1,'inverse_probe_local_coordinates':(0.0,0.0),'inverse_roundtrip_tolerance':1e-8},'operator':{'space':{},'numerical':{}}}
    pair={'raw_X_AST':{},'raw_T_AST':{}}
    curve=pf0._asp_field_curve(pair, [], F(), N(), base, [0.6,1.0,1.4])
    assert curve[0]['J_princ'] is None
    assert curve[2]['J_princ'] is None
    assert curve[0]['rejection_codes'][0].startswith('ASP_NEUTRAL_BASELINE_DOMAIN_FAILURE:FloatingPointError:')
    assert curve[1]['J_princ'] == 1.25
    assert curve[1]['stage_index'] == 5


def test_pf0r1_restart_lock_migration_is_exact_source_only():
    import p13rawxt.s1_pf0_postfreeze_diagnostics as pf0
    new={'PF0_source_sha256':'new','PF0_config_sha256':'c','K3_semantic_digest':'k','lambda_grid':[0.6,1.0]}
    old=dict(new); old['PF0_source_sha256']=pf0.PF0_V1_ORIGINAL_SOURCE_SHA256
    assert pf0._restart_lock_migration_allowed(old,new) is True
    bad=dict(old); bad['lambda_grid']=[1.0]
    assert pf0._restart_lock_migration_allowed(bad,new) is False


def test_pf0r1_partial_restart_row_requires_lambda1_integrity():
    import p13rawxt.s1_pf0_postfreeze_diagnostics as pf0
    row={'scientific_branch_id':'b','same_AST_theta_zero_refit':True,'lambda_grid':[0.6,1.0],
         'lambda_1_relative_integrity_error':1e-12,'status':'RESOLVED_FULL_GRID'}
    pf0._validate_asp_partial_row_for_restart(row, {'b'}, [0.6,1.0])
    row2=dict(row); row2['lambda_1_relative_integrity_error']=1e-5
    import pytest
    with pytest.raises(RuntimeError):
        pf0._validate_asp_partial_row_for_restart(row2, {'b'}, [0.6,1.0])
