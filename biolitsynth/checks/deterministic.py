"""Deterministic checks that need only the schemas (plan §5.9; roadmap W1). Each returns CheckResults; a report
passes a check when every result for it has passed=True. Checks that need study clusters, conflict balance or the
cherry-pick scan (inferred_multi_study, study_count_language, conflict_addressed, balanced_no_winner,
cherry_pick_scan, …) arrive with P4/P6.

Report-level results use claim_id="*".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from ..schemas import CheckResult, CitedReport, Claim, ClaimType, Conflict, EvidenceItem, Paper

HARD_FAIL = {"evidence_exists", "pmid_in_run", "quote_in_abstract", "claim_type_rules", "retraction_guard",
             "no_orphan_sentences"}                              # §5.9 "hard fail"; contested_both_sides is "fail"


@dataclass
class RunFacts:
    """What the checks look things up in: this run's store contents."""
    evidence: dict[str, EvidenceItem]
    papers: dict[str, Paper]                     # every paper retrieved in this run, by PMID
    conflicts: dict[str, Conflict] = field(default_factory=dict)

    @property
    def run_pmids(self) -> set[str]:
        return set(self.papers)


def _r(claim_id: str, check: str, ok: bool, detail: str | None = None) -> CheckResult:
    return CheckResult(claim_id=claim_id, check=check, passed=ok, detail=None if ok else detail)


def evidence_exists(c: Claim, f: RunFacts) -> CheckResult:
    missing = [e for e in c.evidence_ids if e not in f.evidence]
    return _r(c.id, "evidence_exists", not missing, f"unknown evidence ids {missing}")


def claim_type_rules(c: Claim, f: RunFacts) -> CheckResult:
    try:
        Claim.model_validate(c.model_dump())
    except ValidationError as e:
        return _r(c.id, "claim_type_rules", False, "; ".join(x["msg"] for x in e.errors()))
    return _r(c.id, "claim_type_rules", True)


def retraction_guard(c: Claim, f: RunFacts) -> CheckResult:
    bad = sorted({f.evidence[e].pmid for e in c.evidence_ids if e in f.evidence
                  and f.evidence[e].pmid in f.papers and f.papers[f.evidence[e].pmid].is_retracted})
    return _r(c.id, "retraction_guard", not bad, f"cites retracted PMIDs {bad}")


def contested_both_sides(c: Claim, f: RunFacts) -> CheckResult | None:
    if c.claim_type != ClaimType.CONTESTED:
        return None
    conflict = f.conflicts.get(c.conflict_id or "")
    if conflict is None:
        return _r(c.id, "contested_both_sides", False, f"conflict {c.conflict_id!r} is not in this run")
    cited = set(c.evidence_ids)
    uncovered = [s.position.value for s in conflict.sides if not cited & set(s.evidence_ids)]
    return _r(c.id, "contested_both_sides", not uncovered, f"no evidence cited for side(s) {uncovered}")


def pmid_in_run(report: CitedReport, f: RunFacts) -> list[CheckResult]:
    out = []
    by_claim: dict[str, set[str]] = {c.id: {f.evidence[e].pmid for e in c.evidence_ids if e in f.evidence}
                                     for c in report.claims}
    for sentences in report.sections.values():
        for s in sentences:
            for cid in s.claim_ids or ["*"]:
                by_claim.setdefault(cid, set()).update(cit.pmid for cit in s.citations)
    for cid, pmids in by_claim.items():
        stray = sorted(pmids - f.run_pmids)
        out.append(_r(cid, "pmid_in_run", not stray, f"PMIDs not retrieved in this run {stray}"))
    return out


def quote_in_abstract(report: CitedReport, f: RunFacts) -> list[CheckResult]:
    out = []
    for c in report.claims:
        bad = []
        for eid in c.evidence_ids:
            ev = f.evidence.get(eid)
            paper = f.papers.get(ev.pmid) if ev else None
            if ev is None or paper is None:
                continue                        # reported by evidence_exists / pmid_in_run
            if ev.quote_start is None or ev.quote_end is None \
                    or paper.abstract[ev.quote_start:ev.quote_end] != ev.quote:
                bad.append(eid)
        for sentences in report.sections.values():
            for s in sentences:
                if c.id in s.claim_ids:
                    bad += [cit.evidence_id for cit in s.citations
                            if cit.evidence_id in f.evidence and cit.quote != f.evidence[cit.evidence_id].quote]
        out.append(_r(c.id, "quote_in_abstract", not bad, f"quote not at its stored offsets: {sorted(set(bad))}"))
    return out


def no_orphan_sentences(report: CitedReport, f: RunFacts) -> CheckResult:
    return _r("*", "no_orphan_sentences", not report.uncited_sentences,
              f"{len(report.uncited_sentences)} uncited sentence(s): {report.uncited_sentences[:2]}")


def run_checks(report: CitedReport, facts: RunFacts) -> list[CheckResult]:
    results: list[CheckResult] = []
    for c in report.claims:
        for check in (evidence_exists, claim_type_rules, retraction_guard, contested_both_sides):
            r = check(c, facts)
            if r is not None:
                results.append(r)
    results += pmid_in_run(report, facts)
    results += quote_in_abstract(report, facts)
    results.append(no_orphan_sentences(report, facts))
    return results


def failures(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not r.passed]
