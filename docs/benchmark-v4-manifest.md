# Benchmark v4 manifest — work packages with multi-person truth

- Built: 2026-08-14, worker session on `agent/benchmark-v4`
- Order: `docs/work-orders/benchmark-v4.md` (backlog `G12`)
- Manifest: `data/eval/benchmark_manifest.v4.jsonl`, version `tawos-v1.1-benchmark-v4`,
  seed `20260814`
- Builder: `src/capgraph/eval/packages.py` (offline) + `src/capgraph/eval/rewrite.py`
  (the one paid step)

This document is the record of *how the instrument was built*. The numbers it produced
are in `docs/eval-results.md` under the benchmark-v4 marker.

A v1 case was one issue, asked at its creation time, whose truth was the one person who
resolved it. A v4 case is one **work package** — a sprint — asked at the package's
recorded start date, whose brief is written from the issues planned into it *before* it
started, and whose truth is **everyone** who resolved any of its issues from that moment
on.

---

## 1. Grouping unit: verified, then chosen

The order required epic→child hierarchy to be verified before sprints were assumed. The
backlog recorded it as "not confirmed", on the evidence that `Issue_Link`'s observed
relationship types are semantic. That evidence is correct and the conclusion drawn from
it was wrong: **TAWOS does carry epic hierarchy, in the change log rather than in
`Issue_Link`.** Sprints were still chosen, for a different reason. Both halves are
recorded here.

### 1.1 `Issue` has no hierarchy column

```sql
DESCRIBE `Issue`;
```

The v1.1 `Issue` table has no `Parent_ID`, no `Epic_Link`, and no `Epic_ID`. Its only
grouping foreign key is `Sprint_ID`. Sub-tasks (3,767 in FAB, 1,232 in TIMOB) therefore
have no recorded parent either.

### 1.2 `Issue_Link` is semantic, as the backlog said

```sql
SELECT il.`Name`, il.`Direction`, COUNT(*)
FROM `Issue_Link` il
JOIN `Issue` i ON i.`ID` = il.`Issue_ID`
JOIN `Project` p ON p.`ID` = i.`Project_ID`
WHERE p.`Project_Key` IN ('MESOS','FAB','TIMOB','DM','EVG')
GROUP BY il.`Name`, il.`Direction` ORDER BY 3 DESC;
```

Every link is stored twice (once per direction). The largest types are `Relates` 9,267,
`Blocks` 3,579, `Duplicate` 2,675, `Reference` 1,529, `Gantt: start-finish` 1,222,
`Cloners` 974, `Blocker` 702, `Related` 603, `Depends` 529. The only containment-shaped
names are `Child-Issue` (348), `Containment` (186), `Includes` (134), `Incorporates`
(60), `Container` (46) and `Parent Feature` (1) — and restricted to links with an
`Epic`-typed endpoint they total **under 60 links across the whole slice**. No
epic-parent link type exists.

### 1.3 The change log *does* record epic membership — with timestamps

```sql
SELECT LOWER(TRIM(cl.`Field`)), COUNT(*)
FROM `Change_Log` cl
JOIN `Issue` i ON i.`ID` = cl.`Issue_ID`
JOIN `Project` p ON p.`ID` = i.`Project_ID`
WHERE p.`Project_Key` IN ('MESOS','FAB','TIMOB','DM','EVG')
  AND LOWER(TRIM(cl.`Field`)) IN ('epic link','epic child','sprint')
GROUP BY 1;
```

| Change-log field | Rows in the slice | What it records |
|---|---:|---|
| `epic link` | 39,502 | on the **child**: `To_String` = the epic's issue key, dated |
| `epic child` | 46,779 | on the **epic**: the child that was added, dated |
| `sprint` | 53,595 | on the issue: the full set of sprint ids after the change, dated |

Reconstructed from the child side: **27,296 issues have a final epic**, across **2,620
distinct epics** (2,031 with ≥3 children, 1,527 with ≥5). 2,439 of those epic keys
resolve to an `Epic`-typed issue in the Stage 0 export. **Not one epic-link row is
undated**, so epic membership is reconstructible as of any point in time.

So the hierarchy exists and is usable. It was still not chosen.

### 1.4 Why sprints, not epics

The order's requirement 2 is that a package has *one as-of time at its start boundary*,
with the brief written from what was already in the package at that moment. An epic
fails that test on this data:

| Question | Epics | Sprints |
|---|---|---|
| Is there a start boundary? | No recorded one. The epic issue's creation date is the only candidate. | `Sprint.Start_Date`, recorded. |
| Is there work in the package *before* that boundary? | Almost never: **4,817 of 24,979** child links (19%) are to a child created before its epic, and the **median epic has 0** of them. | Yes: the median selected package has 15 issues created and planned in before the sprint starts. |
| Could the package's own text carry the brief instead? | Rarely. Of the 732 post-cutoff epics with children, only **88** have ≥300 characters of their own creation-time text. | N/A — the brief is the planned work itself. |
| Does it stratify across projects? | No: **668 of 732** post-cutoff epics with children are DM. | 4 of 5 projects (see §1.5). |

An epic-grouped benchmark would therefore be ~88 cases, ~90% of them from one project,
with the brief being a single Epic ticket's description — which is the single-ticket
brief `G12` exists to replace. A sprint is a bounded body of work, done by a real team,
with a recorded start and end; the as-of time comes for free.

**Decision: sprints.** Epics are recorded here as *available and timestamped* — a real
correction to the backlog — and are the obvious unit to revisit if the corpus ever
changes. Version/`Issue_Link`-cluster grouping was not needed and was not built.

### 1.5 Two surprises in the sprint data, both recorded

1. **`Activated_Date` is NULL for all 1,066 sprints in the slice.** The backlog listed
   it as present. `Start_Date` is present for every CLOSED and ACTIVE sprint (only the
   10 FUTURE sprints have no dates at all), so **`Start_Date` is the as-of time**, not
   activation. `Complete_Date` and `End_Date` are recorded and are carried as the
   package's end boundary for audit.
2. **EVG has no sprints at all** — zero `Sprint` rows and zero issues with a
   `Sprint_ID`. Benchmark v4 therefore covers **4 of the 5 configured projects** (DM,
   MESOS, TIMOB, FAB). This is one reason v4 numbers are not comparable to v1-v3's: the
   project mix is different, and EVG had the smallest roster (21) and so the easiest
   Hit@10.

A third quirk that shapes the code: **`Sprint.JiraID` is unique only *within* a
project** (275 ids are reused across projects), so every join between the change log and
the sprint calendar carries the project key with it, and a package is identified as
`<project>:sprint:<jira id>`.

---

## 2. What Stage 0 now exports

`src/capgraph/pipeline/stage0_load.py` gained the grouping key and the effort columns.
The re-export was verified to be **identical to the previous one on every pre-existing
column** (82,703 tickets, 316 eligible people, 5 projects), so the frozen v1-v3
artifacts and the loaded graph remain valid.

| Artifact | Contents |
|---|---|
| `data/parquet/sprints.parquet` | 1,066 sprints: surrogate id, Jira id, project, name, state, start/end/activated/complete dates |
| `data/parquet/sprint_membership.parquet` | 66,633 rows: **48,029 dated change-log joins** + 18,604 undated final-snapshot memberships, each with its provenance |
| `tickets.parquet` (new columns) | `sprint_id` + `sprint_provenance` (final snapshot, audit only), `story_point`, `timespent`, `in_progress_minutes`, `total_effort_minutes` (verbatim TAWOS units, nothing consumes them yet — the only capacity signal this dataset offers, for `G10`) |

**The sprint change log stores the whole membership set, not a delta** (`From_Value`
`"599, 600"` → `To_Value` `"599, 600, 602"`), so a join is the set difference between
the two sides and carries that transition's timestamp. The final `Issue.Sprint_ID`
pointer is *not* a substitute: it has no recorded timing, so it can never establish that
something was planned before a sprint began.

Both new ticket-level fields are **redacted from the Stage 1 evidence view** exactly as
the final assignee and the unversioned component names are (`sprint_provenance` becomes
`redacted_unversioned_final_snapshot`). Nothing that feeds a profile, an embedding, or a
baseline can see them.

---

## 3. The package, precisely

For a sprint *S* in project *P* with recorded start *T*:

| Part | Rule |
|---|---|
| **as-of time** | *T* = `Sprint.Start_Date`. `query_time_source` = `sprint_start`. |
| **planned issues** | issues with a **dated** change-log join to *S* at a time strictly before *T*. |
| **brief material** | planned issues that were also *created* strictly before *T*, are not temporally excluded by Stage 0, and whose creation-time summary/description provenance is safe (`change_log_from_*` or `snapshot_no_recorded_change`). |
| **brief** | those issues' creation-time text, sanitized, in creation order, capped at 30 issues and 8,000 characters (whole issues only; what was left out is counted per case), then rewritten (§5). |
| **package issues** | every issue ever joined to *S* by a dated transition, **plus** its final-snapshot members. |
| **truth** | the Stage 0 evidence assignee — reconstructed at the safe resolution boundary — of every package issue resolved at or after *T*, filtered to the frozen roster. |
| **end boundary** | `Complete_Date`, else `End_Date`. Audit metadata; nothing reads it. |

