# Finding the right person from what they've actually done

**A research prototype, evaluated honestly. What it does, what it proves, what it costs, and what it does not.**

Written 2026-08-13. Every number in this document is copied from a file in this
repository — mostly `docs/eval-results.md` (the benchmark record) and
`data/llm_costs.jsonl` (the spend ledger). Nothing here is recomputed, rounded
in our favour, or estimated. The last section says where each number came from.

*(**Update, 2026-08-16.** A dated addendum at the end of this document reports
everything measured since: a new benchmark that asks a more product-shaped
question, the first result that clearly beats free keyword search, an accusation
this project made against its own most expensive component and then had to
withdraw, and the close of the research track. The body below is unchanged from
2026-08-14; short pointers mark the handful of places the addendum overtakes
it.)*

---

## The one-minute version

1. **It works, at roughly the level published research reports.** Given a
   description of a piece of work, the system puts the person who actually did
   it in its top 10 candidates about 83% of the time, and first about 23–33% of
   the time depending on configuration. Independent published systems on
   similar-sized teams report top-1 around 0.33–0.35 and top-10 around 0.81.
   We are in that band. We are not ahead of it.

2. **The expensive part of the system is mostly not what earns the accuracy.**
   The cheap, deterministic ranking — no large language model in the ranking
   step — reaches essentially the same top-5 and top-10 accuracy as the full
   system, at **$0.0037 and 2.8 seconds per query** versus **$0.0316 and 30.1
   seconds**. That is roughly 8× cheaper and 10× faster for the same practical
   result. This is the single most important finding for any product decision.

   *(Partly superseded — addendum §A2 and §A3. On the newer benchmark the full
   system scores 0.508 on top-1 against its own cheap arm's 0.311, and a
   controlled study sizes the model stage's contribution at +0.250 on identical
   candidate pools. The cost and speed ratios stand.)*

3. **Our last round of changes made one number clearly worse.** Version 3
   raised top-10 accuracy from 0.775 to 0.833, and at the same time dropped
   top-1 accuracy from 0.308 to 0.225 — which is now *below* plain 1990s
   keyword search (0.258). That regression is the strongest single signal in
   the whole study (it came closest to statistical significance of anything we
   measured). We are reporting it as loudly as the gain.

   *(Still true of this benchmark. On the work-package benchmark built
   afterwards, the same configuration leads keyword search on all six measures —
   addendum §A2.)*

And the boundary that is not negotiable: **this is a research prototype on
public, pseudonymous open-source data. It must not be used to make decisions
about real people's jobs.** Section 5 explains why in detail.

---

## 1. The problem, and what was built

**The problem.** When a new piece of work comes in, someone has to decide who
should do it. That decision usually rests on memory — who has done something
like this before, and recently. Memory is uneven, it favours the visible
people, and it does not scale past the handful of names a manager happens to
know well.

Meanwhile, the actual record of what people have done sits in ticket systems:
years of closed work, with descriptions, in the team's own vocabulary. Nobody
reads it, because reading it is not humanly possible.

**What was built.** A system that reads that record and turns it into
searchable, evidence-backed capability profiles, then answers a plain-English
request for staffing with a ranked shortlist where every recommendation cites
the specific tickets it is based on.

**How it works, in one paragraph.** Each person's closed tickets are grouped by
project and by three-month period. A cheap language model reads each group and
writes down what that person actually did — in the words their own domain uses,
not from a fixed list of skills — and records which ticket IDs each claim came
from. Near-duplicate skill names ("Kafka consumer", "Kafka consumers") are
merged automatically. Claims are aged: recent work counts for more than old
work. The results are stored in a graph database — a store built for
"who is connected to what" questions — while the raw ticket text stays outside
it, so the graph holds claims and pointers to evidence, never the source
tickets themselves. When a request arrives, the system pulls candidates two
different ways at once (by meaning, and by keyword and skill match), scores
them with a fixed arithmetic formula, then asks a stronger model to re-order
the top few and write a one-line reason for each, quoting ticket IDs. Any
recommendation citing evidence that person does not actually have is thrown
out automatically.

**Scale of what was processed.** Five open-source projects from a public
research dataset of Jira tickets: 82,703 issues total, of which 62,554 were
created before the evaluation cutoff and supplied the evidence. 316 people had
enough history to profile. Their work was grouped into 2,668 person-project-
quarter groups, of which 2,666 produced a contribution record and 2 were
skipped, yielding 10,630 distinct skill terms, 344 broader specialisations, and
19,950 capability links between people and skills.

**Total model spend for the entire research track: $25.20**, across 4,203 API
calls, every one of them logged (this includes the $0.07 live demo run). The
profile-building step — the bulk of the work — cost $1.84. That is the ledger at
the close of the research track on 2026-08-14; improvement work after that date
is logged under its own stages and counted separately.

*(Ledger updated — addendum §A5. The whole project now stands at $49.8621
across 5,402 calls; the figure above is the part of it through 2026-08-14.)*

---

## 2. How it was evaluated, and why these numbers can be trusted

The honest way to test a system like this is to ask it to predict the past.

We picked a cutoff date (1 January 2019). The system was allowed to see only
work finished *before* that date. Then we took 150 real tickets created *after*
it, stripped them down to what was known the moment they were filed, and used
each as a staffing request. The right answer is who actually ended up doing
that work. This is called a **temporal holdout** — hold out the future, and
check whether the system predicts it.

The difficulty is that this kind of test leaks by default. Guards we put in
place, all of them verifiable in the code and the benchmark record:

- **The request text is the ticket as it was created**, rebuilt from the change
  log. Later edits and comments are never substituted in — those would contain
  hints about who took the job.
