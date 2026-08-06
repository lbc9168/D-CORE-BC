"""Verify the stable HTML contracts for the local conflict-first review pages."""

from __future__ import annotations

from fastapi.testclient import TestClient

from gold_standard_studio.web.app import create_app

from test_web_api import store


def test_review_page_contains_required_regions(store) -> None:
    """Expose the accessible two-pane review landmarks and primary review actions."""
    client = TestClient(create_app(store))

    response = client.get("/review/S001")

    assert response.status_code == 200
    assert 'data-region="article-evidence"' in response.text
    assert 'data-region="conflict-panel"' in response.text
    assert 'data-region="structure-gate"' in response.text
    assert 'data-action="confirm-article"' in response.text
    assert 'data-action="expand-consensus"' in response.text


def test_negative_article_has_one_click_confirmation(store) -> None:
    """Keep the low-friction negative article confirmation action in the rendered review shell."""
    client = TestClient(create_app(store))

    response = client.get("/review/N001")

    assert response.status_code == 200
    assert 'data-action="confirm-no-project"' in response.text


def test_queue_page_exposes_the_review_queue_region(store) -> None:
    """Provide a keyboard-accessible queue page that the review shell can populate."""
    client = TestClient(create_app(store))

    response = client.get("/queue")

    assert response.status_code == 200
    assert 'data-region="review-queue"' in response.text
    assert 'data-testid="queue-items"' in response.text
