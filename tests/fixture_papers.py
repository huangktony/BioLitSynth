"""Real papers from tests/fixtures/pubmed, parsed with the project's parser, and a helper that slices quotes out of
their abstracts. Expected quotes in the P3 tests come from `cut`, never from memory: if a marker is wrong, `index`
raises instead of letting a made-up quote pass."""
from biolitsynth.pubmed import parse_pubmed_xml

from .pubmed_mock import FIX


def _load() -> dict:
    out = {}
    for f in sorted(FIX.glob("*.xml")):
        for p in parse_pubmed_xml(f.read_bytes()).papers:
            out[p.pmid] = p
    return out


PAPERS = _load()
CASE_CONTROL, META, COHORT, VETERANS, RCT = "32446797", "32592092", "32968891", "27496094", "42964126"
OTHER = "26331456"


def cut(pmid: str, first: str, last: str) -> str:
    """abstract[index(first) : index(last) + len(last)] of the fixture paper."""
    a = PAPERS[pmid].abstract
    i = a.index(first)
    return a[i:a.index(last, i) + len(last)]
