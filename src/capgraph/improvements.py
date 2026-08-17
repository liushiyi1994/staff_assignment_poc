"""The wave-1 improvement flags: one reader, defaults in one place, OFF everywhere.

Every flag in ``config/settings.yaml``'s ``improvements`` block changes vocabulary,
retrieval or ranking behaviour that the retired 120-case test split can no longer
validate (``docs/improvement-backlog.md`` G3a/G5/G6/G7/G11a). They are therefore all
default OFF, and this module holds the defaults so that "off" means exactly one thing
in the pipeline, in the query engine, and in the offline analysis.

:func:`enabled` is the auditing half. It returns the behaviour block **only when some
flag deviates from its default**, and the benchmark's configuration digest folds it in
on the same condition. With everything off the digest is byte-identical to the one the
frozen v1/v2/v3 checkpoints were written under — so those checkpoints stay readable and
extendable — while a run made with any flag on digests differently and is refused
against them, which is the whole point of the digest.
"""
from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from .settings import settings

OFF = "off"
COMPONENT = "component"
MULTIPLIER = "multiplier"

ORDER_SCORE = "score"
ORDER_REVERSE = "reverse"

CONFIDENCE_MODES = (OFF, COMPONENT, MULTIPLIER)
ACTIVITY_MODES = (OFF, COMPONENT)
PRESENTATION_ORDERS = (ORDER_SCORE, ORDER_REVERSE)

# The optional score components these flags can add. Deliberately not folded into
# rank.SCORE_COMPONENTS: the benchmark-v2 weight sweep is defined over exactly the four
# base components, and widening that constant would silently change the grid.
CONFIDENCE_COMPONENT = "confidence"
ACTIVITY_COMPONENT = "activity_currency"

FLAG_VOCABULARY = "improvements.vocabulary.min_document_frequency"     # G3a
FLAG_CONFIDENCE = "improvements.confidence_signal.mode"                # G5
FLAG_STRENGTH = "improvements.specialization_strength.enabled"         # G6
FLAG_ACTIVITY = "improvements.activity_currency.mode"                  # G11a
FLAG_ORDER = "improvements.rerank_presentation_order"                  # G7

# dotted setting -> its OFF value. A flag absent from settings reads as its default, so
# an older config file behaves exactly as it did before the block existed.
FLAGS: dict[str, Any] = {
    FLAG_VOCABULARY: 0,
    FLAG_CONFIDENCE: OFF,
    FLAG_STRENGTH: False,
    FLAG_ACTIVITY: OFF,
    FLAG_ORDER: ORDER_SCORE,
}

# Sub-block of `improvements` that configures the G7 probe *run* rather than any
# behaviour, so it is not part of the digest-relevant record below.
_RUN_ONLY = ("probe_order",)


# Set only through :func:`overridden`, and always restored.
_OVERRIDES: dict[str, Any] = {}


@contextmanager
def overridden(flags: Mapping[str, Any]) -> Iterator[None]:
    """Temporarily set flags by their dotted setting name, then restore them.

    Two callers, both of which need a flag on for the length of one operation and must
    not leave it on: the tests, and the G7 probe runner, which runs one arm with the
    presentation order reversed. Doing that by editing `config/settings.yaml` would mean
    a run whose configuration depends on an operator remembering to edit it back.

    Overrides are visible to :func:`enabled`, so a run made under one records itself as
    the flagged run it is and cannot be scored together with an unflagged checkpoint.
    """
    unknown = sorted(set(flags) - set(FLAGS))
    if unknown:
        raise KeyError(f"not an improvement flag: {', '.join(unknown)}")
    previous = dict(_OVERRIDES)
    _OVERRIDES.update(flags)
    try:
        yield
    finally:
        _OVERRIDES.clear()
        _OVERRIDES.update(previous)


def _value(dotted: str) -> Any:
    if dotted in _OVERRIDES:
        return _OVERRIDES[dotted]
    return settings.get(dotted, FLAGS[dotted])


def _choice(dotted: str, allowed: tuple[str, ...]) -> str:
    value = str(_value(dotted))
    if value not in allowed:
        raise ValueError(f"{dotted} is {value!r}; use one of {', '.join(allowed)}")
    return value


def any_enabled() -> bool:
    """True when any flag deviates from its default-OFF value."""
    return any(_value(dotted) != default for dotted, default in FLAGS.items())


