from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from p13rawxt.coefficients import make_search_object
from p13rawxt.ast_runtime import FieldJetInterpolator
from p13rawxt.family_evaluator import FieldView

from p13rawxt.s1_k2c_theory_bridge import (
    _alignment_basis,
    _linearized_gauge,
    _log_slope,
    _predictions,
    _scaled_arrays_from_authoritative,
)


def _gen(fid: str, phase: float):
    return {
        'generator': {
            'schema':'P13_COEFFICIENT_GENERATOR_V1','field_id':fid,'role':'TRAIN_OPERATOR','family':'oblique_plane_wave_3mode','seed':1,'epsilon':0.2,'primitive_terms':[],
            'expanded_plane_wave_components':[
                {'amplitude':0.4,'kx':0.25,'kt':0.30,'phase':phase},
                {'amplitude':-0.3,'kx':-0.20,'kt':0.35,'phase':phase+0.7},
                {'amplitude':0.2,'kx':0.40,'kt':-0.15,'phase':phase+1.1},
            ]},
        'generator_semantic_digest':f'digest-{fid}'
    }


def test_linearized_gauge_removes_translation_and_common_scale_direction():
    x1=np.linspace(0,1,5); t1=np.linspace(0,1,5); x,t=np.meshgrid(x1,t1,indexing='ij')
    hx=3.0+2.0*x; ht=-4.0+2.0*t
    gx,gt=_linearized_gauge(hx,ht,2.0,2.0,x,t)
    assert np.allclose(gx,0.0)
    assert np.allclose(gt,0.0)


def test_claim_ii_m2_gauge_aware_basis_has_expected_coefficients_and_rank():
    views=[]
    for i in range(6):
        g=_gen(f'f{i}',0.37*i)['generator']; arr=make_search_object(g,17,4)
        views.append(FieldView(g['field_id'],'TRAIN_OPERATOR',17,arr,FieldJetInterpolator(arr,4)))
    b=_alignment_basis(views,grid=17,rank_rel_tol=1e-8)
    assert np.allclose(b['theory_coefficients'],[1/4,-1/6,1/2,-1/4,1/12,1/12])
    assert b['metadata']['rank']==6
    assert np.isfinite(b['metadata']['condition_number'])



def test_authoritative_amplitude_scaling_ratio_one_is_semantic_copy():
    g=_gen('f0',0.2)['generator']; arr=make_search_object(g,17,4)
    scaled=_scaled_arrays_from_authoritative(arr,0.2,0.2)
    for k in arr:
        assert np.array_equal(arr[k],scaled[k])
    neutral=_scaled_arrays_from_authoritative(arr,0.0,0.2)
    assert np.allclose(neutral['a_d0_0'],1.0)
    assert np.allclose(neutral['a_d1_0'],0.0)

def test_log_slope_is_empirical_only_and_exact_for_power_law():
    assert abs(_log_slope(0.05,0.05**2,0.10,0.10**2)-2.0)<1e-12
    assert _log_slope(0.05,None,0.10,0.1) is None


def test_prediction_schema_is_directional_and_not_membership_gate():
    k2b={'diagnostic_aggregate':{'within_family_relative_RMS_to_identity':{'median':0.5,'max':1.1},'cross_family_relative_RMS_to_identity':{'median':0.4,'max':1.2}}}
    agg={'clear_relative_RMS_eps_0p20':{'median':0.3},'alignment_resolved_clear':7}
    p=_predictions(k2b,agg)
    assert p['frozen_before_DEVELOPMENT'] is True
    assert p['predictions_are_directional_mechanism_predictions_not_gates'] is True
    assert p['S2_candidate_membership_unchanged'] is True


def test_k2c_protocol_forbids_theory_or_epsilon_candidate_filter():
    root=Path(__file__).resolve().parents[1]
    cfg=json.loads((root/'configs/p13_s1_k2c_protocol.json').read_text())
    assert cfg['membership_governance']['K2C_can_add_or_remove_S2_candidates'] is False
    assert cfg['membership_governance']['theory_alignment_filter_forbidden'] is True
    assert cfg['membership_governance']['epsilon_scaling_filter_forbidden'] is True
    assert cfg['data_boundary']['still_forbidden'][0]=='DEVELOPMENT'
