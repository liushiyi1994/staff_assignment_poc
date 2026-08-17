"""Query step 4: deterministic weighted score, then LLM re-rank of top-K with reasons.

The weighted score does the heavy lifting and is dataset-independent, pure, and unit
tested (non-negotiable #4: the LLM never ranks the full candidate pool). Four
components, weighted by ``scoring.weights``:

* ``specialization_match`` / ``skill_overlap`` — the fraction of the role's requested
  specializations / skills this person satisfies, judged on the canonical terms the
  alias-aware resolution mapped each request onto.
* ``recency`` — the highest **stored** ``decay_score`` among the capability edges that
  made this person relevant. Recency is never recomputed here: the query path contains
  no date arithmetic at all, and therefore no wall-clock dependency.
* ``evidence_strength`` — saturating in the number of distinct contributions behind the
  match. Contributions are counted once each: a contribution demonstrating six of the
  role's terms is one piece of evidence, not six.

A component the role does not exercise (no specializations asked for, no skills asked
for) is *dropped* rather than scored zero, and the remaining weights are renormalized,
so the score stays comparable across briefs and always lands in [0, 1].
``score_parts`` records exactly the components that were summed.

A candidate the vector arm alone found may satisfy none of the parsed terms — that is
the point of the union. Its relevance evidence is then the contributions the vector arm
surfaced: recency comes from the stored decay of the capability edges those
contributions demonstrate, and evidence strength from how many of them there are. A
candidate only the lexical arm found has no per-contribution hits at all, so the
contributions expansion retained stand in the same way. All three paths therefore count
the same unit (contributions), and no arm's exclusive finds score a structural zero.

The LLM re-rank consumes the prompt named by ``llm.rerank_prompt`` and is *not*
trusted: an entry naming a person who was not sent, or citing an evidence ticket key
that is not in that person's own contributions, is rejected rather than repaired. That
validation is identical for every candidate view and every sampling mode below —
nothing in benchmark v3 relaxes it.

Three v3 levers sit on top of that single call, each switched off by its own setting so
the v1/v2 behaviour is the default:

* ``retrieval.rerank_candidate_view`` chooses between the full profile view and the
  compact **card** view (pseudonym, top terms with recency, the deterministic score,
  a few cite-able evidence keys). Shorter, uniform contexts are the documented remedy
  for lost-in-the-middle failures, and they are what makes a wider window affordable.
* ``retrieval.rerank_samples`` > 1 runs **permutation self-consistency** (Tang et al.,
  NAACL 2024): the same listwise call over independently shuffled candidate orders,
  aggregated by Borda count, which marginalizes out listwise position bias.
* ``retrieval.finisher_top_k`` > 0 hands the head of the ranking to a stronger model
  for one setwise ordering pass. The finisher may only **reorder** entries the
  validated re-rank already produced; it never contributes prose or citations, so no
  evidence reaches the shortlist without having passed the validator above.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor

from .. import improvements
from ..llm import call_json
from ..models import (
    CandidateProfile,
    Contribution,
    PersonCapability,
    RankedPerson,
    RoleSpec,
)
from ..settings import load_prompt, settings
from .retrieve import TermResolution, normalize_term

SCORE_COMPONENTS = (
    "specialization_match",
    "skill_overlap",
    "recency",
    "evidence_strength",
)

# The two components :func:`score_candidate` always computes. The other two are dropped
# when the role does not exercise them, so a weighting that gives these two nothing is
# unusable: a role that asked for no specializations and no skills would have no
# weighted component left to rank on.
ALWAYS_SCORED_COMPONENTS = ("recency", "evidence_strength")

FIT_VALUES = ("strong", "good", "related")
DEFAULT_FIT = "related"

# Cost-log labels, so a stage's spend can be split by call type.
PURPOSE = "rerank"
FINISH_PURPOSE = "finish"

# How a candidate is rendered for the re-rank prompt.
VIEW_PROFILE = "profile"        # v1/v2: capabilities plus contribution summaries
VIEW_CARD = "card"              # v3 lever 2: fixed-size card, no summaries
VIEW_HYBRID = "hybrid"          # probe: full detail for the head, cards for the tail
VIEWS = (VIEW_PROFILE, VIEW_CARD, VIEW_HYBRID)

# What shape of answer the re-rank asks the model for.
RERANK_LISTWISE = "listwise"        # v1-v4: one entry per candidate, with reasons
RERANK_PERMUTATION = "permutation"  # RankGPT (arXiv:2304.09542): an ordering of ids only
RERANK_MODES = (RERANK_LISTWISE, RERANK_PERMUTATION)

# TAWOS keys are PROJECT-123. Reasons are scanned for these so a fabricated citation
# in prose is caught even when the entry's declared key list is clean.
TICKET_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")


def _norm(term: str) -> str:
    return normalize_term(term)


# ---------- deterministic score ----------

def identity_resolution(role: RoleSpec) -> TermResolution:
    """Fallback when no graph-backed resolution is supplied: exact-name matching."""
    return TermResolution(
        specializations={_norm(t): [t] for t in role.specializations},
        skills={_norm(t): [t] for t in role.skills},
    )


def weights() -> dict[str, float]:
    """scoring.weights, validated. Every component must be configured explicitly.

    An enabled wave-1 component (backlog G5's ``confidence``, G11a's
    ``activity_currency``) is appended with its own configured weight rather than folded
    into ``scoring.weights``: those four weights were chosen on the v2 validation split
    and their meaning is the frozen record, so a flag must not silently redefine them.
    With every flag off this returns exactly the four the v1-v3 runs used.
    """
    configured = settings["scoring.weights"]
    if not isinstance(configured, Mapping):
        raise TypeError("scoring.weights must be a mapping of component to weight")
    missing = [name for name in SCORE_COMPONENTS if name not in configured]
    if missing:
        raise ValueError(f"scoring.weights is missing {', '.join(missing)}")
    values = {name: float(configured[name]) for name in SCORE_COMPONENTS}
    if improvements.confidence_mode() == improvements.COMPONENT:
        values[improvements.CONFIDENCE_COMPONENT] = improvements.confidence_weight()
    if improvements.activity_currency_mode() == improvements.COMPONENT:
        values[improvements.ACTIVITY_COMPONENT] = improvements.activity_currency_weight()
    if any(value < 0 for value in values.values()):
        raise ValueError("scoring.weights must not contain negative weights")
    return values


def evidence_strength(supporting_contributions: int) -> float:
    """Saturating evidence depth in [0, 1]; full credit at the configured count."""
    full_credit = float(settings["scoring.evidence_full_credit_count"])
    if full_credit <= 0:
        raise ValueError("scoring.evidence_full_credit_count must be positive")
    if supporting_contributions <= 0:
        return 0.0
    return min(1.0, math.sqrt(supporting_contributions / full_credit))


def _satisfied(
    requested: Sequence[str],
    resolved: Mapping[str, list[str]],
    have: Mapping[str, PersonCapability],
    *,
    weighted: bool = False,
) -> tuple[list[str], list[PersonCapability], float]:
    """Requested terms this person satisfies, the edges that do it, and their credit.

    Credit is one point per satisfied request — so ``credit == len(matched_terms)`` and
    the match fraction is the plain count it has always been. Under backlog G6
    (``weighted``) a satisfied specialization earns credit in proportion to how much of
    its evidence called the term *primary* rather than *secondary*, and a request that
    several canonical terms satisfy takes the best of them, because the person does have
    that capability at that strength.
    """
    matched_terms: list[str] = []
    caps: dict[str, PersonCapability] = {}
    credit = 0.0
    for request in requested:
        canonical = resolved.get(_norm(request), [])
        hits = [have[_norm(term)] for term in canonical if _norm(term) in have]
        if hits:
            matched_terms.append(request)
            caps.update({_norm(cap.term): cap for cap in hits})
            credit += (
                max(improvements.strength_credit(cap.primary_share) for cap in hits)
                if weighted
                else 1.0
            )
    return matched_terms, [caps[term] for term in sorted(caps)], credit


def _vector_relevance(
    candidate: CandidateProfile, have: Mapping[str, PersonCapability]
) -> tuple[list[PersonCapability], int]:
    """Relevance evidence for a candidate no parsed term matched: its vector hits."""
    hit_ids = set(candidate.vector_hit_contribution_ids)
    terms = {
        _norm(ref.name)
        for contribution in candidate.contributions
        if contribution.contribution_id in hit_ids
        for ref in [*contribution.specializations, *contribution.skills]
    }
    caps = [have[term] for term in sorted(terms) if term in have]
    return caps, len(hit_ids)


def _profile_relevance(
    candidate: CandidateProfile, have: Mapping[str, PersonCapability]
) -> tuple[list[PersonCapability], int]:
    """Relevance evidence for a candidate the lexical arm alone found.

    BM25 ranks a person's whole evidence document, so unlike the vector arm it cannot
    say *which* contributions matched. The retained contributions expansion carried in
    stand in for them, exactly as the vector arm's hits do: recency comes from the
    stored decay of the capability edges those contributions demonstrate, and evidence
    strength from how many of them there are.

    Without this a lexical-only candidate scores a structural zero on every component
    and is ranked last however well its evidence matches — the arm would raise
    candidate recall and nothing else. It is reached only when a candidate has neither
    a matched term nor a vector hit, which no v1/v2 candidate could be: those pools
    came from the two arms that guarantee one or the other.
    """
    terms = {
        _norm(ref.name)
        for contribution in candidate.contributions
        for ref in [*contribution.specializations, *contribution.skills]
    }
    caps = [have[term] for term in sorted(terms) if term in have]
    return caps, len(candidate.contributions)


def relevant_contributions(candidate: CandidateProfile) -> list[Contribution]:
    """The retained contributions that make this candidate relevant, best evidence first.

    The same three-way fallback the score's ``recency``/``evidence_strength`` components
    use: contributions demonstrating a term the role asked for, else the ones the vector
    arm surfaced, else everything expansion retained for a lexical-only find. Used by
    backlog G5, which needs the *confidence* of that evidence rather than its count.
    Call it after :func:`score_candidate` has set the matched-term lists.
    """
    matched = {_norm(term) for term in candidate.matched_specializations + candidate.matched_skills}
    if matched:
        by_term = [
            contribution
            for contribution in candidate.contributions
            if matched & {
                _norm(ref.name)
                for ref in [*contribution.specializations, *contribution.skills]
            }
        ]
        if by_term:
            return by_term
    hit_ids = set(candidate.vector_hit_contribution_ids)
    if hit_ids:
        hits = [c for c in candidate.contributions if c.contribution_id in hit_ids]
        if hits:
            return hits
    return list(candidate.contributions)


def confidence_signal(candidate: CandidateProfile) -> float | None:
    """Mean configured credit of the confidence labels on this match's evidence.

    ``None`` when the candidate carries no contributions at all — a hand-built profile,
    or a pool assembled without expansion. The caller drops the component in that case
    rather than scoring it zero, which is how every other inapplicable component here is
    handled: absent evidence about confidence is not evidence of low confidence.
    """
    evidence = relevant_contributions(candidate)
    if not evidence:
        return None
    return sum(improvements.confidence_value(c.confidence) for c in evidence) / len(evidence)


def score_candidate(
    candidate: CandidateProfile,
    role: RoleSpec,
    resolution: TermResolution | None = None,
) -> CandidateProfile:
    """Transparent weighted score in [0,1]. Parts kept for explainability."""
    resolution = identity_resolution(role) if resolution is None else resolution
    have_spec = {_norm(cap.term): cap for cap in candidate.specializations}
    have_skill = {_norm(cap.term): cap for cap in candidate.skills}

    spec_requests, spec_caps, spec_credit = _satisfied(
        role.specializations,
        resolution.specializations,
        have_spec,
        weighted=improvements.specialization_strength_enabled(),
    )
    skill_requests, skill_caps, _ = _satisfied(role.skills, resolution.skills, have_skill)
    candidate.matched_specializations = [cap.term for cap in spec_caps]
    candidate.matched_skills = [cap.term for cap in skill_caps]

    relevant = spec_caps + skill_caps
    have = {**have_skill, **have_spec}
    if relevant:
        # Contributions behind the match, counted once each by expansion. Without that
        # count (a profile built by hand, or expanded with no resolved terms) the
        # matched edges stand in — a lower bound, never an inflated one.
        supporting = candidate.matched_contribution_count or len(relevant)
    elif candidate.vector_hit_contribution_ids:
        relevant, supporting = _vector_relevance(candidate, have)
    else:
        relevant, supporting = _profile_relevance(candidate, have)

    parts: dict[str, float] = {}
    if role.specializations:
        parts["specialization_match"] = spec_credit / len(role.specializations)
    if role.skills:
        parts["skill_overlap"] = len(skill_requests) / len(role.skills)
    parts["recency"] = max((cap.decay_score for cap in relevant), default=0.0)
    parts["evidence_strength"] = evidence_strength(supporting)
    _apply_improvement_components(candidate, parts)

    # Score first: an unusable weighting must abort before the candidate is mutated.
    score = combine_parts(parts)
    candidate.score_parts = {name: round(value, 4) for name, value in parts.items()}
    candidate.score = score
    return candidate


def _apply_improvement_components(
    candidate: CandidateProfile, parts: dict[str, float]
) -> dict[str, float]:
    """Fold the enabled wave-1 signals into ``parts``. A no-op with every flag off.

    Two of the three ranking signals the PRD lists and this system extracts but never
    scored (``docs/improvement-backlog.md`` G5, G11a). Both are default OFF and stay off
    until benchmark v4 can measure them; this function is where they enter the score, so
    there is one place to read and one place to switch them off again.
    """
    mode = improvements.confidence_mode()
    if mode != improvements.OFF:
        value = confidence_signal(candidate)
        if value is not None:
            if mode == improvements.COMPONENT:
                parts[improvements.CONFIDENCE_COMPONENT] = value
            else:
                # Multiplier form: confidence discounts how much the evidence *count*
                # is worth, rather than competing with it for weight.
                parts["evidence_strength"] *= value
    if improvements.activity_currency_mode() == improvements.COMPONENT:
        parts[improvements.ACTIVITY_COMPONENT] = float(candidate.activity_currency)
    return parts


def split_by_count(
    ranking: Sequence[RankedPerson], count: int
) -> tuple[list[str], list[str]]:
    """Backlog G8: the role's proposed set and its alternates, by ``RoleSpec.count``.

    Purely a partition of an already-final ranking — nothing here reorders, rescores, or
    filters anyone, and the alternates are the same people in the same order they were
    already in. A count below 1 is read as 1: the intent prompt defaults it to 1 and a
    role needing nobody is not a role.
    """
    ids = [person.person_id for person in ranking]
    head = max(int(count), 1)
    return ids[:head], ids[head:]


def combine_parts(
    parts: Mapping[str, float], configured: Mapping[str, float] | None = None
) -> float:
    """Weighted mean of the components that applied, renormalized over their weights.

    Separate from :func:`score_candidate` so a weight experiment can re-score
    checkpointed components through the identical arithmetic instead of a second
    implementation that could drift from it.
    """
    configured = weights() if configured is None else configured
    missing = [name for name in parts if name not in configured]
    if missing:
        raise ValueError(f"no weight configured for {', '.join(sorted(missing))}")
    total = sum(configured[name] for name in parts)
    if total <= 0:
        raise ValueError("the applicable scoring.weights sum to zero — nothing to rank on")
    return round(sum(configured[name] * value for name, value in parts.items()) / total, 4)


# ---------- LLM re-rank ----------

def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def own_evidence_keys(candidate: CandidateProfile) -> set[str]:
    """Every evidence ticket key reachable from this candidate's own contributions."""
    return {
        key
        for contribution in candidate.contributions
        for key in contribution.evidence_ticket_keys
    }


