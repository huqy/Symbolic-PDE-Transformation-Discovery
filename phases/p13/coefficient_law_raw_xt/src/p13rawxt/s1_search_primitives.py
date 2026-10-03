from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import numpy as np

Node = dict[str, Any]


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def derive_seed(namespace: str, paired_seed: int, member_index: int, attempt: int = 0) -> int:
    b = f"{namespace}|{paired_seed}|{member_index}|{attempt}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(b).digest()[:8], "big", signed=False)


def _import_p11():
    from p11rawxt_ast import Const, Op, Theta, Var, parameter_names, pair_stats
    from p11rawxt_s1.k1_representation import canonicalize_pair
    from p11rawxt_s1.k2_ast_runtime import _raw_grammar_expansion, replace_subtree, typed_subtree_sites
    return Const, Op, Theta, Var, parameter_names, pair_stats, canonicalize_pair, _raw_grammar_expansion, replace_subtree, typed_subtree_sites


def _terminal_from_quantile(u: float, theta_cap: int, grammar_prior: str) -> Node:
    Const, _, Theta, Var, *_ = _import_p11()
    if grammar_prior == "coordinate_only":
        choices: list[Node] = [Var("x"), Var("t"), Const(-1), Const(0), Const(1)]
    elif grammar_prior == "full_raw_coefficient":
        choices = [Var("x"), Var("t"), Var("a"), Var("q"), Const(-1), Const(0), Const(1)]
    else:
        raise ValueError(grammar_prior)
    if theta_cap > 0:
        choices.extend(Theta(f"theta_{i}") for i in range(1, theta_cap + 1))
    idx = min(len(choices) - 1, int(math.floor(float(u) * len(choices))))
    return copy.deepcopy(choices[idx])


def _choice_u(rng: np.random.Generator, n: int) -> tuple[int, float]:
    u = float(rng.random())
    return min(n - 1, int(math.floor(u * n))), u


def random_ast_policy(
    rng: np.random.Generator,
    max_nodes: int,
    max_depth: int,
    theta_cap: int,
    derivative_nesting_max: int,
    grammar_prior: str,
    derivative_level: int = 0,
    trace: list[dict[str, Any]] | None = None,
) -> Node:
    _, Op, Theta, _, *_ = _import_p11()
    if max_nodes <= 1 or max_depth <= 1:
        u = float(rng.random())
        if trace is not None:
            trace.append({"kind": "terminal", "u": u, "nodes": max_nodes, "depth": max_depth})
        return _terminal_from_quantile(u, theta_cap, grammar_prior)
    categories = ["terminal", "unary", "powint"]
    if max_nodes >= 3:
        categories.extend(["binary", "powparam"])
    if derivative_level < derivative_nesting_max:
        categories.append("derivative")
    ci, ucat = _choice_u(rng, len(categories)); category = categories[ci]
    if trace is not None:
        trace.append({"kind": "category", "u": ucat, "value": category, "nodes": max_nodes, "depth": max_depth, "derivative_level": derivative_level})
    if category == "terminal":
        u = float(rng.random())
        if trace is not None: trace.append({"kind": "terminal", "u": u})
        return _terminal_from_quantile(u, theta_cap, grammar_prior)
    if category in {"unary", "powint", "derivative"}:
        child = random_ast_policy(rng, max_nodes - 1, max_depth - 1, theta_cap, derivative_nesting_max, grammar_prior, derivative_level + int(category == "derivative"), trace)
        if category == "unary":
            ops = ["Inv", "Sqrt", "Log"]; oi, u = _choice_u(rng, len(ops))
            if trace is not None: trace.append({"kind": "unary_op", "u": u, "value": ops[oi]})
            return Op(ops[oi], child)
        if category == "powint":
            exps = [-3, -2, -1, 2, 3]; ei, u = _choice_u(rng, len(exps))
            if trace is not None: trace.append({"kind": "powint", "u": u, "value": exps[ei]})
            return Op("PowInt", child, exponent=exps[ei])
        di, u = _choice_u(rng, 2)
        if trace is not None: trace.append({"kind": "derivative", "u": u, "value": "Dx" if di == 0 else "Dt"})
        return Op("Dx" if di == 0 else "Dt", child)
    if category == "powparam":
        if theta_cap <= 0:
            u = float(rng.random())
            if trace is not None: trace.append({"kind": "terminal", "u": u})
            return _terminal_from_quantile(u, theta_cap, grammar_prior)
        base = random_ast_policy(rng, max_nodes - 2, max_depth - 1, theta_cap, derivative_nesting_max, grammar_prior, derivative_level, trace)
        idx, u = _choice_u(rng, theta_cap)
        if trace is not None: trace.append({"kind": "theta_choice", "u": u, "value": idx + 1})
        return Op("PowParam", base, Theta(f"theta_{idx + 1}"))
    # binary
    if max_nodes <= 2:
        u = float(rng.random())
        return _terminal_from_quantile(u, theta_cap, grammar_prior)
    span = max_nodes - 2
    bi, ub = _choice_u(rng, span)
    left_budget = bi + 1
    right_budget = max(1, max_nodes - 1 - left_budget)
    if trace is not None: trace.append({"kind": "binary_budget", "u": ub, "left": left_budget, "right": right_budget})
    left = random_ast_policy(rng, left_budget, max_depth - 1, theta_cap, derivative_nesting_max, grammar_prior, derivative_level, trace)
    right = random_ast_policy(rng, right_budget, max_depth - 1, theta_cap, derivative_nesting_max, grammar_prior, derivative_level, trace)
    ops = ["Add", "Sub", "Mul", "Div"]; oi, uo = _choice_u(rng, len(ops))
    if trace is not None: trace.append({"kind": "binary_op", "u": uo, "value": ops[oi]})
    return Op(ops[oi], left, right)


