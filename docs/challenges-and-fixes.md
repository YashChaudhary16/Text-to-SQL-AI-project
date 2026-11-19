# Challenges and fixes

A running log of the real problems this system ran into: what we saw, how we traced it, the root cause, what we
changed, and how we verified it. Every entry happened while building and testing this project. Nothing here is
hypothetical.

Each entry follows the same shape, which maps directly onto the STAR format (Situation, Task, Action, Result)
interviewers expect:

> **Symptom** → **How we found it** → **Root cause** → **Fix** → **Verification** → **Lesson**

## Summary

| # | Problem | Area | Found by | Status |
|---|---|---|---|---|
| 1 | Keyword search alone picked unrelated tables | Retrieval | Testing retrieval on sample questions | Fixed |
| 2 | Join bridging could route through the calendar table | Retrieval | Design review | Fixed |
| 3 | Visitor API keys would have been saved to the database | Security | Reading library source | Prevented |
| 4 | Claude structured output would fail with HTTP 400 | LLM integration | Inspecting library defaults | Prevented |
| 5 | Second database connection crashed with "configuration locked" | Database | Failing API tests | Fixed |
| 6 | Intake agent misread questions and wrote SQL in the wrong field | LLM behaviour | First live runs | Fixed |
| 7 | The auto-generated "handbook" was vague and partly misleading | Semantic layer | Writing the data dictionary | Fixed |
| 8 | "What's this data about?" got a skewed answer | Agent design | Manual testing in the UI | Fixed |
| 9 | Product rankings grouped by name could split products | SQL correctness | Manual testing, checked against the data | Fixed |
| 10 | "Returned the most" has several valid meanings | Ambiguity | Same investigation as #9 | Decided: default + stated assumption |
| 11 | Models emit markdown the UI shows as raw `**` | LLM behaviour / UI | Live runs | Partly fixed |
| 12 | Refusal text written into the rewritten-question field | LLM behaviour | Manual testing | Open (cosmetic) |
| 13 | Primary LLM provider rejected every request (403) | Operations / resilience | First run with a Groq key | Fixed (account setting); fallback kept the system up |
| 14 | UI looked frozen for minutes when the free quota ran out | Resilience / UX | Manual testing in the UI | Fixed |
| 15 | First evaluation: 77% accuracy, and the misses are silent | Evaluation | The gold-set harness | Baseline recorded; fixes next |
| 16 | Measuring whether the fixes worked, without fooling ourselves | Evaluation methodology | Held-out set + before/after runs | Partly answered; see results |
| 17 | Cloud warehouse rejected our DuckDB version | Deployment / dependencies | First upload to MotherDuck | Fixed (pinned 1.5.x, regenerated data) |
| 18 | First deploy crashed: an invisible tab in an environment variable | Deployment / configuration | Vercel runtime logs | Fixed (value re-entered; settings now strip whitespace) |
| 19 | Second crash: no home directory on serverless, and platform detection that didn't fire | Deployment / robustness | Vercel runtime logs | Fixed; startup failures now diagnosable from /api/health |

---

## 1. Keyword search alone picked unrelated tables

**Symptom.** Before connecting any LLM, we ran the schema retriever (BM25 keyword search over table and column
descriptions) on nine realistic questions:
- *"Which states have the most customers?"* returned `call_center`, `store`, `warehouse` and two returns tables
  alongside the correct `customer` and `customer_address`.
- *"Top 10 products by revenue last year"* dropped `store_sales`, the biggest sales table, entirely.

**How we found it.** We printed the chosen tables and the score of every candidate for each test question.

**Root cause.**
- The word "state" appears in the address columns of five different tables, so all of them scored well.
- The automatic join-path step then added *more* tables to connect that noise.
- The three sales tables had identical scores for "revenue", so the top-k cutoff dropped one arbitrarily.

Lexical search understands words, not meaning.

**Fix.** Two-stage schema linking:
1. The intake LLM picks tables from a one-line-per-table overview. It is good at meaning: "online" means
   `web_sales`.
2. BM25 adds only strong column-level matches, such as "TV" matching `p_channel_tv`, and only when they join
   directly to a table the LLM chose.
3. Join bridging runs only for LLM-chosen tables.

Unrelated keyword hits are now dropped instead of bridged in.

**Verification.** Regression tests in `test_retrieval.py`: the "states" question returns exactly `customer` +
`customer_address`, and the "TV promotions" question still pulls in `promotion` through column evidence.