def validated_evidence(
    entry: Mapping, candidate: CandidateProfile
) -> tuple[list[str], str | None]:
    """Evidence keys an entry may claim, or a rejection reason.

    Keys are taken from the declared list *and* from any key-shaped token in the prose,
    so citing another candidate's ticket in the sentence is caught too. An entry that
    cites nothing verifiable is rejected: an unevidenced reason is the failure mode this
    whole design exists to prevent.
    """
    declared = entry.get("evidence_ticket_keys") or []
    if not isinstance(declared, list):
        return [], "evidence_ticket_keys is not a list"
    reason = str(entry.get("reason") or "")
    cited = _dedupe([str(key).strip() for key in declared] + TICKET_KEY_PATTERN.findall(reason))
    cited = [key for key in cited if key]
    if not cited:
        return [], "cites no evidence ticket key"
    own = own_evidence_keys(candidate)
    foreign = [key for key in cited if key not in own]
    if foreign:
        return [], f"cites evidence not in this person's contributions: {', '.join(foreign)}"
    return cited, None


def profile_view(candidate: CandidateProfile) -> dict:
    """The candidate as the re-rank prompt sees it: capabilities plus cite-able keys."""
    max_keys = int(settings["retrieval.rerank_evidence_keys_per_contribution"])
    max_contributions = int(settings["retrieval.rerank_contributions_per_candidate"])

    def capabilities(caps: Sequence[PersonCapability], limit: int) -> list[dict]:
        ordered = sorted(caps, key=lambda cap: (-cap.evidence_count, cap.term))[:limit]
        return [
            {
                "term": cap.term,
                "evidence_count": cap.evidence_count,
                "last_used": str(cap.last_used),
            }
            for cap in ordered
        ]

    return {
        "person_id": candidate.person_id,
        "person_name": candidate.person_name,
        "score": candidate.score,
        "found_by": candidate.retrieval_sources,
        "specializations": capabilities(
            candidate.specializations,
            int(settings["retrieval.rerank_specializations_per_candidate"]),
        ),
        "skills": capabilities(
            candidate.skills, int(settings["retrieval.rerank_skills_per_candidate"])
        ),
        "contributions": [
            {
                "summary": contribution.contribution_summary,
                "period": contribution.period,
                "project": contribution.project_key,
                "evidence_tickets": contribution.evidence_ticket_keys[:max_keys],
            }
            for contribution in candidate.contributions[:max_contributions]
        ],
    }


