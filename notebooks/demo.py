# %% [markdown]
# # Capability Graph PoC — demo
#
# **From Jira ticket exhaust to evidence-backed staffing shortlists.**
#
# What this notebook shows, in order:
#
# 1. one person's raw tickets → the capability profile extracted from them → their
#    subgraph in Neo4j;
# 2. **one live staffing query** — a ranked shortlist where every reason cites the
#    ticket keys it is built on;
# 3. why the delta-batch section is not run here;
# 4. how well it actually works, transcribed from the frozen benchmark report.
#
# This is a research PoC on pseudonymous public data (TAWOS). It is **not** usable for
# real hiring, staffing, promotion, or performance decisions, and the people in it are
# opaque project-scoped pseudonyms, never named individuals.
#
# (jupytext percent format — `make demo` converts to .ipynb)

# %% Setup — imports, spend envelope, Neo4j connection
import base64
import json

import matplotlib.pyplot as plt
from IPython.display import HTML, Markdown, display
from pyvis.network import Network

from capgraph.eval.run_eval import SYSTEM_LABELS
from capgraph.llm import cost_log_path, stage_cost_so_far
from capgraph.query.engine import connected_driver, print_result, query
from capgraph.settings import DATA_DIR, REPO_ROOT, settings

# The demo's authorized spend envelope: docs/work-orders/manager-pitch.md records the
# owner's 2026-08-14 approval of <= $0.50 under the cost-log stage name `demo`. Every
# model call in this notebook goes through src/capgraph/llm.py, which refuses a call
# before sending it once the stage's logged spend plus the call's estimate exceeds the
# ceiling set below.
DEMO_STAGE = "demo"
DEMO_CEILING_USD = 0.50

driver = connected_driver()

# %% [markdown]
# ## 0. The configuration this demo runs on
#
# The engine's current defaults are the **benchmark v3** configuration. This demo is
# pinned instead to the **frozen benchmark v2** configuration, which is the strongest
# *measured* setting for top-1 precision (test-split Hit@1 0.308 and MRR 0.445, against
# v3's 0.225 and 0.413). v3 traded that away for reach: it added a keyword-search arm,
# swapped the re-rank's full profiles for compact cards, and widened the re-rank window
# from 15 to 32, which raised Hit@10 from 0.775 to 0.833. A demo shortlist is read from
# the top down, so the v2 setting is the honest one to show — and pinning it means the
# numbers in section 4 describe the system you are watching run.
#
# The pinned values are read out of the repo's own record of that frozen configuration
# (`config/settings.yaml` → `eval.v3.v2_baseline`), not typed in here, so this notebook
# cannot drift from the configuration those v2 numbers were measured under.

# %% Pin the engine to the frozen benchmark-v2 configuration


def override(dotted: str, value):
    """Set one settings key for this kernel only; config/settings.yaml is untouched.

    Same seam the test suite uses (`settings._cfg`), so pinning the demo needs no edit
    to the settings file that could leak into a later pipeline or benchmark run.
    """
    node = settings._cfg
    *path, leaf = dotted.split(".")
    for part in path:
        node = node[part]
    node[leaf] = value


V2_FROZEN = dict(settings["eval.v3.v2_baseline"])
V2_RETRIEVAL_KEYS = (
    "vector_top_k",
    "structured_top_k",
    "bm25_top_k",
    "rerank_top_k",
    "rerank_candidate_view",
    "rerank_samples",
    "finisher_top_k",
)

PINNED = {f"retrieval.{key}": V2_FROZEN[key] for key in V2_RETRIEVAL_KEYS}
PINNED["llm.rerank_prompt"] = V2_FROZEN["rerank_prompt"]
# Spend under this notebook's own stage name and ceiling rather than the engine's
# default query stage (`stage6_pilot`) and the full $25 per-stage ceiling.
PINNED["llm.query_stage"] = DEMO_STAGE
PINNED["llm.max_stage_cost_usd"] = DEMO_CEILING_USD

for key, value in PINNED.items():
    override(key, value)

# Every pin is read back out of live settings, so what is printed below is what the
# query in section 2 will actually run on.
applied = {key: settings[key] for key in PINNED}
assert applied == PINNED, f"pins did not take: {applied} != {PINNED}"

