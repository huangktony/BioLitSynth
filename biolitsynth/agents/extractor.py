"""Evidence Extractor (plan §5.4): abstracts → verified EvidenceItems.

The model proposes findings (EvidenceDraft). Code decides everything checkable: the quote is found in the abstract
and gets exact offsets, the study design is seeded from PubMed publication types, sample size, effect size and
cohort name must appear in the text, strength comes from a table, and ids are assigned here. A paper whose quotes
fail gets ONE retry that asks only for the failed findings; whatever still fails is dropped.

These functions don't depend on LangGraph; the graph's extract node calls `extract_batch`.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from ..checks import quote_span
from ..llm import StageLLM, load_prompt
from ..schemas import (EvidenceDraft, EvidenceItem, ExtractionDraft, Organism, Paper, StudyDesign,
                       SubQuestion)

PROMPT = "extractor"
MAX_FINDINGS_PER_PAPER = 5
QUOTE_MIN_CHARS = 20
QUOTE_MAX_CHARS = 600
Strength = Literal["high", "moderate", "low"]

_SEEDS = (("meta-analysis", StudyDesign.META_ANALYSIS), ("systematic review", StudyDesign.SYSTEMATIC_REVIEW),
          ("randomized controlled trial", StudyDesign.RCT), ("case reports", StudyDesign.CASE_REPORT))
_VERIFIED = ("exact", "normalized", "fuzzy")


def strength_for(design: StudyDesign, organism: Organism, sample_size: int | None) -> Strength:
    if organism != Organism.HUMAN:
        return "low"
    if design in (StudyDesign.META_ANALYSIS, StudyDesign.SYSTEMATIC_REVIEW):
        return "high"
    if design == StudyDesign.RCT:
        return "moderate" if sample_size is not None and sample_size < 50 else "high"
    if design in (StudyDesign.COHORT, StudyDesign.CASE_CONTROL):
        return "moderate" if sample_size is not None and sample_size >= 1000 else "low"
    return "low"


def seed_design(publication_types: Sequence[str]) -> StudyDesign | None:
    types = {t.strip().lower() for t in publication_types}
    return next((d for name, d in _SEEDS if name in types), None)


def _number_in(text: str, n: int) -> bool:
    """n written as a whole number: plain, or with thousands separators (comma or a space character)."""
    grouped = f"{n:,}".replace(",", "[,\u00a0\u2009\u202f ]")
    return re.search(rf"(?<![\d.,])(?:{n}|{grouped})(?!\d|[.,]\d)", text) is not None


@dataclass(frozen=True)
class QuoteOutcome:
    pmid: str
    attempt: int
    seq: int
    status: str
    score: float | None = None
    evidence_id: str | None = None


@dataclass
class PaperExtraction:
    pmid: str
    items: list[EvidenceItem]
    outcomes: list[QuoteOutcome]
    dropped: int
    retried: bool
    warnings: list[str]


def check_findings(paper: Paper, drafts: Sequence[EvidenceDraft], *, attempt: int, first_n: int,
                   sub_question_ids: Sequence[str], model: str, prompt_version: str,
                   ) -> tuple[list[EvidenceItem], list[EvidenceDraft], list[QuoteOutcome], list[str]]:
    """(verified items, drafts whose quote failed, one outcome per draft, warnings). Item ids are
    ev_<pmid>_<n> with n starting at first_n, counting verified items only, in draft order."""
    items: list[EvidenceItem] = []
    failed: list[EvidenceDraft] = []
    outcomes: list[QuoteOutcome] = []
    warnings: list[str] = []
    pmid, abstract = paper.pmid, paper.abstract
    norm_abstract, _ = quote_span.normalize(abstract)
    norm_context, _ = quote_span.normalize(f"{paper.title} {abstract}")
    norm_context = norm_context.lower()
    seed = seed_design(paper.publication_types)

    for seq, d in enumerate(drafts, start=1):
        q = d.quote.strip()
        if len(q) < QUOTE_MIN_CHARS:
            outcomes.append(QuoteOutcome(pmid, attempt, seq, "too_short"))
            failed.append(d)
            continue
        if len(q) > QUOTE_MAX_CHARS:
            outcomes.append(QuoteOutcome(pmid, attempt, seq, "too_long"))
            failed.append(d)
            continue
        m = quote_span.validate_quote(abstract, q)
        if not m.found:
            outcomes.append(QuoteOutcome(pmid, attempt, seq, "not_found"))
            failed.append(d)
            continue
        statement = d.statement.strip()
        if not statement:
            outcomes.append(QuoteOutcome(pmid, attempt, seq, m.status, m.score if m.status == "fuzzy" else None))
            warnings.append(f"extract.empty_statement pmid:{pmid}")
            continue

        design = seed or d.study_design
        if seed and seed != d.study_design:
            warnings.append(f"extract.design_from_pubtype pmid:{pmid} llm={d.study_design.value} pubmed={seed.value}")
        sample = d.sample_size
        if sample is not None and not (sample > 0 and _number_in(abstract, sample)):
            warnings.append(f"extract.sample_size_unverified pmid:{pmid} n={sample}")
            sample = None
        effect = d.effect_size.strip() if d.effect_size else None
        if effect and quote_span.normalize(effect)[0] not in norm_abstract:
            warnings.append(f"extract.effect_size_unverified pmid:{pmid}")
            effect = None
        cohort = d.cohort_or_trial_name.strip() if d.cohort_or_trial_name else None
        if cohort and quote_span.normalize(cohort)[0].lower() not in norm_context:
            warnings.append(f"extract.cohort_name_unverified pmid:{pmid}")
            cohort = None

        ev_id = f"ev_{pmid}_{first_n + len(items)}"
        items.append(EvidenceItem(
            id=ev_id, pmid=pmid, sub_question_ids=list(sub_question_ids), statement=statement, quote=m.quote,
            quote_start=m.start, quote_end=m.end, subject=d.subject.strip().lower(),
            outcome=d.outcome.strip().lower(), cohort_or_trial_name=cohort, direction=d.direction,
            effect_size=effect, population=(d.population or "").strip() or None, organism=d.organism,
            study_design=design, sample_size=sample, strength=strength_for(design, d.organism, sample),
            limitations=[x.strip() for x in d.limitations if x.strip()], extractor_model=model,
            prompt_version=prompt_version))
        outcomes.append(QuoteOutcome(pmid, attempt, seq, m.status, m.score if m.status == "fuzzy" else None, ev_id))
    return items, failed, outcomes, warnings


async def extract_batch(papers: Sequence[Paper], sub_questions_by_pmid: Mapping[str, Sequence[SubQuestion]],
                        llm: StageLLM, *, version: int | None = None, parent: str | None = None,
                        ) -> list[PaperExtraction]:
    prompt = load_prompt(PROMPT, version=version)
    by_pmid = {p.pmid: p for p in papers}
    distinct = {sq.id: sq for p in papers for sq in sub_questions_by_pmid.get(p.pmid, [])}
    entries = {p.pmid: {"pmid": p.pmid, "title": p.title, "abstract": p.abstract, "publication_types": p.publication_types,
                "sub_question_ids": [sq.id for sq in sub_questions_by_pmid.get(p.pmid, [])]} for p in papers}
    reply = await llm.complete(
        prompt.render("extract", papers=list(entries.values()), sub_questions=[distinct[k] for k in sorted(distinct)]),
        ExtractionDraft, input_refs=[f"pmid:{p.pmid}" for p in papers], parent=parent)

    grouped: dict[str, list[EvidenceDraft]] = {p.pmid: [] for p in papers}
    warnings: dict[str, list[str]] = {p.pmid: [] for p in papers}
    for f in reply.findings:
        if f.pmid in grouped:
            grouped[f.pmid].append(f)
        elif papers:
            warnings[papers[0].pmid].append(f"extract.unknown_pmid {f.pmid}")

    results: dict[str, PaperExtraction] = {}
    failed_by: dict[str, list[EvidenceDraft]] = {}
    for p in papers:
        drafts = grouped[p.pmid]
        if len(drafts) > MAX_FINDINGS_PER_PAPER:
            warnings[p.pmid].append(f"extract.too_many_findings pmid:{p.pmid} got={len(drafts)}")
            drafts = drafts[:MAX_FINDINGS_PER_PAPER]
        sq_ids = [sq.id for sq in sub_questions_by_pmid.get(p.pmid, [])]
        items, failed, outcomes, warns = check_findings(p, drafts, attempt=1, first_n=1, sub_question_ids=sq_ids,
                                                        model=llm.model, prompt_version=prompt.version)
        results[p.pmid] = PaperExtraction(p.pmid, items, outcomes, len(failed), bool(failed),
                                          warnings[p.pmid] + warns)
        if failed:
            failed_by[p.pmid] = failed

    async def retry(pmid: str) -> ExtractionDraft:
        failed = failed_by[pmid]
        return await llm.complete(
            prompt.render("retry", paper=entries[pmid],
                          sub_questions=list(sub_questions_by_pmid.get(pmid, [])),
                          failed=[{"statement": f.statement, "quote": f.quote} for f in failed]),
            ExtractionDraft, input_refs=[f"pmid:{pmid}"], parent=parent)

    retried = list(failed_by)
    replies = await asyncio.gather(*(retry(pmid) for pmid in retried))
    for pmid, rep in zip(retried, replies):
        res, failed = results[pmid], failed_by[pmid]
        drafts = [f for f in rep.findings if f.pmid == pmid][:len(failed)]
        sq_ids = [sq.id for sq in sub_questions_by_pmid.get(pmid, [])]
        items, _, outcomes, warns = check_findings(by_pmid[pmid], drafts, attempt=2, first_n=len(res.items) + 1,
                                                   sub_question_ids=sq_ids, model=llm.model,
                                                   prompt_version=prompt.version)
        res.items += items
        res.outcomes += outcomes
        res.warnings += warns
        res.dropped = len(failed) - len(items)
    return [results[p.pmid] for p in papers]