def card_view(candidate: CandidateProfile) -> dict:
    """The compact card: identity, deterministic score, top terms, cite-able keys.

    Every card has the same shape and roughly the same length, whatever the candidate's
    profile looks like — that uniformity is the lever. Contribution summaries, which
    dominate :func:`profile_view` and vary tenfold in length between candidates, are
    dropped; the evidence keys they carried stay, so the citation rules and the
    validator are unaffected. ``score`` is the deterministic weighted score: putting it
    in front of the model is the point of the card, not incidental metadata.
    """
    max_keys = int(settings["retrieval.card_evidence_keys_per_candidate"])

    def terms(caps: Sequence[PersonCapability], limit: int) -> list[str]:
        ordered = sorted(caps, key=lambda cap: (-cap.evidence_count, cap.term))[:limit]
        return [f"{cap.term} (x{cap.evidence_count}, last {cap.last_used})" for cap in ordered]

    return {
        "person_id": candidate.person_id,
        "person_name": candidate.person_name,
        "score": candidate.score,
        "found_by": candidate.retrieval_sources,
        "specializations": terms(
            candidate.specializations,
            int(settings["retrieval.card_specializations_per_candidate"]),
        ),
        "skills": terms(
            candidate.skills, int(settings["retrieval.card_skills_per_candidate"])
        ),
        "evidence_tickets": _dedupe(
            key
            for contribution in candidate.contributions
            for key in contribution.evidence_ticket_keys
        )[:max_keys],
    }


