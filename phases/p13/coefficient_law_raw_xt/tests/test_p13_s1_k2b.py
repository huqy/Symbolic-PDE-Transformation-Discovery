from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import numpy as np

from p13rawxt.coefficients import canonical_json_bytes, make_search_object, search_object_semantic_digest, sha256_bytes, write_npz_exact
from p13rawxt.s1_k2b_post_search_diagnostics import _aggregate_candidate_results, _mean_train_arrays, _private_views
from p13rawxt.family_evaluator import FieldView
from p13rawxt.ast_runtime import FieldJetInterpolator


def test_mean_train_arrays_is_pointwise_channel_mean():
    fields=[]
    for i in range(6):
        arr={
            'x':np.linspace(0,1,3), 't':np.linspace(0,1,3), 'q':np.ones((3,3)),
            'a_d0_0':np.full((3,3),1+i,dtype=float), 'a_d1_0':np.full((3,3),2*i,dtype=float)
        }
        fields.append(FieldView(f'f{i}','TRAIN_OPERATOR',3,arr,None))
    m=_mean_train_arrays(fields)
    assert np.allclose(m['a_d0_0'],3.5)
    assert np.allclose(m['a_d1_0'],5.0)


def test_private_views_verifies_committed_archive(tmp_path: Path):
    protocol={
      'regime':{'epsilon_abs':0.2},
    }
    # Construct a minimal generator accepted by make_search_object.
    gen={'schema':'P13_COEFFICIENT_GENERATOR_V1','field_id':'P13_WITHIN_FAMILY_TRANSFER_DIAGNOSTIC_01','role':'WITHIN_FAMILY_TRANSFER_DIAGNOSTIC','family':'oblique_plane_wave_3mode','seed':123,'epsilon':0.2,'primitive_terms':[], 'expanded_plane_wave_components':[{'amplitude':0.5,'kx':0.2,'kt':0.3,'phase':0.1}]}
    staging=tmp_path/'st'; staging.mkdir()
    (staging/f"{gen['field_id']}_generator.json").write_text(json.dumps(gen,sort_keys=True,indent=2)+'\n')
    objs=[]
    for grid in [33,65]:
        arr=make_search_object(gen,grid,4); p=staging/f"{gen['field_id']}_G{grid}.npz"; write_npz_exact(p,arr)
        objs.append({'grid':grid,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'semantic_digest':search_object_semantic_digest(arr)})
    archive=tmp_path/'private.tar.xz'
    with tarfile.open(archive,'w:xz') as tf:
        for p in sorted(staging.iterdir()): tf.add(p,arcname=p.name,recursive=False)
    commitment={'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'public_row_commitments':[{'field_id':gen['field_id'],'role':gen['role'],'generator_semantic_digest':sha256_bytes(canonical_json_bytes(gen)),'search_object_commitments':objs}]}
    views=_private_views(archive,commitment,33,65)
    assert len(views)==1 and views[0].field_id==gen['field_id'] and views[0].grid_n==33


def test_aggregate_keeps_clear_and_unresolved_separate():
    base={'transfer':{'within_family_G65':{'relative_RMS_to_identity':0.4},'cross_family_G65':{'relative_RMS_to_identity':0.7},'max_G33_G65_relative_J_discrepancy':0.001},'coefficient_ablation':{'Delta_coef_rel':1.5,'ablation_valid_all_fields':True},'lower_order':{'TRAIN_G65':{'max_abs_L_T_over_C_TT':2.0,'max_abs_L_X_over_C_TT':3.0,'min_C_TT':0.5}}}
    rows=[{'TRAIN_membership_status':'OPERATOR_QUALIFIED_TRAIN','coefficient_dependent_syntax':True,**base},{'TRAIN_membership_status':'OPERATOR_QUALIFIED_UNRESOLVED','coefficient_dependent_syntax':True,**base}]
    a=_aggregate_candidate_results(rows)
    assert a['rows']==2 and a['clear_rows']==1 and a['unresolved_rows']==1
    assert a['Delta_coef_rel']['median']==1.5


def test_route_interpretation_pattern_a_b_without_new_threshold():
    from p13rawxt.s1_k2b_post_search_diagnostics import _route_interpretation_from_k2a
    cons={'summary':{'stability':'CONSISTENT_ROUTE_FEASIBILITY_DIRECTION','left_clear':4}}
    nost={'summary':{'stability':'NO_STABLE_PAIRED_ADVANTAGE','left_clear':2}}
    k={'cohort_counts':{'FULL_clear':3},'paired_arm_effects':{'equal_structural_proposals':{'FULL-V1__vs__NULL-V2':cons,'FULL-V2__vs__NULL-V2':cons,'FULL-V2__vs__FULL-V1':nost}},'coefficient_dependent_vs_free_frontier_effects':{}}
    r=_route_interpretation_from_k2a(k)
    assert r['K2A_route_pattern']=='A+B'
    assert r['diagnostic_results_may_change_route_or_membership'] is False
