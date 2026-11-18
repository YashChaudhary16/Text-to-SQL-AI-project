"""Execution-accuracy comparison: does the predicted result answer the question like gold does?

We compare RESULTS, not SQL text, with two deliberate relaxations over strict set equality:

1. Extra predicted columns are allowed. Every gold column must match a distinct predicted
   column, and the matched columns must form the same rows. Showing a product name next to
   the gold's product ID isn't wrong.
2. Numbers match within an absolute tolerance of 0.01, so ROUND(x, 2) on either side
   doesn't matter.

Row order is only checked when the case says `order_matters`. `strict` additionally requires
the same number of columns, and is reported alongside the relaxed headline metric.
"""

from __future__ import annotations

import datetime as dt
import decimal
import itertools
from dataclasses import dataclass
from typing import Any

TOLERANCE = 0.01


@dataclass(frozen=True)
class Comparison:
    match: bool
    strict: bool
    reason: str


def normalize(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, decimal.Decimal)):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, str):
        return v.strip()
    return v


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= TOLERANCE or abs(a - b) <= 1e-9 * max(abs(a), abs(b))
    return a == b


def _key(v: Any) -> tuple:
    if v is None:
        return (0, 0)
    if isinstance(v, float):
        return (1, round(v, 2))
    return (2, str(v))


def _same(seq_a: list, seq_b: list, ordered: bool) -> bool:
    """Compare two sequences of values (or tuples of values) with numeric tolerance."""
    if len(seq_a) != len(seq_b):
        return False
    if not ordered:
        keyf = (lambda r: tuple(_key(x) for x in r)) if seq_a and isinstance(seq_a[0], tuple) else _key
        seq_a, seq_b = sorted(seq_a, key=keyf), sorted(seq_b, key=keyf)
    for x, y in zip(seq_a, seq_b, strict=True):
        if isinstance(x, tuple):
            if not all(_eq(p, q) for p, q in zip(x, y, strict=True)):
                return False
        elif not _eq(x, y):
            return False
    return True


def compare(
    gold_cols: list[str],
    gold_rows: list[list[Any]],
    pred_cols: list[str],
    pred_rows: list[list[Any]],
    order_matters: bool = False,
) -> Comparison:
    gold = [[normalize(v) for v in r] for r in gold_rows]
    pred = [[normalize(v) for v in r] for r in pred_rows]
    if len(gold) != len(pred):
        return Comparison(False, False, f"row count {len(pred)} != gold {len(gold)}")
    if not gold:
        return Comparison(True, len(pred_cols) == len(gold_cols), "both empty")

    n_gold, n_pred = len(gold_cols), len(pred_cols)
    if n_pred < n_gold:
        return Comparison(False, False, f"{n_pred} columns < gold's {n_gold}")

    gold_col = [[r[j] for r in gold] for j in range(n_gold)]
    pred_col = [[r[i] for r in pred] for i in range(n_pred)]
    candidates = [
        [i for i in range(n_pred) if _same(gold_col[j], pred_col[i], order_matters)] for j in range(n_gold)
    ]
    for j, cands in enumerate(candidates):
        if not cands:
            return Comparison(False, False, f"no predicted column matches gold column '{gold_cols[j]}'")

    gold_tuples = [tuple(r) for r in gold]
    # Try assignments of gold columns to distinct predicted columns (tiny search space).
    for assignment in itertools.product(*candidates):
        if len(set(assignment)) != n_gold:
            continue
        projected = [tuple(r[i] for i in assignment) for r in pred]
        if _same(gold_tuples, projected, order_matters):
            return Comparison(True, n_pred == n_gold, "match")
    return Comparison(False, False, "columns match individually but rows don't line up")