def random_pair_policy(seed: int, caps: dict[str, int], grammar_prior: str, max_attempts: int = 256) -> dict[str, Any]:
    *_, canonicalize_pair, _, _, _ = _import_p11()
    for attempt in range(max_attempts):
        rng = np.random.default_rng(int(seed) + attempt * 0x9E3779B97F4A7C15 % (2**63 - 1))
        trace: list[dict[str, Any]] = []
        total_span = int(caps["total_nodes_max"]) - 1
        ti, ut = _choice_u(rng, total_span)
        total_target = ti + 2
        xmax = min(int(caps["nodes_X_max"]), total_target - 1)
        xi, ux = _choice_u(rng, xmax)
        x_budget = xi + 1
        t_budget = max(1, min(int(caps["nodes_T_max"]), total_target - x_budget))
        trace.extend([{"kind": "pair_total", "u": ut, "value": total_target}, {"kind": "pair_x_budget", "u": ux, "value": x_budget, "t_budget": t_budget}])
        raw_x = random_ast_policy(rng, x_budget, int(caps["depth_max"]), int(caps["unique_theta_total_max"]), int(caps["derivative_nesting_max"]), grammar_prior, trace=trace)
        raw_t = random_ast_policy(rng, t_budget, int(caps["depth_max"]), int(caps["unique_theta_total_max"]), int(caps["derivative_nesting_max"]), grammar_prior, trace=trace)
        try:
            pair = canonicalize_pair(raw_x, raw_t, caps)
        except Exception:
            continue
        if pair["raw_grammar_valid"] and pair["caps_valid"] and not pair["static_domain_impossible"]:
            pair["initialization_provenance"] = {
                "grammar_prior": grammar_prior,
                "seed": int(seed),
                "accepted_attempt": attempt,
                "raw_choice_trace_sha256": sha256_bytes(canonical_json_bytes(trace)),
                "raw_choice_trace": trace,
            }
            return pair
    raise RuntimeError(f"could not generate legal pair for grammar={grammar_prior}")


def paired_initial_population(paired_seed: int, count: int, caps: dict[str, int], namespace: str = "P13-S1-K0R-PAIRED-INIT") -> dict[str, list[dict[str, Any]]]:
    full: list[dict[str, Any]] = []
    null: list[dict[str, Any]] = []
    for i in range(count):
        seed = derive_seed(namespace, paired_seed, i)
        full.append(random_pair_policy(seed, caps, "full_raw_coefficient"))
        null.append(random_pair_policy(seed, caps, "coordinate_only"))
    return {"NULL-V2": null, "FULL-V1": full, "FULL-V2": copy.deepcopy(full)}


def contains_coefficient_syntax(pair: dict[str, Any]) -> bool:
    def visit(node: Node) -> bool:
        if node.get("op") == "Var" and node.get("name") == "a":
            return True
        return any(visit(c) for c in node.get("args", []))
    return visit(pair["raw_X_AST"]) or visit(pair["raw_T_AST"])


