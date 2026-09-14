"""Наскрізний тест: справжній HTTP через urllib проти локальних фейкових серверів.

Піднімаємо один HTTP-сервер, який грає і за Realting (GET експорт), і за Bitrix24
(POST REST), і ганяємо через нього реальний CLI — з розбором відповіді, дедупом,
станом у SQLite і повторним запуском.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from realting_sync.cli import main

ORDERS = [
    {
        "id": 101,
        "created_at": "2026-09-14 10:00:00",
        "name": "Іван Петренко",
        "phone": "+38 (067) 123-45-67",
        "email": "ivan@example.com",
        "message": "Цікавить квартира",
        "object_url": "https://realting.com/o/1",
        "lang": "uk",
    },
    {"id": 102, "name": "Ольга Ткач", "phone": "0501112233", "message": "Передзвоніть"},
    {"id": 103, "name": "Заявка без контактів"},
]

KNOWN_DUPLICATE_PHONE = "0501112233"


class _Handler(BaseHTTPRequestHandler):
    calls: list[tuple[str, object]] = []

    def log_message(self, *args):  # тиша у виводі тестів
        pass

    def _send(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer test-token":
            self._send({"error": "unauthorized"})
            return
        page = 1
        if "page=" in self.path:
            page = int(self.path.split("page=")[1].split("&")[0])
        type(self).calls.append(("realting_page", page))
        self._send({"data": ORDERS if page == 1 else []})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        method = self.path.rsplit("/", 1)[-1].removesuffix(".json")
        type(self).calls.append((method, body))

        if method == "crm.lead.list":
            self._send({"result": []})
        elif method == "crm.duplicate.findbycomm":
            found = body["values"][0] == KNOWN_DUPLICATE_PHONE
            self._send({"result": {"LEAD": [555]} if found else {}})
        elif method == "crm.lead.add":
            self._send({"result": 900})
        elif method == "crm.timeline.comment.add":
            self._send({"result": 1})
        elif method == "crm.lead.fields":
            self._send({"result": {"TITLE": {}, "UF_CRM_REALTING_ID": {}}})
        else:
            self._send({"error": "UNKNOWN_METHOD", "error_description": method})


class _FakePlatforms(unittest.TestCase):
    """Фікстура: локальний HTTP-сервер за Realting і Bitrix24 одночасно."""

    @classmethod
    def setUpClass(cls):
        cls._proxy_backup = {k: os.environ.pop(k, None) for k in
                             ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")}
        os.environ["no_proxy"] = "*"
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for key, value in cls._proxy_backup.items():
            if value is not None:
                os.environ[key] = value

    def setUp(self):
        _Handler.calls = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = f"http://127.0.0.1:{self.port}"
        self.env_file = Path(self.tmp.name) / "sync.env"
        self.env_file.write_text(
            "\n".join([
                f"REALTING_EXPORT_URL={base}/orders",
                "REALTING_API_TOKEN=test-token",
                "REALTING_AUTH_MODE=bearer",
                "REALTING_PAGE_SIZE=3",
                f"BITRIX_WEBHOOK_URL={base}/rest/1/hook/",
                f"STATE_PATH={self.tmp.name}/state.db",
            ]),
            encoding="utf-8",
        )

    def run_cli(self, *args) -> int:
        return main(["--env-file", str(self.env_file), "--log-level", "ERROR", *args])

    def methods(self, name: str) -> list[object]:
        return [body for method, body in _Handler.calls if method == name]


class CliEndToEndTest(_FakePlatforms):
    def test_check_passes_against_live_endpoints(self):
        self.assertEqual(self.run_cli("check"), 0)

    def test_dry_run_touches_nothing(self):
        self.assertEqual(self.run_cli("sync", "--dry-run"), 0)
        self.assertEqual(self.methods("crm.lead.add"), [])
        self.assertEqual(self.run_cli("stats"), 0)

    def test_full_cycle_creates_comments_and_is_idempotent(self):
        self.assertEqual(self.run_cli("sync"), 0)

        added = self.methods("crm.lead.add")
        self.assertEqual(len(added), 1)
        fields = added[0]["fields"]
        self.assertEqual(fields["UF_CRM_REALTING_ID"], "101")
        self.assertEqual(fields["PHONE"], [{"VALUE": "+380671234567", "VALUE_TYPE": "WORK"}])
        self.assertEqual(fields["NAME"], "Іван")
        self.assertIn("Цікавить квартира", fields["COMMENTS"])

        comments = self.methods("crm.timeline.comment.add")
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["fields"]["ENTITY_ID"], 555)

        # другий прогін: нічого нового в портал не летить
        _Handler.calls = []
        self.assertEqual(self.run_cli("sync"), 0)
        self.assertEqual(self.methods("crm.lead.add"), [])
        self.assertEqual(self.methods("crm.timeline.comment.add"), [])

    def test_empty_page_stops_pagination(self):
        self.run_cli("sync")
        pages = [page for method, page in _Handler.calls if method == "realting_page"]
        self.assertEqual(pages, [1, 2])

    def test_probe_reports_mapping(self):
        self.assertEqual(self.run_cli("probe", "--days", "3"), 0)


class WebhookEndToEndTest(_FakePlatforms):
    """Повний шлях хука: HTTP-приймач → черга в SQLite → CLI drain → Bitrix24."""

    def setUp(self):
        super().setUp()
        with self.env_file.open("a", encoding="utf-8") as f:
            f.write("\nWEBHOOK_TOKEN=s3cret\nWEBHOOK_AUTH_MODE=query\nWEBHOOK_PORT=0\n")

        from realting_sync.config import Config
        from realting_sync.state import SyncState
        from realting_sync.webhook import make_server

        config = Config.from_env(env={}, env_file=str(self.env_file))
        self.state = SyncState(config.state_path)
        self.addCleanup(self.state.close)
        self.receiver = make_server(config.webhook, self.state)
        self.receiver_port = self.receiver.server_address[1]
        threading.Thread(target=self.receiver.serve_forever, daemon=True).start()
        self.addCleanup(self.receiver.server_close)
        self.addCleanup(self.receiver.shutdown)

    def send_hook(self, order: dict, token: str = "s3cret") -> int:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"http://127.0.0.1:{self.receiver_port}/realting/webhook?token={token}",
            data=json.dumps(order).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_hook_reaches_bitrix_through_the_queue(self):
        self.assertEqual(self.send_hook(ORDERS[0]), 200)
        self.assertEqual(self.methods("crm.lead.add"), [])       # приймач сам у портал не ходить

        self.assertEqual(self.run_cli("drain"), 0)

        added = self.methods("crm.lead.add")
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["fields"]["UF_CRM_REALTING_ID"], "101")
        self.assertEqual(added[0]["fields"]["PHONE"], [{"VALUE": "+380671234567", "VALUE_TYPE": "WORK"}])

        # повторна доставка того самого хука нового ліда не створює
        self.assertEqual(self.send_hook(ORDERS[0]), 200)
        _Handler.calls = []
        self.assertEqual(self.run_cli("drain"), 0)
        self.assertEqual(self.methods("crm.lead.add"), [])

    def test_hook_with_wrong_token_never_reaches_the_queue(self):
        self.assertEqual(self.send_hook(ORDERS[0], token="wrong"), 401)
        self.assertEqual(self.run_cli("drain"), 0)
        self.assertEqual(self.methods("crm.lead.add"), [])

    def test_stats_shows_queue(self):
        self.send_hook(ORDERS[1])
        self.assertEqual(self.run_cli("stats"), 0)


if __name__ == "__main__":
    unittest.main()


class GuardsTest(unittest.TestCase):
    """Команди мають відмовлятися працювати з недоналаштованим каналом."""

    def _env(self, extra: str = "") -> str:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "sync.env"
        path.write_text(
            "BITRIX_WEBHOOK_URL=https://portal.bitrix24.ua/rest/1/hook/\n"
            f"STATE_PATH={tmp.name}/state.db\n" + extra,
            encoding="utf-8",
        )
        return str(path)

    def test_sync_without_export_url_exits_with_error(self):
        self.assertEqual(main(["--env-file", self._env(), "--log-level", "CRITICAL", "sync"]), 2)

    def test_serve_without_token_refuses_to_start(self):
        self.assertEqual(main(["--env-file", self._env(), "--log-level", "CRITICAL", "serve"]), 2)

    def test_serve_starts_when_auth_disabled_explicitly(self):
        # WEBHOOK_AUTH_MODE=none — свідомо відкритий ендпоінт; перевіряємо, що конфіг проходить
        from realting_sync.config import Config

        config = Config.from_env(env={}, env_file=self._env("WEBHOOK_AUTH_MODE=none\n"))
        self.assertEqual(config.webhook.auth_mode, "none")

    def test_drain_on_empty_queue_is_fine(self):
        self.assertEqual(main(["--env-file", self._env(), "--log-level", "CRITICAL", "drain"]), 0)
