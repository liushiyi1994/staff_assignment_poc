# Improvement backlog — known gaps and proposed remedies

- Date: 2026-08-14
- Status: **proposal**. Nothing here is authorized, scoped, or accepted.
- Relates to: `docs/tech-design.md` (design of record), `docs/eval-results.md`
  (what was measured), `docs/agent-handoff.md` (current state),
  `docs/direction-decision.md` (research vs MVP phasing), `prd (1).md` (product target).

This document inventories what is weak, missing, or unproven in the research track,
and proposes concrete remedies. It exists so the next phase starts from a written
list rather than from memory. Each gap has an ID (`G1`…`G13`) so a work order can
cite it.

Every claim below is backed by a file reference or a measured number from this
repository. Where something is opinion or untested, it says so.

## How to read an entry

- **Evidence** — where the problem is visible in code or data.
- **Impact** — what it costs us, stated as concretely as the evidence allows.
- **Options** — the alternatives considered, including doing nothing.
- **Recommendation** — one choice, with the reasoning.
- **Effort / spend** — S (< 1 day), M (1–3 days), L (> 3 days); LLM spend if any.
- **Success test** — how we would know it worked. If a gap has no success test,
  it is not ready to be a work order.

## The blocker that shapes everything below

**The 120-case test split is retired** (`docs/agent-handoff.md`). It was exposed
three times, which is its budget. That means:

> Most remedies in this document **cannot be honestly measured** until a new
> benchmark exists. `G12` is therefore not one item among many — it gates the
> validation of `G3`, `G4`, `G5`, `G6`, `G7`, and `G9`.

The only work that can proceed and be trusted without a new benchmark is work
that is correct by construction (`G1`), free and offline (`G3a`), or measured on
the validation split as a directional signal only.

## Priority summary

| ID | Gap | Effort | Spend | Blocked by |
|---|---|---|---|---|
| **G12** | Benchmark briefs are single tickets, not staffing needs | L | ~$10–15 | — |
| **G3** | Skill vocabulary is fragmented (10,630 terms / 316 people) | M | $0–2 | G12 to validate |
| **G4** | Contribution embeddings are topic-diluted | M | ~$4 to validate | G12 to validate |
| **G5** | `confidence` is extracted, stored, and never ranked on | S | $0 | G12 to validate |
| **G6** | `strength` is extracted, stored, and never ranked on | S | $0 | G12 to validate |
| **G7** | Position bias in re-rank only partly probed | S | ~$1 | — |
| **G8** | `count` (headcount) is parsed but unused | S | $0 | — |
| **G1** | Description truncation is a blind character slice | S | $0 | — |
| **G2** | No source compression for long evidence | M | $0 API | MVP-relevant |
| **G9** | The graph's value is asserted, not demonstrated | M | $0 | — |
| **G10** | No availability concept anywhere | L | — | MVP phase |
| **G11** | No roster currency — departed people rank forever | S / L | $0 | — |
| **G13** | Carried small items (`httpx`, latency) | S | $0 | — |

---

# 1. Evidence and extraction

## G1 — Description truncation is a blind character slice

**Evidence.** `src/capgraph/pipeline/stage0_load.py:509`:

```python
def strip_markup(text_: str | None, max_chars: int) -> str | None:
    ...
    return cleaned[:max_chars] or None
```

`bucketing.max_description_chars: 1200`. Markup is stripped, then the string is
hard-sliced. The cut is not sentence-aware and can land mid-word.

**Impact.** Two distinct problems, and only one of them is the truncation:

1. A description cut mid-sentence can end on a dangling clause. The design already
   warns that a clipped summary misleads the model (`config/settings.yaml`, on
   `rerank_contributions_per_candidate`); the same argument applies upstream.
2. More importantly, the *first* 1,200 characters are not necessarily the
   informative ones. Jira descriptions routinely open with reproduction steps,
   environment dumps, or pasted stack traces, and state the actual problem below.
   We take the head regardless.

Unquantified — no one has measured how many descriptions exceed 1,200 characters
or what is lost. That measurement is step one.

**Options.**

- **(a) Do nothing.** Defensible: extraction reads 3–30 tickets per bucket, so one
  clipped description is diluted by its neighbours.