def theta_names(pair: dict[str, Any]) -> list[str]:
    _, _, _, _, parameter_names, *_ = _import_p11()
    names = parameter_names(pair["raw_X_AST"]) | parameter_names(pair["raw_T_AST"])
    return sorted(names, key=lambda x: int(x.split("_")[1]))


def _raw_policy_expansion(node: Node) -> Node:
    *_, raw_expand, _, _ = _import_p11()
    return raw_expand(node)


def mutate_pair_policy(parent: dict[str, Any], rng: np.random.Generator, caps: dict[str, int], grammar_prior: str, max_attempts: int = 128) -> dict[str, Any] | None:
    Const, _, Theta, _, _, _, canonicalize_pair, _, replace_subtree, typed_subtree_sites = _import_p11()
    for _ in range(max_attempts):
        component = "X" if int(rng.integers(0, 2)) == 0 else "T"
        base_x = _raw_policy_expansion(parent["raw_X_AST"]); base_t = _raw_policy_expansion(parent["raw_T_AST"])
        source = base_x if component == "X" else base_t
        sites = typed_subtree_sites(source); site = sites[int(rng.integers(0, len(sites)))]
        pos = site["position"]
        if site["required_kind"] == "theta_terminal":
            idx = int(rng.integers(1, int(caps["unique_theta_total_max"]) + 1)); repl = Theta(f"theta_{idx}")
        else:
            budget_max = int(caps["nodes_X_max"] if component == "X" else caps["nodes_T_max"])
            budget = int(rng.integers(1, budget_max + 1))
            repl = random_ast_policy(rng, budget, int(caps["depth_max"]), int(caps["unique_theta_total_max"]), int(caps["derivative_nesting_max"]), grammar_prior)
        rx = replace_subtree(base_x, pos, repl) if component == "X" else base_x
        rt = replace_subtree(base_t, pos, repl) if component == "T" else base_t
        try: child = canonicalize_pair(rx, rt, caps)
        except Exception: continue
        if child["raw_grammar_valid"] and child["caps_valid"] and not child["static_domain_impossible"]:
            return child
    return None


@dataclass
class GraftOutcome:
    child: dict[str, Any] | None
    outcome: str
    component: str | None = None
    new_theta: str | None = None


def additive_root_residual_graft(parent: dict[str, Any], rng: np.random.Generator, caps: dict[str, int], subtree_nodes_max: int, grammar_prior: str, max_attempts: int = 256) -> GraftOutcome:
    _, Op, Theta, _, parameter_names, _, canonicalize_pair, raw_expand, _, _ = _import_p11()
    existing = parameter_names(parent["raw_X_AST"]) | parameter_names(parent["raw_T_AST"])
    ids = sorted(int(x.split("_")[1]) for x in existing)
    # canonical parents must be compact; enforce, do not repair silently.
    if ids != list(range(1, len(ids) + 1)):
        raise RuntimeError(f"NONCOMPACT_CANONICAL_THETA:{ids}")
    new_id = len(ids) + 1
    if new_id > int(caps["unique_theta_total_max"]):
        return GraftOutcome(None, "graft_parent_theta_saturated")
    theta_name = f"theta_{new_id}"
    for _ in range(max_attempts):
        component = "X" if int(rng.integers(0, 2)) == 0 else "T"
        budget = int(rng.integers(1, int(subtree_nodes_max) + 1))
        g = random_ast_policy(rng, budget, min(int(caps["depth_max"]), 8), int(caps["unique_theta_total_max"]), int(caps["derivative_nesting_max"]), grammar_prior)
        term = Op("Mul", Theta(theta_name), g)
        rx = raw_expand(parent["raw_X_AST"]); rt = raw_expand(parent["raw_T_AST"])
        if component == "X": rx = Op("Add", rx, term)
        else: rt = Op("Add", rt, term)
        try: child = canonicalize_pair(rx, rt, caps)
        except Exception: continue
        if child["raw_grammar_valid"] and child["caps_valid"] and not child["static_domain_impossible"]:
            return GraftOutcome(child, "graft_successful_child", component, theta_name)
    return GraftOutcome(None, "graft_no_legal_child")


def empty_v2_counters() -> dict[str, int]:
    return {k: 0 for k in [
        "mutation_slots_total", "standard_mutation_selected", "graft_selected", "graft_attempted",
        "graft_successful_child", "graft_parent_theta_saturated", "graft_no_legal_child", "silent_fallback_to_V1"
    ]}


