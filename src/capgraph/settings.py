"""Config loader: config/settings.yaml + .env. Import `settings` from here everywhere."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
PROMPTS_DIR = REPO_ROOT / "prompts"

load_dotenv(REPO_ROOT / ".env")


class Settings:
    def __init__(self, cfg: dict[str, Any]):
        self._cfg = cfg
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.neo4j_uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        self.neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
        self.neo4j_password = os.environ.get("NEO4J_PASSWORD", "capgraph-local")
        self.mysql_url = os.environ.get("MYSQL_URL", "")

    def __getitem__(self, dotted: str) -> Any:
        """settings['scoring.weights.recency'] -> 0.20"""
        node: Any = self._cfg
        for part in dotted.split("."):
            node = node[part]
        return node

    def get(self, dotted: str, default: Any = None) -> Any:
        try:
            return self[dotted]
        except (KeyError, TypeError):
            return default


@lru_cache
def get_settings() -> Settings:
    with open(REPO_ROOT / "config" / "settings.yaml") as f:
        return Settings(yaml.safe_load(f))


def load_prompt(name: str, **kwargs: str) -> str:
    """Load prompts/{name}.md and substitute {{placeholders}}."""
    text = (PROMPTS_DIR / f"{name}.md").read_text()
    for key, value in kwargs.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


settings = get_settings()
