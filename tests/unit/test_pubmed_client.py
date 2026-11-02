import json

import pytest

from biolitsynth.config import NcbiConfig
from biolitsynth.pubmed import PubMedClient, PubMedError, TokenBucket
from biolitsynth.store import Database
from biolitsynth.trace import Tracer

from ..pubmed_mock import CRC_IDS, FakeNcbi

NO_ABSTRACT = {"28506970", "30385499", "32384400", "28506966", "21949227"}


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


async def instant(_):                           # no real sleeping in tests
    pass


@pytest.fixture
def make(tmp_path):
    made = []

    def _make(ncbi=None, *, env=None, cfg=None, clock=None, tracer=False, **kw):
        ncbi = ncbi or FakeNcbi()
        db = Database(tmp_path / "t.db")
        limiter = TokenBucket(1000)
        client = PubMedClient(cfg or NcbiConfig(), db, tmp_path / "cache", transport=ncbi.transport, env=env or {},
                              clock=clock or Clock(), sleep=instant, limiter=limiter,
                              tracer=Tracer(db, "r1") if tracer else None, **kw)
        made.append((client, db))
        return client, ncbi, db

    yield _make
    # connections are closed with the temp dir


# --- the P1 acceptance criterion --------------------------------------------------------------
async def test_second_run_of_the_same_query_makes_zero_network_calls(make):
    client, ncbi, _ = make()
    first = await client.search_and_fetch("metformin AND colorectal", retmax=25)
    assert len(ncbi.requests) == 2                          # one esearch, one efetch
    again = await client.search_and_fetch("metformin AND colorectal", retmax=25)
    assert len(ncbi.requests) == 2                          # nothing new
    assert again.search.cached and again.fetch.cache_hits == 25 and again.fetch.fetched == 0
    assert [p.pmid for p in again.fetch.papers] == [p.pmid for p in first.fetch.papers]


async def test_25_ids_fetched_20_kept_5_skipped_for_no_abstract(make):
    client, _, _ = make()
    got = await client.search_and_fetch("anything", retmax=25)
    assert len(got.search.pmids) == 25 and len(got.fetch.papers) == 20
    assert {s.pmid for s in got.fetch.skipped} == NO_ABSTRACT
    assert all(s.reason == "no_abstract" for s in got.fetch.skipped)
    assert got.fetch.missing == []


async def test_ranks_come_from_the_search_order_including_skipped_papers(make):
    client, _, _ = make()
    got = await client.search_and_fetch("anything", retmax=25)
    ranks = dict(got.ranked())
    assert ranks[CRC_IDS[0]] == 1
    assert ranks[CRC_IDS[24]] == 25 or CRC_IDS[24] in NO_ABSTRACT        # rank 25 is the last id, skipped or not
    assert len(ranks) == 20 and set(ranks) == set(CRC_IDS) - NO_ABSTRACT


# --- per-PMID cache ---------------------------------------------------------------------------
async def test_only_uncached_pmids_are_downloaded(make):
    client, ncbi, _ = make()
    await client.fetch(CRC_IDS[:10])
    await client.fetch(CRC_IDS[:15])
    assert ncbi.fetched_ids() == CRC_IDS[:10] + CRC_IDS[10:15]             # second call asked for 5 new ids only


async def test_cache_survives_a_new_client(make, tmp_path):
    client, ncbi, _ = make()
    await client.fetch(CRC_IDS[:5])
    client2, _, _ = make(ncbi)
    res = await client2.fetch(CRC_IDS[:5])
    assert res.cache_hits == 5 and len(ncbi.requests) == 1


async def test_cache_files_are_one_valid_document_per_pmid_and_no_temp_files(make, tmp_path):
    client, _, _ = make()
    await client.fetch(CRC_IDS[:3])
    files = sorted((tmp_path / "cache").iterdir())
    assert [f.name for f in files] == sorted(f"{p}.xml" for p in CRC_IDS[:3])
    for f in files:
        assert f.read_bytes().startswith(b"<PubmedArticleSet>")


async def test_ids_are_deduplicated_and_batched_at_the_configured_size(make):
    client, ncbi, _ = make(cfg=NcbiConfig(efetch_batch_size=10))
    res = await client.fetch(CRC_IDS + CRC_IDS[:4])                        # 29 ids with 4 repeats
    assert [len(r.content.decode().split("id=")[1].split("&")[0].split("%2C")) for r in ncbi.calls("efetch.fcgi")] == [10, 10, 5]
    assert len(res.papers) + len(res.skipped) == 25


async def test_pmids_ncbi_does_not_return_are_reported_missing(make):
    client, _, _ = make()
    res = await client.fetch([CRC_IDS[0], "99999999"])
    assert res.missing == ["99999999"] and [p.pmid for p in res.papers] == [CRC_IDS[0]]