**Lesson.** Measure each component on its own before wiring it into the agent. Cheap lexical search and an LLM
fail in *different* ways, so combining them covers both.

## 2. Join bridging could route through the calendar table

**Symptom.** While designing the "connect the chosen tables" step, we noticed that the calendar table
(`date_dim`) has a link from almost every table: sale dates, return dates, customer first-purchase dates, store
open and close dates. A shortest-path search would happily connect `customer` to `store_sales` *through*
`date_dim`, producing a join that is valid SQL but meaningless.

**How we found it.** Design review of the FK graph, before it caused a wrong answer.

**Fix.** `date_dim` and `time_dim` are excluded as bridges (`BRIDGE_EXCLUDED` in `retrieval.py`). They can still
be chosen directly, and `date_dim` is added automatically whenever a fact table is involved.

**Verification.** `test_never_bridges_through_calendar`.

**Lesson.** "Shortest path" isn't "meaningful path". Hub tables in a schema need special handling.

## 3. Visitor API keys would have been saved to the database

**Symptom.** None visible. This was caught before shipping. The plan was to pass a visitor's own Claude key
through LangGraph's per-request `config["configurable"]`, the documented place for per-run settings.

**How we found it.** Before implementing, we read the source of LangGraph's checkpoint code
(`get_checkpoint_metadata`). It copies every string value from `configurable` into checkpoint metadata, which in
production is written to Postgres.

**Fix.** Secrets travel through LangGraph's **runtime context** (`context=RunContext(...)`), which is never
persisted. In the browser the key lives only in `sessionStorage` and is sent per request; the server never
logs or caches it.

**Lesson.** When a library will store data on your behalf, read exactly *what* it stores. The obvious API was
the leaky one.

## 4. Claude structured output would fail with HTTP 400

**Symptom.** Prevented before it happened.

**How we found it.**
- We inspected `with_structured_output()` across all four LangChain providers we use. `ChatAnthropic` defaults
  to `function_calling`, which works by *forcing* the model to call a tool.
- Current Claude models reject forced tool choice with a 400 error. They also reject `temperature`, which the
  old prototype set.

**Fix.** Every provider uses `method="json_schema"` (native structured output), and no sampling parameters are
sent.

**Verification.** A test asserts that `json_schema` is always the method used.

**Lesson.** Abstraction layers hide provider differences until they don't. Check the defaults of every provider
you route to.

## 5. Second database connection crashed with "configuration locked"

**Symptom.** All API tests errored with:
`InvalidInputException: Cannot change configuration option "enable_external_access" - the configuration has
been locked`.

**How we found it.** The failure appeared only when the test suite *and* the API opened the same DuckDB file in
one process. The first connection worked; the second crashed on its own security setup.

**Root cause.** DuckDB shares **one database instance per file per process**. Our code opened a connection, then
ran `SET enable_external_access=false` and `SET lock_configuration=true`. The first connection locked the shared
instance, so the second connection's `SET` was rejected.

**Fix.** Pass the security settings as **connection config at connect time** (identical for every connection),
not as `SET` statements afterwards.

**Verification.** All API tests pass. `test_warehouse.py` proves the raw connection still refuses writes and file
access and can't unlock its settings.

**Lesson.** Know your database's process model. Settings you think are per-connection may be global.

## 6. Intake agent misread questions and wrote SQL in the wrong field

**Symptom.** The first live runs used the local `gpt-oss:20b` model. Asking the **same question** twice
(*"Top 5 product categories by revenue in 2002?"*) failed in two different ways:
- **Run 1:** classified as a *question about the data*, so it described revenue columns instead of answering.
- **Run 2:** wrote a full SQL query into the field meant for the plain-English rewritten question.

**How we found it.** Live end-to-end runs with the step-by-step CLI trace (`sqlagent ask`). All unit tests passed,
because they use a scripted fake model.

**Root cause.** The intake form's field descriptions were too loose for a small model:
- "Schema question" wasn't clearly separated from "data question".
- Nothing said the rewritten question must not be SQL.

Small models fill structured forms less reliably and vary between runs.

**Fix.**
1. Sharper field descriptions and prompt rules: requests for numbers, rankings and totals are *always* data
   questions; the rewritten question is plain English only.
