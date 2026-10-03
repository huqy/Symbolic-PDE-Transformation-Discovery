from __future__ import annotations

import copy
from typing import Any
import numpy as np


def residual_graft(parent: dict[str, Any], rng: np.random.Generator, caps: dict[str, int], subtree_nodes_max: int) -> dict[str, Any]:
    """Generic P13 V2 residual-graft move.

    This module is intentionally isolated from all calibration instruments and physical-data code.
    The move is purely syntactic and uses only the frozen raw primitive grammar/caps.
    """
    from p11rawxt_ast import Op, Theta, parameter_names
    from p11rawxt_s1.k1_representation import canonicalize_pair
    from p11rawxt_s1.k2_ast_runtime import random_ast, _raw_grammar_expansion

    for _ in range(256):
        existing = parameter_names(parent["raw_X_AST"]) | parameter_names(parent["raw_T_AST"])
        new_id = max([int(x.split("_")[1]) for x in existing], default=0) + 1
        # Frozen design says theta_new. Saturated parents simply do not admit this proposal type.
        if new_id > int(caps["unique_theta_total_max"]):
            raise ValueError("RESIDUAL_GRAFT_PARENT_THETA_SATURATED")
        theta_name = f"theta_{new_id}"
        component = "X" if int(rng.integers(0, 2)) == 0 else "T"
        budget = int(rng.integers(1, int(subtree_nodes_max) + 1))
        g = random_ast(
            rng,
            budget,
            min(int(caps["depth_max"]), 8),
            int(caps["unique_theta_total_max"]),
            int(caps["derivative_nesting_max"]),
        )
        term = Op("Mul", Theta(theta_name), g)
        rx = _raw_grammar_expansion(parent["raw_X_AST"])
        rt = _raw_grammar_expansion(parent["raw_T_AST"])
        if component == "X":
            rx = Op("Add", rx, term)
        else:
            rt = Op("Add", rt, term)
        try:
            child = canonicalize_pair(rx, rt, caps)
        except Exception:
            continue
        if child["raw_grammar_valid"] and child["caps_valid"] and not child["static_domain_impossible"]:
            return {
                "pair": child,
                "component": component,
                "graft_subtree": g,
                "new_theta": theta_name,
                "proposal_semantics": "generic_additive_residual_graft_no_theory_motif",
            }
    raise RuntimeError("RESIDUAL_GRAFT_NO_LEGAL_CHILD")