def candidate_view(candidate: CandidateProfile) -> dict:
    """Render one candidate the way ``retrieval.rerank_candidate_view`` asks for.

    The hybrid view is refused here rather than approximated: which people it details is
    a property of the *window*, not of one person, so it is only renderable through
    :func:`candidate_views`. A silent fallback to the card would let a caller believe it
    had sent full evidence when it had not.
    """
    view = str(settings["retrieval.rerank_candidate_view"])
    if view == VIEW_CARD:
        return card_view(candidate)
    if view == VIEW_PROFILE:
        return profile_view(candidate)
    if view == VIEW_HYBRID:
        raise ValueError(
            f"the {VIEW_HYBRID!r} view depends on the whole window, so one candidate "
            "cannot be rendered on its own — call candidate_views() instead"
        )
    raise ValueError(
        f"retrieval.rerank_candidate_view is {view!r}; use one of {', '.join(VIEWS)}"
    )


def candidate_views(candidates: Sequence[CandidateProfile]) -> list[dict]:
    """Render a whole window. The only renderer the re-rank call itself uses.

    Per-window rather than per-candidate because of the hybrid view, whose whole point
    is that the window is *not* uniform: the head of the deterministic ordering is spent
    on full contribution detail (:func:`profile_view`) and everyone else keeps the
    compact card. The head is chosen by deterministic score, never by position in
    ``candidates``, so reversing the presentation order details exactly the same people
    — otherwise the view and the G7 order flag would be confounded.
    """
    if str(settings["retrieval.rerank_candidate_view"]) != VIEW_HYBRID:
        return [candidate_view(candidate) for candidate in candidates]
    top_k = max(int(settings["retrieval.rerank_hybrid_detail_top_k"]), 0)
    detailed = {
        candidate.person_id
        for candidate in sorted(candidates, key=lambda c: (-c.score, c.person_id))[:top_k]
    }
    return [
        profile_view(candidate) if candidate.person_id in detailed else card_view(candidate)
        for candidate in candidates
    ]


