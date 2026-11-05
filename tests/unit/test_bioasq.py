"""BioASQ loading and subset selection rules (plan §11.3b). The data here is synthetic and only exercises the
mechanics; real questions and labels come from the BioASQ download, never from a test."""
import json

import pytest

from biolitsynth.eval.bioasq import load_bioasq, load_subset, select_subset, write_subset


def bioasq_file(tmp_path, questions):
    f = tmp_path / "training.json"
    f.write_text(json.dumps({"questions": questions}))
    return f


def q(i, type_="yesno", n_docs=5):
    return {"id": f"q{i:03d}", "body": f" Question {i}? ", "type": type_,
            "documents": [f"http://www.ncbi.nlm.nih.gov/pubmed/{1000 + i * 10 + d}" for d in range(n_docs)]}


def test_pmids_are_parsed_from_urls_and_deduplicated(tmp_path):
    f = bioasq_file(tmp_path, [{"id": "a", "body": "B?", "type": "summary", "documents": [
        "http://www.ncbi.nlm.nih.gov/pubmed/23727394", "https://pubmed.ncbi.nlm.nih.gov/23727394/", "junk"]}])
    got = load_bioasq(f)["a"]
    assert (got.body, got.type, got.relevant_pmids) == ("B?", "summary", ("23727394",))


def test_selection_keeps_only_eligible_questions_and_is_reproducible(tmp_path):
    qs = [q(i) for i in range(40)] + [q(i, "summary") for i in range(40, 70)] \
        + [q(i, "factoid") for i in range(70, 90)] + [q(i, n_docs=4) for i in range(90, 100)]
    loaded = load_bioasq(bioasq_file(tmp_path, qs))
    a, b = select_subset(loaded, seed=7), select_subset(loaded, seed=7)
    assert a == b and len(a["dev"]) == 30 and len(a["held_back"]) == 20
    picked = a["dev"] + a["held_back"]
    assert len(set(picked)) == 50 and all(int(i[1:]) < 70 for i in picked)   # no factoid, no <5 documents
    assert select_subset(loaded, seed=8) != a


def test_too_few_eligible_questions_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="only 3"):
        select_subset(load_bioasq(bioasq_file(tmp_path, [q(i) for i in range(3)])))


def test_written_subset_records_the_source_hash_and_needs_review(tmp_path):
    qs = [q(i) for i in range(60)]
    f = bioasq_file(tmp_path, qs)
    out = tmp_path / "subset.yaml"
    write_subset(select_subset(load_bioasq(f)), f, out)
    s = load_subset(out)
    assert len(s["source_sha256"]) == 64 and s["reviewed_by"] is None and len(s["dev"]) == 30
    assert "Question" not in out.read_text()                     # ids only: no question text is committed


def test_a_check_set_never_overlaps_the_excluded_ids(tmp_path):
    loaded = load_bioasq(bioasq_file(tmp_path, [q(i) for i in range(100)]))
    first = select_subset(loaded, seed=0)
    used = set(first["dev"] + first["held_back"])
    check = select_subset(loaded, n=30, dev=30, seed=1, exclude=used)
    assert len(check["dev"]) == 30 and check["held_back"] == [] and not used & set(check["dev"])
