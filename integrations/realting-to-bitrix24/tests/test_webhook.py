"""Приймач хуків: авторизація, розбір тіла, черга, коди відповіді."""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
import urllib.error
import urllib.parse
import urllib.request
from threading import Thread

from helpers import make_config
from realting_sync.config import WebhookConfig
from realting_sync.state import SyncState
from realting_sync.webhook import PayloadError, parse_payload, make_server, verify

ORDER = {"id": 501, "name": "Іван Петренко", "phone": "+380671234567", "message": "Цікавить обʼєкт"}


class ParsePayloadTest(unittest.TestCase):
    def test_json(self):
        self.assertEqual(parse_payload(b'{"id": 1}', "application/json"), {"id": 1})

    def test_json_without_content_type(self):
        self.assertEqual(parse_payload(b'[{"id": 1}]', ""), [{"id": 1}])

    def test_form_urlencoded(self):
        body = urllib.parse.urlencode({"id": "5", "phone": "+380671234567"}).encode()
        self.assertEqual(parse_payload(body, "application/x-www-form-urlencoded"),
                         {"id": "5", "phone": "+380671234567"})

    def test_form_with_nested_json_field(self):
        body = urllib.parse.urlencode({"payload": json.dumps({"id": 7})}).encode()
        self.assertEqual(parse_payload(body, "application/x-www-form-urlencoded"), {"id": 7})

    def test_xml(self):
        body = b"<order><id>9</id><phone>+380671234567</phone></order>"
        self.assertEqual(parse_payload(body, "application/xml"), {"id": "9", "phone": "+380671234567"})

    def test_empty_body_rejected(self):
        with self.assertRaises(PayloadError):
            parse_payload(b"", "application/json")

    def test_broken_json_rejected(self):
        with self.assertRaises(PayloadError):
            parse_payload("{нісенітниця".encode("utf-8"), "application/json")


class VerifyTest(unittest.TestCase):
    def config(self, **overrides) -> WebhookConfig:
        return WebhookConfig(token="s3cret", **overrides)

    def test_query_mode(self):
        cfg = self.config(auth_mode="query")
        self.assertTrue(verify(cfg, {}, {"token": ["s3cret"]}, b""))
        self.assertFalse(verify(cfg, {}, {"token": ["wrong"]}, b""))
        self.assertFalse(verify(cfg, {}, {}, b""))

    def test_header_mode(self):
        cfg = self.config(auth_mode="header", auth_header="X-Api-Key")
        self.assertTrue(verify(cfg, {"X-Api-Key": "s3cret"}, {}, b""))
        self.assertTrue(verify(cfg, {"x-api-key": "s3cret"}, {}, b""))   # регістр не має значення
        self.assertFalse(verify(cfg, {"X-Api-Key": "nope"}, {}, b""))

    def test_bearer_mode(self):
        cfg = self.config(auth_mode="bearer")
        self.assertTrue(verify(cfg, {"Authorization": "Bearer s3cret"}, {}, b""))
        self.assertFalse(verify(cfg, {"Authorization": "Basic s3cret"}, {}, b""))

    def test_hmac_mode(self):
        cfg = self.config(auth_mode="hmac", hmac_header="X-Signature")
        body = b'{"id": 1}'
        digest = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify(cfg, {"X-Signature": digest}, {}, body))
        self.assertTrue(verify(cfg, {"X-Signature": f"sha256={digest}"}, {}, body))
        self.assertFalse(verify(cfg, {"X-Signature": digest}, {}, b'{"id": 2}'))
        self.assertFalse(verify(cfg, {}, {}, body))

    def test_none_mode_accepts_everything(self):
        self.assertTrue(verify(self.config(auth_mode="none"), {}, {}, b""))


class ServerTest(unittest.TestCase):
    """Піднімаємо справжній HTTP-сервер і стукаємо в нього через urllib."""

    def setUp(self):
        self.state = SyncState(":memory:")
        self.addCleanup(self.state.close)
        config = WebhookConfig(host="127.0.0.1", port=0, token="s3cret", auth_mode="query")
        self.server = make_server(config, self.state)
        self.port = self.server.server_address[1]
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path: str, body: bytes, content_type: str = "application/json", headers=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={"Content-Type": content_type, **(headers or {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path: str):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_valid_hook_is_queued(self):
        status, payload = self.post("/realting/webhook?token=s3cret", json.dumps(ORDER).encode())
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(self.state.inbox_stats(), {"pending": 1})
        row = self.state.due_inbox()[0]
        self.assertEqual(json.loads(row["payload"])["id"], 501)

    def test_wrong_token_is_rejected_and_not_queued(self):
        status, payload = self.post("/realting/webhook?token=wrong", json.dumps(ORDER).encode())
        self.assertEqual(status, 401)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(self.state.inbox_stats(), {})

    def test_missing_token_is_rejected(self):
        self.assertEqual(self.post("/realting/webhook", json.dumps(ORDER).encode())[0], 401)

    def test_unknown_path_is_404(self):
        self.assertEqual(self.post("/", json.dumps(ORDER).encode())[0], 404)

    def test_broken_body_is_400_and_not_queued(self):
        status, _ = self.post("/realting/webhook?token=s3cret", "{зламано".encode("utf-8"))
        self.assertEqual(status, 400)
        self.assertEqual(self.state.inbox_stats(), {})

    def test_form_encoded_hook_is_accepted(self):
        body = urllib.parse.urlencode({"id": "77", "phone": "+380671234567"}).encode()
        status, _ = self.post("/realting/webhook?token=s3cret", body, "application/x-www-form-urlencoded")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(self.state.due_inbox()[0]["payload"])["id"], "77")

    def test_healthcheck(self):
        self.assertEqual(self.get("/healthz"), (200, {"status": "ok"}))

    def test_get_on_webhook_path_is_ok_for_platform_validation(self):
        status, payload = self.get("/realting/webhook")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_concurrent_hooks_all_land_in_queue(self):
        threads = [Thread(target=lambda n=n: self.post(
            "/realting/webhook?token=s3cret", json.dumps({**ORDER, "id": n}).encode()
        )) for n in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.state.inbox_stats(), {"pending": 10})


if __name__ == "__main__":
    unittest.main()