- **The right answer is reconstructed at the point the ticket was resolved**,
  not read off the dataset's final "assignee" field, which can be edited years
  later. The final field is kept only for auditing.
- **The pool of candidates is frozen at the cutoff.** Every system ranks the
  same list of eligible people for that project, and any output naming someone
  outside it is recorded as a failure rather than quietly ignored.
- **"How recent is this person's work" is recalculated as of each ticket's own
  date**, never today's date. Wall-clock time is not an input anywhere.

Then there is the discipline around the numbers themselves, which matters more
than any single result:

- **We tuned on 30 cases and tested on 120, and we ran the 120 exactly once per
  version.** Three versions, three test runs, then the test set was retired. A
  test set you keep peeking at stops being a test.
- **We measured our own noise.** We re-ran one identical configuration twice,
  changing nothing, and the results moved by up to 0.100. So any improvement
  smaller than 0.100 has not been shown to be real. That floor was later
  confirmed a second, independent way. Most of what we tried falls under it —
  and we say so rather than claiming it.

  *(The newer benchmark has since had a floor measured on itself, per metric and
  mostly tighter — addendum §A4.)*
- **We used paired statistics, not just averages.** Because every version
  answers the same 150 cases, we can count exactly which cases got better and
  which got worse, rather than watching an average drift.
- **Every comparison is reported in whichever direction it falls.** The
  benchmark record contains the experiments that failed, the ones that were
  rejected, and one place where our own earlier reasoning turned out to be
  wrong at a wider setting, corrected in writing.

We also compare against three deliberately unflattering baselines: plain
keyword search (BM25, a standard scoring formula from the 1990s, free and
instant), plain semantic search over the same text, and "just pick whoever has
been busiest on this project", which ignores the request entirely. If a
sophisticated system cannot beat a free one, that is the finding.

---

## 3. Results

### Reading the metrics

| Term | In plain words |
|---|---|
| **Hit@1** | How often the person who actually did the work was ranked **first**. 0.325 means "about a third of the time". |
| **Hit@5 / Hit@10** | How often they appeared **anywhere in the top 5 / top 10**. |
| **MRR** | "Mean reciprocal rank" — one number rewarding higher placement. 1.0 = always first, 0.50 = always second, 0.20 = always fifth. |
| **Candidate recall** | Before ranking, the system pulls a working pool of roughly 30 people. This is how often the right person made it into that pool at all. It is a **ceiling**: someone never pulled can never be ranked. |

One thing to hold on to: **each request is ranked against its own project's
team only** — between 21 and 105 people. Being in the top 10 of 21 is far
easier than the top 10 of 105, which is why the full record breaks every
number down per project.

### The test split — 120 cases, one run per version

Baselines make no model calls, so they produce identical results in every
version; that repetition is itself a check that the harness is deterministic.

| System | Hit@1 | Hit@5 | Hit@10 | MRR | Candidate recall |
|---|---:|---:|---:|---:|---:|
| **Full system, v1** | 0.325 | 0.567 | 0.767 | 0.449 | 0.925 |
| **Full system, v2** | 0.308 | 0.592 | 0.775 | 0.445 | 0.925 |
| **Full system, v3** | 0.225 | 0.625 | 0.833 | 0.413 | 0.975 |
| Cheap ranking only, v1 | 0.158 | 0.483 | 0.708 | 0.319 | 0.925 |
| Cheap ranking only, v2 | 0.175 | 0.600 | 0.775 | 0.366 | 0.925 |
| Cheap ranking only, v3 | 0.158 | 0.558 | 0.808 | 0.360 | 0.975 |
| Keyword search (BM25) | 0.258 | 0.592 | 0.708 | 0.404 | 1.000 |
| Semantic search only | 0.175 | 0.467 | 0.658 | 0.340 | 1.000 |
| Busiest person | 0.042 | 0.308 | 0.375 | 0.175 | 1.000 |

Baselines rank the entire team, so their candidate recall is 1.000 by
construction — not a strength, just a consequence of not narrowing first.

### What actually changed between versions

**v1 → v2** (re-weighted the scoring formula toward recent work): the full
system did not move — the largest change was 0.025, well inside our 0.100 noise
floor. But the *cheap* ranking improved substantially: Hit@5 +0.117, MRR
+0.047. The finding was uncomfortable and useful: the language-model re-ranking
step was producing the same end result from a better input as it had from a
worse one. It, not the arithmetic score, was the limit.

**v2 → v3** (added keyword search to the candidate pull, widened the re-rank
window, compressed how candidates are described to the model):

| Metric | v2 | v3 | Change | Cases better | Cases worse | Significance |
|---|---:|---:|---:|---:|---:|---|
| Hit@1 | 0.308 | 0.225 | **−0.083** | 6 | 16 | p = 0.052 |
| Hit@5 | 0.592 | 0.625 | +0.033 | 18 | 14 | p = 0.597 |
| Hit@10 | 0.775 | 0.833 | +0.058 | 12 | 5 | p = 0.143 |
| MRR | 0.445 | 0.413 | −0.033 | 50 | 37 | 95% range [−0.094, +0.023] |
| Candidate recall | 0.925 | 0.975 | +0.050 | — | — | — |

Read the Hit@1 row plainly. Version 3 got 6 cases right that version 2 missed,
and missed 16 that version 2 got right. "p = 0.052" means: if the two versions
were genuinely equally good, a split this lopsided or worse would turn up by
luck alone only about 5% of the time. **That makes the regression the closest thing to a
statistically significant result in this entire study, and it points the wrong
way.** At 0.225, the current default configuration puts the right person first
less often than free keyword search does (0.258).