- **(b) Sentence-boundary truncation.** Cut at the last sentence end before the
  limit. Trivial, no dependency, strictly better than a mid-word slice.
- **(c) Head + tail.** Keep the first ~800 and last ~400 characters with an elision
  marker. Cheap way to catch "…the actual fix is X" conclusions.
- **(d) Further structural stripping.** Drop stack traces and log dumps before
  measuring length, so the budget is spent on prose.

  > **Partly done already.** TAWOS separates `Description_Text` (prose) from
  > `Description_Code`, and Stage 0 already prefers `Description_Text`, falling
  > back to raw `Description` only when it is empty
  > (`stage0_load.py:57`, `:300`). So fenced code is already excluded. What
  > remains is prose-embedded noise — pasted logs, environment tables,
  > reproduction boilerplate — which `Description_Text` still contains.

**Recommendation. (b), and measure before doing more.** Sentence-boundary
truncation is unambiguously an improvement and costs nothing. Beyond that, the
honest position is that we do not yet know whether truncation is hurting: nobody
has counted how many descriptions exceed 1,200 characters after code stripping.
Measure first, then decide between (a) and (c)/(d).

This must stay **pure Stage 0**: no LLM, no rewriting. Stage 0 is the provenance
layer, and if it paraphrases, "here is the evidence" stops being literally true.

**Effort / spend.** S / $0. Re-running Stage 2 to see the effect costs $1.84.

**Success test.** Report the share of descriptions currently truncated and the
share that are majority code/logs. If both are small, close this gap as
not-worth-fixing and record that. If truncation is common, the fix ships and
extraction is re-run.

## G2 — No compression path for long evidence (a small local model)

**Proposal on the table:** use a small local model to rewrite/summarize source text
before extraction, to save tokens.

**A correction to the premise first.** Token savings in extraction are worth
almost nothing here. Measured from `data/llm_costs.jsonl`:

| Stage | Spend | Share |
|---|---:|---:|
| Stage 2 extraction (all 2,666 profiles) | **$1.84** | 7% |
| Query engine + benchmark re-ranking | **$23.35** | 93% |

Halving extraction input tokens saves roughly **$0.50 on a full corpus rebuild**.
The money is in the re-rank, which is a per-query cost and is not affected by how
source text is compressed.

**Where the idea is genuinely right: the MVP.** A Jira ticket is ~1–2 KB. The
PRD's sources (§8.1) are Statements of Work, Technical Design Documents, Slack
threads, and meeting transcripts — tens to hundreds of pages. There, a fixed 1,200
character truncation is not a rounding error, it discards the document. Some
extract-then-summarize stage is **mandatory** for the MVP, and it is worth
prototyping in the research track precisely because the research track is where
mistakes are cheap.

**Options.**

- **(a) Extractive selection, no generation.** Score paragraphs for relevance
  (embedding similarity to the person, or simple heuristics) and keep the top-N
  verbatim. **Preserves provenance exactly** — every retained sentence is real
  source text.
- **(b) Local abstractive summarization.** A small local model rewrites long
  sources into a shorter brief. Cheapest at inference, but the extraction now runs
  on a paraphrase, and any hallucination in the summarizer silently becomes
  "evidence".
- **(c) Hierarchical summarization.** Summarize sections, then summarize summaries.
  Standard for long documents; compounds the paraphrase risk.

**Recommendation. (a) for anything that feeds a claim; (b) only for material that
is never cited.** The entire credibility of this system rests on the chain from
claim → ticket key → source text. An abstractive rewrite breaks that chain in a way
no downstream validator can detect, and our one automated safeguard — rejecting
citations a person does not own — cannot catch a hallucinated *claim* about text
that was itself invented.

If (b) is adopted anyway, it must:

1. Run in a **separate, clearly-labelled stage** (Stage 1.5), never inside Stage 0.
2. **Retain the original text** alongside the summary, with the summary marked as
   derived.
3. Be graded on a sample before use, the way extraction was meant to be
   (`docs/implementation-plan.md` Task 3 step 2, never run).

**Effort / spend.** M / $0 API cost (local model), plus $1.84 to re-run extraction
for comparison.