def rerank_mode() -> str:
    """The answer shape the re-rank asks for, validated."""
    mode = str(settings["retrieval.rerank_mode"])
    if mode not in RERANK_MODES:
        raise ValueError(
            f"retrieval.rerank_mode is {mode!r}; use one of {', '.join(RERANK_MODES)}"
        )
    return mode


def rerank_input(candidates: Sequence[CandidateProfile]) -> list[CandidateProfile]:
    """Top retrieval.rerank_top_k by deterministic score; ties break on person_id."""
    top_k = int(settings["retrieval.rerank_top_k"])
    return sorted(candidates, key=lambda c: (-c.score, c.person_id))[:top_k]


def rerank_output_tokens(n_candidates: int) -> int:
    """Output allowance for a re-rank of ``n_candidates``, capped.

    The answer is one entry per candidate, so a window twice as wide needs roughly
    twice the allowance; a fixed one would truncate a wide window's answer mid-JSON and
    pay for a full retry. The configured base and per-candidate figures reproduce the
    v1/v2 allowance exactly at a window of 15.
    """
    return min(
        int(settings["llm.rerank_output_tokens_base"])
        + int(settings["llm.rerank_output_tokens_per_candidate"]) * n_candidates,
        int(settings["llm.rerank_max_output_tokens"]),
    )


