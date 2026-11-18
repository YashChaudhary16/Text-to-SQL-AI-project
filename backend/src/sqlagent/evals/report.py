"""Summarise an evaluation run as JSON (for tooling) and Markdown (for humans)."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

# USD per million tokens (input, output). Free-tier providers cost $0. Claude list prices as
# of 2026-11; update when they change.
PRICES: dict[str, tuple[float, float]] = {
    "anthropic:claude-opus-5-5": (4.00, 20.00),
    "anthropic:claude-sonnet-5-5": (2.00, 10.00),
    "anthropic:claude-haiku-5-5": (0.10, 0.50),
}
DATA_CATEGORIES = ("easy", "medium", "hard", "ambiguous")
CATEGORY_ORDER = ("easy", "medium", "hard", "ambiguous", "schema", "out_of_scope")


def call_cost(call: dict[str, Any]) -> float:
    p_in, p_out = PRICES.get(call["model"], (0.0, 0.0))
    return (call["input_tokens"] * p_in + call["output_tokens"] * p_out) / 1e6


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}% ({n}/{d})" if d else "n/a"


def summarize(results: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    scored = [r for r in results if not r["infra_error"]]
    data = [r for r in scored if r["category"] in DATA_CATEGORIES]
    correct = [r for r in data if r["route_ok"] and r["match"]]
    strict = [r for r in data if r["route_ok"] and r["strict"]]
    calls = [c for r in scored for c in r["llm_calls"]]

    by_cat: dict[str, dict[str, Any]] = {}
    for cat in CATEGORY_ORDER:
        rows = [r for r in scored if r["category"] == cat]
        if not rows:
            continue
        ok = [r for r in rows if r["route_ok"] and r["match"] is not False]
        by_cat[cat] = {"n": len(rows), "correct": len(ok), "accuracy": len(ok) / len(rows)}

    models = Counter(c["model"] for c in calls)
    tokens_in = sum(c["input_tokens"] for c in calls)
    tokens_out = sum(c["output_tokens"] for c in calls)
    multi = [r for r in data if r["attempts"] > 1]
    return {
        "meta": meta,
        "cases_total": len(results),
        "cases_scored": len(scored),
        "infra_failures": len(results) - len(scored),
        "execution_accuracy": len(correct) / len(data) if data else None,
        "execution_accuracy_strict": len(strict) / len(data) if data else None,
        "data_cases": len(data),
        "routing_accuracy": sum(r["route_ok"] for r in scored) / len(scored) if scored else None,
        "by_category": by_cat,
        "clarifications_asked": sum(1 for r in scored if r["clarifications"]),
        "sql_retries": {
            "cases_needing_retry": len(multi),
            "rescued_by_retry": sum(1 for r in multi if r["match"]),
        },
        "latency_s": {
            "median": statistics.median(r["latency_s"] for r in scored) if scored else None,
            "p90": sorted(r["latency_s"] for r in scored)[int(0.9 * (len(scored) - 1))] if scored else None,
        },
        "tokens_per_case": {
            "input": tokens_in / len(scored) if scored else 0,
            "output": tokens_out / len(scored) if scored else 0,
        },
        "cost_per_case_usd": sum(call_cost(c) for c in calls) / len(scored) if scored else 0,
        "model_calls": dict(models),
        "fallback_events": sum(
            1 for r in scored for n in r.get("notices", []) if n.get("type") == "fallback"
        ),
        "rate_limit_waits": sum(
            1 for r in scored for n in r.get("notices", []) if n.get("type") == "waiting"
        ),
    }


def render_markdown(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    s, m = summary, summary["meta"]
    lines = [
        f"# Evaluation report: {m['name']}",
        "",
        f"- **Date:** {m['date']} · **Git commit:** `{m.get('commit', '?')}` · **Gold set:** {m['gold_set']}",
        f"- **Router models:** `{m['router_models']}`",
        f"- **SQL models:** `{m['sql_models']}`",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| **Execution accuracy** (data questions, relaxed) | **{_fmt(s['execution_accuracy'])}** "
        f"({round((s['execution_accuracy'] or 0) * s['data_cases'])}/{s['data_cases']}) |",
        f"| Execution accuracy, strict (no extra columns) | {_fmt(s['execution_accuracy_strict'])} |",
        f"| Routing accuracy (all cases) | {_fmt(s['routing_accuracy'])} |",
        f"| Cost per question | ${s['cost_per_case_usd']:.4f} |",
        f"| Median / p90 latency | {s['latency_s']['median']} s / {s['latency_s']['p90']} s |",
        f"| Tokens per question (in / out) | {s['tokens_per_case']['input']:,.0f} / "
        f"{s['tokens_per_case']['output']:,.0f} |",
        f"| SQL retries: needed / rescued | {s['sql_retries']['cases_needing_retry']} / "
        f"{s['sql_retries']['rescued_by_retry']} |",
        f"| Clarifying questions asked | {s['clarifications_asked']} |",
        f"| Provider fallbacks / rate-limit waits | {s['fallback_events']} / {s['rate_limit_waits']} |",
        f"| Cases not scored (all providers unavailable) | {s['infra_failures']} |",
        "",
        "## By category",
        "",
        "| Category | Accuracy |",
        "|---|---|",
    ]
    lines += [f"| {cat} | {_pct(v['correct'], v['n'])} |" for cat, v in s["by_category"].items()]
    lines += ["", "## Model calls", "", "| Model | Calls |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in sorted(s["model_calls"].items())]

    failures = [
        r for r in results if not r["infra_error"] and not (r["route_ok"] and r["match"] is not False)
    ]
    lines += ["", f"## Failures ({len(failures)})", ""]
    if not failures:
        lines.append("None.")
    for r in sorted(failures, key=lambda r: r["id"]):
        why = r["reason"] or ("wrong route" if not r["route_ok"] else "")
        if not r["route_ok"]:
            why = f"routed as `{r['intent']}`, expected `{r['expected_intent']}`"
        lines += [f"### {r['id']} ({r['category']}): {r['question']}", "", f"- **Why:** {why}"]
        if r["error"]:
            lines.append(f"- **Last error:** `{r['error'][:300]}`")
        if r["tables"]:
            lines.append(f"- **Tables:** {', '.join(r['tables'])}")
        if r["sql"]:
            lines += ["", "```sql", r["sql"].strip(), "```"]
        lines.append("")
    lines += [
        "## Method",
        "",
        "Each case runs through the full agent graph (intake, retrieval, SQL writing, guarded execution,",
        "self-correction) with the final summary step skipped. If the agent asks a clarifying",
        "question, the case's scripted reply is sent. The predicted result is compared with the",
        "gold query's result: every gold column must match a distinct predicted column (extra columns",
        "allowed), rows must line up, numbers within 0.01, and order is checked only where the question",
        "implies it. Strict accuracy also requires the same number of columns.",
    ]
    return "\n".join(lines) + "\n"


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def write_reports(run_dir: Path, results: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    summary = summarize(results, meta)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (run_dir / "report.md").write_text(render_markdown(summary, results))
    return summary
