# %% [markdown]
# # Capability Graph PoC — Demo
# From Jira exhaust to evidence-backed staffing shortlists.
# (jupytext percent format — `make demo` converts to .ipynb)

# %% Setup
from capgraph.pipeline.stage5_graph import get_driver
from capgraph.query.engine import print_result, query

driver = get_driver()

# %% [markdown]
# ## 1. From tickets to a capability profile
# Pick one person; show their raw tickets, extracted contributions, then their subgraph.

# %%
# TODO: load one person's tickets (parquet) and contributions (jsonl); display side by side.
# TODO: pyvis viz of their Person–Contribution–Skill subgraph from Neo4j.

# %% [markdown]
# ## 2. Live staffing queries

# %%
result = query("We need two backend engineers with distributed systems and streaming "
               "experience for a real-time data platform build")
print_result(result)

# %%
# TODO: 2 more briefs, one phrased as an agency-style campaign brief.
# TODO: pyvis viz of the winning candidates' matched subgraph for one query.

# %% [markdown]
# ## 3. The graph is alive (delta batch)
# Profile before/after ingesting one more quarter (see implementation plan Task 8).

# %%
# TODO: before/after comparison for one person.

# %% [markdown]
# ## 4. Does it actually work? (eval)

# %%
# TODO: load data/eval/results.md table + bar chart (capgraph vs bm25 vs vector vs most-active).
