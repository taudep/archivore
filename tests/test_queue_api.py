"""Tests for the archivore-queue HTTP client. No live network calls —
requests.post/get are mocked throughout."""

from unittest.mock import Mock, patch

from archivore.clients.queue_api import claim, complete, list_items
from archivore.config import Config


def _cfg() -> Config:
    cfg = Config()
    cfg.queue_api_url = "https://queue.example.workers.dev"
    cfg.queue_api_token = "test-token"
    return cfg


class TestClaim:
    def test_empty_items_makes_no_request(self):
        with patch("archivore.clients.queue_api.requests.post") as mock_post:
            result = claim(_cfg(), [])
        assert result == []
        mock_post.assert_not_called()

    def test_posts_items_with_auth_and_returns_results(self):
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "results": [
                {"item_id": "1", "claimed": True, "status": "pending", "retries": 0}
            ]
        }
        with patch(
            "archivore.clients.queue_api.requests.post", return_value=mock_resp
        ) as mock_post:
            result = claim(
                _cfg(),
                [
                    {
                        "item_id": "1",
                        "source": "hn",
                        "comments_url": "https://x",
                        "article_url": None,
                    }
                ],
            )

        args, kwargs = mock_post.call_args
        assert args[0] == "https://queue.example.workers.dev/claim"
        assert kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert kwargs["json"]["items"][0]["item_id"] == "1"
        assert result[0]["claimed"] is True
        mock_resp.raise_for_status.assert_called_once()


class TestComplete:
    def test_empty_items_makes_no_request(self):
        with patch("archivore.clients.queue_api.requests.post") as mock_post:
            complete(_cfg(), [])
        mock_post.assert_not_called()

    def test_posts_batch_with_auth(self):
        mock_resp = Mock()
        mock_resp.json.return_value = {"updated": 1}
        with patch(
            "archivore.clients.queue_api.requests.post", return_value=mock_resp
        ) as mock_post:
            complete(
                _cfg(),
                [
                    {
                        "item_id": "1",
                        "status": "done",
                        "title": "T",
                        "is_selfpost": False,
                        "filename": "1-t.md",
                        "last_error": None,
                    }
                ],
            )

        args, kwargs = mock_post.call_args
        assert args[0] == "https://queue.example.workers.dev/complete"
        assert kwargs["json"]["items"][0]["status"] == "done"
        mock_resp.raise_for_status.assert_called_once()


class TestListItems:
    def test_gets_items_with_auth(self):
        mock_resp = Mock()
        mock_resp.json.return_value = {"items": [{"item_id": "1"}]}
        with patch(
            "archivore.clients.queue_api.requests.get", return_value=mock_resp
        ) as mock_get:
            result = list_items(_cfg())

        args, kwargs = mock_get.call_args
        assert args[0] == "https://queue.example.workers.dev/items"
        assert kwargs["headers"]["Authorization"] == "Bearer test-token"
        assert result == [{"item_id": "1"}]
        mock_resp.raise_for_status.assert_called_once()