def enabled() -> dict[str, Any]:
    """The behaviour block when something is on, or ``{}`` when everything is off.

    Returned whole rather than as the deviating keys alone: once a flag is on, the
    tuning constants beside it (a weight, a secondary credit, a frequency floor) decide
    what the run actually did, and a record that omitted them could not reproduce it.
    """
    if not any_enabled():
        return {}
    configured = settings.get("improvements") or {}
    if not isinstance(configured, Mapping):
        raise TypeError("improvements must be a mapping in config/settings.yaml")
    block = copy.deepcopy(
        {key: value for key, value in sorted(configured.items()) if key not in _RUN_ONLY}
    )
    for dotted, value in _OVERRIDES.items():
        path = dotted.split(".")[1:]           # drop the leading "improvements"
        node = block
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = value
    return block


def record(config: dict[str, Any]) -> dict[str, Any]:
    """Add the behaviour block to a run configuration, in place, only when it is on."""
    block = enabled()
    if block:
        config["improvements"] = block
    return config


# ---------- G3a: vocabulary frequency gating ----------

def vocabulary_min_document_frequency() -> int:
    floor = int(_value(FLAG_VOCABULARY))
    if floor < 0:
        raise ValueError("improvements.vocabulary.min_document_frequency must not be negative")
    return floor


# ---------- G5: extraction confidence ----------

def confidence_mode() -> str:
    return _choice(FLAG_CONFIDENCE, CONFIDENCE_MODES)


def confidence_weight() -> float:
    weight = float(settings.get("improvements.confidence_signal.weight", 0.10))
    if weight < 0:
        raise ValueError("improvements.confidence_signal.weight must not be negative")
    return weight


def confidence_values() -> dict[str, float]:
    """The high/medium/low rubric mapped onto [0, 1]. Every level must be configured."""
    configured = settings.get("improvements.confidence_signal.values") or {}
    if not isinstance(configured, Mapping):
        raise TypeError("improvements.confidence_signal.values must be a mapping")
    missing = [level for level in ("high", "medium", "low") if level not in configured]
    if missing:
        raise ValueError(
            f"improvements.confidence_signal.values is missing {', '.join(missing)}"
        )
    return {level: float(configured[level]) for level in ("high", "medium", "low")}


def confidence_value(level: str) -> float:
    """One confidence level's numeric credit; an unknown level scores the lowest."""
    values = confidence_values()
    return values.get(str(level), min(values.values()))


# ---------- G6: primary/secondary specialization strength ----------

def specialization_strength_enabled() -> bool:
    return bool(_value(FLAG_STRENGTH))


def secondary_weight() -> float:
    weight = float(settings.get("improvements.specialization_strength.secondary_weight", 0.5))
    if not 0.0 <= weight <= 1.0:
        raise ValueError(
            "improvements.specialization_strength.secondary_weight must be within [0, 1]"
        )
    return weight


def strength_credit(primary_share: float) -> float:
    """Credit for one matched specialization, interpolated by its primary share.

    A capability whose supporting contributions all called it primary counts 1.0; one
    they all called secondary counts ``secondary_weight``; a mixture lands between.
    Interpolating rather than thresholding avoids inventing a cut-off nobody measured.
    """
    share = min(max(float(primary_share), 0.0), 1.0)
    return secondary_weight() + (1.0 - secondary_weight()) * share


# ---------- G11a: activity currency ----------

def activity_currency_mode() -> str:
    return _choice(FLAG_ACTIVITY, ACTIVITY_MODES)


def activity_currency_weight() -> float:
    weight = float(settings.get("improvements.activity_currency.weight", 0.10))
    if weight < 0:
        raise ValueError("improvements.activity_currency.weight must not be negative")
    return weight


# ---------- G7: re-rank presentation order ----------

def rerank_presentation_order() -> str:
    return _choice(FLAG_ORDER, PRESENTATION_ORDERS)


__all__ = [
    "ACTIVITY_COMPONENT",
    "ACTIVITY_MODES",
    "COMPONENT",
    "CONFIDENCE_COMPONENT",
    "CONFIDENCE_MODES",
    "FLAGS",
    "FLAG_ACTIVITY",
    "FLAG_CONFIDENCE",
    "FLAG_ORDER",
    "FLAG_STRENGTH",
    "FLAG_VOCABULARY",
    "MULTIPLIER",
    "OFF",
    "ORDER_REVERSE",
    "ORDER_SCORE",
    "PRESENTATION_ORDERS",
    "activity_currency_mode",
    "activity_currency_weight",
    "any_enabled",
    "confidence_mode",
    "confidence_value",
    "confidence_values",
    "confidence_weight",
    "enabled",
    "overridden",
    "record",
    "rerank_presentation_order",
    "secondary_weight",
    "specialization_strength_enabled",
    "strength_credit",
    "vocabulary_min_document_frequency",
]