# %% Print the stated configuration
weights = dict(settings["scoring.weights"])
config_rows = [
    ("Ranking configuration", f"frozen benchmark v2 (recorded digest `{V2_FROZEN['digest']}`)"),
    ("Candidate generation",
     f"vector top-{settings['retrieval.vector_top_k']} ∪ structured skill filter "
     f"top-{settings['retrieval.structured_top_k']} (union, not intersection)"),
    ("Lexical (BM25) arm",
     f"`bm25_top_k` = {settings['retrieval.bm25_top_k']} — off, as v2 froze it"),
    ("Re-rank window", f"top {settings['retrieval.rerank_top_k']} by deterministic score"),
    ("Re-rank candidate view",
     f"`{settings['retrieval.rerank_candidate_view']}` (full profiles, the v1/v2 view)"),
    ("Re-rank prompt", f"`prompts/{settings['llm.rerank_prompt']}.md`"),
    ("Re-rank samples",
     f"{settings['retrieval.rerank_samples']} (no permutation self-consistency)"),
    ("Strong-model finisher", f"`finisher_top_k` = {settings['retrieval.finisher_top_k']} — off"),
    ("Intent model", f"`{settings['llm.intent_model']}`"),
    ("Re-rank model", f"`{settings['llm.rerank_model']}`"),
    ("Embedding model", f"`{settings['embedding.model']}` ({settings['embedding.dims']} dims)"),
    ("Score weights", ", ".join(f"{name} {value}" for name, value in weights.items())),
    ("Recency half-life", f"{settings['projections.recency_half_life_days']} days"),
    ("Graph cutoff", f"built from data before {settings['dataset.holdout_cutoff']}"),
    ("Cost-log stage", f"`{settings['llm.query_stage']}`, ceiling ${DEMO_CEILING_USD:.2f}"),
]
display(Markdown(
    "### Engine configuration for this run\n\n"
    "| Setting | Value |\n|---|---|\n"
    + "\n".join(f"| {name} | {value} |" for name, value in config_rows)
    + "\n\nThe v2 digest above is the record's own identifier and is quoted, not "
    "recomputed: `settings.yaml` gained v3-only keys after v2 was frozen, so a digest "
    "computed today would differ from it even on an identical ranking configuration. "
    "The pins are therefore verified field by field against `eval.v3.v2_baseline` "
    "instead, by the assertion in the cell above."
))

# %% [markdown]
# ## 1. From ticket exhaust to a capability profile
#
# The pipeline never asks anyone what they are good at. It reads the tickets they
# resolved, groups them into person × project × quarter buckets, and has a cheap model
# write one **Contribution** per bucket: a summary, the specializations and skills it
# demonstrates, and the ticket keys that back the claim. Raw tickets stay out of the
# graph; the Contribution carries their keys as provenance pointers.
#
# The person below is picked by a fixed rule — most contributions in `MESOS`, the
# cluster-orchestration project in the slice, ties broken by id — chosen before the
# query in section 2 ran, not from its results.

# %% Pick the demo person and load their pipeline artifacts
DEMO_PROJECT = "MESOS"

by_person: dict[str, list[dict]] = {}
for line in (DATA_DIR / "contributions" / "normalized.jsonl").open(encoding="utf-8"):
    record = json.loads(line)
    if not record.get("skip") and record["project_key"] == DEMO_PROJECT:
        by_person.setdefault(record["person_id"], []).append(record)
PERSON_ID = min(by_person, key=lambda pid: (-len(by_person[pid]), pid))

# One pass over the 41MB bucket file: the demo person's buckets, plus a key -> ticket
# index used to resolve the citations the shortlist produces in section 2.
person_buckets: list[dict] = []
ticket_index: dict[str, dict] = {}
for line in (DATA_DIR / "buckets" / "buckets.jsonl").open(encoding="utf-8"):
    bucket = json.loads(line)
    if bucket["person_id"] == PERSON_ID:
        person_buckets.append(bucket)
    for ticket in bucket["tickets"]:
        # Only the fields displayed below, so indexing the whole corpus stays cheap.
        ticket_index.setdefault(
            ticket["key"], {"summary": ticket["summary"], "created_at": ticket.get("created_at")}
        )

person_contributions = sorted(by_person[PERSON_ID], key=lambda record: record["period"])
print(f"{PERSON_ID}: {len(person_buckets)} quarterly buckets, "
      f"{len(person_contributions)} extracted contributions, "
      f"{sum(len(b['tickets']) for b in person_buckets)} evidence tickets")


def clean(text: str) -> str:
    """TAWOS summaries arrive wrapped in literal quotes; strip only that wrapper."""
    text = (text or "").strip()
    return text[1:-1] if len(text) > 1 and text[0] == text[-1] == '"' else text


