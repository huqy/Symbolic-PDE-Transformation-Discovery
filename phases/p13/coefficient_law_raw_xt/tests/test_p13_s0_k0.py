from pathlib import Path
import json,sys
import numpy as np

HERE=Path(__file__).resolve(); HOME=HERE.parents[1]; ROOT=HERE.parents[4]; P11=ROOT/'phases/p11/raw_xt_td'
sys.path.insert(0,str(HOME/'src')); sys.path.insert(0,str(P11/'src'))
from p13rawxt.gauge import canonicalize_common_translation_positive_scale, apply_group_action
from p11rawxt_ast import Op,Var,derivative_nesting,check_pair_caps
from p11rawxt_s1.k1_representation import validate_raw_ast


def protocol():
    return json.load(open(HOME/'configs/p13_s0_k0_protocol.json'))


def test_k0_forbids_data_response_and_search():
    p=protocol()['forbidden_in_K0']
    assert p['generate_or_open_development_response'] is True
    assert p['generate_or_open_sealed_response'] is True
    assert p['use_claim_II_characteristic_formula_as_search_seed_or_fitness'] is True
    assert p['formal_candidate_search'] is True and p['large_search_code'] is True


def test_l3_caps_exact_and_executable_complete():
    caps=protocol()['l3_activation']['caps']
    assert caps=={'nodes_X_max':41,'nodes_T_max':41,'total_nodes_max':72,'depth_max':13,'unique_theta_total_max':10,'unique_theta_component_max':6,'theta_occurrences_total_max':12,'derivative_nesting_max':2}
    ok,fail,stats=check_pair_caps(Op('Dx',Op('Dt',Var('a'))),Var('t'),caps)
    assert ok and not fail and stats['derivative_nesting_max']==2


def test_derivative_nesting_two_allowed_three_rejected():
    d2=Op('Dx',Op('Dt',Var('a'))); d3=Op('Dx',Op('Dt',Op('Dx',Var('a'))))
    assert derivative_nesting(d2)==2 and validate_raw_ast(d2,2)==[]
    assert derivative_nesting(d3)==3 and 'derivative_nesting>2' in validate_raw_ast(d3,2)


def _affine_raw(x,t):
    one=np.ones_like(x); zero=np.zeros_like(x)
    return {'X':1.07*x+0.03*t,'T':0.02*x+1.04*t,'Xx':1.07*one,'Xt':0.03*one,'Tx':0.02*one,'Tt':1.04*one,'Xxx':zero,'Xxt':zero,'Xtt':zero,'Txx':zero,'Txt':zero,'Ttt':zero}


def test_gauge_group_action_canonicalizes_identically():
    x=np.linspace(0,1,9);t=np.linspace(0,1,9);xm,tm=np.meshgrid(x,t,indexing='ij')
    raw=_affine_raw(xm,tm); acted=apply_group_action(raw,scale=3.2,shift_x=7.1,shift_t=-2.3)
    a,ra=canonicalize_common_translation_positive_scale(raw); b,rb=canonicalize_common_translation_positive_scale(acted)
    assert a is not None and b is not None
    assert max(np.max(np.abs(a[k]-b[k])) for k in a)<=1e-12
    assert ra['per_field_optimized_gauge'] is False and rb['per_field_optimized_gauge'] is False


def test_upstream_sha_lock_has_core_raw_semantics():
    p=protocol()['inherited_file_sha256']
    for rel in ['phases/p11/raw_xt_td/src/p11rawxt_ast.py','phases/p11/raw_xt_td/src/p11rawxt_validity.py','phases/p11/raw_xt_td/src/p11rawxt_operator.py','phases/p11/raw_xt_td/src/p11rawxt_s1/k2_ast_runtime.py']:
        assert rel in p and len(p[rel])==64
