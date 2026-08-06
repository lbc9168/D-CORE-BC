# Full-Database Golden Standard Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, read-only Golden Standard Studio that samples 60 pilot articles, generates four-source candidate labels, supports conflict-first evidence-backed human review, publishes versioned rich/eval gold, and extends eval without breaking existing gold.

**Architecture:** A standalone FastAPI + SQLite tool lives under `external-tools/gold-standard-studio/`; it reads existing news/project databases through SQLite read-only URIs and stores all mutable review state in its own `data/review.db`. Rich gold is the single authoritative artifact, while a deterministic exporter produces a backward-compatible eval projection under `eval/gold_sets/`.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Jinja2, Pydantic v2, Typer, SQLite, vanilla JavaScript/CSS, pytest, FastAPI TestClient, Playwright for one browser smoke test.

## Global Constraints

- Work only in `E:/Projects/BRI_RE_Database_v2/.claude/worktrees/database-refactor-expansion-aa6198` on `claude/database-refactor-expansion-aa6198`.
- The worktree begins dirty with 35 pre-existing paths; never use `git add -A`, `git add .`, reset, checkout, clean, or destructive commands.
- Before implementation, record `git status --short`, `git diff --name-only`, and `git diff --cached --name-only`; stage only exact task paths.
- Existing energy-taxonomy changes in `eval/gold_loader.py`, `eval/metrics.py`, `eval/predict.py`, and `eval/run_eval.py` are user work and must be preserved when those files are extended.
- Every function and major module must have a one-sentence Chinese or English purpose docstring/comment, per `AGENTS.md`.
- Any code change must update the relevant `docs/status/` file and `docs/changes/2026-W32.md` before completion.
- Production databases and raw-news databases are read-only inputs; all mutable state belongs to the Studio review DB.
- No AI candidate becomes gold without an explicit human article confirmation.
- `unreviewed` and `needs_adjudication` records cannot be published.
- Existing legacy gold JSONL must continue to load and score unchanged.
- Use TDD for every task: failing test, observed failure, minimal implementation, passing test, exact-path commit.
- Pilot size is 60 articles; final design target is about 250 articles with 15%–20% negative samples.
- Do not batch-call external models or publish a formal gold version during implementation; those are separately authorized runtime operations.

---

## Planned File Map

### New Studio package

- `external-tools/gold-standard-studio/pyproject.toml` — isolated dependencies, CLI entry point, pytest configuration.
- `external-tools/gold-standard-studio/README.md` — setup, read-only guarantees, Pilot runbook.
- `external-tools/gold-standard-studio/src/gold_standard_studio/config.py` — validated paths and sampling/runtime settings.
- `external-tools/gold-standard-studio/src/gold_standard_studio/schemas.py` — rich gold, evidence, candidate, difficulty and manifest contracts.
- `external-tools/gold-standard-studio/src/gold_standard_studio/field_registry.py` — reviewed fields, types, evidence requirements and eval mappings.
- `external-tools/gold-standard-studio/src/gold_standard_studio/sources.py` — read-only article/project repositories and immutable snapshots.
- `external-tools/gold-standard-studio/src/gold_standard_studio/sampling.py` — deterministic stratified/risk-weighted sample selection.
- `external-tools/gold-standard-studio/src/gold_standard_studio/candidate_cache.py` — candidate cache keyed by article/model/prompt/parser versions.
- `external-tools/gold-standard-studio/src/gold_standard_studio/candidate_builder.py` — four-source candidate orchestration.
- `external-tools/gold-standard-studio/src/gold_standard_studio/review_prompt.py` — independent audit prompt and output contract.
- `external-tools/gold-standard-studio/src/gold_standard_studio/evidence.py` — evidence quote normalization and offset relocation.
- `external-tools/gold-standard-studio/src/gold_standard_studio/difficulty.py` — explainable conflict/risk scoring and audit sampling.
- `external-tools/gold-standard-studio/src/gold_standard_studio/review_store.py` — review DB schema, decisions, audit trail and backup.
- `external-tools/gold-standard-studio/src/gold_standard_studio/exporter.py` — validation, rich gold publication and eval projection.
- `external-tools/gold-standard-studio/src/gold_standard_studio/cli.py` — init/sample/build/serve/publish/report commands.
- `external-tools/gold-standard-studio/src/gold_standard_studio/web/app.py` — FastAPI routes and dependency wiring.
- `external-tools/gold-standard-studio/src/gold_standard_studio/web/templates/review.html` — conflict-first two-pane review page.
- `external-tools/gold-standard-studio/src/gold_standard_studio/web/templates/queue.html` — progress, filters and difficulty queue.
- `external-tools/gold-standard-studio/src/gold_standard_studio/web/static/review.js` — autosave, keyboard shortcuts and structure edits.
- `external-tools/gold-standard-studio/src/gold_standard_studio/web/static/style.css` — evidence and conflict visual states.

### Existing eval integration

- `eval/gold_loader.py` — accept projection metadata and per-field eligibility while retaining old JSONL compatibility.
- `eval/predict.py` — expose all newly scored production fields in normalized predictions.
- `eval/metrics.py` — per-type metrics, eligibility-aware denominators and slices.
- `eval/run_eval.py` — aggregate split/risk/category metrics and record run manifest.
- `eval/README.md` and `docs/rules_and_plans/eval.md` — documented workflow and metric definitions.

### Tests and docs

- `external-tools/gold-standard-studio/tests/` — focused package tests and one full temporary-SQLite integration test.
- `tests/test_gold_loader.py`, `tests/test_eval_metrics.py`, `tests/test_predict.py`, `tests/test_run_eval_e2e.py` — backward compatibility and new projection scoring.
- `docs/status/external-tools.md`, `docs/status/s01b.md`, `docs/changes/2026-W32.md` — current state and change log.
- `.gitignore` — ignore Studio runtime data but explicitly retain versioned `eval/gold_sets/**/*.jsonl` and manifests.