def route_v2_mutation_slot(parent: dict[str, Any], rng: np.random.Generator, caps: dict[str, int], grammar_prior: str, graft_probability: float = 0.5, subtree_nodes_max: int = 9) -> tuple[dict[str, Any] | None, dict[str, int], str]:
    counters = empty_v2_counters(); counters["mutation_slots_total"] = 1
    if float(rng.random()) < float(graft_probability):
        counters["graft_selected"] = 1; counters["graft_attempted"] = 1
        out = additive_root_residual_graft(parent, rng, caps, subtree_nodes_max, grammar_prior)
        counters[out.outcome] += 1
        # Crucial: no standard-mutation fallback.
        return out.child, counters, out.outcome
    counters["standard_mutation_selected"] = 1
    child = mutate_pair_policy(parent, rng, caps, grammar_prior)
    return child, counters, "standard_mutation_child" if child is not None else "standard_mutation_no_legal_child"


def aggregate_family_objective(J_i: Iterable[float], identity_i: Iterable[float] | None = None) -> dict[str, Any]:
    vals = [float(x) for x in J_i]
    if len(vals) != 6: raise ValueError("formal family objective requires exactly six TRAIN fields")
    if not all(math.isfinite(x) and x >= 0.0 for x in vals): raise ValueError("unresolved/nonfinite J_i")
    out = {"J_i": vals, "J_family": float(math.sqrt(sum(x*x for x in vals) / 6.0)), "J_max": max(vals)}
    if identity_i is not None:
        ids = [float(x) for x in identity_i]
        if len(ids) != 6 or any(x <= 0.0 or not math.isfinite(x) for x in ids): raise ValueError("identity baseline invalid")
        out["J_family_rel"] = float(math.sqrt(sum((x/y)**2 for x,y in zip(vals, ids)) / 6.0))
    return out


def boundary_status(value: float, q: float, tau_num: float = 0.005) -> str:
    v=float(value); q=float(q); tau=float(tau_num)
    if v < q * (1.0 - tau): return "CLEAR_PASS"
    if v > q * (1.0 + tau): return "CLEAR_FAIL"
    return "UNRESOLVED_NUMERICAL_BOUNDARY"


def operator_qualification(J_i: Iterable[float], identity_i: Iterable[float], f4_all_fields: bool, all_J_resolved: bool, tau_num: float = 0.005) -> dict[str, Any]:
    vals=[float(x) for x in J_i]; ids=[float(x) for x in identity_i]
    if len(vals)!=6 or len(ids)!=6: raise ValueError("six TRAIN fields required")
    if not f4_all_fields: return {"status":"NOT_OPERATOR_QUALIFIED", "reason":"F0_F4_NOT_ALL_FIELDS"}
    if not all_J_resolved: return {"status":"OPERATOR_QUALIFIED_UNRESOLVED", "reason":"J_NUMERICAL_UNRESOLVED"}
    Jf=aggregate_family_objective(vals)["J_family"]; If=aggregate_family_objective(ids)["J_family"]
    fam_ratio=Jf/If
    field_ratios=[x/y for x,y in zip(vals,ids)]
    statuses=[boundary_status(fam_ratio,0.5,tau_num)] + [boundary_status(r,1.0,tau_num) for r in field_ratios]
    if any(s=="CLEAR_FAIL" for s in statuses): final="NOT_OPERATOR_QUALIFIED"
    elif any(s.startswith("UNRESOLVED") for s in statuses): final="OPERATOR_QUALIFIED_UNRESOLVED"
    else: final="OPERATOR_QUALIFIED_TRAIN"
    return {"status":final,"family_ratio":fam_ratio,"field_ratios":field_ratios,"boundary_statuses":statuses,"hard_gate_unchanged":True,"tau_num_role":"ambiguity_only"}


def scientific_branch_id(pair: dict[str, Any], theta_hat: Iterable[float], deterministic_gauge: dict[str, Any], fit_provenance: dict[str, Any]) -> str:
    payload={"canonical_skeleton":pair["structural_hash"],"theta_hex":[float(x).hex() for x in theta_hat],"deterministic_gauge":deterministic_gauge,"fit_provenance":fit_provenance}
    return sha256_bytes(canonical_json_bytes(payload))


