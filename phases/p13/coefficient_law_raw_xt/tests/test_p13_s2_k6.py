import json, math
from pathlib import Path
import numpy as np
from p13rawxt.s2_k6_post_response_diagnostics import qstats, spearman_pairs, _case_census, _extract_pf0


def test_spearman_no_pvalue_and_monotone():
    rows=[{"x":i,"y":2*i+1} for i in range(10)]
    r=spearman_pairs(rows,"x","y")
    assert r["n"]==10 and abs(r["spearman"]-1.0)<1e-14 and r["p_value"] is None


def test_qstats_complete():
    q=qstats([1,2,3,4,5])
    assert q["n"]==5 and q["min"]==1 and q["max"]==5 and q["median"]==3


def test_case_census_is_descriptive_only():
    rows=[{"response_cases":{"F::c1":0.1,"F::c2":0.2}},{"response_cases":{"F::c1":0.2,"F::c2":0.3}}]
    controls={"identity":{"nominal_by_case":{"F::c1":0.4,"F::c2":0.4}},"frozen_null":{"nominal_by_case":{"F::c1":0.5,"F::c2":0.5}}}
    x=_case_census(rows,controls)
    assert x["membership_authority"] is False and len(x["rows"])==2
    assert all(r["fraction_candidates_below_identity"]==1.0 for r in x["rows"])


def test_pf0_extract_does_not_create_gate():
    g={"geometry_status":"RESOLVED","s_norm_ratio":1.0,"c_theory_cosine":.9,"r_theory_relative_residual":.2,"radial_fraction_of_r_squared":.7,"signed_radial_offset_s_minus_c":.1,"projection_relative_residual":.01,"G_metric_coefficient_cosine_to_theory":.95,"G_metric_coefficient_relative_residual":.05}
    a={"status":"RESOLVED_FULL_GRID","lambda_star_on_grid":0.5,"radial_slack":.2,"minimum_at_grid_boundary":False}
    x=_extract_pf0(g,a)
    assert abs(x["asp_abs_log_lambda_star"]-abs(math.log(.5)))<1e-15
    assert "membership_authority" not in x