---

### Task 1: Package Skeleton, Configuration, and Dirty-Tree Guard

**Files:**
- Create: `external-tools/gold-standard-studio/pyproject.toml`
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/__init__.py`
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/config.py`
- Create: `external-tools/gold-standard-studio/tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: explicit CLI paths to raw news DB, projects DB, main DB and Studio data directory.
- Produces: `StudioConfig.from_paths(...) -> StudioConfig` and `sqlite_ro_uri(path: Path) -> str` for every later task.

- [ ] **Step 1: Capture the dirty-tree baseline before touching files**

Run:

```powershell
git status --short
git diff --name-only
git diff --cached --name-only
git branch --show-current
```

Expected: branch is `claude/database-refactor-expansion-aa6198`; existing dirty paths remain unstaged. Save the command output in the task handoff, not in the repository.

- [ ] **Step 2: Write failing configuration tests**

```python
def test_config_requires_existing_input_databases(tmp_path):
    with pytest.raises(ValueError, match="raw_news_db does not exist"):
        StudioConfig.from_paths(
            raw_news_db=tmp_path / "missing.db",
            projects_db=tmp_path / "projects.db",
            main_db=tmp_path / "main.db",
            data_dir=tmp_path / "studio",
        )


def test_sqlite_ro_uri_is_explicitly_read_only(tmp_path):
    db = tmp_path / "a.db"
    db.touch()
    assert sqlite_ro_uri(db).endswith("?mode=ro")
```

- [ ] **Step 3: Run the tests and observe the expected import failure**

Run:

```powershell
uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_config.py -q
```

Expected: FAIL because `gold_standard_studio.config` does not exist.

- [ ] **Step 4: Add the package and minimal validated configuration**

```python
@dataclass(frozen=True)
class StudioConfig:
    raw_news_db: Path
    projects_db: Path
    main_db: Path
    data_dir: Path
    seed: int = 20260806
    pilot_size: int = 60
    target_size: int = 250
    negative_fraction: float = 0.18

    @classmethod
    def from_paths(cls, *, raw_news_db, projects_db, main_db, data_dir):
        values = {
            "raw_news_db": Path(raw_news_db),
            "projects_db": Path(projects_db),
            "main_db": Path(main_db),
        }
        for name, path in values.items():
            if not path.is_file():
                raise ValueError(f"{name} does not exist: {path}")
        return cls(**values, data_dir=Path(data_dir))


def sqlite_ro_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"
```

Set project dependencies to `fastapi`, `uvicorn`, `jinja2`, `pydantic>=2`, and `typer`; set dev dependencies to `pytest`, `httpx`, and `playwright`.

- [ ] **Step 5: Add precise ignore rules**

Append:

```gitignore
external-tools/gold-standard-studio/data/
!external-tools/gold-standard-studio/data/.gitkeep
eval/gold_sets/**/reports/
```

Do not add a broad `eval/gold_sets/` ignore rule; formal gold must remain trackable.

- [ ] **Step 6: Run tests and commit exact paths**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_config.py -q`  
Expected: PASS.

```powershell
git add -- .gitignore external-tools/gold-standard-studio/pyproject.toml external-tools/gold-standard-studio/src/gold_standard_studio/__init__.py external-tools/gold-standard-studio/src/gold_standard_studio/config.py external-tools/gold-standard-studio/tests/test_config.py
git commit -m "feat(gold-studio): scaffold isolated review tool"
```

---

### Task 2: Rich Gold and Field Registry Contracts

**Files:**
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/schemas.py`
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/field_registry.py`
- Create: `external-tools/gold-standard-studio/tests/test_schemas.py`

**Interfaces:**
- Consumes: Pydantic v2.
- Produces: `EvidenceSpan`, `GoldField`, `GoldEntity`, `GoldProject`, `RichGoldRecord`, `CandidateValue`, `CandidateBundle`, `DifficultyScore`, `DatasetManifest`, and `FIELD_REGISTRY`.

- [ ] **Step 1: Write failing schema tests for state and publication rules**

```python
def test_present_critical_field_requires_confirmed_evidence():
    with pytest.raises(ValidationError):
        GoldField(state="present", value=100, evidence=[], evidence_required=True)


def test_non_present_field_rejects_value():
    with pytest.raises(ValidationError):
        GoldField(state="not_applicable", value=100, evidence=[])


def test_formal_record_rejects_needs_adjudication():
    record = make_record(field_state="needs_adjudication")
    with pytest.raises(ValueError, match="not publishable"):
        record.assert_publishable()
```

- [ ] **Step 2: Run the focused tests and observe missing contracts**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_schemas.py -q`  
Expected: FAIL on missing imports.

- [ ] **Step 3: Implement exact state and evidence contracts**

```python
FieldState = Literal[
    "present", "not_mentioned", "not_applicable", "uncertain", "needs_adjudication"
]


class EvidenceSpan(BaseModel):
    quote: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    article_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["article"] = "article"


class GoldField(BaseModel):
    state: FieldState
    value: JsonValue | None = None
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    evidence_required: bool = False

    @model_validator(mode="after")
    def validate_state(self):
        if self.state == "present" and self.value is None:
            raise ValueError("present field requires value")
        if self.state != "present" and self.value is not None:
            raise ValueError("non-present field cannot carry value")
        if self.state == "present" and self.evidence_required and not self.evidence:
            raise ValueError("present critical field requires evidence")
        return self