# %% The raw material: one quarter of one person's tickets
BUCKET = max(person_buckets, key=lambda b: (len(b["tickets"]), b["bucket_id"]))
display(Markdown(
    f"### Input — `{BUCKET['bucket_id']}` ({len(BUCKET['tickets'])} tickets)\n\n"
    "| Ticket | Created | Summary |\n|---|---|---|\n"
    + "\n".join(
        f"| `{t['key']}` | {str(t.get('created_at'))[:10]} | {clean(t['summary'])[:110]} |"
        for t in BUCKET["tickets"]
    )
))

# %% The extracted capability profile for exactly those tickets
extracted = next(c for c in person_contributions if c["contribution_id"] == BUCKET["bucket_id"])
display(Markdown(
    f"### Output — Contribution `{extracted['contribution_id']}`\n\n"
    f"**Summary.** {extracted['contribution_summary']}\n\n"
    f"**Specializations.** "
    + ", ".join(f"{s['name']} *({s['strength']})*" for s in extracted["specializations"])
    + "\n\n**Skills.** " + ", ".join(s["name"] for s in extracted["skills"])
    + f"\n\n**Confidence.** {extracted['confidence']} — {extracted['reason']}\n\n"
    f"**Evidence.** {', '.join(f'`{k}`' for k in extracted['evidence_ticket_keys'])}\n\n"
    f"The extraction cites {len(extracted['evidence_ticket_keys'])} of the "
    f"{len(BUCKET['tickets'])} tickets it read: the ones carrying the claim, not the "
    "whole bucket. Fewer than five citations is what downgrades a record's confidence."
))

# %% The whole person, projected into the graph
CAPABILITY_QUERY = """
MATCH (p:Person {id: $person_id})-[h]->(t)
WHERE type(h) IN ['HAS_SPECIALIZATION', 'HAS_SKILL']
RETURN type(h) AS kind, t.name AS term, h.evidence_count AS evidence_count,
       toString(h.last_used) AS last_used, h.decay_score AS decay_score
ORDER BY kind, evidence_count DESC, term
"""
with driver.session() as session:
    capabilities = [record.data() for record in session.run(CAPABILITY_QUERY, person_id=PERSON_ID)]

kinds = {"HAS_SPECIALIZATION": "specializations", "HAS_SKILL": "skills"}
tables = []
for kind, label in kinds.items():
    rows = [row for row in capabilities if row["kind"] == kind][:8]
    tables.append(
        f"**Top {label}** (of {sum(row['kind'] == kind for row in capabilities)})\n\n"
        "| Term | Evidence | Last used | Recency |\n|---|---:|---|---:|\n"
        + "\n".join(
            f"| {row['term']} | {row['evidence_count']} | {row['last_used'][:10]} "
            f"| {row['decay_score']:.3f} |"
            for row in rows
        )
    )
display(Markdown(
    f"### The graph's view of `{PERSON_ID}`\n\n"
    "`Evidence` counts the contributions behind a term; `Recency` is the stored decay "
    f"score at the graph cutoff ({settings['dataset.holdout_cutoff']}, "
    f"{settings['projections.recency_half_life_days']}-day half-life). Neither is "
    "recomputed at query time.\n\n" + "\n\n".join(tables)
))

# %% The same profile as a subgraph
SUBGRAPH_QUERY = """
MATCH (p:Person {id: $person_id})-[:MADE]->(c:Contribution)
WITH c ORDER BY c.period DESC LIMIT $limit
MATCH (c)-[d:DEMONSTRATES]->(t:Specialization)
RETURN c.id AS contribution_id, c.period AS period, t.name AS term,
       d.strength AS strength
"""
with driver.session() as session:
    edges = [record.data() for record in session.run(
        SUBGRAPH_QUERY, person_id=PERSON_ID, limit=6
    )]

net = Network(height="480px", width="100%", notebook=False, directed=True,
              cdn_resources="remote", bgcolor="#fcfcfb", font_color="#0b0b0b")
net.add_node(PERSON_ID, label=PERSON_ID, color="#2a78d6", shape="dot", size=26)
for edge in edges:
    net.add_node(edge["contribution_id"], label=edge["period"], color="#86b6ef",
                 shape="dot", size=16, title=edge["contribution_id"])
    net.add_edge(PERSON_ID, edge["contribution_id"], label="MADE")
    net.add_node(edge["term"], label=edge["term"], color="#eb6834", shape="box", size=14)
    net.add_edge(edge["contribution_id"], edge["term"],
                 label=edge["strength"] or "", title="DEMONSTRATES")
