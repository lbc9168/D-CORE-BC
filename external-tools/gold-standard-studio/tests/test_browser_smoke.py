"""Smoke-test persisted review actions in a real local browser session."""

from __future__ import annotations

from contextlib import contextmanager
import socket
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest
import uvicorn
from playwright.sync_api import Browser, expect, sync_playwright

from gold_standard_studio.schemas import CandidateBundle, CandidateValue, GoldField, GoldProject
from gold_standard_studio.web.app import create_app

from test_web_api import ARTICLE_SHA256, make_field, store


@contextmanager
def running_server(app):
    """Run the real FastAPI app on an ephemeral loopback port for browser assertions."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
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
        page.get_by_test_id("accept-structure").click()
        page.get_by_test_id("accept-P01.category").click()
        expect(page.get_by_test_id("value-P01.category")).to_have_text("光伏")
        page.reload()
        expect(page.get_by_test_id("value-P01.category")).to_have_text("光伏")


def test_shortcuts_do_not_run_while_a_field_editor_has_focus(store, browser: Browser) -> None:
    """Keep review shortcuts inert while a reviewer is typing in an editable control."""
    page = browser.new_page()

    with running_server(create_app(store)) as live_server:
        page.goto(f"{live_server}/review/S001")
        page.get_by_test_id("accept-structure").click()
        before = len(store.list_audit("S001"))
        page.get_by_test_id("edit-P01.category").click()
        page.get_by_test_id("editor-P01.category").press("a")
        sleep(0.1)
        assert len(store.list_audit("S001")) == before


def test_confirm_stays_unconfirmed_when_server_rejects_it(store, browser: Browser) -> None:
    """Avoid optimistic confirmation when evidence validation rejects the final server request."""
    page = browser.new_page()

    with running_server(create_app(store)) as live_server:
        page.goto(f"{live_server}/review/S001")
        page.get_by_test_id("confirm-article").click()
        expect(page.get_by_test_id("save-state")).to_contain_text("Save failed")
        assert store.get_article("S001").article_review_status == "unreviewed"
