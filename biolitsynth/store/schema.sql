-- Plan §6.1. Applied on every startup (all statements are IF NOT EXISTS). No migrations framework.
-- Run-scoped tables use (run_id, id) keys: ids like ev_<pmid>_<n> and q1 repeat across runs.

CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    question     TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    config_json  TEXT,
    plan_json    TEXT
);

CREATE TABLE IF NOT EXISTS sub_questions (
    run_id    TEXT NOT NULL,
    id        TEXT NOT NULL,
    text      TEXT NOT NULL,
    facet     TEXT NOT NULL,
    rationale TEXT,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS queries (
    run_id                  TEXT NOT NULL,
    id                      TEXT NOT NULL,
    sub_question_id         TEXT NOT NULL,
    query_string            TEXT NOT NULL,
    filters_json            TEXT,
    hit_count               INTEGER,
    refinement_history_json TEXT,
    round                   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, id)
);

-- shared across runs
CREATE TABLE IF NOT EXISTS papers (
    pmid              TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    abstract          TEXT NOT NULL,
    abstract_sha256   TEXT NOT NULL,
    journal           TEXT,
    year              INTEGER,
    doi               TEXT,
    pub_types_json    TEXT,
    mesh_json         TEXT,
    registry_ids_json TEXT,
    is_retracted      INTEGER NOT NULL DEFAULT 0,
    fetched_at        TEXT
);

CREATE TABLE IF NOT EXISTS run_papers (
    run_id   TEXT NOT NULL,
    pmid     TEXT NOT NULL,
    query_id TEXT NOT NULL,
    rank     INTEGER,
    study_id TEXT,
    PRIMARY KEY (run_id, pmid, query_id)
);

CREATE TABLE IF NOT EXISTS study_clusters (
    run_id             TEXT NOT NULL,
    id                 TEXT NOT NULL,
    primary_pmid       TEXT NOT NULL,
    pmids_json         TEXT NOT NULL,
    link_reasons_json  TEXT,
    confidence         TEXT NOT NULL,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS evidence (
    run_id              TEXT NOT NULL,
    id                  TEXT NOT NULL,
    pmid                TEXT NOT NULL,
    study_id            TEXT,
    statement           TEXT NOT NULL,
    quote               TEXT NOT NULL,
    quote_start         INTEGER,
    quote_end           INTEGER,
    subject             TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    canonical_subject   TEXT,
    canonical_outcome   TEXT,
    direction           TEXT NOT NULL,
    effect_size         TEXT,
    population          TEXT,
    organism            TEXT NOT NULL,
    study_design        TEXT NOT NULL,
    sample_size         INTEGER,
    cohort_or_trial_name TEXT,
    strength            TEXT,
    limitations_json    TEXT,
    extractor_model     TEXT,
    prompt_version      TEXT,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS evidence_subq (
    run_id          TEXT NOT NULL,
    evidence_id     TEXT NOT NULL,
    sub_question_id TEXT NOT NULL,
    PRIMARY KEY (run_id, evidence_id, sub_question_id)
);

CREATE TABLE IF NOT EXISTS relevance_scores (
    run_id            TEXT NOT NULL,
    sub_question_id   TEXT NOT NULL,
    pmid              TEXT NOT NULL,
    query_text_sha256 TEXT,
    model             TEXT NOT NULL,
    score             REAL NOT NULL,
    rank              INTEGER NOT NULL,
    pubmed_rank       INTEGER NOT NULL,
    kept              INTEGER NOT NULL,
    drop_reason       TEXT,
    PRIMARY KEY (run_id, sub_question_id, pmid)
);

CREATE TABLE IF NOT EXISTS term_map (
    run_id    TEXT NOT NULL,
    kind      TEXT NOT NULL,          -- subject | outcome
    raw       TEXT NOT NULL,
    canonical TEXT NOT NULL,
    source    TEXT NOT NULL,          -- seed | llm | unmapped
    PRIMARY KEY (run_id, kind, raw)
);

CREATE TABLE IF NOT EXISTS conflicts (
    run_id             TEXT NOT NULL,
    id                 TEXT NOT NULL,
    canonical_subject  TEXT NOT NULL,
    canonical_outcome  TEXT NOT NULL,
    conflict_type      TEXT NOT NULL,
    sides_json         TEXT NOT NULL,
    balance            TEXT NOT NULL,
    leaning_side       TEXT,
    status             TEXT NOT NULL,
    explanations_json  TEXT,
    adjudicator_model  TEXT,
    PRIMARY KEY (run_id, id)
);

CREATE TABLE IF NOT EXISTS omitted_evidence (
    run_id           TEXT NOT NULL,
    claim_id         TEXT NOT NULL,
    attempt          INTEGER NOT NULL,
    evidence_id      TEXT NOT NULL,
    reason           TEXT NOT NULL,
    weight           REAL NOT NULL,
    critic_judgment  TEXT,
    critic_rationale TEXT,
    PRIMARY KEY (run_id, claim_id, attempt, evidence_id)
);

-- one row per (claim, attempt): a retry regenerates failed claims, passed claims are frozen
CREATE TABLE IF NOT EXISTS claims (
    run_id         TEXT NOT NULL,
    id             TEXT NOT NULL,
    attempt        INTEGER NOT NULL,
    text           TEXT NOT NULL,
    claim_type     TEXT NOT NULL,
    confidence     TEXT NOT NULL,
    conflict_id    TEXT,
    reasoning_json TEXT,
    caveats_json   TEXT,
    status         TEXT,
    PRIMARY KEY (run_id, id, attempt)
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    run_id      TEXT NOT NULL,
    claim_id    TEXT NOT NULL,
    attempt     INTEGER NOT NULL,
    evidence_id TEXT NOT NULL,
    PRIMARY KEY (run_id, claim_id, attempt, evidence_id)
);

CREATE TABLE IF NOT EXISTS checks (
    run_id   TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    attempt  INTEGER NOT NULL,
    check_id TEXT NOT NULL,           -- "check" is an SQL keyword
    passed   INTEGER NOT NULL,
    detail   TEXT,
    PRIMARY KEY (run_id, claim_id, attempt, check_id)
);

CREATE TABLE IF NOT EXISTS verdicts (
    run_id       TEXT NOT NULL,
    claim_id     TEXT NOT NULL,
    attempt      INTEGER NOT NULL,
    verdict_json TEXT NOT NULL,
    status       TEXT NOT NULL,
    critic_model TEXT,
    PRIMARY KEY (run_id, claim_id, attempt)
);

CREATE TABLE IF NOT EXISTS trace_events (
    run_id           TEXT NOT NULL,
    span_id          TEXT PRIMARY KEY,
    parent_span_id   TEXT,
    stage            TEXT NOT NULL,
    agent            TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    model            TEXT,
    input_refs_json  TEXT,
    output_refs_json TEXT,
    prompt_version   TEXT,
    prompt_hash      TEXT,
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    latency_ms       INTEGER,
    cache_hit        INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL,
    message          TEXT,
    ts               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_run ON trace_events (run_id, ts);

-- key = sha256(model | prompt_version | rendered prompt | schema); table is created now, used from P2
CREATE TABLE IF NOT EXISTS llm_cache (
    key            TEXT PRIMARY KEY,
    model          TEXT NOT NULL,
    prompt_version TEXT,
    response_json  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