2. **A code guard:** if the rewritten question starts with `SELECT`/`WITH`, fall back to the user's original
   text. Don't rely on the prompt alone.
3. A regression test for that guard.

**Verification.** Re-ran live: correct classification and a clean rewritten question. The final answer
(Music $194,490,482.39, Electronics, Home, Shoes, Women) **matched a hand-written query exactly**.

In the same run, the SQL writer's first attempt failed (`Referenced table "i" not found`). The error was fed
back automatically and attempt 2 succeeded, which showed the self-correction loop working on a real failure.

**Lesson.**
- Unit tests prove the machinery; only live runs reveal model behaviour.
- Back prompt instructions with code-level guards.
- The same input can fail differently on each run, which is why the next milestone is an evaluation harness,
  not more spot checks.

Commit `fe5d1a7`.

## 7. The auto-generated "handbook" was vague and partly misleading

**Symptom.** While generating the data dictionary from the semantic catalog, 197 of 425 column descriptions
were tautologies ("Fiscal year year.", "Gmt offset.", "Am pm."). The SQL agent reads these same descriptions.

**How we found it.** Listing every column whose description came from automatic abbreviation expansion, then
probing actual values in the warehouse.

**Root cause, and the dangerous part.** Some columns were actively misleading without proper notes:
- `d_first_dom`, `d_last_dom`, `d_same_day_ly`, `d_same_day_lq` look like dates but hold **date keys** (Julian
  day numbers such as `2452349`). An agent that compared them to a date would get wrong results silently.
- `hd_vehicle_count = -1` means *unknown*, so an average would be skewed.
- Several columns are constants or placeholders (`p_cost` is always 1000.00, many `'Unknown'` fields).

**Fix.**
- Added shared patterns for address and organisation fields, plus 140+ per-column descriptions written from the
  real value ranges.
- Taught the builder to describe primary and business keys.

The improvement flows to both the documentation and the agent's prompts. While doing this, a self-review also
caught a bug in the new business-key detection (a condition that was always true) before it shipped.

**Verification.** Rebuilt the catalog. `test_docs.py` checks every column appears in the generated reference.

**Lesson.** Your AI is only as good as the context it's given. Writing documentation for humans exposed what
the model was missing.

Commit `600e381`.

## 8. "What's this data about?" got a skewed answer

**Symptom.** In the UI, this broad question produced an overview that singled out the web-site table, and left
out inventory, promotions and the date range the data covers. It also looked at first as if the system had
written SQL for a general question.

**How we found it.**
1. **Read the trace first.** The UI's *Agent steps* showed **2 model calls**, which meant it had taken the
   schema-question route (understand → explain). No SQL was written. That ruled out a routing bug.
2. **Reproduced retrieval in isolation.** The question's only searchable words were "data" and "about", so
   retrieval returned a semi-random slice of tables (`date_dim`, `item`, `promotion`, `web_site`, `store`, …).

**Root cause.** The explainer was shown *only* the retrieved slice: it was answering "what is all this data?"
while looking through a keyhole. Retrieval is the right tool for specific questions and the wrong one for
broad ones.

**Fix.** Schema answers always receive the whole-warehouse overview (every table, plus data coverage), with
column detail added for up to 4 tables the intake agent linked.

**Verification.**
- Regression test `test_schema_question_always_sees_whole_warehouse`.
- Re-asked in the UI: the answer now covers all channels, returns, inventory, every dimension and the correct
  date ranges.

**Lesson.** Debug from the trace before forming a theory. Here the observability built into the UI ruled out
the wrong hypothesis in seconds.

Commit `60962d4`.

## 9. Product rankings grouped by name could split products

**Symptom.** *"What are the two products that are returned the most?"* produced SQL grouping by both
`i_item_id` **and** `i_product_name`. The answer looked plausible.

**How we found it.**
1. Re-computed the answer independently: 2,607 and 2,493 units, **correct**.
2. Questioned *why* it was correct, and checked whether the grouping could ever be wrong:
   `SELECT COUNT(*) … HAVING COUNT(DISTINCT i_product_name) > 1` showed **5,986 products have different names
   across their record versions**.

**Root cause.**
- TPC-DS keeps history for products: a price or name change creates a new row with the same `i_item_id`.
- Grouping by name as well splits such a product into several rows, each with a partial total, and can push it
  out of a top-N ranking.
- The top two products here had only one name each, so the answer was **right by luck**.