*(On the work-package benchmark built afterwards, this same configuration puts a
right person first 0.508 of the time against keyword search's 0.303, and the gap
is statistically significant — addendum §A2. That is a different question being
asked, not a re-measurement of this one.)*

What v3 genuinely delivered is the recall ceiling: 0.925 → 0.975. The right
person now reaches the working pool in 117 of 120 cases instead of 111. The
retrieval problem is close to solved. The ranking problem is not — v3 spent
the extra headroom on top-10 and gave up top-1 to get it.

Two further honesty notes on v3, both recorded in the benchmark file:

- We adopted the compressed candidate description partly because it eliminated
  bad citations at the narrower setting (8 → 0). At the wider setting it did
  not hold: v3 rejects 1.25% of the model's recommendations for citing
  evidence a person does not have, against v2's 0.65%. We wrote that
  correction into the record rather than leaving the original claim standing.
  In both versions rejected recommendations are discarded, not repaired, so no
  unevidenced claim reaches a shortlist.
- Two further techniques (running the model several times and voting;
  finishing with a stronger, more expensive model) were measured and
  **rejected** — neither beat the noise floor, and one cost three times as
  much.

### How this compares to published work

Independent research on this problem, on teams of comparable size, reports
top-1 accuracy of roughly 0.33–0.35 and top-10 of roughly 0.81 (TriagerX,
arXiv:2508.16860; its OpenJ9 results are 0.327 / 0.633 / 0.807 for top-1 /
top-5 / top-10). Our v2 figures are 0.308 / 0.592 / 0.775, and our v3 top-10 is
0.833.

The fair statement is **parity with the published frontier, not an advance on
it.** Different data, different teams, so this is a sanity check on the order
of magnitude, not a head-to-head.

There is also a ceiling nobody in this field gets past. The literature on this
data finds that somewhere between 18% and 44% of historical "who did this"
labels are contestable — the recorded person is not always the person who did
the work, and several other people were often equally capable. We audited our
own 120 test cases for the worst version of this problem and found **zero**
cases where the dataset later named a different person. But 62 of the 120
truths have no recorded assignment event to corroborate their timing, and the
system performs the same on those as on the 58 corroborated ones (Hit@1 0.242
vs 0.207) — so the audit rules out one failure mode, not all of them. A system
scoring 1.000 here would be suspicious, not impressive.

---

## 4. The cost finding — the one a product decision should rest on

Strip the language model out of the *ranking* step and keep only the fixed
arithmetic score, and here is what happens on the same 120 cases:

| | Full system (v3) | Cheap ranking only (v3) |
|---|---:|---:|
| Hit@5 | 0.625 | 0.558 |
| Hit@10 | 0.833 | 0.808 |
| Hit@1 | 0.225 | 0.158 |
| Cost per query | $0.0316 | **$0.0037** |
| Average time per query | 30.1s | **2.8s** |

Every accuracy gap in that table — 0.067, 0.025, 0.067 — is **smaller than the
0.100 run-to-run noise we measured on ourselves.** In the v2 configuration the
picture was even starker: the cheap arm reached Hit@5 0.600 against the full
system's 0.592, and matched it exactly at Hit@10 (0.775 each), for roughly a
tenth of the cost and a seventh of the time.

*(Re-measured on the newer benchmark — addendum §A2 and §A3. There the full
system scores 0.508 on top-1 against the cheap arm's 0.311, and the model
stage's own contribution, measured on identical candidate pools, is +0.250. The
cheap arm still costs under a tenth as much and still holds its own at top-10,
so the design conclusion below survives; the "not measurably better" half does
not.)*

So what does the expensive stage actually buy?

- **In the v2 configuration: top-1 precision.** 0.308 versus 0.175 is a 0.133
  gap, comfortably above the noise floor. If your product puts one name in
  front of a user, this is real and worth paying for.
- **In the v3 configuration: not measurably that.** The gap narrows to 0.067,
  inside noise. The current default configuration does not have a
  demonstrated top-1 advantage over its own cheap arm.
- **In both: the explanations.** The cited, evidence-backed reason for each
  recommendation is produced by the model stage and by nothing else. The
  arithmetic score produces an ordering with no argument attached.

The practical read: **cheap ranking for breadth, model ranking for the moment a
human needs to be persuaded.** A product that shows ten candidates could serve
them for a third of a cent in under three seconds, and spend the model call
only on the shortlist a user actually opens. That is a design decision the
benchmark supports directly, and it was not the answer we expected going in.

For reference, plain keyword search costs nothing and answers in 3.6
milliseconds. Any product built here has to keep justifying itself against
that.

---

## 5. Limitations and ethics

**This system must not be used to make decisions about real people's jobs.**
Not hiring, not staffing, not promotion, not performance review, not
redundancy. That is not a hedge, a disclaimer, or a "for now" — it is the
condition under which the work was done, and it is written into the project's
own operating rules.

The reasons are concrete:

- **The data is public open-source Jira from a research dataset, and the
  people in it are pseudonyms.** They are identified as `Person DM-104` and
  similar, scoped to one project. They never consented to being profiled for
  staffing. No cross-project identity is ever inferred, and no name is ever
  reconstructed.

- **What the system measures is what got recorded, not what someone can do.**
  Ticket history is a biased record. Work that was never ticketed, mentoring,
  design work, code review, unglamorous maintenance, and anything a person did
  before they joined this project are all invisible to it. Anyone with a
  thinner ticket trail looks less capable to this system regardless of whether
  they are.

- **Being ranked first is not evidence of being the best choice.** The target
  is "who was historically assigned this", which the benchmark record states
  plainly as a prediction target rather than proof of optimal qualification.
  Historical assignment carries whatever bias the original team had — who was
  available, who was favoured, who was already overloaded. A system trained to
  reproduce past assignment reproduces past patterns, including the unfair
  ones.