def execution_equivalence_key(pair: dict[str, Any], theta_hat: Iterable[float], deterministic_gauge: dict[str, Any]) -> str:
    # Conservative exact key: differing fit provenance may share execution if AST/theta/gauge are byte-identical.
    payload={"canonical_pair_serialization":pair["canonical_pair_serialization"],"theta_hex":[float(x).hex() for x in theta_hat],"deterministic_gauge":deterministic_gauge}
    return sha256_bytes(canonical_json_bytes(payload))


def continuation_progress(best_4096: float | None, best_8192: float | None) -> float:
    if best_8192 is None: return 0.0
    if best_4096 is None: return 1.0
    if best_4096 <= 0.0: raise ValueError("best J must be positive")
    return 1.0 - float(best_8192) / float(best_4096)


def continuation_decision(per_seed: list[tuple[float | None,float | None]], clear_full_qualified_exists: bool, invariants_pass: bool) -> dict[str, Any]:
    if len(per_seed)!=4: raise ValueError("primary cohort is exactly four paired seeds")
    rs=[continuation_progress(a,b) for a,b in per_seed]
    med=float(np.median(np.asarray(rs,dtype=float)))
    auth=(not clear_full_qualified_exists) and med >= 0.10 and bool(invariants_pass)
    return {"authorized":auth,"r_s":rs,"median_r_s":med,"reason":"AUTHORIZED_BY_PREREGISTERED_TRAIN_ONLY_RULE" if auth else "NOT_AUTHORIZED_BY_PREREGISTERED_TRAIN_ONLY_RULE","forbidden_inputs_used":[]}


def frontier_update(frontier: dict[str, float | None], pair: dict[str, Any], J_family: float) -> dict[str, float | None]:
    out=dict(frontier)
    key="coefficient_dependent" if contains_coefficient_syntax(pair) else "coefficient_free"
    old=out.get(key)
    out[key]=float(J_family) if old is None else min(float(old),float(J_family))
    return out


def proposal_ledger_row(*, arm:str, paired_seed:int, proposal_index:int, parent_provenance:dict[str,Any], pair:dict[str,Any], theta_hat:Iterable[float], fit_provenance:dict[str,Any], f_status:str, J_i:Iterable[float] | None, evaluator_calls:int, worker_cpu_seconds:float, wall_seconds:float, gauge:dict[str,Any], v2_counters:dict[str,int] | None=None) -> dict[str,Any]:
    theta=list(map(float,theta_hat)); branch=scientific_branch_id(pair,theta,gauge,fit_provenance); eq=execution_equivalence_key(pair,theta,gauge)
    agg=None if J_i is None else aggregate_family_objective(J_i)
    return {
        "arm":arm,"paired_seed":int(paired_seed),"proposal_index":int(proposal_index),"parent_proposal_provenance":parent_provenance,
        "structural_hash":pair["structural_hash"],"coefficient_dependent_syntax":contains_coefficient_syntax(pair),"theta_count":len(theta),
        "theta_hex":[x.hex() for x in theta],"scientific_branch_id":branch,"fit_provenance":fit_provenance,"F0_F4_status":f_status,
        "J_i":None if agg is None else agg["J_i"],"J_family":None if agg is None else agg["J_family"],"J_max":None if agg is None else agg["J_max"],
        "evaluator_calls":int(evaluator_calls),"worker_cpu_seconds":float(worker_cpu_seconds),"wall_seconds":float(wall_seconds),
        "exact_equivalence_class":eq,"v2_counters":v2_counters or {},"membership_rule":"complete_ledger_no_topk_pareto_percentile_target_count_response_filter"
    }


def evaluate_shared_theta_family(pair: dict[str, Any], theta_hat: Iterable[float], fields: list[Any], evaluator: Callable[[dict[str, Any], list[float], Any], float]) -> dict[str, Any]:
    if len(fields) != 6:
        raise ValueError("formal TRAIN family must contain six fields")
    theta = [float(x) for x in theta_hat]
    rows=[]
    for i, field in enumerate(fields):
        # same immutable numeric vector passed to every field; there is no per-field fitter hook.
        j=float(evaluator(pair, list(theta), field))
        rows.append({"field_index":i,"theta_hex":[x.hex() for x in theta],"J_i":j})
    agg=aggregate_family_objective([r["J_i"] for r in rows])
    return {**agg,"records":rows,"shared_theta":True,"per_field_theta_refit":False}