```

Define `GoldProject.fields: dict[str, GoldField]`, stable `gold_project_id`, entity records with per-role evidence, and `RichGoldRecord.assert_publishable()` that requires an article-level reviewed status and rejects `needs_adjudication`. It permits `uncertain`, which the projection marks ineligible.

- [ ] **Step 4: Define the reviewed field registry once**

```python
FIELD_REGISTRY = {
    "project_name": FieldSpec("text", evidence_required=True, eval_metric="name_similarity"),
    "country": FieldSpec("enum", evidence_required=False, eval_metric="exact"),
    "location": FieldSpec("text", evidence_required=False, eval_metric="normalized_exact"),
    "category": FieldSpec("enum", evidence_required=True, eval_metric="exact"),
    "generation_sources": FieldSpec("set", evidence_required=True, eval_metric="set_f1"),
    "storage_status": FieldSpec("enum", evidence_required=True, eval_metric="exact"),
    "capacity_mw": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "storage_power_mw": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "storage_energy_mwh": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "project_stage": FieldSpec("enum", evidence_required=True, eval_metric="exact"),
    "project_developer": FieldSpec("text", evidence_required=True, eval_metric="normalized_name"),
    "planned_start_date": FieldSpec("date", evidence_required=True, eval_metric="date_exact"),
    "planned_complete_date": FieldSpec("date", evidence_required=True, eval_metric="date_exact"),
    "investment_date": FieldSpec("date", evidence_required=True, eval_metric="date_exact"),
    "total_investment_usd": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "contract_amount_usd": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "financing_amount_usd": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "financing_model": FieldSpec("enum", evidence_required=True, eval_metric="exact"),
    "project_summary": FieldSpec("text", evidence_required=False, eval_metric=None),
}
```

- [ ] **Step 5: Run tests, export JSON schema, and commit**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_schemas.py -q`  
Expected: PASS.

```powershell
git add -- external-tools/gold-standard-studio/src/gold_standard_studio/schemas.py external-tools/gold-standard-studio/src/gold_standard_studio/field_registry.py external-tools/gold-standard-studio/tests/test_schemas.py
git commit -m "feat(gold-studio): define rich gold contracts"
```

---

### Task 3: Read-Only Source Repositories and Immutable Snapshots

**Files:**
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/sources.py`
- Create: `external-tools/gold-standard-studio/tests/test_sources.py`

**Interfaces:**
- Consumes: `StudioConfig`, existing `raw_news`, `extracted_projects`, `canonical_projects`, `project_events`, and `chinese_entities` tables.
- Produces: `SourceRepository.load_article(source_link) -> ArticleSnapshot`, `load_current_labels(source_link) -> list[CurrentProject]`, and `snapshot_hashes() -> dict[str, str]`.

- [ ] **Step 1: Build a temporary three-database fixture and failing read-only tests**

```python
def test_repository_reads_article_and_current_project_without_writes(db_fixture):
    before = db_fixture.hashes()
    repo = SourceRepository(db_fixture.config)
    article = repo.load_article("https://example.test/a")
    projects = repo.load_current_labels(article.source_link)
    assert article.title == "肯尼亚项目"
    assert projects[0].category == "综合能源"
    assert db_fixture.hashes() == before


