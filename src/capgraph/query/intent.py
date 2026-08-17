"""Query step 1: natural-language brief -> models.Intent."""
from __future__ import annotations

from ..llm import call_json
from ..models import Intent, RoleSpec
from ..settings import load_prompt, settings

# Cost-log label for this call, so a stage's spend can be split by call type.
PURPOSE = "intent"


def parse_intent(
    brief: str, known_specializations: list[str], *, stage: str | None = None
) -> Intent:
    """Parse a brief into roles. A parse with no usable role falls back to the brief.

    The fallback keeps the union honest rather than convenient: with no parsed terms
    the structured arm contributes nothing and the vector arm answers alone, which is
    a legible outcome — an empty shortlist because intent parsing wobbled is not.
    """
    prompt = load_prompt(
        "intent_parsing",
        brief=brief,
        specializations="\n".join(f"- {s}" for s in sorted(known_specializations)),
    )
    raw = call_json(
        prompt,
        model=settings["llm.intent_model"],
        stage=stage or str(settings["llm.query_stage"]),
        purpose=PURPOSE,
    )
    intent = Intent.model_validate(raw)
    if not intent.roles:
        intent.roles = [RoleSpec(role=brief.strip()[:80] or "unspecified role")]
    return intent