**Fix.** A new rule in the semantic layer (which the SQL agent reads on every query) and in the data dictionary:
group products by `i_item_id` only, and display a name with `ANY_VALUE(i_product_name)`.

**Verification.** Catalog rebuilt; the rule now appears in the SQL writer's prompt. A golden-question test
for this belongs in the upcoming evaluation set.

**Lesson.** A correct answer isn't proof of a correct query. Verify the *logic* against the data's quirks, not
just the output.

Commit `1775f35`.

## 10. "Returned the most" has several valid meanings

**Symptom.** In the same investigation as #9, the answer depends entirely on what "returned the most" means:

| Interpretation | Top product |
|---|---|
| Most units returned (the agent's default) | `…PKGDAAAA`, 2,607 units |
| Most return transactions | `…AKMBAAAA`, 91 returns: a different product |
| Highest return rate (returned ÷ sold) | likely different again |

The agent picked "units" and stated the assumption. That's defensible, but the interpretations disagree
completely.

**Why it matters.** This is the system's core trade-off. Ask too often and users get annoyed; ask too rarely and
they get confident, wrong-for-them answers.

**Options under consideration:**
- (a) keep defaulting with a stated assumption
- (b) clarify whenever interpretations give different top results
- (c) answer with the default and mention the alternative's result in one line

**Decision (2026-11-17).** Keep (a): answer with a sensible default and state the assumption clearly. It
keeps the conversation fast, and the stated assumption lets users re-ask. Revisit once the evaluation harness
can measure how often defaults mislead; option (c) is the likely next step.

## 11. Models emit markdown the UI shows as raw `**`

**Symptom.** Answers contained `**bold**`, which the UI shows literally.

**Fix so far.** Prompts instruct plain text. The SQL-answer path complies; the 20B model still uses bold in
schema answers.

**Planned fix.** Render basic markdown (bold, bullet lists) safely in the UI instead of depending on the model
obeying a formatting instruction.

**Lesson.** Don't make correctness depend on an LLM following a style rule. Make the consumer tolerant.

## 12. Refusal text written into the rewritten-question field (open, cosmetic)

**Symptom.** For *"What is my name?"*, routing was correct (out of scope, no SQL, one model call), but the trace
showed the rewritten question as *"I'm sorry, but I can't help with that."*

**Impact.** Cosmetic only; the user-facing answer is the fixed out-of-scope message. A guard like the one in #6
could fall back to the original text when intent is out-of-scope.

## 13. Primary LLM provider rejected every request (403)

**Symptom.** After adding a Groq API key, every Groq call failed with
`403 model_permission_blocked_org`. The key itself was valid: listing models worked, but no chat model could be
called.

**How we found it.**
1. **Fallback warnings in the trace.** The `sqlagent ask` trace showed a warning per failed Groq call, then
   every step served by `ollama:gpt-oss:20b`.
2. **Narrowed it down.** We listed the models the key could see (all present), then made a 5-token test call per
   model. Every chat model was blocked, so the problem was account permissions, not one model.
3. **Two layers of permissions.** After the organisation-level allow-list was changed, the error became
   `model_permission_blocked_project`: a second, project-level allow-list. Then a re-test showed the
   project change hadn't been saved yet.

**Root cause.** Groq accounts gate models through two allow-lists (organisation and project). By default this
project only allowed Groq's own `compound` models.

**Fix.** Allowed `openai/gpt-oss-120b` and `openai/gpt-oss-20b` at both levels. No code change was needed.

**What went right.** Throughout the outage, the provider fallback chain (§7 of the architecture) moved every call
to the local Ollama model. Users would have seen only a slower answer, never an error, and the answers stayed
correct (Music $194,490,482.39, …). Once fixed, the same question took **5.3 s instead of 2 min 15 s**.

**Lesson.**
- Build the fallback path before you need it, and make it observable: the per-call model attribution showed
  the outage immediately.
- Diagnose with the smallest possible probe (list models, then a 5-token call) before touching code.

## 14. UI looked frozen for minutes when the free quota ran out

**Symptom.** Asking a second question soon after the first left the UI on "Writing SQL…" for about 3 minutes
before an answer appeared.

**How we found it.**
1. **Read the server log.** Groq returned `429 Too Many Requests` on `gpt-oss-120b`.
2. **Then a silent wait.** The line `Retrying request … in 39.000000 seconds` came from Groq's own SDK. After
   that retry also failed, our fallback sent the call to the local Ollama model, which takes about 2 minutes.
3. **Measured the prompts.** The SQL-writing prompt was about 4,300 tokens, against a free limit of **8,000
   tokens per minute per model**. So two questions inside one minute exhaust the quota.

**Root cause.** Three things stacked up:
- **A large prompt against a small per-minute quota.**
- **Hidden retries inside the provider SDK** (honouring a 39 s `retry-after`) that our code couldn't see.
- **A fallback order that preferred a slow local model** over briefly waiting for a fast hosted one.

None of it was visible in the UI.

**Fix.**
1. **Fail fast:** SDK retries disabled (`max_retries=0`); our own chain decides what happens next.
2. **More independent quota:** Groq limits are per model, so `gpt-oss-20b` became the second SQL option.
3. **Three-phase fallback:** hosted models first; if all are rate-limited and the provider's wait is short
   (≤ 20 s), wait once and retry them; only then the slow local model.
4. **Visible:** each switch and wait is streamed as a `notice` event, shown in the UI timeline
   ("Model busy, switching…", "Free-tier limit reached, retrying in 8s") and in the CLI.
5. **Smaller prompts:** foreign keys render compactly and repeated per-channel column descriptions are printed
   once (−12% tokens). The Ollama context window was set explicitly so long prompts aren't truncated.

**Verification.**
- 7 new tests (fail-fast config, notifications, retry-after parsing, wait-then-retry before the local model,
  long waits skipped, still-limited case).
- Live: three questions back to back. The third hit the limit on both Groq models, **waited 8 s and succeeded
  on gpt-oss-120b in 11.9 s total**, instead of about 3 minutes.

**Lesson.**
- Retries are policy: own them in one place rather than letting each SDK retry silently.
- On free tiers, *latency* comes from capacity limits, so measure prompt size against quota.
- A short, visible wait beats a long invisible fallback.

## 15. First evaluation: 77% accuracy, and the misses are silent

**Context.** We built a 41-question gold set with hand-verified SQL and a harness that runs each question
through the full agent graph and compares *results*, not SQL text. Baseline: gpt-oss-120b for SQL and
gpt-oss-20b for intake, both on Groq's free tier.

**Result.** **77.1% execution accuracy** (27/35 data questions), with routing 100% correct on all 41 cases.
Median latency 3.3 s, $0 per question.

**The finding that matters.** None of the 8 failures produced a SQL error, so the self-correction loop
(which feeds *errors* back) never had a chance to help. Every miss was valid SQL with the wrong meaning:

| Failure type | Cases | Example |
|---|---|---|
| Ignored a metric definition from the semantic layer | e05, h06, m04 | Counted order *lines* (142,182) instead of distinct orders (11,859); used net revenue for "gross sales" |
| Ignored a modelling rule | m06 | Used the customer's *current* household profile instead of the profile at time of purchase |
| Linked a business term to the wrong column | e07, h07, h08 | "Preferred customers" became credit rating 'A' (the flag column exists); "items managed by manager 1" became a *store* manager |
| Data hygiene | h01 | Items with no category produced two junk rows |

**Debugging the evaluation itself.** Before trusting the number we audited the failures, and found problems
in our own test set:
- **An impossible question.** No promotion ran on TV at this data size, so "TV promotions" had an *empty*
  correct answer, which any broken query returning nothing would match. Replaced it, and added a gold check
  that rejects empty answers.
- **A scorer false negative.** A correct quarterly answer labelled quarters `'2002Q1'` while gold used `1`.
  Gold now compares only the metric, in quarter order.
- **A wrong gold answer.** Gold counted "no category" as a product category.
- **An ambiguous question.** "Units shipped in 2002" can mean ship date or order date. The agent's reading was
  reasonable, so the question was reworded.

The rule we followed: change the gold set only when the agent was *right* but scored wrong, or the test item
itself was flawed. Never reword a question to suit the agent. Each change is noted in `tpcds_gold.yaml`.
A `--rescore` option re-checks stored predictions against corrected gold without calling the LLM again.

**Operational issues during the run.** 7 Groq requests hung until our 60 s timeout, and 3 hit rate-limit waits
over 20 s. The runner records these as *infrastructure* failures, not wrong answers, and resuming the same run
retries only those.

**Lesson.**
- Evaluate the evaluator: 4 of the first-run "failures" were test-set bugs.
- Error-driven retries only fix errors. Semantic mistakes need a different mechanism: stronger grounding in
  the metric definitions, disambiguation of overloaded terms like "manager", or a critic step that checks the
  SQL against the question.
- Separate infrastructure failures from model failures, or the accuracy number lies.

Report: `backend/evals/runs/2026-11-17-groq-gpt-oss-120b/report.md`.

## 16. Measuring whether the fixes worked, without fooling ourselves

**The risk.** Tuning prompts against the same 35 questions you report on inflates the score: the fixes can
memorise the test rather than improve the system.

**What we did.**
1. **Held-out set first.** We wrote 22 new questions and committed them (`2ccce55`) *before* making any fix.
   The rule: they're never used to choose fixes, and gold may change only for genuine gold bugs.
2. **Fixes from the dev set only, kept general.** While writing them we caught ourselves putting dev answers
   into prompt examples ("manager 28 → `i_manager_id`") and replaced them with neutral examples. Specific
   knowledge went into the semantic layer, where a data team would document it anyway.
3. **"Before" on the old code.** The held-out baseline was interrupted by a quota limit after the fixes were
   committed. We resumed it from a **git worktree at the pre-fix commit**, so the "before" number couldn't be
   contaminated.
4. **The same conditions on both sides.** When Groq's free daily cap (200k tokens for gpt-oss-120b) ran out, we
   ran a second before/after comparison on a local model (Ollama). The old code never set Ollama's context
   window, so we set it server-side (16k) for both versions instead of letting the newer code alone benefit.
5. **Per-question flips, not just totals.**

**Results.**

| SQL model | Set | Before | After | Fixed / broken |
|---|---|---|---|---|
| gpt-oss-120b | held-out | 70.0% (14/20) | 78.9% (15/19) | 2 / 0 |
| gpt-oss-20b (local) | held-out | 80.0% | 75.0% | 3 / 4 |
| gpt-oss-20b (local) | dev | 71.4% | 74.3% | 4 / 3 |

The large model improved on unseen questions with nothing broken. The small model churned with no net effect.
Plausibly it can't use the richer instructions, and its run-to-run variance swamps a one-question difference.
The Groq dev "after" run is pending quota.

**Held-out failures we did NOT fix** (audited only for gold bugs; none found):
- Counting 60 web-page *rows* instead of 30 versioned pages.
- "Preferred customers" mapped to a credit rating again, so the dev-set glossary fix didn't transfer to new
  wording.
- An off-by-one from counting the NULL "customer" in a set difference.

These are the next round's targets, to be evaluated on fresh held-out questions.

**Harness problems found along the way.**
- **A dead quota looked like a result.** A run started on an exhausted quota raced through 33 cases in about a
  second each and printed "100%" (2 scored questions). The runner now stops after 3 consecutive provider
  outages, and partial reports carry a warning.
- **Interactive and batch use need different waits.** Batch evals can wait out rate limits that an interactive
  user shouldn't (`--max-wait`, default 90 s for evals vs 20 s in the app).

**Lesson.**
- A before/after claim needs held-out data, identical conditions, and per-item flips.
- Report the result you got, including "no effect on the small model". It's more credible than a number that
  only goes up.
- With 20-question sets, one question is 5 points: say so.

## 17. Cloud warehouse rejected our DuckDB version

**Symptom.** The first upload to MotherDuck failed: *"Your DuckDB version (v2.0.0) is not yet supported by
MotherDuck. The latest supported version is v1.5.6."*

**Root cause.** Dependencies were declared as `duckdb>=2.0.0`, the newest release when the project started. A
managed service built on DuckDB lags behind new major versions.

**Fix and its ripple effects.**
1. **Pinned `duckdb>=1.5.6,<1.6`.**
2. **Regenerated the local data file.** The old file was written by DuckDB 2.0.
3. **Compared the rebuilt catalog field by field** before trusting it. The data generator differs slightly
   between versions:
   - most tables keep the same row counts but have slightly different values (e.g. 47,377 preferred customers
     instead of 47,428)
   - the `reason` table has 35 rows instead of 75; the 2.0 build had added placeholder reasons like
     "reason 68"
4. **Re-validated every gold question on the new data.** One top-10 question now had a tie at 10th place, so
   it became a tie-free top 5 (noted in the YAML).
5. **Rechecked the facts the docs quote** (6 real stores, 51 customer states, the "ordred" typo, NULL rates);
   all still hold.

Development-evaluation results and debugging stories earlier in this log were produced on the DuckDB 2.0
data, so their specific numbers belong to that dataset. The final evaluation runs on the pinned version.

**Hardening verified against MotherDuck.** Running with `saas_mode=true` blocked reading local files,
installing extensions and attaching databases. A `CREATE TABLE` *succeeded* with the full-access token, though
(the test table was dropped straight away). Only the **read-scaling token** makes writes impossible at the
database level, which confirms the production rule: the deployed app gets the read-only token, and the
full-access token is used only by `upload-motherduck`.

**Lesson.**
- Pin dependencies to what your managed services support, not to the newest release.
- When regenerated data changes, re-validate everything derived from it: gold answers, documented facts, ties.
- Test every security layer against the real service, not only locally.

## 18. First deploy crashed: an invisible tab in an environment variable

**Symptom.** The first Vercel deployment returned HTTP 500 `FUNCTION_INVOCATION_FAILED` on every request,
with a 100% error rate.

**How we found it.**
1. **Separate cause from side effect in the runtime logs.** The logs showed two errors. `cannot import name
   'vc_handler'` came from Vercel's wrapper and was a *consequence*. The real one was "Application startup
   failed" in our FastAPI `lifespan`.
2. **Read the final line of the full traceback:**
   `IOException: Cannot open database "/var/task/<TAB>md:tpcds?saas_mode=true" in read-only mode`.

**Root cause.** The `WAREHOUSE_URL` value pasted into Vercel began with a tab character. The code checks
`url.startswith("md:")` to choose MotherDuck, so a leading tab silently sent it down the local-file branch.

**Fix.**
- Re-entered the variable by typing it rather than pasting.
- In code, settings now strip surrounding whitespace from every value (`str_strip_whitespace=True`), with a
  regression test.

**Lesson.**
- In logs, find the *first* failure; later errors are often fallout.
- Treat configuration as untrusted input: normalise it at the boundary, because copy-paste adds invisible
  characters.

**Also learned while fixing it.** The fix branch was first created from a stale local copy of `main` (PR #2's
merge hadn't been fetched). That surfaced *before* pushing because a docs edit failed to apply. Always
`git fetch` before branching from `origin/main`.

## 19. Second crash: no home directory on serverless, and platform detection that didn't fire

**Symptom.** After fixing #18, the app got further (it now connected to MotherDuck) but still crashed at
startup: `IOException: Can't find the home directory at ''`.

**Root cause.**
- DuckDB keeps extensions and temp files under `$HOME`, and Vercel's functions have no usable home directory.
- The code was meant to handle this by checking for the `VERCEL` environment variable, but that variable
  isn't exposed to the running function, so the fallback never activated.

**Fix, attempt 1 (didn't work).** We detected the condition rather than the platform (`$HOME` missing or not
writable) and passed `/tmp/duckdb` through DuckDB's `home_directory` setting.

The new health diagnostics (below) showed the *same* error on the preview deployment. So we **reproduced
Vercel locally** by running with `HOME` unset and tested each candidate:

| With `HOME` unset | Result |
|---|---|
| No fix | ❌ "Can't find the home directory" |
| DuckDB `home_directory` setting | ❌ same error |
| `HOME` environment variable set | ✅ works |

**Root cause, refined.** The MotherDuck extension reads `$HOME` from the process environment directly and ignores
DuckDB's setting.

**Fix, attempt 2.**
1. **Detect the condition, not the platform.** If `$HOME` is unusable, point it at a writable `/tmp/duckdb` and
   create the directories before connecting.
2. **Verify before deploying.** Checked with the real `Warehouse` class against MotherDuck with `HOME` unset,
   plus a unit test.
2. **Degrade instead of dying.** Startup errors no longer kill the function. `/api/health` reports which
   startup step failed (`catalog`, `warehouse` or `checkpointer`) with the error message, redacting anything
   that looks like a credential. Chat and schema endpoints return a clear 503. A broken deployment can now be
   diagnosed with one request, instead of someone copying tracebacks out of the logs.

**A second problem found along the way.** The API tests build the real app, whose settings load the developer's
`.env`. Once Neon credentials were added, every API test quietly connected to the real database, and the
suite hung on a flaky network. API tests now pin isolated offline settings. The suite went from hanging to
2.3 s.

**Lesson.**
- Check for the condition you need (a writable home directory), not a proxy for it (an environment variable
  that may or may not be set).
- When a fix doesn't take, reproduce the environment locally and test the candidates side by side. Don't
  guess again in production.
- Make failures observable from the outside.
- Tests must be hermetic: real credentials appearing in `.env` shouldn't change what tests do.

## 20. Pinning a Claude model for evaluation silently ran Opus

**Symptom.** None visible yet: this was caught before any Claude evaluation ran. Had it shipped, the "Claude
Haiku" and "Claude Sonnet" rows of the final accuracy-vs-cost table would both have been Opus results with the
wrong labels.

**How we found it.** While setting up the Anthropic key for the final evaluation, we read the code path an eval
run takes, then asked the model chain directly which models it would use with
`--sql-models anthropic:claude-haiku-5-5`:

```
router ['anthropic:claude-haiku-5-5']
sql    ['anthropic:claude-opus-5-5']     <- should be haiku
```

**Root cause.**
- Entries in `SQL_MODELS` / `ROUTER_MODELS` were always built with no API key, because Claude was only
  expected to come in through the special BYO / escalation / owner slots. A pinned `anthropic:` entry was
  therefore skipped as "no credentials".
- The owner slot then added Claude as the last resort, using `CLAUDE_SQL_MODEL`, which defaults to Opus.
- The router looked correct only by coincidence: its default Claude model is already Haiku.
- Run metadata records the *requested* chain, so the report would have been mislabelled. Per-call records
  store the serving model, so the error would have been detectable, but only if someone checked.

**Fix.** If a chain lists an `anthropic:` model itself, it is used exactly as written: those entries get the
available key (the visitor's own, or the owner's if `OWNER_ANTHROPIC_ENABLED=true`), and none of the implicit
Claude slots are added. That also stops escalation or a fallback from switching a "Haiku" run to Opus.
Production chains contain no Claude entries, so the live demo's behaviour is unchanged.

**Verification.** Two regression tests in `test_llm.py`: a pinned chain serves exactly the pinned model (also
under escalation and with a BYO key), and a pinned chain still needs the owner key to be enabled. Against the
old code the first test fails with exactly the symptom (`['anthropic:claude-opus-5-5']`); the full suite passes
with the fix.

**Lesson.**
- Check what the system *actually* does with an evaluation configuration before spending money on it. A run
  that quietly measures a different model is worse than no run.
- When a value is chosen from several places (the configured chain or a built-in fallback), test the
  combination you're about to rely on, not just each path on its own.

---

## Patterns across all of these

1. **Observability made debugging fast.** The live agent trace (route, tables, SQL, retries, model per step)
   ruled out wrong hypotheses immediately in #6 and #8.
2. **Verify against ground truth.** Every live answer was checked with a hand-written query. That's how #9 was
   found despite a correct-looking answer.
3. **Prompts are suggestions; code is a guarantee.** Wherever correctness or safety matters, there's a
   code-level guard behind the prompt: #6, the SQL guard, read-only connections, #11.
4. **Read the source of what you depend on.** #3, #4 and #5 were all library or engine behaviours that the
   obvious usage got wrong.
5. **Fix at the source of truth.** Knowledge fixes (#7, #9) go into the semantic layer, which feeds both the
   agent and the docs, so one change fixes both.
6. **Every fix gets a regression test,** except model-behaviour issues that need the evaluation harness.
   Building that harness is the next milestone for exactly this reason.

## How to tell these stories in an interview

Pick 2–3 entries that cover different skills, for example **#8** (debugging with observability), **#9**
(rigour: correct output, wrong logic) and **#3** (security judgement). For each, aim for about 60 seconds:

1. **Situation:** "Users ask plain-English questions; a team of LLM agents writes SQL over a 425-column
   warehouse."
2. **Symptom:** one concrete sentence, e.g. "A broad question got an answer that ignored half the warehouse."
3. **Investigation:** what you looked at first and what it ruled out.
4. **Root cause:** one sentence a non-specialist would understand ("it was looking through a keyhole").
5. **Fix and verification:** the change, plus the test that keeps it fixed.
6. **Lesson:** the general principle you'd apply elsewhere.

Keep adding entries as new issues appear. A log that grows with the project is far more convincing than one
written from memory at the end.