Two asymmetries are deliberate. Membership evidence for the **brief** must be dated,
because the claim being made is about timing; membership evidence for **truth** need not
be, because the claim being made is only "this work belonged to the package". And an
issue resolved *before* the package started is not the package's work: its resolver does
not become truth (135 such issues across the selected cases).

---

## 4. Leakage guards

Every v1 guard (`docs/work-orders/stage7-benchmark.md`) applies unchanged. What each one
means for a package:

1. **No post-as-of text.** Brief material must be both created and joined before the
   as-of time. Issues that joined later, or that are known only from the undated final
   snapshot, contribute to truth and never to the brief. Tested:
   `test_brief_holds_only_material_planned_and_created_before_the_as_of_time`.
2. **No mutable text.** Creation-time summary/description reconstructed from the change
   log; rows whose provenance is unsafe are dropped from the brief, as are rows Stage 0
   flagged with a `temporal_exclusion_reason` (moved project/key, edited resolution).
3. **Sanitizer.** Every issue block passes through `LeakageSanitizer` before it is
   joined into the brief, the rewriter sees only that sanitized text, and its answer is
   sanitized again and re-checked. A case whose brief still trips the guard is excluded
   rather than patched.
4. **Truth eligibility.** Every truth id must be in the project's roster frozen at the
   holdout cutoff, which by construction means ≥15 pre-cutoff resolved tickets *and* a
   retained Stage 1 profile bucket. Verified on the built manifest: every truth id is in
   `people.parquet` and inside its case's roster.
5. **Recency at the as-of time.** Unchanged from v1 — the harness recomputes decay from
   each edge's `last_used` at the case's as-of time; the graph's stored decay is frozen
   at the cutoff and is never read.
6. **Roster restriction.** Unchanged from v1 — every system ranks only the case's frozen
   roster, enforced in the harness and again at scoring time.

Two honest limits, neither new in v4:

- **Free-text personal names in ticket descriptions are not removable.** The sanitizer
  strips project-qualified ids, pseudonyms, mentions and e-mail addresses — the things
  that identify a *roster member*. A description that says "Robert's input" survives, in
  v1 exactly as in v4. It cannot leak the label, because no system in this pipeline can
  resolve a free-text first name to a `person_id`.
- **Package membership is partly a final-snapshot fact.** Which issues ended up in a
  sprint is known only after the fact; what v4 guarantees is that the *brief text* and
  the *timing claim* use only pre-as-of information.

---

## 5. The brief rewrite

