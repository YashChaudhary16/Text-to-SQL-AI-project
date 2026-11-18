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
