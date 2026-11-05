"""A fake NCBI built from the recorded fixtures in tests/fixtures/pubmed. Counts every request it serves.

It assembles efetch responses with plain lxml, deliberately not with biolitsynth.pubmed.split_articles, so a
bug in the code under test cannot cancel itself out here.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from lxml import etree

FIX = Path(__file__).resolve().parent / "fixtures" / "pubmed"
RECORDED_SEARCH = json.loads((FIX / "esearch_metformin_crc.json").read_text())
CRC_IDS: list[str] = RECORDED_SEARCH["esearchresult"]["idlist"]


def fixture(name: str) -> bytes:
    return (FIX / f"{name}.xml").read_bytes()


def articles_by_pmid() -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for f in sorted(FIX.glob("*.xml")):
        for art in etree.parse(str(f)).getroot().findall("PubmedArticle"):
            out[art.findtext("MedlineCitation/PMID")] = etree.tostring(art)
    return out


ARTICLES = articles_by_pmid()


class FakeNcbi:
    """`searches` maps a query string to the PMIDs esearch should return; any other query returns CRC_IDS.
    `totals` sets the hit count esearch reports per query string (else `total`, else the number of ids);
    queries in `rejected` get an esearch ERROR response, as PubMed sends for a malformed query."""

    def __init__(self, searches: dict[str, list[str]] | None = None, total: int | None = None,
                 totals: dict[str, int] | None = None, rejected: set[str] | None = None):
        self.searches = searches or {}
        self.total = total
        self.totals = totals or {}
        self.rejected = rejected or set()
        self.requests: list[httpx.Request] = []
        self.fail_next: list[int] = []          # statuses to return before behaving, e.g. [429, 503]
        self.esearch_override: dict | None = None
        self.transport = httpx.MockTransport(self._handle)

    def _params(self, req: httpx.Request) -> dict[str, str]:
        raw = req.content.decode() if req.method == "POST" else req.url.query.decode()
        return dict(httpx.QueryParams(raw))

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0))
        p, endpoint = self._params(req), req.url.path.rsplit("/", 1)[-1]
        if endpoint == "esearch.fcgi":
            if self.esearch_override is not None:
                return httpx.Response(200, json=self.esearch_override)
            if p["term"] in self.rejected:
                return httpx.Response(200, json={"esearchresult": {"ERROR": "Invalid query"}})
            ids = self.searches.get(p["term"], CRC_IDS)
            body = json.loads(json.dumps(RECORDED_SEARCH))
            total = self.totals.get(p["term"], self.total if self.total is not None else len(ids))
            body["esearchresult"].update(count=str(total),
                                         idlist=ids[: int(p["retmax"])], retmax=p["retmax"])
            return httpx.Response(200, json=body)
        if endpoint == "efetch.fcgi":
            docs = b"".join(ARTICLES[i] for i in p["id"].split(",") if i in ARTICLES)
            return httpx.Response(200, content=b"<?xml version=\"1.0\" ?><PubmedArticleSet>" + docs + b"</PubmedArticleSet>")
        return httpx.Response(404)

    # --- inspection ---------------------------------------------------------------------------
    def calls(self, endpoint: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(endpoint)]

    def search_terms(self) -> list[tuple[str, str]]:
        """(term, retmax) for every esearch served, in order. retmax "0" is a count-only probe."""
        return [(self._params(r)["term"], self._params(r)["retmax"]) for r in self.calls("esearch.fcgi")]

    def fetched_ids(self) -> list[str]:
        return [i for r in self.calls("efetch.fcgi") for i in self._params(r)["id"].split(",")]
