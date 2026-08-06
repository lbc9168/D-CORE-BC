"""Smoke-test persisted review actions in a real local browser session."""

from __future__ import annotations

from contextlib import contextmanager
import json
import socket
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest
import uvicorn
from playwright.sync_api import Browser, expect, sync_playwright
from typer.testing import CliRunner

from gold_standard_studio.cli import app as cli_app
from gold_standard_studio.field_registry import FIELD_REGISTRY
from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.schemas import CandidateBundle, CandidateValue, EvidenceSpan, GoldField, GoldProject, RichGoldRecord, SamplingProvenance
from gold_standard_studio.web.app import create_app

from test_final_hardening import _snapshot
from test_final_hardening_fix_round import _source_repository
from test_web_api import ARTICLE_SHA256, configured_source_client, make_field, store


@contextmanager
def running_server(app):
    """Run the real FastAPI app on an ephemeral loopback port for browser assertions."""
    unsafe_ports = {1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77, 79, 87, 95, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135, 137, 139, 143, 161, 179, 389, 427, 465, 512, 513, 514, 515, 526, 530, 548, 554, 556, 563, 587, 601, 636, 993, 995, 2049, 3659, 4045, 5060, 5061, 6000, 6566, 6665, 6666, 6667, 6668, 6669, 6697, 10080}
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in unsafe_ports:
            break
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = monotonic() + 10
    while monotonic() < deadline:
        try:
            if httpx.get(f"{url}/queue", timeout=0.2).status_code == 200:
                break
        except httpx.HTTPError:
            sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("local Studio browser server did not start")
    try:
        yield url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def browser() -> Browser:
    """Open one headless Chromium browser using the locally installed Playwright runtime."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


def test_accept_conflict_persists_after_reload(store, browser: Browser) -> None:
    """Save a chosen candidate before rendering it and retain the server value after reload."""
    store.save_structure(
        "S001",
        [GoldProject(gold_project_id="P01", fields={"project_name": make_field("示例项目"), "category": make_field("风电")})],
        base_revision=1,
        actor="human",
    )
    store.save_candidate_bundle(
        CandidateBundle(
            sample_id="S001",
            article_sha256=ARTICLE_SHA256,
            sources={
                "database": CandidateValue(
                    source_name="database",
                    projects=[
                        GoldProject(
                            gold_project_id="D01",
                            fields={"project_name": make_field("示例项目"), "category": make_field("风电")},
                        )
                    ],
                ),
                "review": CandidateValue(
                    source_name="review",
                    projects=[
                        GoldProject(
                            gold_project_id="R01",
                            fields={"project_name": make_field("示例项目"), "category": make_field("光伏")},
                        )
                    ],
                ),
            },
        )
    )
    page = browser.new_page()
    page_errors: list[str] = []
    responses: list[tuple[str, int]] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("response", lambda response: responses.append((response.url, response.status)))

    with running_server(create_app(store)) as live_server:
        page.goto(f"{live_server}/review/S001")
        page.wait_for_timeout(200)
        assert any(url.endswith("/static/review.js") and status == 200 for url, status in responses), responses
        assert not page_errors
        expect(page.get_by_test_id("save-state")).to_contain_text("草稿已加载")
        page.get_by_test_id("accept-P01.category").click()
        expect(page.get_by_test_id("value-P01.category")).to_have_text("光伏")
        page.reload()
        expect(page.get_by_test_id("value-P01.category")).to_have_text("光伏")


def test_shortcuts_do_not_run_while_a_field_editor_has_focus(store, browser: Browser) -> None:
    """Keep review shortcuts inert while a reviewer is typing in an editable control."""
    page = browser.new_page()

    with running_server(create_app(store)) as live_server:
        page.goto(f"{live_server}/review/S001")
        expect(page.get_by_test_id("save-state")).to_contain_text("草稿已加载")
        before = len(store.list_audit("S001"))
        page.get_by_test_id("edit-P01.category").click()
        page.get_by_test_id("editor-P01.category").press("a")
        sleep(0.1)
        assert len(store.list_audit("S001")) == before


def test_confirm_stays_unconfirmed_when_server_rejects_it(store, browser: Browser) -> None:
    """Avoid optimistic confirmation when evidence validation rejects the final server request."""
    store.save_entities("S001", "P01", [], actor="fixture")
    page = browser.new_page()

    with running_server(create_app(store)) as live_server:
        page.goto(f"{live_server}/review/S001")
        page.get_by_test_id("confirm-article").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("Save failed")
        assert store.get_article("S001").article_review_status == "unreviewed"


def test_reversed_source_projects_display_and_save_the_matching_gold_project(store, browser: Browser) -> None:
    """Accept the PV suggestion only on the human PV card when source project ordering is reversed."""
    store.seed_article(
        RichGoldRecord(
            sample_id="S003",
            article_sha256=ARTICLE_SHA256,
            projects=[
                GoldProject(gold_project_id="P01", fields={"project_name": make_field("风电项目"), "category": make_field("风电")}),
                GoldProject(gold_project_id="P02", fields={"project_name": make_field("光伏项目"), "category": make_field("风电")}),
            ],
        )
    )
    store.save_candidate_bundle(
        CandidateBundle(
            sample_id="S003",
            article_sha256=ARTICLE_SHA256,
            sources={
                "database": CandidateValue(source_name="database", projects=[
                    GoldProject(gold_project_id="D-PV", fields={"project_name": make_field("光伏项目"), "category": make_field("光伏")}),
                    GoldProject(gold_project_id="D-WIND", fields={"project_name": make_field("风电项目"), "category": make_field("风电")}),
                ]),
                "review": CandidateValue(source_name="review", projects=[
                    GoldProject(gold_project_id="R-PV", fields={"project_name": make_field("光伏项目"), "category": make_field("光伏")}),
                    GoldProject(gold_project_id="R-WIND", fields={"project_name": make_field("风电项目"), "category": make_field("风电")}),
                ]),
            },
        )
    )
    page = browser.new_page()

    with running_server(create_app(store)) as live_server:
        page.goto(f"{live_server}/review/S003")
        page.get_by_role("button", name="展开").first.click()
        expect(page.get_by_test_id("accept-P02.category")).to_be_visible()
        page.get_by_test_id("value-P02.category").click()
        page.keyboard.press("a")
        expect(page.get_by_test_id("value-P02.category")).to_have_text("光伏")
        assert store.get_article("S003").projects[0].fields["category"].value == "风电"
        assert store.get_article("S003").projects[1].fields["category"].value == "光伏"


def test_structural_gate_blocks_enter_until_persisted_structure_save(store, browser: Browser) -> None:
    """Disable UI confirmation and Enter until the server records a newer structure decision."""
    store.save_candidate_bundle(
        CandidateBundle(
            sample_id="S001",
            article_sha256=ARTICLE_SHA256,
            sources={
                "database": CandidateValue(source_name="database", projects=[GoldProject(gold_project_id="D01")]),
                "review": CandidateValue(source_name="review", projects=[GoldProject(gold_project_id="R01"), GoldProject(gold_project_id="R02")]),
            },
        )
    )
    page = browser.new_page()

    with running_server(create_app(store)) as live_server:
        page.goto(f"{live_server}/review/S001")
        expect(page.get_by_test_id("confirm-article")).to_be_disabled()
        page.keyboard.press("Enter")
        assert store.get_article("S001").article_review_status == "unreviewed"
        page.get_by_test_id("accept-structure").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("项目结构已保存")
        expect(page.get_by_test_id("confirm-article")).to_be_enabled()


def test_source_backed_page_highlights_evidence_in_the_browser(configured_source_client, browser: Browser) -> None:
    """Render source-repository article text and the evidence mark through the real local server."""
    client, _repository = configured_source_client
    page = browser.new_page()

    with running_server(client.app) as live_server:
        page.goto(f"{live_server}/review/SRC001")
        expect(page.locator("#article-content mark")).to_have_text("光伏")


def test_stale_human_evidence_is_neither_highlighted_nor_silently_confirmed(configured_source_client, browser: Browser) -> None:
    """Keep a wrong-hash human span visible as a warning but out of the article evidence layer and confirmation flow."""
    client, _repository = configured_source_client
    review_store = client.app.state.review_store
    review_store.save_field_decision(
        "SRC001",
        "P01.category",
        GoldField(
            state="present",
            value="光伏",
            evidence_required=True,
            evidence=[EvidenceSpan(quote="光伏", start=0, end=2, article_sha256="b" * 64)],
        ),
        actor="human",
    )
    page = browser.new_page()

    with running_server(client.app) as live_server:
        page.goto(f"{live_server}/review/SRC001")
        expect(page.locator("#article-content mark")).to_have_count(0)
        expect(page.get_by_test_id("human-evidence-warning-P01.category")).to_be_visible()
        page.get_by_test_id("confirm-article").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("Save failed")
        assert review_store.get_article("SRC001").article_review_status == "unreviewed"


def test_browser_uses_dedicated_adjudication_and_multiple_evidence_entity_editors(store, browser: Browser) -> None:
    """Adjudicate through its public action and add/edit/delete repeated field/entity evidence in the real UI."""
    store.save_entities("S001", "P01", [], actor="fixture")
    page = browser.new_page()

    with running_server(create_app(store)) as live_server:
        page.goto(f"{live_server}/review/S001")
        page.get_by_test_id("edit-P01.category").click()
        page.get_by_test_id("state-P01.category").select_option("needs_adjudication")
        page.get_by_test_id("save-editor-P01.category").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("草稿已保存")

        page.get_by_test_id("edit-P01.category").click()
        page.get_by_test_id("state-P01.category").select_option("present")
        page.get_by_test_id("editor-P01.category").fill("光伏")
        page.get_by_test_id("add-evidence-P01.category").click()
        page.get_by_test_id("add-evidence-P01.category").click()
        page.get_by_test_id("evidence-quote-P01.category-0").fill("风电")
        page.get_by_test_id("evidence-start-P01.category-0").fill("0")
        page.get_by_test_id("evidence-quote-P01.category-1").fill("光伏")
        page.get_by_test_id("evidence-start-P01.category-1").fill("2")
        page.get_by_test_id("save-editor-P01.category").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("草稿已保存")
        assert store.list_audit("S001")[-1].action == "adjudication"
        assert len(store.get_article("S001").projects[0].fields["category"].evidence) == 2

        page.get_by_test_id("add-entity-P01").click()
        page.get_by_test_id("entity-name-P01-0").fill("中国电建")
        page.get_by_test_id("entity-role-P01-0").select_option("EPC总承包")
        page.get_by_test_id("add-entity-evidence-P01-0").click()
        page.get_by_test_id("entity-evidence-quote-P01-0-0").fill("风电")
        page.get_by_test_id("entity-evidence-start-P01-0-0").fill("0")
        page.get_by_test_id("save-entities-P01").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("实体已保存")
        assert store.get_article("S001").projects[0].entities[0].role == "EPC总承包"

        page.get_by_test_id("remove-entity-P01-0").click()
        with page.expect_response(
            lambda response: response.url.endswith(
                "/api/articles/S001/projects/P01/entities"
            )
            and response.request.method == "PUT"
        ) as deletion:
            page.get_by_test_id("save-entities-P01").click()
        assert deletion.value.ok
        expect(page.get_by_test_id("save-state")).to_contain_text("实体已保存")
        assert store.get_article("S001").projects[0].entities == []


def test_public_ui_builds_complete_positive_gold_and_cli_publishes_it(
    tmp_path, browser: Browser
) -> None:
    """Complete every human review decision in Chromium, then publish and validate both CLI artifacts."""
    snapshot = _snapshot()
    config, repository = _source_repository(tmp_path, [snapshot])
    repository.close()
    runner = CliRunner()
    initialized = runner.invoke(
        cli_app,
        [
            "init",
            "--raw-news-db",
            str(config.raw_news_db),
            "--projects-db",
            str(config.projects_db),
            "--main-db",
            str(config.main_db),
            "--data-dir",
            str(config.data_dir),
        ],
    )
    assert initialized.exit_code == 0, initialized.output
    review_store = ReviewStore(config.data_dir / "review.db", source_paths=config.source_paths)
    review_store.seed_article(
        RichGoldRecord(
            sample_id="UI-POSITIVE",
            article_sha256=snapshot.article_sha256,
            source_link=snapshot.source_link,
            sampling_provenance=SamplingProvenance(
                sampling_seed=20260806,
                inclusion_reasons=["public_ui_e2e"],
                original_category_strata=["光伏"],
                risk_tags=["browser_review"],
                negative_proxy=False,
                verified=True,
                verification_source="sample_selection",
            ),
        ),
        snapshot=snapshot,
    )
    page = browser.new_page()

    with running_server(create_app(review_store)) as live_server:
        page.goto(f"{live_server}/review/UI-POSITIVE")
        expect(page.get_by_test_id("save-state")).to_contain_text("草稿已加载")
        page.get_by_role("button", name="新增项目", exact=True).click()
        page.get_by_test_id("save-structure").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("项目结构已保存")

        page.get_by_test_id("edit-P01.category").click()
        page.get_by_test_id("state-P01.category").select_option("needs_adjudication")
        page.get_by_test_id("save-editor-P01.category").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("草稿已保存")
        page.get_by_test_id("edit-P01.category").click()
        page.get_by_test_id("state-P01.category").select_option("present")
        page.get_by_test_id("editor-P01.category").fill("光伏")
        page.get_by_test_id("add-evidence-P01.category").click()
        page.get_by_test_id("add-evidence-P01.category").click()
        page.get_by_test_id("evidence-quote-P01.category-0").fill("光伏")
        page.get_by_test_id("evidence-start-P01.category-0").fill(
            str(snapshot.content.index("光伏"))
        )
        page.get_by_test_id("evidence-quote-P01.category-1").fill("100MW")
        page.get_by_test_id("evidence-start-P01.category-1").fill(
            str(snapshot.content.index("100MW"))
        )
        page.get_by_test_id("save-editor-P01.category").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("草稿已保存")

        for field_name in FIELD_REGISTRY:
            if field_name == "category":
                continue
            field_path = f"P01.{field_name}"
            page.get_by_test_id(f"edit-{field_path}").click()
            page.get_by_test_id(f"state-{field_path}").select_option("not_mentioned")
            page.get_by_test_id(f"save-editor-{field_path}").click()
            expect(page.get_by_test_id("save-state")).to_contain_text("草稿已保存")

        page.get_by_test_id("add-entity-P01").click()
        page.get_by_test_id("entity-name-P01-0").fill("中国电建")
        page.get_by_test_id("entity-role-P01-0").select_option("EPC总承包")
        for quote in ("中国电建", "EPC总承包商"):
            page.get_by_test_id("add-entity-evidence-P01-0").click()
            evidence_index = 0 if quote == "中国电建" else 1
            page.get_by_test_id(
                f"entity-evidence-quote-P01-0-{evidence_index}"
            ).fill(quote)
            page.get_by_test_id(
                f"entity-evidence-start-P01-0-{evidence_index}"
            ).fill(str(snapshot.content.index(quote)))
        page.get_by_test_id("save-entities-P01").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("实体已保存")
        page.locator("#article-status").select_option("valid")
        page.get_by_test_id("save-article-status").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("文章状态已保存")
        page.get_by_test_id("confirm-article").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("文章已确认")

    published = runner.invoke(
        cli_app,
        [
            "publish",
            "--version",
            "v2.0.0",
            "--output",
            str(tmp_path / "gold"),
            "--data-dir",
            str(config.data_dir),
        ],
    )
    assert published.exit_code == 0, published.output
    release = tmp_path / "gold" / "v2.0.0"
    rich = json.loads((release / "rich_gold.jsonl").read_text(encoding="utf-8").strip())
    projection = json.loads(
        (release / "eval_projection.jsonl").read_text(encoding="utf-8").strip()
    )
    fields = rich["projects"][0]["fields"]
    assert rich["article_review_status"] == "reviewed"
    assert set(fields) == set(FIELD_REGISTRY)
    assert all(field["state"] not in {"unreviewed", "needs_adjudication"} for field in fields.values())
    assert len(fields["category"]["evidence"]) == 2
    assert len(rich["projects"][0]["entities"][0]["evidence"]) == 2
    assert projection["projects"][0]["category"] == "光伏"
    assert projection["projects"][0]["entities"] == [
        {"name": "中国电建", "involvement_type": "EPC总承包"}
    ]