| Setting | Value |
|---|---|
| Model | `openai/gpt-5.6-luna` (the owner's instruction: the cheap model) |
| Prompt | `prompts/brief_rewrite.md` |
| Stage | `bench4_rewrite` |
| Input | the sanitized pre-as-of package text and the project domain. Nothing else — no assignee, no comment, no resolution, no post-as-of issue, no truth |
| Output guard | sanitized, re-checked against the guard, minimum 200 characters |
| Checkpoint | `data/eval/v4/rewrites.jsonl`, one record per package with the model, prompt digest and a digest of the exact input text |

**Determinism.** The manifest freezes the rewrite. Rebuilding it reads the checkpoint and
makes no model call; a rewrite whose stored `input_digest` no longer matches the package
text it claims to describe is treated as absent rather than reused. Tested:
`test_rebuilding_from_the_frozen_manifest_is_deterministic`,
`test_a_rewrite_of_different_source_text_is_refused_as_stale`.

**Both variants are kept.** `brief_raw` (the un-rewritten package text) stays in every
manifest entry, and a run can be pointed at it with `--briefs raw`. The validation split
was run both ways so the rewrite's own effect on the numbers is measured rather than
assumed — see the rewrite-effect table in `docs/eval-results.md`.

**Headcount stays untestable, deliberately.** The rewriter is instructed *not* to state a
team size, because the only available source for a number would be the truth set. `G8`
therefore remains unmeasured; what v4 supplies instead is the truth-set-size distribution
and a real `Recall@K`.

---

## 6. Manifest discipline

- **Deterministic**: fixed seed `20260814`, candidates iterated in `(project, sprint id)`
  order, project-stratified round-robin selection, project-stratified split assignment.
  The manifest version is part of every sampling hash, so v4 never draws v1's sample.
- **Auditable**: every candidate sprint is written to the manifest, selected or not, with
  its exclusion reason, its as-of time, its roster, its truth ids, its brief issue keys,
  and how much of the package the brief caps left out.
- **Exposure budget**: tuning and every exploratory comparison happen on the validation
  split, which may be re-run. **The test split is run once per engine configuration**,
  in the rewritten brief variant, with the configuration digest recorded in each
  checkpoint. In this study that means **one exposure so far**: `v3frozen`. The
  `v2frozen` test run did not fit the authorized ceiling (see §6.4) and was escalated
  rather than run; the test split therefore still has an unused exposure for it, and
  nothing else may consume that exposure without the orchestrator's decision.
- **The v1 manifest is untouched.** No v1-manifest run was made; `data/eval/runs/`,
  `data/eval/v2/`, `data/eval/v3/` were not written to. The retired 120-case v1 test
  split stays retired.
- **Determinism is relative to the Stage 0 export it was built from.** Rebuilding from
  the same `tickets.parquet` reproduces the manifest byte for byte with no model call.
  A *different* Stage 0 export changes the brief text — the open `agent/improvement-wave1`
  branch changes description truncation (`G1`), which is exactly such a change — and the
  builder will then correctly refuse the frozen rewrites as stale rather than pair a
  brief with material it was not written from. If that branch merges and Stage 0 is
  re-exported, the manifest must be rebuilt and re-rewritten (≈$0.09) or kept as built;
  it must not be silently re-used.

### 6.1 What the data supported

Every sprint with at least one resolvable membership row is a candidate and is written
to the manifest. Reasons are recorded in priority order — the first rule a candidate
fails is the one recorded — so a sprint counted as pre-cutoff may also have failed
later rules.

| Outcome | Sprints |
|---|---:|
| candidates examined | 1,061 |
| `sprint_start_not_post_cutoff` (started before the 2019-01-01 holdout cutoff) | 760 |
| `nothing_planned_before_start` (membership only ever recorded after the start) | 115 |
| `too_few_brief_issues` (< 3 usable pre-as-of issues) | 18 |
| `sprint_start_missing` (FUTURE sprints, no dates at all) | 10 |
| `no_truth_resolver` (nothing in the package resolved with a safe owner) | 1 |
| `sampled_out` (eligible, not drawn — 157 were eligible, 150 wanted) | 7 |
| **selected** | **150** |

Three exclusion classes the code implements never fired, which is itself a result:
`brief_too_short` (0), `truth_not_eligible` (0), and `leakage_guard_failed` (0). **Every
eligible package had at least one roster-eligible person in its truth set** — the
failure mode that discarded 4,992 v1 cases (`truth_not_eligible`, more than the 3,320
that passed every other filter) does not discard a single v4 case, because truth is a
set and losing a member narrows it instead of emptying it.

### 6.2 Splits

| Split | Cases | DM | TIMOB | MESOS | FAB | Mean truth-set size |
|---|---:|---:|---:|---:|---:|---:|
| validation | 28 | 15 | 6 | 6 | 1 | 3.39 |
| test | 122 | 64 | 28 | 26 | 4 | 4.39 |
| **total** | **150** | **79** | **34** | **32** | **5** | **4.21** |

The order asked for the same order of magnitude as v1 (~30 validation / ~120 test) and
for an escalation if the test split fell below ~80. It did not: 28 / 122, from 157
eligible packages. As-of times span 2019-01-02 to 2020-10-12.

### 6.3 The shape of a case

| Measure | min | median | max |
|---|---:|---:|---:|
| Issues in the brief | 3 | 13 | 30 |
| Raw brief characters | 377 | 5,977 | 7,999 |
| Rewritten brief characters | 963 | 1,127 | 1,263 |
| Issues in the whole package | 1 | 48 | 274 |
| Issues contributing truth | 1 | 34 | 169 |
| Truth-set size | 1 | 4 | 11 |

**Truth-set sizes**: 33 cases have 1 person, and **117 of 150 have 2 or more** (2: 27,
3: 11, 4: 8, 5: 14, 6: 24, 7: 12, 8: 12, 9: 6, 10: 2, 11: 1). Mean 4.21. This is what
makes `Recall@K` a different measurement from `Hit@K`.

**Survivorship, now measured instead of hidden**: 1,133 person-slots worked the selected
packages; 631 survive roster eligibility and 502 (44%) do not. In v1 each of those
would have deleted a case; here each narrows a truth set, and the count is in the
manifest per case (`truth_dropped_ineligible`). The benchmark is still easier than
reality — a system is never asked to name someone outside the frozen roster — but the
size of that gift is now on the record.

**Cap truncation**: 59 of 150 briefs hit the 30-issue / 8,000-character cap, leaving out
a median of 15 further pre-as-of issues (max 50). Those issues still count toward truth.
This makes the brief a *sample* of a large package rather than its whole content, which
is the honest way to read the biggest DM and FAB sprints.

**Case correlation** (a caveat, not a defect): consecutive packages within a project
share a mean Jaccard overlap of 0.34 in their truth sets, because the same team runs
consecutive sprints. Cases are therefore not independent, the effective sample size is
smaller than 150, and a query-independent baseline like `most_active` is structurally
stronger here than it was on v1. Every comparison in the report is paired for this
reason.

### 6.4 What was run, and what the budget did not reach

| Arm | Split | Cases | Systems | Stage |
|---|---|---:|---|---|
| `v3frozen`, rewritten | validation | 28 | all five | `bench4_val` |
| `v2frozen`, rewritten | validation | 28 | all five | `bench4_val` |
| `v3frozen`, **raw** briefs | validation | 28 | all five | `bench4_val` |
| `v3frozen`, rewritten | **test** | 122 | all five | `bench4_test` |
| `v2frozen`, rewritten | test | — | **not run** | — |

Measured per case on the 28 validation cases of each arm, which is where the prospective
projection now comes from:

| Arm | Roles per case | Cost per case | Latency per case |
|---|---:|---:|---:|
| `v3frozen`, rewritten | 1.82 | $0.0569 | 47 s |
| `v2frozen`, rewritten | 2.00 | $0.0675 | 40 s |
| `v3frozen`, raw | 3.89 | $0.1195 | 96 s |

The raw variant is twice the price because its brief is five times longer and parses
into twice as many roles — and a role is a re-rank call. That is a finding in its own
right: the rewrite makes the benchmark both more realistic *and* materially cheaper to
run.

**Why the second test run was escalated rather than made.** Before the test split, the
prospective projection was $0.062/case × 122 = $7.56 against $6.89 already logged, for
$14.45 of the $15.00 authorization — so `v3frozen` was run, as the order's fallback
rule directs. Adding `v2frozen` on the same split projects a further $7.56, i.e. **$22.02
total**, which does not fit. The order's instruction in that case is explicit: run
v3-default only and escalate for the second. The v2-vs-v3 comparison therefore exists on
the 28 validation cases only, and is reported as directional.

### 6.5 What it cost

| Stage | Calls | Cost (USD) | What |
|---|---:|---:|---|
| `bench4_rewrite` | 164 | 0.0614 | 157 package rewrites (+7 retries) on the cheap model |
| `bench4_val` | 302 | 6.8280 | three validation arms × 28 cases |
| `bench4_test` | 357 | 7.3159 | the frozen test split, 122 cases |
| **total** | **823** | **14.2053** | against the **$15.00** authorization |

Reconciled against `data/llm_costs.jsonl` by stage name, retries included. By call
type: `brief_rewrite` $0.0614, `intent` $1.1940, `rerank` $12.9499 — 91% of the study's
spend is the re-rank, which is the same finding v1-v3 recorded, now on briefs that parse
into ~1.8 roles instead of ~1.2.

### 6.6 Verification run on the built manifest

`scripts`-free, offline re-derivation over all 150 selected cases (re-runnable from the
manifest and the Stage 0 exports) found **zero violations** of: brief issues created
before as-of, brief issues joined before as-of, no temporally-excluded issue in a brief,
both brief variants clean under `LeakageSanitizer`, truth non-empty, truth inside the
frozen roster, and every truth id present in `people.parquet` (i.e. owning a retained
Stage 1 profile bucket). Zero rewritten briefs contain a ticket-key pattern.


---

## 7. Reproducing it

```bash
make stage0                 # exports sprints.parquet + sprint_membership.parquet
make eval-v4-manifest       # offline: builds the manifest from whatever is checkpointed
make eval-v4-rewrite        # SPENDS under bench4_rewrite; a no-op once every package has one
make eval-v4-manifest       # offline: rebuild — byte-identical, no model call
make eval-v4-baselines      # offline: the three baselines in every reported namespace
make eval-v4-validation ENGINE=v3frozen     # SPENDS under bench4_val
make eval-v4-test ENGINE=v3frozen           # SPENDS under bench4_test, once per engine
make eval-v4-report         # offline: rebuilds the v4 section of docs/eval-results.md
```

Graph state these numbers were measured against (unchanged across the study): Person
316, Contribution 2,666, Skill 10,630, `HAS_SKILL` 17,589, `HAS_SPECIALIZATION` 2,361.