- **The ground truth is one name, and one name is wrong.** In real teams
  several people are usually acceptable for a given piece of work. Our
  measurements count all but one of them as a miss, so the numbers understate
  useful behaviour — and, more seriously, the single-label framing quietly
  encodes "there is a right person" when there usually is not.

- **Open-source Jira is not agency work.** Different vocabulary, different
  project shapes, different assignment culture. The pipeline is domain-neutral;
  the numbers are not, and none of them transfer to a different setting without
  being measured again there.

- **The accuracy is not good enough to be trusted unsupervised even if the
  ethics allowed it.** Getting the right person first roughly a quarter to a
  third of the time is a useful research result and a poor autopilot. The
  honest product framing is a research aid that surfaces evidence a human then
  judges — never a recommendation a human rubber-stamps.

Two design choices were made specifically so that a future product could be
defensible: every claim carries the ticket IDs it came from, so a human can
check it; and recommendations citing evidence a person does not have are
automatically discarded. Those help. They do not turn the above into a
solvable problem.

---

## 6. What transfers to a product MVP

If the next phase is a real product, this is the inventory of what is reusable
and what is not.

**Transfers:**

- The extraction prompt and its evidence-guard pattern — the mechanism that
  forces every claim to name the tickets it came from.
- The chunking granularity: person × project × three-month period. Not
  per-ticket (too noisy, too expensive), not whole-history (loses the timeline).
- Emergent-vocabulary normalisation with human override review — letting skill
  names come from the data instead of a fixed taxonomy, with a review step.
- Hybrid retrieval: pulling candidates by meaning *and* by keyword, then
  merging. This is the change that lifted the recall ceiling to 0.975.
- The ranking architecture: cheap deterministic score first, model re-ranking
  only on the top few, with evidence-citation enforcement.
- Prospective cost gating — estimate spend before a stage runs, abort if it
  exceeds its ceiling. It is why the whole track cost $25.20 and never
  surprised anyone.

  *(Track total is now $49.8621 — addendum §A5. The gating held to the end: the
  last two rounds of work returned $6.44 and $10.00 of their ceilings unspent.)*
- The benchmark methodology itself: temporal holdout, leakage guards, frozen
  test split, measured noise floor, paired statistics. Arguably the most
  valuable artefact here.

**Does not transfer:**

- **The data.** The research dataset is research-only. None of it moves into a
  product.
- **The numbers.** They describe open-source projects, not the real setting.
- **Pseudonymous identity handling.** A product needs canonical, consented
  roster identity — a different problem with different requirements.

**What the MVP phase would need to add** (per the project's direction
decision): curator-mediated ingestion so a human vets what enters the system,
canonical roster identity, conventional relational-plus-vector storage rather
than a graph, and a real pilot evaluation against the actual setting.

---

## Where every number came from

