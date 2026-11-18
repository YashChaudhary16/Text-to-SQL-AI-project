"""Schema retrieval: pick the slice of the warehouse relevant to a question.

v1 is lexical (BM25 over table + column documents enriched with business descriptions,
synonyms and sample values), followed by join-graph expansion so the selected tables are
always connected. It runs in-process with no embedding API, which keeps it free and fast.
`SchemaRetriever` is the seam for swapping in embeddings (pgvector) later.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from sqlagent.semantic.catalog import Catalog

_STOP = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "for",
    "to",
    "by",
    "and",
    "or",
    "with",
    "what",
    "which",
    "who",
    "how",
    "many",
    "much",
    "is",
    "are",
    "was",
    "were",
    "me",
    "show",
    "list",
    "give",
    "top",
    "each",
    "per",
    "all",
    "from",
    "that",
    "this",
    "do",
    "does",
    "did",
    "be",
    "it",
    "as",
    "at",
    "than",
    "most",
    "least",
    "number",
    "count",
    "total",
    "average",
    "join",
    "key",
}
_TOKEN = re.compile(r"[a-z0-9]+")
BRIDGE_EXCLUDED = {"date_dim", "time_dim"}


def tokenize(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(text.lower().replace("_", " ")):
        if tok in _STOP or len(tok) < 2:
            continue
        if len(tok) > 4 and tok.endswith("ies"):
            tok = tok[:-3] + "y"
        elif len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


@dataclass
class RetrievalResult:
    tables: list[str]
    scores: dict[str, float]
    added_for_joins: list[str]


class SchemaRetriever:
    def __init__(self, catalog: Catalog, top_tables: int = 5):
        self.catalog = catalog
        self.top_tables = top_tables
        self._doc_table: list[str] = []
        self._doc_weight: list[float] = []
        docs: list[list[str]] = []
        for t in catalog.tables:
            table_text = " ".join([t.name, t.business_name, t.description, *t.synonyms])
            docs.append(tokenize(table_text))
            self._doc_table.append(t.name)
            self._doc_weight.append(1.5)
            for c in t.columns:
                if c.references:  # FK columns describe other tables; don't let them win
                    continue
                col_text = " ".join([c.name, c.description, *c.sample_values, t.business_name])
                docs.append(tokenize(col_text))
                self._doc_table.append(t.name)
                self._doc_weight.append(1.0)
        self._bm25 = BM25Okapi(docs)

    def score(self, question: str) -> dict[str, float]:
        q = tokenize(question)
        raw = self._bm25.get_scores(q) if q else []
        per_table: dict[str, list[float]] = {}
        for i, s in enumerate(raw):
            if s > 0:
                per_table.setdefault(self._doc_table[i], []).append(s * self._doc_weight[i])
        # Table score = best doc + damped contribution of the next two (many weak matches
        # shouldn't beat one strong one).
        return {
            t: sorted(v, reverse=True)[0] + 0.3 * sum(sorted(v, reverse=True)[1:3])
            for t, v in per_table.items()
        }

    def retrieve(self, question: str, linked_tables: list[str] | None = None) -> RetrievalResult:
        """Select tables for `question`.

        `linked_tables` are tables an LLM picked from the catalog overview (stage 1). BM25
        (stage 2) adds strong column-level matches the overview can't see, but only when
        they join directly to a linked table; unrelated lexical hits are dropped rather than
        bridged. Without linked tables, falls back to pure BM25 top-k.
        """
        by_name = self.catalog.by_name
        q = tokenize(question)
        scores = self.score(question)
        ranked = sorted(scores, key=scores.get, reverse=True)
        linked = list(dict.fromkeys(t for t in (linked_tables or []) if t in by_name))

        if linked:
            chosen = list(linked)
            top = scores[ranked[0]] if ranked else 0
            for t in ranked[:3]:
                if t not in chosen and scores[t] >= 0.6 * top and self.catalog.neighbors[t] & set(linked):
                    chosen.append(t)
        elif ranked:
            cutoff = scores[ranked[0]] * 0.25  # drop long-tail noise
            chosen = [t for t in ranked[: self.top_tables] if scores[t] >= cutoff]
        else:
            return RetrievalResult(tables=[], scores={}, added_for_joins=[])

        if not any(by_name[t].kind == "fact" for t in chosen):
            # Dimension-only questions ("which states do we have customers in?") are fine;
            # but a metric word means we need the sales fact.
            if set(q) & {"revenue", "sale", "sold", "profit", "sell", "order", "bought", "spent", "spend"}:
                chosen.append("store_sales")
        if any(by_name[t].kind == "fact" for t in chosen) and "date_dim" not in chosen:
            chosen.append("date_dim")  # nearly every fact question filters/groups by time

        connected, added = self._connect(chosen, bridge_only=linked or None)
        return RetrievalResult(tables=connected, scores=scores, added_for_joins=added)

    def _connect(
        self, tables: list[str], bridge_only: list[str] | None = None
    ) -> tuple[list[str], list[str]]:
        """Add bridge tables so the chosen tables form one join graph.

        Only tables in `bridge_only` (default: all) get bridge tables added for them.
        """
        result = list(dict.fromkeys(tables))
        added: list[str] = []
        adj = self.catalog.neighbors
        targets = [t for t in result[1:] if bridge_only is None or t in bridge_only]
        for target in targets:
            path = self._shortest_path(set(result) - {target}, target, adj)
            for t in path:
                if t not in result:
                    result.append(t)
                    added.append(t)
        return result, added

    @staticmethod
    def _shortest_path(sources: set[str], target: str, adj: dict[str, set[str]]) -> list[str]:
        # Calendar/clock dimensions touch nearly every table; bridging through them would
        # produce meaningless joins (customer -> first-purchase date -> sales), so they
        # never act as sources or intermediates.
        sources = sources - BRIDGE_EXCLUDED
        if target in BRIDGE_EXCLUDED:
            return []
        if not sources or target in sources or any(target in adj[s] for s in sources):
            return []
        prev: dict[str, str | None] = {s: None for s in sources}
        queue = deque(sources)
        while queue:
            node = queue.popleft()
            for nxt in adj[node]:
                if nxt in prev or nxt in BRIDGE_EXCLUDED:
                    continue
                prev[nxt] = node
                if nxt == target:
                    path, cur = [], prev[target]
                    while cur is not None and cur not in sources:
                        path.append(cur)
                        cur = prev[cur]
                    return path
                queue.append(nxt)
        return []
