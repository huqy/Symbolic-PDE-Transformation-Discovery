from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import numpy as np

Array=np.ndarray

@dataclass(frozen=True)
class GaugeSemantics:
    reference_index: tuple[int,int]=(0,0)
    group: str="translation_X x translation_T x common_positive_scale"
    per_field_optimization: bool=False


def canonicalize_common_translation_positive_scale(raw: dict[str,Array], reference_index: tuple[int,int]=(0,0)) -> tuple[dict[str,Array]|None,dict[str,Any]]:
    """Exact P13 wrapper for the frozen P11 minimal gauge.

    This is a deterministic quotient representative, not a fitted field-specific
    parameter. It deliberately delegates first-jet semantics to the byte-frozen
    P11 implementation. Second derivatives, when present, scale by the same
    common positive scale.
    """
    from p11rawxt_validity import common_scale_translation_gauge
    first={k:np.asarray(raw[k],dtype=np.float64) for k in ("X","T","Xx","Xt","Tx","Tt")}
    gauged,record=common_scale_translation_gauge(first,reference_index=reference_index)
    if gauged is None:
        return None,{**record,"p13_semantics":"DETERMINISTIC_GAUGE_QUOTIENT_NOT_OPTIMIZATION"}
    scale=float(record["common_positive_scale"])
    out=dict(gauged)
    for key in ("Xxx","Xxt","Xtt","Txx","Txt","Ttt"):
        if key in raw:
            out[key]=np.asarray(raw[key],dtype=np.float64)/scale
    rec={
        **record,
        "p13_semantics":"DETERMINISTIC_GAUGE_QUOTIENT_NOT_OPTIMIZATION",
        "same_rule_all_coefficient_fields":True,
        "per_field_optimized_gauge":False,
        "raw_record_required":True,
        "canonical_record_required":True,
    }
    return out,rec


def apply_group_action(raw: dict[str,Array], *, scale: float, shift_x: float, shift_t: float) -> dict[str,Array]:
    if not np.isfinite(scale) or scale<=0:
        raise ValueError("scale must be finite and positive")
    out={}
    for key,value in raw.items():
        arr=np.asarray(value,dtype=np.float64)
        if key=="X": out[key]=scale*arr+shift_x
        elif key=="T": out[key]=scale*arr+shift_t
        elif key.startswith("X") or key.startswith("T"): out[key]=scale*arr
        else: out[key]=arr.copy()
    return out