net.repulsion(node_distance=170, spring_length=170)

subgraph_html = net.generate_html(notebook=False)
(REPO_ROOT / "notebooks" / "demo_subgraph.html").write_text(subgraph_html, encoding="utf-8")
print(f"Person -> 6 most recent Contributions -> the Specializations they demonstrate "
      f"({len(edges)} edges); also saved to notebooks/demo_subgraph.html")
# Inlined as a data URI rather than a file reference, so the graph renders wherever the
# notebook is opened without depending on the Jupyter server's document root.
display(HTML(
    '<div><iframe style="width:100%;height:500px;border:1px solid #e1e0d9" '
    'src="data:text/html;base64,'
    + base64.b64encode(subgraph_html.encode("utf-8")).decode("ascii")
    + '"></iframe></div>'
))

# %% [markdown]
# ## 2. One live staffing query
#
# This is the only cell in the notebook that calls a model, and it runs **one** query.
# The brief goes to an intent parse, then candidates come from the union of vector
# search and a structured skill filter, then a deterministic weighted score ranks the
# whole pool, and only its top 15 go to the re-rank — which must cite evidence ticket
# keys the candidate actually owns. An entry citing anything else is rejected, not
# repaired, and the rejections are printed with the result.

# %% The live query — spends under stage `demo`
BRIEF = (
    "We need two backend engineers with distributed systems and streaming "
    "experience for a real-time data platform build"
)

spent_before = stage_cost_so_far(DEMO_STAGE)
result = query(BRIEF, driver)
spent_after = stage_cost_so_far(DEMO_STAGE)

print_result(result)
print(f"\nthis query cost ${spent_after - spent_before:.4f}; "
      f"stage '{DEMO_STAGE}' has now logged ${spent_after:.4f} of the "
      f"${DEMO_CEILING_USD:.2f} ceiling")

# %% The same shortlist, laid out to be read rather than audited
for shortlist in result.shortlists:
    display(Markdown(
        f"### Shortlist — {shortlist.role.role} (need {shortlist.role.count})\n\n"
        "| # | Person | Score | Fit | Why | Evidence |\n|---:|---|---:|---|---|---|\n"
        + "\n".join(
            f"| {rank} | `{person.person_id}` | {person.score:.3f} | {person.fit} "
            f"| {person.reason} "
            f"| {', '.join(f'`{key}`' for key in person.evidence_ticket_keys)} |"
            for rank, person in enumerate(shortlist.ranking, 1)
        )
        + f"\n\n{len(shortlist.rejected)} re-rank entries were rejected as unverifiable"
        + (f": {'; '.join(shortlist.rejected)}." if shortlist.rejected else ".")
    ))

# %% [markdown]
# ### The citations, resolved back to tickets
#
# The point of the shortlist is that its reasons are checkable. Below, every ticket key
# the top-ranked person's entry cited is looked up in the Stage 1 evidence the graph was
# built from — the same sanitized text the extraction saw.

# %% Resolve the top entry's citations
top = next((person for shortlist in result.shortlists for person in shortlist.ranking), None)
if top is None:
    display(Markdown("_The re-rank returned no validated entry — nothing to resolve._"))
else:
    cited = [(key, ticket_index.get(key)) for key in top.evidence_ticket_keys]
    display(Markdown(
        f"### `{top.person_id}` — {top.fit} fit, score {top.score:.3f}\n\n"
        f"**Reason given.** {top.reason}\n\n"
        "| Cited ticket | Created | Summary |\n|---|---|---|\n"
        + "\n".join(
            f"| `{key}` | {str(ticket.get('created_at'))[:10] if ticket else '—'} "
            f"| {clean(ticket['summary'])[:110] if ticket else 'not in retained evidence'} |"
            for key, ticket in cited
        )
    ))

# %% [markdown]
# ## 3. The graph is alive (delta batch) — not run here
#
# Task 8 also calls for a delta batch: move the cutoff one quarter later, re-run stages
# 1–5, and show a person's profile updating in place (every graph write is a `MERGE` on
# a stable key, so a re-run updates rather than duplicates). That is **not run in this
# notebook**: it re-extracts contributions with the model, which costs far more than the
# $0.50 this demo is authorized for, and it would mutate the graph the benchmark numbers
# in section 4 were measured on. It needs its own work order and its own approval.

