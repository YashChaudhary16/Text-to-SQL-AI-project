# Evaluation protocol

How the final, reportable accuracy numbers for this project will be produced. These rules are written down
**before** the final evaluation, so they can't be adjusted to fit the results.

## Why there are two phases

During development we evaluated, diagnosed failures, fixed them and re-evaluated. That was useful, but each
round "spends" the questions it looks at: once a failure has been inspected, a fix aimed at it inflates future
scores on that question. The 63 questions used so far have all been inspected in this way. They're
**development sets**, good for finding problems and no longer able to give an unbiased score. The history of
that phase, including what went wrong in the evaluation itself, is in
[challenges-and-fixes.md](challenges-and-fixes.md) (#15, #16).

## Phase 1: development (now)

- **Sets:** `backend/evals/tpcds_gold.yaml` (41 questions) and `backend/evals/tpcds_heldout.yaml` (22).
- **Use:** run on demand to find and debug failures. Results are internal and not quoted as headline numbers.
- **Development results so far** (for the record): baseline 77.1% on the 41-question set (gpt-oss-120b);
  70.0% → 78.9% on the 22-question set after the first round of fixes. Reports are in `backend/evals/runs/`.

## Phase 2: final evaluation (after the product is complete)

### 1. Freeze

- Tag the commit (`eval-freeze`). From then on, no changes to prompts, the semantic layer, retrieval, the
  guard or any agent code until all final runs are done.
- Record the exact model IDs and settings for each configuration in the run metadata (automatic).

### 2. Fresh test set

- Write about 40 new questions in `backend/evals/tpcds_final.yaml`, using the same format and category mix as the
  development sets (easy / medium / hard / ambiguous / routing).
- Validate gold with `uv run sqlagent eval --check-gold --gold evals/tpcds_final.yaml`: every answer non-empty,
  tie-free at LIMIT cutoffs, and fitting in the row cap.
- Commit the set at the freeze, **before** any model sees it. Its failures are never used to change the system.

### 3. Run every configuration on the frozen code

| Configuration | SQL model | Intake model | Cost | Notes |
|---|---|---|---|---|
| Local | `ollama:gpt-oss:20b` | same | $0 | No limits; about 1 hour |
| Groq (free tier) | `groq:openai/gpt-oss-120b` | `groq:openai/gpt-oss-20b` | $0 | Start on a fresh daily quota (200k tokens/day for 120b) |
| Claude Haiku | `anthropic:claude-haiku-5-5` | same | ~$0.25 | Owner key with a Console spend cap |
| Claude Sonnet | `anthropic:claude-sonnet-5-5` | `anthropic:claude-haiku-5-5` | ~$2–4 | |
| Claude Opus | `anthropic:claude-opus-5-5` | `anthropic:claude-haiku-5-5` | ~$3–7 | |

- Pin each configuration's model chain, so no answer can come from a fallback model.
- Only **complete** runs (0 unscored cases) are reported. Interrupted runs are resumed with the same `--name`.
- Optional: repeat the cheap configurations (local, Haiku) three times to show run-to-run variance.

### 4. Scoring rules (fixed in advance)

- Execution accuracy on results, as implemented in `sqlagent/evals/compare.py`. Strict accuracy is reported
  alongside.
- Gold answers may be corrected **only** for documented gold bugs (wrong gold SQL, or an ambiguous question).
  Each correction is noted in the YAML and applied to **all** configurations with `--rescore`.
- No question is removed or reworded because a model got it wrong.

### 5. Report

- One table: accuracy (overall and by difficulty), routing accuracy, median latency and cost per question,
  for each configuration.
- State the sample size: with about 40 questions, one question is about 2.5 points.
- Publish the reports in `backend/evals/runs/final-*` and the table in the README.

## Checklist

- [ ] Product complete; `eval-freeze` tag created
- [ ] `tpcds_final.yaml` written, gold validated, committed at the freeze
- [ ] All five configurations run to completion
- [ ] Gold-bug audit done; any corrections noted and re-scored for all configurations
- [ ] README table updated
