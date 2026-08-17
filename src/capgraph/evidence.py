"""The Stage 1 evidence view, shared by every baseline and the lexical retrieval arm.

The baselines must run on exactly the information the graph system was built from —
otherwise a win proves only that one arm saw more history. The benchmark-v3 lexical
retrieval arm (see :mod:`capgraph.lexical`) reads the same corpus for the same reason,
which is why this module sits at the package root rather than under ``eval/``: it is
pipeline-derived evidence, not evaluation machinery. That corpus is
``data/buckets/buckets.jsonl``: the retained person x project x quarter buckets whose
tickets are resolved strictly before the holdout cutoff, privacy-sanitized, and
stripped of final assignment/status fields and unversioned component names. Stage 2
read those same buckets to produce every Contribution in the graph.

Consequences worth stating plainly:

* Ticket ownership here is ``evidence_person_id`` — the assignee reconstructed at the
  resolution boundary — never the dump's final assignee snapshot.
* "Pre-cutoff resolved ticket count" therefore means *retained evidence tickets*.
  Buckets below ``bucketing.min_tickets_per_bucket`` were dropped upstream, so this
  is the graph's view of activity rather than a raw Jira count. Both the most-active
  baseline and the graph system inherit that same truncation.
* Nothing here depends on a case's as-of time: every benchmark query time is after
  the cutoff, so the whole view is admissible for every case.

Loading the buckets file takes a few seconds, so the derived per-person documents and
per-ticket embeddings are cached under ``data/eval/``.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from .settings import DATA_DIR, settings

BUCKETS_PATH = DATA_DIR / "buckets" / "buckets.jsonl"
CACHE_DIR = DATA_DIR / "eval" / "cache"
DOCS_CACHE = CACHE_DIR / "evidence_person_docs.json"
EMBEDDINGS_CACHE = CACHE_DIR / "evidence_ticket_embeddings.npz"


@dataclass(frozen=True)
class EvidenceTicket:
    """One sanitized pre-cutoff ticket, attributed to its resolution-time owner."""

    key: str
    person_id: str
    project_key: str
    text: str


def ticket_text(ticket: dict) -> str:
    """Summary and description of one Stage 1 evidence ticket, in a fixed order."""
    parts = [str(ticket.get(field) or "").strip() for field in ("summary", "description")]
    return "\n".join(part for part in parts if part)


@dataclass
class EvidenceView:
    """The pre-cutoff evidence corpus: tickets, per-person documents, activity counts."""

    tickets: tuple[EvidenceTicket, ...]

    @classmethod
    def load(cls, path: Path | None = None) -> EvidenceView:
        """Read the Stage 1 buckets file into a flat, deterministically ordered corpus."""
        path = BUCKETS_PATH if path is None else path
        if not path.is_file():
            raise FileNotFoundError(
                f"missing Stage 1 evidence view: {path}; run `make stage1` first"
            )
        rows: list[EvidenceTicket] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                bucket = json.loads(line)
                for ticket in bucket["tickets"]:
                    rows.append(
                        EvidenceTicket(
                            key=str(ticket["key"]),
                            # The bucket's owner is the reconstructed evidence owner;
                            # the per-ticket field agrees with it by construction.
                            person_id=str(bucket["person_id"]),
                            project_key=str(bucket["project_key"]),
                            text=ticket_text(ticket),
                        )
                    )
        # A ticket can be split across chunked buckets only by person, never duplicated;
        # sorting by (person, key) makes the corpus order independent of file order.
        rows.sort(key=lambda ticket: (ticket.person_id, ticket.key))
        return cls(tickets=tuple(rows))

    @cached_property
    def person_ids(self) -> tuple[str, ...]:
        return tuple(sorted({ticket.person_id for ticket in self.tickets}))

    @cached_property
    def ticket_counts(self) -> dict[str, int]:
        """Retained pre-cutoff evidence tickets per person."""
        return dict(Counter(ticket.person_id for ticket in self.tickets))

    @cached_property
    def documents(self) -> dict[str, str]:
        """One concatenated document per person, in stable ticket order."""
        parts: dict[str, list[str]] = defaultdict(list)
        for ticket in self.tickets:
            if ticket.text:
                parts[ticket.person_id].append(ticket.text)
        return {person_id: "\n".join(texts) for person_id, texts in sorted(parts.items())}

    @cached_property
    def ticket_indexes_by_person(self) -> dict[str, list[int]]:
        indexes: dict[str, list[int]] = defaultdict(list)
        for index, ticket in enumerate(self.tickets):
            indexes[ticket.person_id].append(index)
        return dict(indexes)

    def ticket_indexes_for(self, person_ids: Iterable[str]) -> list[int]:
        """Corpus positions owned by the given people, in corpus order."""
        wanted = set(person_ids)
        return [
            index
            for person_id in sorted(wanted)
            for index in self.ticket_indexes_by_person.get(person_id, [])
        ]

    def write_document_cache(self, path: Path | None = None) -> Path:
        """Persist per-person documents so a rerun does not re-read every bucket."""
        path = DOCS_CACHE if path is None else path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "documents": self.documents,
            "ticket_counts": self.ticket_counts,
            "n_tickets": len(self.tickets),
        }
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path

    def embeddings(
        self, *, path: Path | None = None, force: bool = False, embed_fn=None
    ) -> np.ndarray:
        """Per-ticket embeddings aligned with ``tickets``, cached on disk.

        The embedding model is deterministic, so the cache is a speed checkpoint and
        never a correctness one. It is rejected whenever the model, the dimensions, or
        the corpus size no longer match.
        """
        path = EMBEDDINGS_CACHE if path is None else path
        dims = int(settings["embedding.dims"])
        model = str(settings["embedding.model"])
        if not force and path.exists():
            with np.load(path, allow_pickle=False) as archive:
                vectors = archive["vectors"]
                cached_keys = [str(value) for value in archive["keys"]]
                if (
                    str(archive["model"]) == model
                    and vectors.shape == (len(self.tickets), dims)
                    and cached_keys == [ticket.key for ticket in self.tickets]
                ):
                    return np.asarray(vectors, dtype=np.float32)

        if embed_fn is None:
            from ..embeddings import embed as embed_fn
        batch = int(settings["eval.baselines.embedding_batch_size"])
        texts = [ticket.text for ticket in self.tickets]
        chunks = [
            np.asarray(embed_fn(texts[start:start + batch]), dtype=np.float32)
            for start in range(0, len(texts), batch)
        ]
        vectors = (
            np.vstack(chunks)
            if chunks
            else np.zeros((0, dims), dtype=np.float32)
        )
        if vectors.shape != (len(texts), dims):
            raise ValueError(
                f"evidence embeddings have shape {vectors.shape}, expected "
                f"({len(texts)}, {dims})"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path,
            vectors=vectors,
            keys=np.asarray([ticket.key for ticket in self.tickets], dtype=str),
            model=np.asarray(model),
        )
        return vectors


def fingerprint(person_ids: Sequence[str]) -> tuple[str, ...]:
    """Stable identity of a frozen roster, used to cache per-roster baseline indexes."""
    return tuple(sorted({str(person_id) for person_id in person_ids}))