def rerank_max_tokens(n_candidates: int) -> int:
    """Output allowance for the configured re-rank mode.

    A permutation answer is an ordering of ids and nothing else, so it needs a small
    fixed allowance rather than one that grows with the window: the entries a listwise
    answer spends its tokens on — a sentence and a citation list per person — are not
    written at all. The allowance is still generous, because a reasoning model bills its
    reasoning as output and a truncated answer costs a full retry.
    """
    if rerank_mode() == RERANK_PERMUTATION:
        return int(settings["llm.rerank_permutation_output_tokens"])
    return rerank_output_tokens(n_candidates)


def sample_orders(person_ids: Sequence[str], samples: int) -> list[list[str]]:
    """One candidate order per self-consistency sample.

    A single sample keeps the deterministic score order, so the default configuration
    sends exactly what v1 and v2 sent. Two or more samples are *all* shuffled: the
    method marginalizes position bias out by averaging over permutations, and leaving
    one sample in score order would re-import the bias it exists to cancel. The card
    carries the deterministic score, so that signal is not lost with the ordering.

    Shuffles are seeded from the shortlist itself, so a re-run of the same case sends
    the same permutations and the arm stays reproducible.

    Backlog G7: ``improvements.rerank_presentation_order: reverse`` sends that single
    sample worst-first instead. It is an ablation of the un-ablated prior — best-first
    presentation — and applies only at one sample, because a shuffled arm has no
    presentation order left to reverse.
    """
    ids = [str(person_id) for person_id in person_ids]
    if samples < 1:
        raise ValueError("retrieval.rerank_samples must be at least 1")
    if samples == 1:
        if improvements.rerank_presentation_order() == improvements.ORDER_REVERSE:
            return [ids[::-1]]
        return [ids]
    seed = int.from_bytes(hashlib.sha256("\0".join(ids).encode("utf-8")).digest(), "big")
    orders = []
    for index in range(samples):
        order = list(ids)
        random.Random(seed ^ index).shuffle(order)
        orders.append(order)
    return orders


def borda_order(
    rankings: Sequence[Sequence[str]], shortlist: Sequence[CandidateProfile]
) -> list[str]:
    """Borda count over several sampled rankings of the same shortlist.

    A person at position *i* of a sample scores ``len(shortlist) - i``; a person a
    sample omitted scores nothing from it. Only people at least one sample ranked are
    returned, so an aggregated arm never has wider coverage than a single call would —
    that would make the comparison a coverage difference rather than an ordering one.
    Ties fall back to the deterministic score, then the person id.
    """
    n = len(shortlist)
    score = {c.person_id: c.score for c in shortlist}
    points = {person_id: 0.0 for person_id in score}
    ranked_by_any: set[str] = set()
    for ranking in rankings:
        for position, person_id in enumerate(ranking):
            if person_id in points:
                points[person_id] += n - position
                ranked_by_any.add(person_id)
    return sorted(
        ranked_by_any, key=lambda person_id: (-points[person_id], -score[person_id], person_id)
    )


def _validated_entries(
    raw: Mapping, by_id: Mapping[str, CandidateProfile]
) -> tuple[list[RankedPerson], list[str]]:
    """Turn one model answer into validated ranked people plus rejection reasons."""
    ranking: list[RankedPerson] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for entry in raw.get("ranking") or []:
        if not isinstance(entry, Mapping):
            rejected.append(f"malformed entry: {entry!r}")
            continue
        person_id = str(entry.get("person_id") or "")
        candidate = by_id.get(person_id)
        if candidate is None:
            rejected.append(f"{person_id or '<missing id>'}: not among the ranked candidates")
            continue
        if person_id in seen:
            rejected.append(f"{person_id}: duplicate entry")
            continue
        keys, problem = validated_evidence(entry, candidate)
        if problem is not None:
            rejected.append(f"{person_id}: {problem}")
            continue
        seen.add(person_id)
        fit = str(entry.get("fit") or DEFAULT_FIT)
        ranking.append(
            RankedPerson(
                person_id=candidate.person_id,
                person_name=candidate.person_name,
                fit=fit if fit in FIT_VALUES else DEFAULT_FIT,
                reason=str(entry.get("reason") or ""),
                score=candidate.score,
                found_by=list(candidate.retrieval_sources),
                matched_specializations=list(candidate.matched_specializations),
                matched_skills=list(candidate.matched_skills),
                evidence_ticket_keys=keys,
            )
        )
    return ranking, rejected