# %% [markdown]
# ## 4. Does it actually work?
#
# The table below is transcribed from `docs/eval-results.md` — the frozen benchmark
# report — not recomputed here. Method: freeze history before each query's own time,
# hand the system a later ticket's creation-time text as a brief, and check where the
# person who actually resolved it lands in the ranking. Hit@1/5/10 is how often that
# person is in the top 1 / 5 / 10; MRR (mean reciprocal rank) rewards being near the
# top. Each version was tuned on 30 validation cases and run **once** on the 120 test
# cases below.
#
# `capgraph_full` is score + LLM re-rank; `capgraph_score` is the deterministic score
# alone, with no model call in the ranking at all.

# %% Transcribe the frozen test-split table
report = (REPO_ROOT / "docs" / "eval-results.md").read_text(encoding="utf-8")
section = report.split("## v1 vs v2 vs v3 — test split", 1)[1].split("\n### ", 1)[0]
table_lines = [line for line in section.splitlines() if line.startswith("|")]
display(Markdown("### Test split (120 cases), transcribed\n\n" + "\n".join(table_lines)))

rows = [[cell.strip(" `") for cell in line.strip("|").split("|")] for line in table_lines[2:]]
V2_COLUMN = 3  # | System | Metric | v1 | v2 | v3 | delta |
measured = {(row[0], row[1]): float(row[V2_COLUMN]) for row in rows}

# %% Chart the pinned (v2) column against the baselines
SYSTEMS = ["capgraph_full", "capgraph_score", "bm25", "vector_only", "most_active"]
METRICS = ["Hit@1", "Hit@5", "Hit@10"]
GRAPH_FILL, BASELINE_FILL = "#2a78d6", "#898781"   # our system / reference baseline
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e1e0d9", "#fcfcfb"

figure, axes = plt.subplots(1, 3, figsize=(13, 3.6), sharey=True, facecolor=SURFACE)
positions = range(len(SYSTEMS))
for axis, metric in zip(axes, METRICS, strict=True):
    values = [measured[(system, metric)] for system in SYSTEMS]
    colors = [GRAPH_FILL if system.startswith("capgraph") else BASELINE_FILL
              for system in SYSTEMS]
    axis.barh(list(positions), values, height=0.6, color=colors)
    for y, value in zip(positions, values, strict=True):
        axis.text(value + 0.02, y, f"{value:.3f}", va="center", fontsize=9, color=INK)
    axis.set_title(metric, fontsize=11, color=INK, loc="left")
    axis.set_xlim(0, 1.0)
    axis.set_facecolor(SURFACE)
    axis.invert_yaxis()
    axis.xaxis.grid(True, color=GRID, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.tick_params(colors=MUTED, labelsize=9, length=0)
    for side in ("top", "right", "bottom", "left"):
        axis.spines[side].set_visible(False)
axes[0].set_yticks(list(positions), [SYSTEM_LABELS[system] for system in SYSTEMS],
                   fontsize=9, color=INK)
figure.suptitle(
    "How often the person who actually did the work is in the top N — 120 test cases, "
    "frozen benchmark v2",
    fontsize=11, color=INK, x=0.02, ha="left",
)
figure.tight_layout(rect=(0, 0, 1, 0.9))
plt.show()

display(Markdown(
    "Blue is this system, grey the baselines. Read it with two caveats the frozen "
    "report states: the 'right answer' is the single person history recorded, so a "
    "differently qualified colleague counts as a miss; and `capgraph_score` — no model "
    "call in the ranking — is within noise of the full system at Hit@5 and Hit@10, "
    "which is the finding a product decision should be built on."
))

# %% [markdown]
# ## 5. What this demo spent

# %% The ledger lines this notebook wrote
ledger = [json.loads(line) for line in cost_log_path().read_text(encoding="utf-8").splitlines()]
demo_lines = [record for record in ledger if record["stage"] == DEMO_STAGE]
display(Markdown(
    f"### `data/llm_costs.jsonl`, stage `{DEMO_STAGE}`\n\n"
    "| Model | Purpose | Input tokens | Output tokens | Cost |\n|---|---|---:|---:|---:|\n"
    + "\n".join(
        f"| `{record['model']}` | {record.get('purpose', '—')} | {record['input_tokens']} "
        f"| {record['output_tokens']} | ${record['cost_usd']:.4f} |"
        for record in demo_lines
    )
    + f"\n\n**{len(demo_lines)} calls, ${sum(r['cost_usd'] for r in demo_lines):.4f} total** "
    f"against the ${DEMO_CEILING_USD:.2f} ceiling. Whole-ledger total across every stage "
    f"ever run: ${sum(r['cost_usd'] for r in ledger):.4f} over {len(ledger)} calls."
))

# %%
driver.close()
