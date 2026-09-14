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


class EndToEndTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