def _permutation_entries(
    raw: Mapping, by_id: Mapping[str, CandidateProfile]
) -> tuple[list[RankedPerson], list[str]]:
    """Turn a permutation answer into **ranking-only** entries plus rejection reasons.

    A permutation answer carries no prose and no citations, so there is nothing here for
    :func:`validated_evidence` to check and nothing an entry could claim: every person
    below is returned with an empty ``reason`` and an empty ``evidence_ticket_keys``.
    That is the whole safety argument for this mode, and it is a structural one — the
    model is never given the opportunity to write an unevidenced sentence, so no
    uncited claim can reach an output. What is still checked is identity: an id that was
    not in the window, or named twice, is rejected exactly as a listwise entry naming an
    unknown person is, and rejected ids are discarded rather than repaired.

    The consequence is deliberate and is not hidden: this mode ranks, and a shortlist
    built from it has no reasons to show. It is a ranking-quality instrument, not a
    drop-in replacement for the listwise re-rank.
    """
    ranking: list[RankedPerson] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for value in raw.get("order") or []:
        person_id = str(value).strip() if not isinstance(value, Mapping) else str(
            value.get("person_id") or ""
        ).strip()
        candidate = by_id.get(person_id)
        if candidate is None:
            rejected.append(f"{person_id or '<missing id>'}: not among the ranked candidates")
            continue
        if person_id in seen:
            rejected.append(f"{person_id}: duplicate entry")
            continue
        seen.add(person_id)
        ranking.append(
            RankedPerson(
                person_id=candidate.person_id,
                person_name=candidate.person_name,
                fit=DEFAULT_FIT,
                reason="",
                score=candidate.score,
                found_by=list(candidate.retrieval_sources),
                matched_specializations=list(candidate.matched_specializations),
                matched_skills=list(candidate.matched_skills),
                evidence_ticket_keys=[],
            )
        )
    return ranking, rejected


def rerank_prompt_text(
    brief: str, role: RoleSpec, ordered: Sequence[CandidateProfile]
) -> str:
    """The exact prompt the re-rank sends for this window, under current settings.

    Split out of :func:`_rerank_call` so an offline cost pre-flight can price the real
    request rather than a second rendering of it that could drift from what is sent.
    """
    return load_prompt(
        str(settings["llm.rerank_prompt"]),
        brief=brief,
        role_json=role.model_dump_json(),
        candidates_json=json.dumps(candidate_views(ordered), indent=1),
    )


def _rerank_call(
    brief: str,
    role: RoleSpec,
    ordered: Sequence[CandidateProfile],
    *,
    stage: str,
    max_tokens: int,
) -> dict:
    return call_json(
        rerank_prompt_text(brief, role, ordered),
        model=settings["llm.rerank_model"],
        stage=stage,
        max_tokens=max_tokens,
        purpose=PURPOSE,
    )


def rerank(
    brief: str,
    role: RoleSpec,
    candidates: Sequence[CandidateProfile],
    *,
    stage: str | None = None,
    max_tokens: int | None = None,
) -> tuple[list[RankedPerson], list[str]]:
    """LLM re-rank of the shortlist. Returns (ranking, rejected entry descriptions).

    With ``retrieval.rerank_samples`` at 1 this is one call and the model's own order
    is the answer. Above 1 the samples run concurrently over shuffled candidate orders
    and are aggregated by Borda count; each person keeps the reason and citations of
    the first sample that ranked them, so every shortlisted entry is still one the
    validator passed verbatim.

    Under ``retrieval.rerank_mode: permutation`` the model answers with an ordering of
    ids instead of an entry per person (:func:`_permutation_entries`). Self-consistency
    is refused in that mode rather than silently combined: shuffle-and-vote over
    permutations is a measured dead end in this project, and Borda over orderings is
    exactly the arm that failed.
    """
    shortlist = rerank_input(candidates)
    if not shortlist:
        return [], []

    mode = rerank_mode()
    stage = stage or str(settings["llm.query_stage"])
    max_tokens = rerank_max_tokens(len(shortlist)) if max_tokens is None else max_tokens
    by_id = {c.person_id: c for c in shortlist}
    samples_requested = int(settings["retrieval.rerank_samples"])
    if mode == RERANK_PERMUTATION and samples_requested != 1:
        raise ValueError(
            f"retrieval.rerank_mode is {RERANK_PERMUTATION!r} and "
            f"retrieval.rerank_samples is {samples_requested} — permutation "
            "self-consistency was measured and rejected on this project (v3), so it is "
            "refused rather than re-run by accident"
        )
    orders = sample_orders([c.person_id for c in shortlist], samples_requested)
    batches = [[by_id[person_id] for person_id in order] for order in orders]

    def run(ordered: Sequence[CandidateProfile]) -> dict:
        return _rerank_call(brief, role, ordered, stage=stage, max_tokens=max_tokens)

    if len(batches) == 1:
        answers = [run(batches[0])]
    else:
        with ThreadPoolExecutor(max_workers=len(batches)) as pool:
            answers = list(pool.map(run, batches))

    parse = _permutation_entries if mode == RERANK_PERMUTATION else _validated_entries
    samples = [parse(answer, by_id) for answer in answers]
    if len(samples) == 1:
        return samples[0]

    # The sample marker goes on the end: run_diagnostics reads a rejection's *class*
    # off the front of the string, and a prefix here would rename every reason.
    rejected = [
        f"{problem} [sample {index}]"
        for index, (_, problems) in enumerate(samples)
        for problem in problems
    ]
    entries: dict[str, RankedPerson] = {}
    for ranking, _ in samples:
        for person in ranking:
            entries.setdefault(person.person_id, person)
    order = borda_order(
        [[person.person_id for person in ranking] for ranking, _ in samples], shortlist
    )
    return [entries[person_id] for person_id in order if person_id in entries], rejected