# --- esearch cache ----------------------------------------------------------------------------
async def test_esearch_cache_expires_after_the_ttl(make):
    clock = Clock()
    client, ncbi, _ = make(clock=clock, cfg=NcbiConfig(esearch_ttl_days=7))
    await client.search("q", retmax=5)
    clock.now += 6.9 * 86400
    assert (await client.search("q", retmax=5)).cached and len(ncbi.calls("esearch.fcgi")) == 1
    clock.now += 0.2 * 86400                                               # now past 7 days
    assert not (await client.search("q", retmax=5)).cached and len(ncbi.calls("esearch.fcgi")) == 2


async def test_cache_key_covers_query_retmax_and_sort(make):
    client, ncbi, _ = make()
    await client.search("q", retmax=5)
    await client.search("q", retmax=6)
    await client.search("q", retmax=5, sort="pub_date")
    await client.search("other", retmax=5)
    assert len(ncbi.calls("esearch.fcgi")) == 4


async def test_count_returns_total_hits_not_the_number_of_ids(make):
    client, ncbi, _ = make(ncbi=FakeNcbi(total=1234))
    assert await client.count("q") == 1234
    assert ncbi.requests[0].url.params["retmax"] == "0"


# --- request contents and secrets -------------------------------------------------------------
async def test_tool_email_and_key_are_sent_when_set_and_omitted_when_not(make):
    client, ncbi, _ = make(env={"NCBI_EMAIL": "a@b.org", "NCBI_API_KEY": "SECRETKEY"})
    await client.search("q", retmax=1)
    p = ncbi.requests[0].url.params
    assert (p["tool"], p["email"], p["api_key"], p["db"], p["retmode"]) == ("biolitsynth", "a@b.org", "SECRETKEY", "pubmed", "json")
    client2, ncbi2, _ = make(env={})                      # shares the db, so use a query not yet cached
    await client2.search("q-without-secrets", retmax=1)
    assert "api_key" not in ncbi2.requests[0].url.params and "email" not in ncbi2.requests[0].url.params


async def test_rate_is_10_per_second_with_a_key_and_3_without(make, tmp_path):
    for env, rate in (({"NCBI_API_KEY": "k"}, 10), ({}, 3)):
        c = PubMedClient(NcbiConfig(), Database(tmp_path / f"{rate}.db"), tmp_path / f"c{rate}", env=env)
        assert c._limiter._interval == pytest.approx(1 / rate)
        await c.aclose()


async def test_the_api_key_never_reaches_the_trace(make):
    client, _, db = make(env={"NCBI_API_KEY": "SECRETKEY"}, tracer=True)
    await client.search_and_fetch("metformin", retmax=3)
    dump = json.dumps([dict(r) for r in db.trace_events("r1")])
    assert "SECRETKEY" not in dump and "api_key" not in dump


async def test_every_network_call_is_traced_and_cache_hits_are_marked(make):
    client, ncbi, db = make(tracer=True)
    await client.search_and_fetch("q", retmax=3)
    await client.search_and_fetch("q", retmax=3)
    ev = db.trace_events("r1")
    assert all(e["agent"] == "pubmed_client" for e in ev)
    http = [e for e in ev if (e["message"] or "").startswith(("GET", "POST"))]
    assert len(http) == len(ncbi.requests) == 2
    assert [bool(e["cache_hit"]) for e in ev if e["message"].startswith(("esearch", "efetch"))] == [False, False, True, True]


# --- failures ---------------------------------------------------------------------------------
async def test_429_and_5xx_are_retried_then_succeed(make):
    client, ncbi, _ = make()
    ncbi.fail_next = [429, 503]
    assert (await client.search("q", retmax=2)).total >= 2
    assert len(ncbi.requests) == 3


async def test_gives_up_after_the_configured_retries(make):
    client, ncbi, _ = make(cfg=NcbiConfig(max_retries=2))
    ncbi.fail_next = [500] * 10
    with pytest.raises(PubMedError, match="3 attempts"):
        await client.search("q", retmax=2)
    assert len(ncbi.requests) == 3


async def test_a_4xx_is_not_retried(make):
    client, ncbi, _ = make()
    ncbi.fail_next = [400]
    with pytest.raises(PubMedError, match="HTTP 400"):
        await client.search("q", retmax=2)
    assert len(ncbi.requests) == 1


@pytest.mark.parametrize("body", [{"error": "API rate limit exceeded"},
                                  {"esearchresult": {"ERROR": "Invalid query"}}, {"esearchresult": {}}])
async def test_error_documents_inside_a_200_response_raise(make, body):
    client, ncbi, _ = make()
    ncbi.esearch_override = body
    with pytest.raises(PubMedError, match="rejected"):
        await client.search("q", retmax=2)


async def test_a_failed_search_is_not_cached(make):
    client, ncbi, _ = make()
    ncbi.esearch_override = {"error": "boom"}
    with pytest.raises(PubMedError):
        await client.search("q", retmax=2)
    ncbi.esearch_override = None
    assert not (await client.search("q", retmax=2)).cached
