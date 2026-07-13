"""Query step 1: natural-language brief -> models.Intent."""
from __future__ import annotations

from ..llm import call_json
from ..models import Intent
from ..settings import load_prompt, settings


def parse_intent(brief: str, known_specializations: list[str]) -> Intent:
    prompt = load_prompt(
        "intent_parsing",
        brief=brief,
        specializations="\n".join(f"- {s}" for s in sorted(known_specializations)),
    )
    raw = call_json(prompt, model=settings["llm.intent_model"], stage="query")
    return Intent.model_validate(raw)