def finish(
    brief: str,
    role: RoleSpec,
    ranking: Sequence[RankedPerson],
    candidates: Sequence[CandidateProfile],
    *,
    stage: str | None = None,
) -> tuple[list[RankedPerson], list[str]]:
    """Reorder the head of a validated ranking with a stronger model, or pass it through.

    Deliberately narrow: the finisher receives the same cards the re-rank saw for the
    top ``retrieval.finisher_top_k`` people and answers with an ordering of their ids.
    It cannot add a person, write a reason, or cite anything — every entry that reaches
    the shortlist has already passed :func:`validated_evidence`. Ids it omits keep their
    incoming relative order behind the ones it named, and the tail below the head is
    untouched.
    """
    top_k = int(settings["retrieval.finisher_top_k"])
    if top_k < 2 or len(ranking) < 2:
        return list(ranking), []
    head, tail = list(ranking[:top_k]), list(ranking[top_k:])
    by_id = {c.person_id: c for c in candidates}
    profiles = [by_id[person.person_id] for person in head if person.person_id in by_id]
    cards = candidate_views(profiles)
    if len(cards) < 2:
        return list(ranking), []

    raw = call_json(
        load_prompt(
            str(settings["llm.finisher_prompt"]),
            brief=brief,
            role_json=role.model_dump_json(),
            candidates_json=json.dumps(cards, indent=1),
        ),
        model=settings["llm.finisher_model"],
        stage=stage or str(settings["llm.query_stage"]),
        max_tokens=int(settings["llm.finisher_max_output_tokens"]),
        purpose=FINISH_PURPOSE,
    )

    by_person = {person.person_id: person for person in head}
    rejected: list[str] = []
    ordered: list[RankedPerson] = []
    seen: set[str] = set()
    for value in raw.get("order") or []:
        person_id = str(value)
        if person_id not in by_person:
            rejected.append(f"finisher: {person_id or '<missing id>'} is not in the head")
            continue
        if person_id in seen:
            rejected.append(f"finisher: {person_id} named twice")
            continue
        seen.add(person_id)
        ordered.append(by_person[person_id])
    ordered += [person for person in head if person.person_id not in seen]
    return ordered + tail, rejected


__all__ = [
    "ALWAYS_SCORED_COMPONENTS",
    "RERANK_LISTWISE",
    "RERANK_MODES",
    "RERANK_PERMUTATION",
    "SCORE_COMPONENTS",
    "VIEWS",
    "VIEW_CARD",
    "VIEW_HYBRID",
    "VIEW_PROFILE",
    "borda_order",
    "candidate_view",
    "candidate_views",
    "card_view",
    "combine_parts",
    "confidence_signal",
    "evidence_strength",
    "finish",
    "identity_resolution",
    "own_evidence_keys",
    "profile_view",
    "relevant_contributions",
    "rerank",
    "rerank_input",
    "rerank_max_tokens",
    "rerank_mode",
    "rerank_output_tokens",
    "rerank_prompt_text",
    "sample_orders",
    "score_candidate",
    "split_by_count",
    "validated_evidence",
    "weights",
]