| Claim | Source |
|---|---|
| All Hit@1/Hit@5/Hit@10/MRR/candidate-recall figures, all three versions and all baselines | `docs/eval-results.md`, test-split tables (v1, v2, v3 sections) |
| Wins/losses/ties and p-values for v3 vs v2 | `docs/eval-results.md`, "Paired per-query statistics — test split" |
| 0.100 noise floor | `docs/eval-results.md`, "Run-to-run variance on 30 cases" and "The noise gauge, measured inside this study" |
| Cost and latency per query ($0.0316 / 30.1s; $0.0037 / 2.8s; v2's $0.0362 / 22.4s and $0.0038 / 3.3s) | `docs/eval-results.md`, v3 and v2 "What this run showed" |
| BM25 latency 3.6 ms | `docs/eval-results.md`, v3 test-split table |
| Citation rejection rates 1.25% / 0.65% | `docs/eval-results.md`, "Re-rank citation validity" |
| Label-noise audit: 0 reassigned, 58 corroborated, 62 unknown timing, Hit@1 0.207 vs 0.242 | `docs/eval-results.md`, "Label-noise audit" |
| Rejected techniques (self-consistency, stronger finisher) | `docs/eval-results.md`, "Lever findings — paid validation A/B (v3)" |
| Total spend $25.20 across 4,203 calls; extraction $1.84 | `data/llm_costs.jsonl`, summed by stage, research-track stages only |
| 2,668 groups / 2,666 contributions, 10,630 skills, 344 specialisations, 19,950 capability links, 150 benchmark cases | `data/buckets/buckets.jsonl`, `data/contributions/raw.jsonl`, `data/contributions/terms.jsonl`, `data/contributions/capabilities.jsonl`, `data/eval/briefs.jsonl` record counts |
| 82,703 issues, 62,554 pre-cutoff, 316 people | `data/parquet/slice_report.md` |
| Roster sizes 21–105 per project | `docs/eval-results.md`, run configuration |
| Published comparison 0.33–0.35 top-1 / ~0.81 top-10; OpenJ9 0.327/0.633/0.807; 18–44% contestable labels | TriagerX, arXiv:2508.16860, via `docs/work-orders/manager-pitch.md` and `docs/agent-handoff.md` |
| MVP transfer list | `docs/agent-handoff.md`, "Next phase" |

Note on which configuration is "the system": version 3 is the current default
in the engine configuration; version 2 remains the strongest measured setting
for top-1 accuracy and MRR. Both are reachable from `config/settings.yaml`, and
any demo or pilot has to state which one it is running. This document reports
both rather than picking the flattering one.

*(Unchanged as of 2026-08-16 — addendum §A5. Version 3's settings are still the
configuration of record; the work-package benchmark measured both frozen
configurations, and no default has been changed since.)*

---

# Addendum — 2026-08-16

**A new measuring instrument, a correction we had to publish about ourselves,
and the close of the research track.**

Written 2026-08-16. Everything above this line is the record as it stood on
2026-08-14 and has not been rewritten; short pointers mark the places this
addendum overtakes it. Same rules as the body: every number is copied from a
file in this repository, nothing is rounded in our favour, and the one figure
that is calculated rather than copied — the total spend — has its method written
out. Section A8 says where each new number came from.

Three things happened after 2026-08-14. We built a new measuring instrument and
got the project's first clearly positive result on it. We accused our own most
expensive component of cheating, and then had to withdraw the accusation. And we
closed out everything that was left, almost entirely for free, and stopped.

## A1. The new instrument — read this before any number below

Sections 1–6 measure one question: given a single ticket, can the system name
the one person who ended up closing it? That is a clean question to score, but
it is not the question a staffing conversation asks. Nobody hands a colleague
one ticket. They hand over a body of work — a sprint, a milestone, a slice of a
project — and they need a handful of people for it, not a single name.

**Benchmark v4 asks that question instead.** Each case is one real sprint out of
these projects' history. The request is a plain-language description of the work
that had been planned into that sprint *before it started*, written up from
those tickets by a cheap language model so that it reads like a staffing brief
rather than a wall of ticket text. The moment we ask is the sprint's recorded
start date, and the system is shown nothing that happened after it. And the
right answer is not one person: it is **everyone who went on to resolve any
ticket in that sprint** — four people on a typical case, eleven at the widest,
631 people across the 150 cases in total.

That makes v4 a **different instrument, not a fourth tuning round.** Different
requests, different right answers, different cases, and one project fewer in the
mix (one of the five recorded no sprints, so it drops out). **A v4 number cannot be
put in a table beside a v1, v2 or v3 number.** They answer different questions.
The v1–v3 record above stands unchanged, on its own terms.

One new measure arrives with the new question. When the right answer is several
people, "did we find them" splits in two:

| Term | In plain words |
|---|---|
| **Hit@5 / Hit@10** | Did the top 5 (or top 10) contain **anyone** who worked that sprint? |
| **Recall@5 / Recall@10** | **What share** of the people who worked it did the top 5 (or top 10) contain? |

Hit@K is the easier bar; Recall@K is the honest one. Both are reported below.
The split-and-freeze discipline is the same as before: 150 sprints, 28 kept for
tuning and 122 held back, and the held-back 122 run once.

## A2. The new headline

On the 122 held-back cases, **the system leads every baseline on all six
measures.** The baseline column below is picked per measure — whichever free
method happens to be strongest on that one — so a weak result cannot hide behind
a baseline it happens to beat:

| Measure | Full system | Best baseline | Gap |
|---|---:|---|---:|
| Hit@1 | 0.508 | 0.303 (keyword search) | +0.205 |
| Hit@5 | 0.754 | 0.631 (keyword search) | +0.123 |
| Hit@10 | 0.803 | 0.721 (keyword search) | +0.082 |
| Recall@5 | 0.396 | 0.284 (busiest person) | +0.112 |
| Recall@10 | 0.597 | 0.411 (keyword search) | +0.185 |
| MRR | 0.622 | 0.459 (keyword search) | +0.163 |

Because every system answered the same 122 cases, we can count them one at a
time instead of watching averages drift. Against keyword search:

| Measure | Keyword search | Full system | Cases better | Cases worse | Ties | Significance |
|---|---:|---:|---:|---:|---:|---|
| Hit@1 | 0.303 | 0.508 | 31 | 6 | 85 | p = 0.000 |
| Hit@5 | 0.631 | 0.754 | 18 | 3 | 101 | p = 0.001 |
| Hit@10 | 0.721 | 0.803 | 14 | 4 | 104 | p = 0.031 |
| Recall@5 | 0.254 | 0.396 | 57 | 17 | 48 | 95% range [+0.088, +0.197] |
| Recall@10 | 0.411 | 0.597 | 63 | 19 | 40 | 95% range [+0.116, +0.256] |
| MRR | 0.459 | 0.622 | 64 | 23 | 35 | 95% range [+0.104, +0.223] |

"p = 0.000" means: if the system and free keyword search were genuinely equally
good, a split as lopsided as 31-better against 6-worse would essentially never
turn up by luck. **This is the first time in the project that the gap between
our system and a free baseline has been statistically significant on a held-back
test split.** Section 3 above records the previous high-water mark for
significance in this study — and that one was a regression, pointing the wrong
way.

The whole picture on the same 122 cases, cheap arm and other baselines included:

| System | Hit@1 | Hit@5 | Hit@10 | Recall@5 | Recall@10 | MRR | Candidate recall | Mean time | Cost of the run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Full system** | 0.508 | 0.754 | 0.803 | 0.396 | 0.597 | 0.622 | 0.974 | 49.3 s | $7.3159 |
| Cheap ranking only | 0.311 | 0.705 | **0.820** | 0.323 | 0.539 | 0.485 | 0.974 | 5.2 s | $0.6847 |
| Keyword search (BM25) | 0.303 | 0.631 | 0.721 | 0.254 | 0.411 | 0.459 | 1.000 | 4.1 ms | free |
| Semantic search only | 0.180 | 0.516 | 0.689 | 0.177 | 0.327 | 0.333 | 1.000 | 11.8 ms | free |
| Busiest person | 0.090 | 0.566 | 0.697 | 0.284 | 0.343 | 0.329 | 1.000 | 0 ms | free |

Three things in that table are worth saying out loud rather than leaving for a
sceptic to find:

- **The cheap arm beats the full system at top-10** — 0.820 against 0.803 —
  while the whole 122-question run cost $0.6847 against $7.3159, under a tenth
  as much. Section 4's product conclusion survives the new instrument intact:
  cheap ranking for breadth, the model for the head of the list.
- **Where the model stage earns its money is that head of the list**, and here
  it earns it plainly: 0.508 against the cheap arm's 0.311 at rank 1, and 0.622
  against 0.485 on MRR. On the old instrument that gap was inside noise.
- **"Busiest person" is a far stronger baseline here than it was before.** When
  the right answer is most of a team, ranking people by sheer volume captures a
  lot of it without reading the request at all — which is exactly why it takes
  the Recall@5 column. Any claim about this system has to clear that, not only
  keyword search.

And three things about how hard these cases really are, all recorded in the
benchmark file rather than left for a reader to discover:

- **Neighbouring cases are not independent.** Consecutive sprints inside a
  project share about a third of their right answers on average, because the
  same team runs them. So 122 cases carry less independent information than 122
  unrelated questions would — which is why every comparison above is counted
  case by case rather than read off the averages.
- **Everyone being scored was already known to the system.** 502 people who
  genuinely worked on these sprints were dropped from the right answers because
  they were not on the roster frozen at the cutoff date. The system is never
  asked to name somebody it has never seen, and real life does not offer that
  courtesy.
- **59 of the 150 requests are truncated** at 30 tickets or 8,000 characters, so
  on those the request describes only part of the sprint while the right answer
  still counts everyone who worked all of it. Those cases are harder than they
  look, not easier.

## A3. The accusation, and the exoneration

Between the two dates on this document, the project accused its own most
expensive component of cheating, and then cleared it. Both halves are in the
record, and the second half is only worth anything because the first was written
down.

**What the accused component does.** After cheap arithmetic sorts the
candidates, the top 32 are printed as cards and handed to a language model,
which is asked to reorder them and write a one-line, evidence-citing reason for
each. The cards are printed best-first.

**The accusation ($0.9454, of a $2 ceiling).** A sceptic's question: what if the
model is not really reading the evidence, and is mostly handing back the order
it was given? So the same 30 requests were re-run with the cards presented
**worst-first**. Top-1 accuracy halved — 0.400 down to 0.200, p = 0.031 — and
MRR fell 0.132. A third measurement already on file — the same cards shuffled
several different ways and the answers voted together — sat neatly in between at
0.267. The recorded conclusion was blunt: presentation order
dominates this stage. The practical consequence was a spending freeze — stop
paying to tune this prompt, because versions 2 and 3 had already spent $11.58 on
re-rank changes for no gain, and this looked like where the money had gone.

**Why the accusation was wrong.** The follow-up study, commissioned to redesign
the prompt, began by doing something no earlier comparison in this project had
done: it **pinned retrieval.** Every arm was replayed against one saved copy of
the same request parse, the same candidate pool, the same cheap scores and the
same 32 cards — byte-for-byte identical, 28 of 28 cases in every pairing. Every
earlier A/B in this project had re-run the whole engine, so a fresh draw of
candidates moved along with whatever was being tested.

Pinning cost something immediately, and that cost is itself a finding: the
benchmark had never saved its request parses, and re-running them at identical
settings does not reproduce them — same candidate pool and order on 2 cases of
28, same cheap ranking on 0 of 28. The arm assumed to be free had to be paid
for.

**The exoneration.** With retrieval pinned, reversing the cards moved top-1
accuracy by **−0.071**, on two disagreeing cases out of 28, p = 0.500. The
−0.200 did not reproduce. What the original probe had measured was presentation
order *plus* a fresh draw of candidates, bundled together and charged entirely
to the first.

**And what the expensive stage is actually worth**, measurable for the first time
without retrieval shifting underneath it. Same pool, same 28 cases: cheap
arithmetic alone puts a right person first 0.143 of the time; hand that identical
pool to the model and it becomes **0.393 — a gain of +0.250, better on 8 cases
and worse on 1, p = 0.039** — with MRR +0.182. Handed the cards deliberately
worst-first it still adds +0.179. A component that was mostly parroting its input
order could not do that.

**What we are not claiming.** Two things changed between the probe and the
follow-up — the instrument and the pinning — and the follow-up cannot separate
them, so whether presentation order genuinely dominated on the retired old
benchmark is now unresolvable, and moot. Nor is −0.071 comfortably nothing: once
this benchmark's own noise floor was measured (§A4) it sits between one and two
floors, so "inside noise" was a luckier phrase than a rigorous one. The +0.250
premium, by contrast, survives at more than twice that floor.

**The rule that replaced the spending freeze** is the most portable thing in this
section: **no comparison between two arms is evidence unless everything except
the thing being tested is held fixed.** Comparing across two separate runs
smuggles in a fresh draw of retrieval, and that smuggling has now produced one
documented false finding.

**The redesigned prompt itself was not adopted.** It bought no ranking
improvement and cost about 38% more per call. It is kept as a selectable file for
one measured property worth remembering in a product: when its input is
disturbed, its recommendations almost never cite evidence the person does not
have — 0.2% discarded either way, against the current prompt's 0.6% normally and
2.8% once the cards are reversed. In every version, a recommendation citing
evidence a person does not have is discarded, never patched.

## A4. What the discipline bought

**The benchmark now has a noise floor measured on itself, per measure.** One arm
was simply run twice with retrieval pinned, so the only thing that varied was the
model answering an identical question a second time:

| Measure | How far a straight repeat moved it |
|---|---:|
| Hit@1 | 0.036 |
| Hit@5 | 0.071 |
| Hit@10 | 0.036 |
| Recall@5 | 0.095 |
| Recall@10 | 0.010 |
| MRR | 0.034 |

Read that as: on this benchmark, a change smaller than the number in its row has
not been shown to be a change at all. It replaces the single 0.100 figure quoted
in section 2, which was measured on the older instrument — tighter here for
top-1, looser for Recall@5, so one number across all six would have been wrong in
both directions. One detail worth keeping: **no case produced the same ranking
twice, yet 25 of 28 put the same person first.** The instability is real and it
lives below the head of the list. Cost of measuring it: $1.5555.

**Then three more improvement ideas were examined, with four paid runs
authorized between them — and not one of the four ran.** Each was gated behind a
free tier: recompute the answer offline from saved numbers first, and spend only
if the free tier shows the paid one could learn something. Every free tier
answered the question on its own.

- **Trimming the skill vocabulary** — dropping terms only a handful of people
  use, taking 10,630 terms down to 1,755. It changed retrieval a great deal, and
  every signal pointed the wrong way at the head of the list: the cheap arm's
  top-1 fell 0.143 → 0.071, and on one case a right person who used to reach the
  32 cards no longer did. **Closed, $0.**
- **Weighting a specialisation by how central it is to that person.** It lost to
  its own control: giving *everybody* the same flat credit scored better (0.214
  against 0.179). The label has genuine spread in the data; it does not separate
  the right people. **Closed, $0.**
- **The scoring-weight retune** that the control above pointed at — below,
  because it is the cleanest example of the whole method. **Closed, $0.**

Between them, the two work orders carrying those experiments authorized **$18**
of model spend and spent **$1.5555** — under a tenth — and that spend bought the
noise floor above, not any of the three levers.

**The last round is worth telling in full.** The question was whether shifting
one step of scoring weight away from "matches this person's specialisation"
toward "how recent is their work" is a real improvement. The free tier is pure
arithmetic over saved numbers, no model calls at all:

- It **is** real. Top-1 on the cheap arm goes 0.143 → 0.214, twice the measured
  floor, and it sits on a plateau rather than a spike: of the 81 neighbouring
  weightings, 70 beat the current one and **none** is worse.
- And it **cannot reach the output.** The model only ever sees 32 cards. Under
  the new weights the cards do change — 11 people in and 11 out across the split
  — but **not one of them is a person we are looking for.** Nobody right enters
  the window; nobody right leaves. That holds across all **270** defensible
  versions of the retune, and across the entire space of **13,776** possible
  weightings the best any of them manages is to move **one** person in, via a
  weighting nobody would defend. It also removes none of the 28 choices the
  model had already made at rank 1 — not the 11 it got right, and not the 17 it
  got wrong.

So a paid run would have measured the model answering the same question twice,
with a story attached. **The round stopped at its gate, returned its entire $10,
and left the final held-back test run it was budgeted for unspent.**

That is the sentence to keep: **we spend to learn, and we stop when the
arithmetic says stop.**

## A5. Totals, and where things stand

**Total model spend for the entire project, first pipeline call to last:
$49.8621 across 5,402 API calls.**

The method, written out so anyone can check it: read `data/llm_costs.jsonl` line
by line, add the `cost_usd` field of every single record — no filtering, no
exclusions, retries and superseded drafts and abandoned runs all included — and
count the records. Nothing is estimated, sampled, or reconciled against a
projection.

Against the $25.20 in section 1: that figure is 4,203 calls and $25.1957 through
2026-08-14, the live demo included. The 1,199 calls and $24.6664 since then
break down as:

| What it paid for | Calls | Cost |
|---|---:|---:|
| Rewriting sprint tickets into staffing briefs | 164 | $0.0614 |
| The presentation-order probe — the accusation | 63 | $0.9454 |
| Benchmark v4, tuning split, both configurations | 302 | $6.8280 |
| Benchmark v4, held-back test split | 357 | $7.3159 |
| The re-rank study that overturned the accusation | 259 | $7.9603 |
| Measuring the benchmark's own noise floor | 54 | $1.5555 |
| **Total since 2026-08-14** | **1,199** | **$24.6664** |

**Status, 2026-08-16.** The research track's experimental program is concluded.
The configuration of record is unchanged: it is the same version-3 default the
body above describes, and no setting has been flipped since benchmark v4 was
run. The held-back test split was budgeted for two runs and has had one, so
**one exposure remains unspent** — deliberately, so that if some future change
ever earns an honest measurement, there is one left to give it.

## A6. What this changes for the MVP conversation

- **The system now demonstrably beats free search on the product-shaped
  question.** That claim could not be made on 2026-08-14 and can be made now, on
  a held-back split, with the statistics behind it.
- **The cheap arm is still the cost story, and now carries a lead for a pilot to
  test.** It answers for under a tenth of the money and beats the full system at
  top-10; the weight retune above is a real improvement *to that arm
  specifically*, parked rather than adopted, and a pilot on real data is the
  right place to measure whether it survives.
- **The evaluation method remains the most transferable asset here** — and it is
  worth more after §A3 than before it, because it now carries a rule learned the
  hard way: pin everything except the thing you are testing, or you are not
  measuring that thing.

## A7. Limitations and ethics — unchanged

Section 5 stands exactly as written and nothing above softens it: **this is a
research prototype on public, pseudonymous open-source data, and it must not be
used to make decisions about real people's jobs** — the target is still who was
historically assigned the work rather than who should have been, and a better
score does not change that.

## A8. Where every number in this addendum came from

| Claim | Source |
|---|---|
| What benchmark v4 is: sprint grouping, as-of = recorded sprint start, cheap-model brief rewrite, 150 packages split 122 test / 28 validation | `docs/eval-results.md`, "Benchmark v4" preamble, "Configuration" and "The manifest"; full leakage accounting in `docs/benchmark-v4-manifest.md` |
| 631 people across 150 cases, median 4, range 1–11 | `docs/eval-results.md`, v4 "The manifest" |
| Four projects, not five (one has no sprints); neighbouring cases share ~a third of their truth sets; 502 people dropped as not roster-eligible; 59 of 150 briefs capped at 30 issues / 8,000 characters | `docs/eval-results.md`, "Caveats specific to this instrument" |
| Best-baseline-per-measure table, test split | `docs/eval-results.md`, "The graph system against BM25 — test split, `v3frozen`", second table |
| Cases better/worse/ties and p-values against keyword search | `docs/eval-results.md`, same section, third table |
| Full 122-case table: all five systems, candidate recall, latency, run cost | `docs/eval-results.md`, "test split — `v3frozen`, rewritten briefs" |
| Mean times 49.3 s and 5.2 s | same table's mean latency (49,307.3 ms and 5,189.0 ms), converted to seconds |
| "$0.6847 against $7.3159 — under a tenth" | the two run-cost figures in that table; the comparison is those two numbers divided |
| The probe: 0.400 → 0.200, p = 0.031, MRR −0.132, shuffle-and-vote 0.267, cost $0.9454 of a $2 ceiling | `docs/improvement-wave1-report.md`, "G7 — re-rank presentation-order probe" |
| $11.58 spent on re-rank levers in v2 and v3 | `docs/improvement-wave1-report.md`, G7 recommendation |
| Pinning: pools byte-identical 28/28 in every pairing; re-parsing reproduces 2/28 pools and 0/28 cheap rankings | `docs/rerank-redesign-report.md`, "What is pinned" and "The baseline is not free" |
| Reversed order under pinning: −0.071, two disagreeing cases, p = 0.500 | `docs/rerank-redesign-report.md`, "Paired per-case statistics", first table |
| Re-rank over the cheap arm on identical pools: 0.143 → 0.393, +0.250, 8 better / 1 worse, p = 0.039, MRR +0.182; +0.179 worst-first | `docs/rerank-redesign-report.md`, "What the re-rank is worth over the deterministic score" |
| Redesign ~38% more expensive; citation rejection 0.2% / 0.6% / 2.8% | `docs/rerank-redesign-report.md`, "Recommendation" and "Rejection accounting" |
| The correction of record, and the pinning rule that replaced the spending freeze | `docs/work-orders/rerank-redesign.md`, Acceptance record (2026-08-15) |
| Per-measure noise floor; 0/28 identical rankings, 25/28 same first person; cost $1.5555 | `docs/deterministic-sweeps-report.md`, "Work item 1" |
| −0.071 sits between one and two floors; +0.250 survives at more than twice the floor | `docs/deterministic-sweeps-report.md`, "What the floor does to claims already on the record" |
| Vocabulary 10,630 → 1,755; cheap-arm top-1 0.143 → 0.071; one right person pushed out of the 32 cards | `docs/deterministic-sweeps-report.md`, "G3a — document-frequency floor 3" |
| Specialisation strength 0.179 against its flat control's 0.214 | `docs/deterministic-sweeps-report.md`, "G6 — strength-weighted specialization match" |
| $8 authorized for that order, $1.5555 spent, no paid arm opened | `docs/deterministic-sweeps-report.md`, "Spend"; `docs/work-orders/deterministic-sweeps.md`, Acceptance record |
| Retune 0.143 → 0.214; plateau 70 of 81 better and none worse; 11 people in / 11 out with 0 right people; 270 and 13,776 vectors; 11 correct and 17 wrong rank-1 choices all unchanged | `docs/weights-round-report.md`, "Tier 0" steps 3–5 and "GATE 1" |
| $10 returned unspent; one test exposure held in reserve; configuration of record unflipped | `docs/work-orders/weights-round.md`, Acceptance record (2026-08-16); `docs/direction-decision.md`, "research track concluded" |
| $18 authorized across the two orders, and the four paid runs it covered (a vocabulary arm, a specialisation arm, a weights validation arm, a weights test run) — none of which ran | the LLM-authorization headers of `docs/work-orders/deterministic-sweeps.md` ($8) and `docs/work-orders/weights-round.md` ($10), with their Acceptance records; $6.44 and $10.00 returned unspent |
| **$49.8621 across 5,402 calls**, and the per-stage table | `data/llm_costs.jsonl`, summed by the method stated in §A5 |
| $25.1957 across 4,203 calls through 2026-08-14 | the same ledger, records up to and including the demo stage — the "$25.20" of section 1 |

The v1–v3 source map in the previous section is unchanged and still applies to
every number in the body above.

---

## Postscript — 2026-08-16, later the same day

After the addendum above was written, the ranking question was reopened once
at the owner's request: two further pre-registered experiments (a stronger
model reading the full candidate window; richer per-candidate evidence) were
measured under the same pinned-pool discipline. Neither moved the top-choice
decision beyond the measured noise floor — the stronger model actually ranked
the head *worse* while ordering the tail better — and the question is now
closed with five method families measured in total. $9.0158 of a $15
authorization was spent, the rest returned, nothing adopted, and the reserved
final test exposure remains unspent. The project ledger stands at **$58.8779
across 5,521 calls** (`data/llm_costs.jsonl`, summed the same way as §A5).
The recorded working hypothesis for any future gain: the missing signal is in
the *evidence* itself — extraction granularity, and signals that separate
close collaborators — a pipeline question for the product phase, not another
ranking method.