**Success test.** On a sample of long sources, does extraction from compressed text
produce the same specializations and skills as extraction from full text? Divergence
above a small threshold means the compression is lossy in the way that matters.

---

# 2. Vocabulary

## G3 — The skill vocabulary is fragmented

**Evidence.** `data/contributions/terms.jsonl`:

```
10,630 canonical skills + 344 specializations, for 316 people
```

The design predicted 300–600 skills (`docs/tech-design.md` §5). We are ~20× over.
Real examples of canonicals that should not be canonicals:

```
"Kubernetes namespace resource quotas"
"Kubernetes LoadBalancer and Helm configuration"
"Kubernetes and GKE deployment"
```

Three separate canonical terms for one capability. Meanwhile the merges that *did*
happen are string-similarity merges (`"Chaincode lifecycle"` → `"Chaincode
deployment"`, 21 aliases), not concept merges.

**Impact.** A term that appears once cannot be matched by a query that phrases it
any other way, so it contributes nothing to the structured retrieval arm while
inflating the vocabulary the intent parser must map onto. This is a plausible
contributor to the structured arm's weakness, though **it has not been isolated
experimentally** — that would need `G12`.

**Root cause.** Embedding-cosine clustering is flat and symmetric. It can merge
near-identical strings; it cannot generalize ("Mesos containerizer" *is a kind of*
container orchestration) and it cannot bridge vocabulary ("Chaincode" ≈ "smart
contract"). A single global threshold of 0.85 is also applied to terms of wildly
different specificity.

**Options.**

- **(a) Frequency gating.** Terms with document frequency below a floor stop being
  canonical and attach as aliases to their nearest canonical instead. Free, no LLM,
  and probably takes 10,630 → ~1,500 on its own.
- **(b) Two-level clustering.** Cluster at 0.85 as now, then cluster the surviving
  canonical names again at ~0.70 to build a parent layer. Retrieval matches at
  whichever level the query lands on. Free. Produces hierarchy without anyone
  authoring a taxonomy.
- **(c) LLM naming per cluster.** Cluster with embeddings (free), then one LLM call
  **per cluster** to choose the canonical name and flag bad merges. ~1,500 clusters
  ≈ **$2**. Note: pairwise LLM comparison is infeasible and was never on the table —
  10,630 terms is 56M pairs.
- **(d) Vocabulary-aware extraction.** Process buckets in a fixed date order and
  pass the current top-N canonical terms *for that project* into the extraction
  prompt: "reuse these where they fit; invent only when nothing matches." Attacks
  the problem at the source instead of cleaning up after it. Costs nothing extra
  beyond a longer prompt.

**Recommendation. (a) immediately, then (d).** (a) is free, reversible, and
addresses the visible symptom. (d) is the real fix — it stops manufacturing 2,666
independent vocabularies — but it introduces order-dependence, so it needs the
bucket order pinned in config and a benchmark to confirm it does not degrade
recall. (c) is good value if the canonical *names* prove to be the problem;
measure before spending.

**Effort / spend.** (a) S / $0. (b) S / $0. (c) M / ~$2. (d) M / $1.84 re-extraction.

**Success test.** Vocabulary size, share of terms with df=1, and — the one that
matters — structured-arm candidate recall on a benchmark. A smaller vocabulary that
does not improve retrieval is cosmetic.

---

# 3. Retrieval

## G4 — Contribution embeddings are topic-diluted

**Evidence.** `src/capgraph/pipeline/stage5_graph.py:498` embeds one vector per
contribution from the summary alone:

```python
vectors = embed_fn([c.contribution_summary for c in contribs])
```

Summaries measure min 149 / median 418 / max 660 characters. A real one from
`data/contributions/raw.jsonl` covers uptime monitoring **and** S3 snapshot
tooling **and** Python 3 compatibility **and** Slack ChatOps — four unrelated
topics averaged into one 384-dimensional vector.

**Impact.** Measured. `vector_only` is the weakest non-trivial baseline on the test
split — 0.175 / 0.467 / 0.658 / MRR 0.340 — losing to BM25 on every metric. Plain
keyword matching beating semantic matching on a semantic task is a strong signal
that the vectors are smeared. Adding the BM25 arm in v3 moved candidate recall
0.925 → 0.975, which is partly this weakness being compensated for rather than
fixed.

**Options.**

- **(a) Do nothing.** The union already compensates. Honest, and cheap.
- **(b) Multi-vector contributions.** Embed each specialization/skill cluster within
  a contribution separately — 2–4 vectors instead of 1 — and score a person by
  their single best-matching vector (max, not mean). Storage goes 2,666 → ~8,000
  vectors, about 12 MB. **The extraction already produced this decomposition and
  nothing currently uses it for retrieval.**
- **(c) Embed skills/specializations directly** and match query terms against term
  embeddings rather than contribution embeddings. Overlaps with `G3b`.
- **(d) A stronger embedding model.** Larger models handle multi-topic text better.
  Requires dropping and rebuilding the Neo4j vector index (dimensions are fixed) and
  re-embedding everything.

**Recommendation. (b).** It is the only option that uses information we already
paid to extract, it is free at inference (local embeddings), and it attacks the
one bottleneck the benchmark actually measured. I would rank this **above further
re-rank tuning**, which is where the last two benchmark versions spent their money
for no aggregate gain.

**Effort / spend.** M / $0 to build, ~$4 for one validation run to measure.

**Success test.** `vector_only` baseline Hit@K, and candidate recall of the vector
arm alone with the BM25 arm disabled. If the vector arm alone reaches what the
three-arm union reaches today, the dilution was the problem.

---

# 4. Ranking — three signals we own and do not use

## G5 — `confidence` is extracted, stored, and never ranked on

**Evidence.** `prompts/extraction.md` requires a `confidence` of high/medium/low on
every contribution. `normalization.min_evidence_keys_for_high_confidence` clamps
over-claimed records down to medium in Stage 3. `models.py:83` carries it. And
`rank.py:192` `score_candidate` never reads it — the score has four components and
confidence is not one of them.

**This is a PRD requirement we silently dropped.** `prd (1).md` §10.6 lists the
ranking signals: *"direct specialization match, supporting skill match, recency,
evidence strength and count, **confidence**, client/industry similarity,
source-type weight."* Of the seven, we implement four. Two of the missing three have
an excuse — TAWOS has one source type and no client — and confidence does not.

**Impact.** Unknown, and that is the point: it is a free, already-populated signal
that has never been tried. A low-confidence contribution and a high-confidence one
currently carry identical ranking weight.

**Recommendation.** Add `confidence` as a fifth score component (high 1.0 / medium
0.6 / low 0.3, weights re-tuned), or as a multiplier on `evidence_strength`. The
offline machinery for this already exists: `make eval-v2-scores` checkpoints score
components, and `combine_parts()` (`rank.py:236`) is deliberately factored so a
weight experiment re-scores checkpointed components through identical arithmetic.

**Effort / spend.** S / $0 offline. Validation on a benchmark needs `G12`.

**Success test.** Does a confidence-weighted score beat the current score on the
same checkpointed components? Measurable offline, at zero cost, today.

## G6 — `strength` is extracted, stored, and never ranked on

**Evidence.** The extraction prompt assigns each specialization `strength:
primary|secondary` ("primary if most tickets support it"). Distribution across
2,666 contributions: **2,853 primary, 2,663 secondary**. It is written to the
`DEMONSTRATES` edge (`stage5_graph.py:127`) and read back into `Contribution`
objects (`retrieve.py:536`).

It never reaches the score. `score_candidate` matches against
`candidate.specializations`, which are `PersonCapability` objects built from the
`HAS_SPECIALIZATION` projections — and those carry `evidence_count`, `last_used`,
`decay_score`, not strength.

> Name collision worth flagging for anyone reading the code: `structured_strength`
> (`retrieve.py:381`) is a *different* quantity — `Σ(evidence_count × decay)`,
> computed in Python. It is unrelated to the LLM's primary/secondary label.

**Impact.** Your PRD §7.3 makes specializations "the primary unit of capability the
MVP queries against" and §7.2's example explicitly labels them "primary match
targets". We extract exactly that distinction and then rank as though it does not
exist. Someone whose *primary* specialization matches the brief scores identically
to someone for whom it is a *secondary* sideline.

**Recommendation.** Weight matched specializations by strength in
`specialization_match` — a primary match counts 1.0, a secondary 0.5. Requires
carrying strength onto the projection edge in Stage 4, which is a small change to
an aggregation that already reads the contributions.

**Effort / spend.** S / $0. Needs a Stage 4 + Stage 5 re-run (both offline).

**Success test.** Same as `G5` — offline re-scoring of checkpointed components.

## G7 — Position bias is only partly probed

**Evidence and what was already tried.** More was done here than the summary
tables suggest:

- **Permutation self-consistency** (Tang et al., NAACL 2024) is implemented
  (`retrieval.rerank_samples`, Borda aggregation, `rank.py:404`) and was measured
  as `ab_selfconsistency`: Hit@1 0.267 vs the adopted arm's 0.400, MRR 0.443 vs
  0.523, at **$2.72 against $0.88**. Rejected on evidence.
- **Uniform-length cards** were adopted specifically as a lost-in-the-middle
  remedy, and they are why a window of 32 costs *fewer* input tokens (9,263) than a
  window of 15 did under the profile view (18,526).

**What was not tried.** With `rerank_samples: 1` candidates are presented in
**deterministic score order — best first**. That is an intentional prior, and it is
un-ablated. Nobody has run the same configuration with the order reversed.

**Impact.** If the model is largely following presentation order, then the "LLM
re-rank" is substantially re-expressing the deterministic score, which would
explain the v2 finding that a better input ordering produced the same output
ranking. That finding is currently attributed to the re-rank being a bottleneck; a
strong position effect is an alternative explanation that has never been excluded.

**Recommendation.** One validation run with candidates in reverse score order.
Cheap, and it distinguishes two hypotheses we currently cannot separate.

**Effort / spend.** S / ~$1.

**Success test.** If reversing the order moves Hit@1 by more than the 0.100 noise
floor, position is dominating and the re-rank prompt needs rethinking. If it does
not, the prior is doing its job and the v2 interpretation stands.

## G8 — `count` is parsed but unused

**Evidence.** `RoleSpec.count` (`models.py:113`) is parsed by the intent prompt
("number of people needed for that role, default 1"). Nothing downstream reads it.
The engine returns a full ranked list per role regardless.

**Impact.** Invisible on the benchmark, because every case has a single truth
person — which is exactly why it survived three benchmark versions unnoticed. It is
visible immediately in any real use: "I need two backend engineers" returns a
ranked list with no notion of a team.

**Recommendation.** Small: surface the top-`count` as a proposed set, keeping the
remainder as alternates. Larger and more interesting: **team composition** — when
`count > 1`, prefer complementary coverage of the role's skills over `count`
near-duplicates of the same profile. That is a genuinely graph-shaped problem and
connects to `G9`.

**Effort / spend.** S for the surfacing. M–L for composition.

**Success test.** Needs multi-person ground truth, which `G12` produces. Not
measurable today.

---

# 5. Storage

## G9 — The graph's value is asserted, not demonstrated

**Evidence.** The design document says it plainly (`docs/tech-design.md` §7):

> Neo4j gives Cypher, viz, one-store hybrid retrieval; heavier setup (Docker), and
> **at PoC scale honestly overkill — chosen because the *pitch* is graph-shaped and
> demo viz matters.**

And the PRD disagrees (`prd (1).md` §7.1):

> At MVP scale, it can be implemented relationally with entity tables and a vector
> index rather than as a dedicated graph database.

The owner chose Neo4j deliberately, valuing learning the graph approach
(`docs/direction-decision.md`, checkpoint resolved 2026-08-11). That was a valid
call. But the consequence is that we now run Docker/Colima as a runtime dependency
for a store holding **316 people and 19,950 edges**, and we have never demonstrated
that the graph earns it.

**What we actually use the graph for today.** Four Cypher queries: term resolution,
a vector lookup joined to owners, a projection lookup, and one neighbourhood
expansion. Every one has a direct SQL equivalent. No query traverses more than two
hops. No variable-length traversal exists anywhere in the codebase.

**Options.**

- **(a) Prove it.** Implement queries that are genuinely awkward relationally and
  show they add value: capability adjacency ("who is close to this skill space"),
  collaboration ("who has worked alongside whom"), team composition (`G8`),
  similar-person retrieval. Note `COLLABORATED_WITH` is already in the schema design
  and deliberately unbuilt, pending a versioned co-work signal.
- **(b) Drop it.** Build the SQLite + numpy twin. `capabilities.jsonl` becomes a
  table verbatim; 2,666 × 384 floats is 4 MB, so brute-force cosine is sub-
  millisecond and no vector index is needed at all. Removes Docker entirely.
- **(c) Keep it and say so honestly.** Retain Neo4j for demo value, and stop
  implying it is a performance or capability decision.

**Recommendation. (a), with (b) as the fallback and a hard deadline.** The
strongest argument for a graph is the one thing we have not built — relationships
*between people* and *between capabilities*. If a scoped experiment shows adjacency
or composition queries measurably improve shortlists, the graph is justified and the
MVP should reconsider the PRD's relational stance. If it does not, we should say so
and move to (b) for the MVP.

Two constraints on (a): the PRD §7.3 explicitly **defers** formal capability
adjacency edges, so building them contradicts a stated MVP decision and needs the
owner's sign-off; and TAWOS's unversioned component data is the reason
`COLLABORATED_WITH` was deferred in the first place — a co-work signal needs
timestamps we may not have.

**Effort / spend.** M / $0. (b) is also M — the twin is small.

**Success test.** Does any graph-native query produce a shortlist a human prefers,
or a measurable metric gain? If neither, the graph is a demo asset, which is a fine
thing to be as long as it is labelled.

---

# 6. Product gaps

## G10 — There is no availability concept anywhere

**Evidence.** Nothing in `models.py`, `retrieve.py`, or `rank.py` refers to
availability, allocation, or capacity. `Intent.recency_years` is often misread as
duration; it means "how fresh must their experience be."

**Impact.** The system answers "who *has done* this work" and is silent on "who
*can do it next month*". A shortlist that ignores availability is not actionable
for staffing, which is the product's stated purpose.

**Three things get conflated and should not be.**

| Question | Source | Where it belongs |
|---|---|---|
| *Can* they do this work? | Evidence | Capability memory (this system) ✅ |
| Are they still on the team? | Roster / HR | A join — **missing** (see `G11`) |
| Are they free in the window? | the resourcing system | Runtime filter, per PRD |

The PRD (§1, §4) explicitly routes the third to the internal resourcing system and makes availability a
non-goal for MVP. That phasing is right and should be kept.

**Recommendation — a design constraint more than a task: availability must be a
filter, never a score component.** Blending it into the ranking means "slightly
less qualified but free" outranks "clearly right but busy" with no way to see which
happened, and it makes results non-reproducible — re-running next week silently
changes the ranking. Rank on capability, filter on availability, show both states,
let a human override.

That separation also protects the benchmark: capability ranking stays measurable
against history, which availability never can be.

**Effort / spend.** L, and it belongs to the MVP phase with a real resourcing-system
integration. Not research-track work.

**Success test.** Not applicable in the research track. In the MVP: does filtering
change which shortlist an operations user accepts?

## G11 — No roster currency: departed people rank forever

**Evidence.** `projections.recency_half_life_days: 540`. Decay is exponential, so a
score asymptotically approaches zero but never reaches it. Nothing anywhere marks a
person inactive.

**Impact.** Someone who left a project in 2016 still appears with a small positive
score and can surface on a shortlist. On the benchmark this is **structurally
invisible** — the roster is frozen and every case's truth is guaranteed inside it —
so three benchmark versions could never have caught it. In production it is an
immediately embarrassing result.

This is distinct from `G10`. It is not "are they busy", it is "are they here at
all", and unlike availability it *is* derivable from the evidence we already have.

**Options.**

- **(a) Activity-currency signal.** How recently did this person do *anything*, as
  opposed to how recently they did *this skill*. Free, computable from Stage 1
  buckets, and catches the departed case with no HR integration.
- **(b) A hard activity window.** Exclude anyone with no contribution in the last
  N quarters. Simple, blunt, and needs care — parental leave and sabbaticals look
  identical to departure.
- **(c) Roster join.** The correct MVP answer, and it depends on the canonical
  roster the PRD requires (§7.5).

**Recommendation. (a) now, (c) for the MVP.** Never (b) alone — an automated
"this person is gone" inference about a real employee is precisely the kind of
judgement this system must not make unsupervised.

**Effort / spend.** (a) S / $0. (c) L, MVP phase.

**Success test.** Report the distribution of "quarters since last contribution"
across the 316 people. If a meaningful share have long gaps and still rank, the
signal is worth adding.

---

# 7. Benchmark

## G12 — Benchmark briefs are single tickets, not staffing needs

**This is the largest gap in the project and it gates most of the others.**

**Evidence.** A real benchmark brief from `data/eval/briefs.jsonl`:

> *"Resolve upsert order in SQLite Registry."* I'm taking the last piece of DM-16227
> — using SQLite's "ON CONFLICT DO NOTHING" variation of INSERT — out of that ticket
> because it doesn't work with 3.24 (it does with 3.26)…

That is a single engineering task: narrow, jargon-dense, referencing other tickets.
A real staffing brief is *"we need two backend engineers with streaming experience
for a six-month data platform build."*

**Impact — four separate distortions.**

1. **It rewards narrow term-matching**, which flatters BM25 and the skill-overlap
   component, and understates semantic retrieval. This is a plausible part of why
   BM25 is so hard to beat.
2. **Single-truth labelling.** One ticket, one assignee. `Recall@K` collapses to
   `Hit@K`, and the pitch already concedes the deeper problem: *"the ground truth is
   one name, and one name is wrong."*
3. **`count` and team composition are untestable** (`G8`).
4. **Survivorship in roster construction.** Measured from the manifest: **4,992
   otherwise-usable cases were dropped as `truth_not_eligible`** — more than the
   3,320 that passed every other filter. Every retained case is guaranteed to have
   its answer inside the roster, which is strictly easier than reality, where the
   right person may be a recent joiner the system has never seen.

```
24,522 candidate issues examined
   5,342  brief_too_short
   4,992  truth_not_eligible      <- the person who did it wasn't eligible
   4,026  unresolved
   4,015  query_not_post_cutoff
   3,170  sampled_out
     150  SELECTED
```

**The proposed remedy: work-package briefs with multi-person truth.**

Group related post-cutoff issues into one work package, use the package as the
brief, and use **everyone who worked its issues** as ground truth. This fixes
distortions 1–3 at once.

**Feasibility — verified against the TAWOS schema, and the obvious grouping key
does not work.**

| Grouping key | Available? | Verdict |
|---|---|---|
| **Epic → children** | ⚠️ **Not confirmed.** 3,032 issues of type `Epic` exist in the slice, but `Issue_Link`'s observed relationship types are `Duplicate`, `Relates`, `Reference`, `Related`, `Depends`, `Blocks`, `Cloners` — semantic, not hierarchical. No epic-parent link was observed in the sampled rows. | **Verify before committing** |
| **`Issue.Sprint_ID` → `Sprint`** | ✅ **Confirmed.** `Sprint` carries `Start_Date`, `End_Date`, `Activated_Date`, `Complete_Date`. Every issue can carry a `Sprint_ID`. | **Recommended** |
| **`Version` / `Affected_Version`** | ✅ Confirmed, with `Release_Date`. | Viable fallback |
| **`Issue_Link` semantic clusters** | ✅ Confirmed (~271k links) | Messy; connected components have no natural boundary |

A **sprint** is arguably a better analogue than an epic anyway: it is a bounded body
of work, done by a real team, with unambiguous start and end dates — which gives the
as-of time for free instead of requiring it to be derived.

Also newly confirmed and currently unused: `Issue` carries `Story_Point`,
`Timespent`, `In_Progress_Minutes`, and `Total_Effort_Minutes`. That is real effort
data, and it is the only route this dataset offers toward modelling capacity.

**The proposed rewrite step, and its one real risk.**

The proposal is to have an LLM rewrite the work package into a natural staffing
brief. This is **materially better than synthesizing briefs outright**, and better
than I would have assumed: the *ground truth stays real*. We are rephrasing a real
body of work and keeping the real people who did it. The circularity objection in
`docs/direction-decision.md` §2 does not fully apply.

The risk that does apply is **leakage through the rewriter**. Guards required:

1. The rewriting model **must not see** assignees, comments, resolution data, or
   anything after the as-of time. It sees creation-time titles and descriptions only.
2. Output must pass the existing `LeakageSanitizer` (`src/capgraph/privacy.py`) —
   identifiers, pseudonyms, mentions, emails stripped.
3. The rewrite must be **checkpointed and frozen** in the manifest, so the benchmark
   stays deterministic and re-runnable without re-paying or re-drawing.
4. A held-out slice should be evaluated on **un-rewritten** package text too, so we
   can measure how much the rewriting itself moved the numbers.

**Recommendation.** Build benchmark v4 as: **sprint-grouped work packages →
leakage-guarded LLM rewrite → multi-person ground truth**, on a freshly cut
manifest. Verify epic linkage first; if it exists, epics are the more intuitive
unit and sprints are the fallback. Keep the existing single-ticket benchmark as a
secondary suite so v1–v3 numbers remain comparable to something.

**Effort / spend.** L, but **less than it looks — the scoring harness is already
multi-truth.** Verified in `src/capgraph/eval/metrics.py`: `hit_at_k`,
`recall_at_k`, `candidate_recall`, and `mrr` all take `truth: set[str]` and handle
multiple IDs correctly today, and the module's own docstring notes that Hit@K and
Recall@K "are identical only for the current single-assignee cases."
`EvalBrief.true_person_ids` is already a list. So the work is concentrated in
Stage 0 (export `Sprint_ID`) and the manifest builder (a new case type), not in
scoring. Rewrite cost is small (~$0.005/brief); a full re-run of all systems on a
new split is ~$10–15.

**Success test.** Two things: `Recall@K` becomes meaningfully different from
`Hit@K` (proving multi-truth is real), and the gap between the graph system and
BM25 changes. If broader briefs do not change the BM25 comparison, that is a
genuinely important negative result.

---

# 8. Carried small items

## G13 — Known small items

- **`httpx` is an undeclared direct dependency.** Fold into the next authorized
  `uv lock` refresh. Carried since the research track closed.
- **Query latency ~20–30s per brief**, dominated by re-rank generation. A waiver is
  recorded in the Stage 6 order. Note the cheap arm answers in 2.8s, so this is a
  configuration choice, not a hard limit.
- **The 5% strong-model extraction grading** (~$3, `docs/implementation-plan.md`
  Task 3 step 2) was never approved and never run. It is the only planned check on
  extraction *quality* as opposed to extraction *validity*. Recorded, not nagged.
- **`docs/manager-pitch.md` spend figures are stale by $0.07** — the ledger now
  reads $25.20 across 4,203 calls after the demo run on 2026-08-14. The document
  claims every number is copied from a repository file, so it should be corrected
  to keep that claim literally true.

---

# Suggested sequencing

**Wave 1 — free, offline, no benchmark needed.** `G1` (measure then fix
truncation), `G3a` (frequency gating), `G5` + `G6` (score the signals we already
own, re-scored offline from checkpointed components), `G8` (surface `count`),
`G11a` (activity currency), `G13`. One work order, no spend, no new manifest.

**Wave 2 — the benchmark rebuild.** `G12`. This is the gate. Until it lands, Wave 1's
changes can be reasoned about but not validated, and no new tuning result is
trustworthy.

**Wave 3 — measured improvements, once there is something to measure on.** `G4`
(multi-vector), `G3d` (vocabulary-aware extraction), `G7` (reverse-order probe),
`G9` (prove or drop the graph).

**MVP phase — not research-track work.** `G2` (source compression for long
documents), `G10` (availability via the resourcing system), `G11c` (canonical roster), plus
everything in the PRD the research track never touched: curator review, identity
resolution, multi-source weighting, operations feedback capture.

# Explicitly out of scope

- Anything that would use TAWOS data for product purposes. Research-only terms.
- Any use of this system for real employment decisions, in any phase.
- Re-running the retired 120-case test split. It is spent; a v4 needs a fresh
  manifest.