def test_repository_connection_rejects_insert(db_fixture):
    repo = SourceRepository(db_fixture.config)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        repo._raw_conn.execute("INSERT INTO raw_news(url) VALUES ('x')")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_sources.py -q`  
Expected: FAIL because `SourceRepository` is missing.

- [ ] **Step 3: Implement read-only connections and stable snapshots**

```python
def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(sqlite_ro_uri(path), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def article_sha256(title: str, content: str, publish_date: str) -> str:
    payload = "\n".join((title.strip(), content.strip(), publish_date.strip()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

Map source links through project events/extracted rows without mutating import flags. Preserve raw database values separately from normalized display values.

- [ ] **Step 4: Run tests and commit**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_sources.py -q`  
Expected: PASS with before/after database hashes equal.

```powershell
git add -- external-tools/gold-standard-studio/src/gold_standard_studio/sources.py external-tools/gold-standard-studio/tests/test_sources.py
git commit -m "feat(gold-studio): add read-only source repositories"
```

---

### Task 4: Deterministic Stratified Pilot Sampling

**Files:**
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/sampling.py`
- Create: `external-tools/gold-standard-studio/tests/test_sampling.py`

**Interfaces:**
- Consumes: `list[SampleCandidate]`, `SamplingConfig(seed, size, negative_fraction, category_floors)`.
- Produces: `select_sample(candidates, config) -> list[SelectedSample]` and `build_manifest(selected, source_hashes) -> DatasetManifest`.

- [ ] **Step 1: Write failing determinism, quota, and dedup tests**

```python
def test_pilot_is_deterministic_and_has_negative_quota(candidates):
    config = SamplingConfig(seed=20260806, size=60, negative_fraction=0.18)
    first = select_sample(candidates, config)
    second = select_sample(list(reversed(candidates)), config)
    assert [x.sample_id for x in first] == [x.sample_id for x in second]
    assert 9 <= sum(x.is_negative for x in first) <= 12
    assert len({x.source_link for x in first}) == 60


def test_rare_categories_receive_configured_floor(candidates):
    chosen = select_sample(candidates, SamplingConfig.pilot())
    counts = Counter(tag for row in chosen for tag in row.category_tags)
    assert counts["储能"] >= 3
    assert counts["输变电建设"] >= 3
```

- [ ] **Step 2: Run and observe failure**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_sampling.py -q`  
Expected: FAIL because sampling functions are missing.

- [ ] **Step 3: Implement stable weighted selection**

Normalize candidates by source link, sort by stable sample ID before seeded selection, satisfy negative and category floors first, then fill remaining slots by risk-weighted reservoir sampling. Store `inclusion_reasons`, `risk_tags`, and intended split on every selection.

```python
def _stable_random(seed: int, sample_id: str) -> float:
    digest = hashlib.sha256(f"{seed}:{sample_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64
```

Use final split targets 150/70/30 only at size 250; for Pilot, allocate 36/17/7 while maintaining category/risk coverage.

- [ ] **Step 4: Run tests and commit**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_sampling.py -q`  
Expected: PASS.

```powershell
git add -- external-tools/gold-standard-studio/src/gold_standard_studio/sampling.py external-tools/gold-standard-studio/tests/test_sampling.py
git commit -m "feat(gold-studio): add deterministic pilot sampling"
```

---

### Task 5: Review Store, Audit Trail, and Backup

**Files:**
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/review_store.py`
- Create: `external-tools/gold-standard-studio/tests/test_review_store.py`

**Interfaces:**
- Consumes: `RichGoldRecord` drafts, candidate bundles, and user decisions.
- Produces: `ReviewStore.initialize()`, `save_candidate_bundle(...)`, `save_field_decision(...)`, `save_structure(...)`, `confirm_article(...)`, `list_queue(...)`, and `backup() -> Path`.

- [ ] **Step 1: Write failing autosave and audit tests**

```python
def test_field_decision_is_atomic_and_audited(tmp_path):
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    store.seed_article(make_ready_article())
    store.save_field_decision("S001", "P01.category", make_gold_field("光伏"), actor="human")
    assert store.get_article("S001").projects[0].fields["category"].value == "光伏"
    audit = store.list_audit("S001")
    assert audit[-1].field_path == "P01.category"
    assert audit[-1].new_value == "光伏"


def test_confirm_rejects_unresolved_conflicts(tmp_path):
    store = seeded_store(tmp_path, unresolved=True)
    with pytest.raises(ValueError, match="unresolved conflicts"):
        store.confirm_article("S001", actor="human")
```

- [ ] **Step 2: Run and observe failure**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_review_store.py -q`  
Expected: FAIL because the store is missing.

- [ ] **Step 3: Implement review DB schema and transaction boundaries**

Create tables `samples`, `article_drafts`, `candidate_bundles`, `field_decisions`, `structure_revisions`, `audit_log`, `review_sessions`, and `studio_meta`. Store Pydantic payloads as canonical JSON and use `BEGIN IMMEDIATE` for each human decision.

```python
def save_field_decision(self, sample_id, field_path, field, *, actor):
    with self._connect() as conn:
        old = self._field_value(conn, sample_id, field_path)
        self._write_field(conn, sample_id, field_path, field)
        self._append_audit(conn, sample_id, field_path, old, field, actor)
```

Backups use `sqlite3.Connection.backup`, validate `PRAGMA integrity_check`, row counts, and latest audit ID.

- [ ] **Step 4: Add resume and backup tests**

```python
def test_latest_in_review_article_resumes_after_reopen(tmp_path):
    store = seeded_store(tmp_path)
    store.start_review("S002", actor="human")
    reopened = ReviewStore(store.path)
    assert reopened.resume_target("human") == "S002"


def test_backup_is_readable_and_preserves_audit_tail(tmp_path):
    store = seeded_store(tmp_path)
    backup = store.backup()
    assert ReviewStore(backup).latest_audit_id() == store.latest_audit_id()
```

- [ ] **Step 5: Run tests and commit**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_review_store.py -q`  
Expected: PASS.

```powershell
git add -- external-tools/gold-standard-studio/src/gold_standard_studio/review_store.py external-tools/gold-standard-studio/tests/test_review_store.py
git commit -m "feat(gold-studio): persist review decisions and audit history"
```

---

### Task 6: Candidate Cache and Four-Source Orchestration

**Files:**
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/candidate_cache.py`
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/candidate_builder.py`
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/review_prompt.py`
- Create: `external-tools/gold-standard-studio/tests/test_candidate_builder.py`

**Interfaces:**
- Consumes: `ArticleSnapshot`, current DB labels, production extractor callable, independent reviewer callable, deterministic parser callable.
- Produces: `CandidateRunner` protocol, `CandidateCacheKey`, and `CandidateBuilder.build(article) -> CandidateBundle`.

- [ ] **Step 1: Write failing independence, cache, and partial-failure tests**

```python
def test_review_runner_receives_article_but_not_production_answer(article):
    reviewer = RecordingRunner("review")
    builder = make_builder(reviewer=reviewer)
    builder.build(article)
    assert reviewer.calls[0].production_candidate is None


def test_partial_runner_failure_keeps_other_candidates(article):
    builder = make_builder(review=FailingRunner("timeout"))
    bundle = builder.build(article)
    assert bundle.sources["database"].status == "ok"
    assert bundle.sources["review"].status == "error"
    assert bundle.sources["review"].error_code == "timeout"


def test_cache_key_changes_with_prompt_hash(article):
    assert cache_key(article, model="m", prompt_hash="a", parser_version="p") != cache_key(
        article, model="m", prompt_hash="b", parser_version="p"
    )
```

- [ ] **Step 2: Run and observe failure**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_candidate_builder.py -q`  
Expected: FAIL because candidate modules are missing.

- [ ] **Step 3: Implement the runner protocol and cache**

```python
class CandidateRunner(Protocol):
    source_name: str
    version: str
    def run(self, article: ArticleSnapshot) -> CandidateValue: ...


@dataclass(frozen=True)
class CandidateCacheKey:
    article_sha256: str
    source_name: str
    source_version: str
    prompt_hash: str
    parser_version: str
```

Store one JSON cache record per key under `data/model_cache/`; write through a temporary file and `Path.replace`. Never include production candidate text in the independent review prompt.

- [ ] **Step 4: Implement the four adapters**

- Database adapter maps `CurrentProject` records.
- Production adapter calls the existing staged extractor and normalizes through `eval.predict.flat_prediction`.
- Review adapter uses a dedicated prompt that requests field values plus exact evidence quotes and includes no database/production values.
- Deterministic adapter invokes capacity/amount/date/energy parsers and versioned classification policy.

The review output contract is the same `CandidateValue` schema; invalid provider JSON is stored as a structured error, not coerced into an empty answer.

- [ ] **Step 5: Run tests and commit**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_candidate_builder.py -q`  
Expected: PASS.

```powershell
git add -- external-tools/gold-standard-studio/src/gold_standard_studio/candidate_cache.py external-tools/gold-standard-studio/src/gold_standard_studio/candidate_builder.py external-tools/gold-standard-studio/src/gold_standard_studio/review_prompt.py external-tools/gold-standard-studio/tests/test_candidate_builder.py
git commit -m "feat(gold-studio): build independent candidate bundles"
```

---

### Task 7: Evidence Relocation, Conflict Detection, Difficulty, and Audit Sampling

**Files:**
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/evidence.py`
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/difficulty.py`
- Create: `external-tools/gold-standard-studio/tests/test_evidence.py`
- Create: `external-tools/gold-standard-studio/tests/test_difficulty.py`

**Interfaces:**
- Consumes: article text/hash, `CandidateBundle`, `FIELD_REGISTRY`, fixed audit seed.
- Produces: `locate_evidence(...) -> EvidenceSpan`, `compare_candidates(...) -> list[FieldConflict]`, `score_difficulty(...) -> DifficultyScore`, and `select_for_audit(...) -> bool`.

- [ ] **Step 1: Write failing evidence tests**

```python
def test_quote_relocation_recovers_shifted_offset():
    text = "前文新增。建设100MW光伏电站，并配套20MW/40MWh储能。"
    span = relocate_quote(text, "20MW/40MWh储能", expected_start=5, article_sha256=sha(text))
    assert text[span.start:span.end] == "20MW/40MWh储能"


def test_ambiguous_quote_requires_review():
    with pytest.raises(AmbiguousEvidenceError):
        relocate_quote("储能系统；储能系统", "储能系统", expected_start=None, article_sha256=sha("储能系统；储能系统"))
```

- [ ] **Step 2: Write failing conflict/difficulty tests**

```python
def test_database_hb_vs_fact_pv_is_explained_conflict(bundle):
    score = score_difficulty(bundle)
    assert "category_disagreement" in score.reason_codes
    assert "storage_context" in score.risk_tags


def test_audit_selection_is_deterministic_and_about_ten_percent():
    selected = [select_for_audit(f"S{i:03}", seed=20260806, rate=0.10) for i in range(1000)]
    assert 80 <= sum(selected) <= 120
    assert selected == [select_for_audit(f"S{i:03}", seed=20260806, rate=0.10) for i in range(1000)]
```

- [ ] **Step 3: Run both files and observe failures**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_evidence.py external-tools/gold-standard-studio/tests/test_difficulty.py -q`  
Expected: FAIL on missing functions.

- [ ] **Step 4: Implement normalization and explainable scoring**

Comparison dispatches through `FIELD_REGISTRY`: enum/date exact, numeric ±5%, sets normalized equality for conflict display, and names normalized fuzzy comparison. Difficulty is a weighted sum with explicit reason codes; no opaque model score is allowed.

```python
REASON_WEIGHTS = {
    "project_count_disagreement": 40,
    "category_disagreement": 20,
    "missing_required_evidence": 15,
    "multiple_amounts": 15,
    "multiple_entities": 10,
    "single_source_support": 10,
}
```

Dynamic audit rate is stored per risk tag in review DB metadata; an audited error raises that tag's rate by 0.10 up to 1.0.

- [ ] **Step 5: Run tests and commit**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_evidence.py external-tools/gold-standard-studio/tests/test_difficulty.py -q`  
Expected: PASS.

```powershell
git add -- external-tools/gold-standard-studio/src/gold_standard_studio/evidence.py external-tools/gold-standard-studio/src/gold_standard_studio/difficulty.py external-tools/gold-standard-studio/tests/test_evidence.py external-tools/gold-standard-studio/tests/test_difficulty.py
git commit -m "feat(gold-studio): rank conflicts and evidence risk"
```

---

### Task 8: Review API and Project-Structure Editing

**Files:**
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/web/__init__.py`
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/web/app.py`
- Create: `external-tools/gold-standard-studio/tests/test_web_api.py`

**Interfaces:**
- Consumes: `ReviewStore`, candidate/conflict data and Pydantic request models.
- Produces: queue/article endpoints and atomic field/structure/confirm actions.

- [ ] **Step 1: Write failing API tests**

```python
def test_queue_defaults_to_ready_low_difficulty(client):
    response = client.get("/api/queue")
    assert response.status_code == 200
    assert [row["sample_id"] for row in response.json()["items"]] == ["S002", "S001"]


def test_structure_revision_can_split_project(client):
    payload = {
        "base_revision": 1,
        "projects": [make_project_json("P01"), make_project_json("P02")],
    }
    response = client.put("/api/articles/S001/structure", json=payload)
    assert response.status_code == 200
    assert len(response.json()["projects"]) == 2


def test_stale_revision_is_rejected(client):
    response = client.put("/api/articles/S001/structure", json={"base_revision": 0, "projects": []})
    assert response.status_code == 409
```

- [ ] **Step 2: Run and observe failure**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_web_api.py -q`  
Expected: FAIL because the app is missing.

- [ ] **Step 3: Implement explicit routes**

```python
GET  /api/queue
GET  /api/articles/{sample_id}
POST /api/articles/{sample_id}/start
PUT  /api/articles/{sample_id}/structure
PUT  /api/articles/{sample_id}/fields/{field_path:path}
POST /api/articles/{sample_id}/confirm
POST /api/articles/{sample_id}/adjudication
GET  /api/articles/{sample_id}/audit
```

Structure requests send the full ordered project list with `base_revision`; the store checks optimistic concurrency and assigns stable new `gold_project_id` values without reusing deleted IDs.

- [ ] **Step 4: Add confirmation and read-only-source assertions**

Test that confirmation fails for unresolved conflict, missing required evidence, or needs-adjudication state; hash all three source DBs before and after API operations and assert equality.

- [ ] **Step 5: Run tests and commit**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_web_api.py -q`  
Expected: PASS.

```powershell
git add -- external-tools/gold-standard-studio/src/gold_standard_studio/web/__init__.py external-tools/gold-standard-studio/src/gold_standard_studio/web/app.py external-tools/gold-standard-studio/tests/test_web_api.py
git commit -m "feat(gold-studio): add atomic review API"
```

---

### Task 9: Conflict-First Browser Interface

**Files:**
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/web/templates/review.html`
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/web/templates/queue.html`
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/web/static/review.js`
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/web/static/style.css`
- Create: `external-tools/gold-standard-studio/tests/test_web_pages.py`
- Create: `external-tools/gold-standard-studio/tests/test_browser_smoke.py`

**Interfaces:**
- Consumes: Task 8 API JSON.
- Produces: two-pane evidence review, conflict cards, collapsed consensus, keyboard actions, autosave and progress queue.

- [ ] **Step 1: Write failing page contract tests**

```python
def test_review_page_contains_required_regions(client):
    html = client.get("/review/S001").text
    assert 'data-region="article-evidence"' in html
    assert 'data-region="conflict-panel"' in html
    assert 'data-action="confirm-article"' in html
    assert 'data-action="expand-consensus"' in html


def test_negative_article_has_one_click_confirmation(client):
    html = client.get("/review/N001").text
    assert 'data-action="confirm-no-project"' in html
```

- [ ] **Step 2: Run and observe failure**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_web_pages.py -q`  
Expected: FAIL because templates/routes are missing.

- [ ] **Step 3: Build the approved two-pane layout**

The left pane renders title, metadata, full article and color-coded evidence spans. The right pane renders, in order: project structure gate; collapsed consensus summary; conflict cards; low-confidence cards; article confirmation. Display database/production/review/deterministic provenance on every candidate value.

- [ ] **Step 4: Implement autosave and shortcuts**

```javascript
const shortcuts = {
  a: () => acceptFocusedSuggestion(),
  e: () => editFocusedField(),
  k: () => keepDatabaseValue(),
  x: () => flagEvidenceError(),
  Enter: () => confirmArticle(),
};

async function saveField(sampleId, path, payload) {
  const response = await fetch(`/api/articles/${sampleId}/fields/${path}`, {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
```

Disable shortcuts while an input/select/textarea has focus. Show save state and never optimistically mark an article confirmed before the server responds.

- [ ] **Step 5: Add a Playwright smoke test**

```python
def test_accept_conflict_persists_after_reload(page, live_server):
    page.goto(f"{live_server}/review/S001")
    page.get_by_test_id("accept-P01.category").click()
    page.reload()
    expect(page.get_by_test_id("value-P01.category")).to_have_text("光伏")
```

- [ ] **Step 6: Run page and browser tests, then commit**

Run:

```powershell
uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_web_pages.py -q
uv run --project external-tools/gold-standard-studio playwright install chromium
uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_browser_smoke.py -q
```

Expected: PASS.

```powershell
git add -- external-tools/gold-standard-studio/src/gold_standard_studio/web/templates external-tools/gold-standard-studio/src/gold_standard_studio/web/static external-tools/gold-standard-studio/tests/test_web_pages.py external-tools/gold-standard-studio/tests/test_browser_smoke.py
git commit -m "feat(gold-studio): add conflict-first review UI"
```

---

### Task 10: Atomic Publication and Eval Projection

**Files:**
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/exporter.py`
- Create: `external-tools/gold-standard-studio/tests/test_exporter.py`
- Create: `eval/gold_sets/.gitkeep`

**Interfaces:**
- Consumes: reviewed records from `ReviewStore`, `FIELD_REGISTRY`, target version such as `v0.1.0`.
- Produces: `publish_gold(store, output_root, version) -> PublishedPaths` containing rich JSONL, eval projection, manifest, schema, distribution and changelog.

- [ ] **Step 1: Write failing publication-gate tests**

```python
def test_publish_rejects_unreviewed_or_adjudication_records(store, tmp_path):
    with pytest.raises(PublishError, match="not publishable"):
        publish_gold(store, tmp_path, "v0.1.0")


def test_publish_refuses_existing_version(reviewed_store, tmp_path):
    publish_gold(reviewed_store, tmp_path, "v0.1.0")
    with pytest.raises(FileExistsError):
        publish_gold(reviewed_store, tmp_path, "v0.1.0")
```

- [ ] **Step 2: Run and observe failure**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_exporter.py -q`  
Expected: FAIL because exporter is missing.

- [ ] **Step 3: Implement deterministic projection semantics**

```python
def project_field(field: GoldField) -> tuple[object | None, bool]:
    if field.state == "present":
        return field.value, True
    if field.state == "not_mentioned":
        return None, True
    if field.state in {"not_applicable", "uncertain"}:
        return None, False
    raise PublishError(f"state {field.state} is not publishable")
```

Projection records include optional `dataset_split`, `risk_tags`, and `field_eligibility`; rich records retain evidence and audit metadata. Sort records by sample ID and JSON keys stably.

- [ ] **Step 4: Implement atomic version directory publication**

Write all files into a sibling temporary directory, validate each JSONL through Pydantic, fsync files, then rename the directory to the requested immutable version. Generate distribution counts by status, category, split, difficulty and risk tag.

- [ ] **Step 5: Add round-trip and unchanged-store tests**

```python
def test_rich_to_projection_round_trip_is_stable(reviewed_store, tmp_path):
    paths = publish_gold(reviewed_store, tmp_path, "v0.1.0")
    first = paths.eval_projection.read_bytes()
    loaded = load_projection(paths.eval_projection)
    assert loaded[0]["field_eligibility"]["capacity_mw"] is True
    assert first == paths.eval_projection.read_bytes()
```

- [ ] **Step 6: Run tests and commit**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_exporter.py -q`  
Expected: PASS.

```powershell
git add -- external-tools/gold-standard-studio/src/gold_standard_studio/exporter.py external-tools/gold-standard-studio/tests/test_exporter.py eval/gold_sets/.gitkeep
git commit -m "feat(gold-studio): publish immutable gold versions"
```

---

### Task 11: Eligibility-Aware Eval and Full-Field Metrics

**Files:**
- Modify: `eval/gold_loader.py`
- Modify: `eval/predict.py`
- Modify: `eval/metrics.py`
- Modify: `eval/run_eval.py`
- Modify: `tests/test_gold_loader.py`
- Modify: `tests/test_predict.py`
- Modify: `tests/test_eval_metrics.py`
- Modify: `tests/test_run_eval_e2e.py`

**Interfaces:**
- Consumes: legacy gold JSONL and Studio eval projection.
- Produces: unchanged legacy behavior plus eligibility-aware metrics and split/category/risk slices.

- [ ] **Step 1: Re-read and preserve the existing unstaged taxonomy diffs**

Run:

```powershell
git diff -- eval/gold_loader.py eval/predict.py eval/metrics.py eval/run_eval.py
```

Expected: existing energy taxonomy additions are present. Do not overwrite them; patch around them.

- [ ] **Step 2: Write backward-compatibility and eligibility tests first**

```python
def test_old_gold_without_eligibility_keeps_existing_denominators(tmp_path):
    record = load_one(old_gold_line(), tmp_path)
    assert record["projects"][0]["field_eligibility"] == {}


def test_not_applicable_field_is_excluded_from_denominator():
    gold = project_gold(field_eligibility={"financing_amount_usd": False})
    result = score_article(project_pred(financing_amount_usd=999), gold)
    assert result["field_hits"]["financing_amount_usd"] == (0, 0)


def test_explicit_not_mentioned_is_scored_as_correct_empty():
    gold = project_gold(financing_amount_usd=None, field_eligibility={"financing_amount_usd": True})
    result = score_article(project_pred(financing_amount_usd=None), gold)
    assert result["field_hits"]["financing_amount_usd"] == (1, 1)
```

- [ ] **Step 3: Run tests and observe targeted failures**

Run: `pytest tests/test_gold_loader.py tests/test_eval_metrics.py tests/test_predict.py tests/test_run_eval_e2e.py -q`  
Expected: FAIL on missing projection metadata/fields, while existing taxonomy tests remain green or unchanged.

- [ ] **Step 4: Extend loader and prediction contracts**

Add optional record metadata `dataset_split`, `risk_tags`, `difficulty`, and project `field_eligibility: dict[str, bool] = {}`. Add normalized predictions for developer, three dates, storage, three money concepts, generation sources and storage status.

- [ ] **Step 5: Make scoring dispatch through exact metric groups**

```python
PROJECT_ENUM_FIELDS = (
    "category", "project_stage", "financing_model", "storage_status", "country"
)
PROJECT_NUMERIC_FIELDS = (
    "capacity_mw", "storage_power_mw", "storage_energy_mwh",
    "total_investment_usd", "contract_amount_usd", "financing_amount_usd",
)
PROJECT_DATE_FIELDS = ("planned_start_date", "planned_complete_date", "investment_date")
PROJECT_NAME_FIELDS = ("project_name", "project_developer")
```

Before scoring any field, use `eligible = gold.get("field_eligibility", {}).get(field, True)` so legacy records preserve current denominators.

- [ ] **Step 6: Aggregate slices without changing the legacy headline silently**

Add `slices.split`, `slices.category`, and `slices.risk_tag`; retain existing `weighted_total` for continuity but label it legacy and add a `core_metrics` section. Challenge metrics are separate and never averaged into representative regression metrics.

- [ ] **Step 7: Run focused and full root eval tests, then commit exact overlapping paths**

Run:

```powershell
pytest tests/test_gold_loader.py tests/test_eval_metrics.py tests/test_predict.py tests/test_run_eval_e2e.py -q
pytest tests -q
```

Expected: all pass; old gold fixture scores are unchanged.

```powershell
git add -- eval/gold_loader.py eval/predict.py eval/metrics.py eval/run_eval.py tests/test_gold_loader.py tests/test_predict.py tests/test_eval_metrics.py tests/test_run_eval_e2e.py
git commit -m "feat(eval): score versioned rich-gold projections"
```

Before committing, inspect `git diff --cached` and confirm it includes the preserved taxonomy changes intentionally; if those changes belong in a separate earlier commit, stop and split them rather than silently mixing scopes.

---

### Task 12: CLI, Pilot Dry Run, Documentation, and Full Verification

**Files:**
- Create: `external-tools/gold-standard-studio/src/gold_standard_studio/cli.py`
- Create: `external-tools/gold-standard-studio/README.md`
- Create: `external-tools/gold-standard-studio/tests/test_cli.py`
- Create: `external-tools/gold-standard-studio/tests/test_integration.py`
- Modify: `docs/status/external-tools.md`
- Modify: `docs/status/s01b.md`
- Modify: `docs/changes/2026-W32.md`
- Modify: `eval/README.md`
- Modify: `docs/rules_and_plans/eval.md`

**Interfaces:**
- Consumes: all prior Studio modules.
- Produces: `gold-studio init`, `sample`, `build-candidates`, `serve`, `publish`, and `report` commands plus a tested Pilot runbook.

- [ ] **Step 1: Write failing CLI safety tests**

```python
def test_sample_defaults_to_dry_run(runner, db_paths, tmp_path):
    result = runner.invoke(app, ["sample", *db_args(db_paths), "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "dry-run" in result.stdout
    assert not (tmp_path / "review.db").exists()


def test_publish_requires_explicit_version(runner, initialized_data):
    result = runner.invoke(app, ["publish", "--data-dir", str(initialized_data)])
    assert result.exit_code != 0
    assert "--version" in result.stdout
```

- [ ] **Step 2: Run and observe failure**

Run: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_cli.py -q`  
Expected: FAIL because CLI is missing.

- [ ] **Step 3: Implement explicit commands and write gates**

```text
gold-studio init --raw-news-db ... --projects-db ... --main-db ... --data-dir ...
gold-studio sample --pilot-size 60 [--apply]
gold-studio build-candidates --sample-id S001 | --all [--resume]
gold-studio serve --host 127.0.0.1 --port 8765
gold-studio publish --version v0.1.0 --output eval/gold_sets/database_core_v0.1.0
gold-studio report --format html --out data/reports/pilot.html
```

`sample` defaults to read-only dry-run and requires `--apply` to seed review DB. `build-candidates --all` prints the exact sample count and requires interactive confirmation unless `--yes` is passed; documentation states that external model calls incur cost. `publish` refuses incomplete datasets and existing targets.

- [ ] **Step 4: Add a full temporary-SQLite integration test**

```python
def test_pilot_flow_never_mutates_sources(temp_source_dbs, tmp_path, fake_runners):
    before = temp_source_dbs.hashes()
    studio = build_test_studio(temp_source_dbs, tmp_path, fake_runners)
    studio.sample(size=6, apply=True)
    studio.build_candidates(all_samples=True)
    studio.review_all_with_fixture_decisions()
    paths = studio.publish("v0.1.0")
    assert paths.rich_gold.exists()
    assert paths.eval_projection.exists()
    assert temp_source_dbs.hashes() == before
```

- [ ] **Step 5: Write the exact operator runbook and update status/change docs**

Document prerequisites, paths, dry-run/apply distinction, model cost warning, browser URL, backup/restore, Pilot acceptance metrics, publication rules, eval command and troubleshooting. Update `docs/status/external-tools.md`, `docs/status/s01b.md`, and `docs/changes/2026-W32.md` in the same task, per `AGENTS.md`.

- [ ] **Step 6: Run package, root, s01b, s02a, and pipeline verification**

Run:

```powershell
uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests -q
pytest tests -q
pytest s01b_project_extractor/tests -q
pytest s02a_duplicate_merger/tests -q
pytest pipeline/tests -q
git diff --check
```

Expected: all suites pass; `git diff --check` returns no errors.

- [ ] **Step 7: Execute a real-database read-only Pilot dry run only**

Run `gold-studio sample` without `--apply` against the configured worktree databases. Record selected counts by status/category/risk and compare SHA-256 hashes plus canonical/event/raw-news row counts before/after. Do not call models and do not create formal gold.

Expected: 60 proposed samples, 9–12 negatives, configured rare-category floors satisfied, and all source hashes/counts unchanged.

- [ ] **Step 8: Commit exact task paths**

```powershell
git add -- external-tools/gold-standard-studio/src/gold_standard_studio/cli.py external-tools/gold-standard-studio/README.md external-tools/gold-standard-studio/tests/test_cli.py external-tools/gold-standard-studio/tests/test_integration.py docs/status/external-tools.md docs/status/s01b.md docs/changes/2026-W32.md eval/README.md docs/rules_and_plans/eval.md
git commit -m "feat(gold-studio): complete pilot review workflow"
```

---

## Implementation Completion Checklist

- [ ] All 12 task commits use exact-path staging and preserve pre-existing dirty work.
- [ ] Studio source DB connections are URI read-only and tested against writes.
- [ ] Pilot sampling is deterministic, has 9–12 negatives, and covers rare-category floors.
- [ ] Independent reviewer never receives production/database candidates.
- [ ] Every formal gold field has an explicit state; critical present fields have evidence.
- [ ] Conflict-first UI supports structure edits, autosave, resume, keyboard shortcuts and audit inspection.
- [ ] Ten-percent high-consensus audit selection is deterministic and dynamically escalates by risk tag.
- [ ] Publisher rejects unresolved/unreviewed data and existing version paths.
- [ ] Legacy gold loads and scores unchanged; new eligibility semantics have focused tests.
- [ ] Full package/root/s01b/s02a/pipeline suites pass.
- [ ] Real-database dry run leaves hashes, row counts and ID sets unchanged.
- [ ] Status, eval documentation and W32 change log are current.
- [ ] No external model batch run or formal gold publication has occurred without separate authorization.
