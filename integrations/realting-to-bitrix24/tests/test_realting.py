import unittest
from datetime import datetime, timezone

from helpers import FakeTransport, json_response, make_config
from realting_sync.config import RealtingConfig
from realting_sync.httpclient import Response
from realting_sync.realting import RealtingClient

FROM = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
TO = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class AuthTest(unittest.TestCase):
    def _headers_for(self, **overrides):
        config = RealtingConfig(url="https://realting.test/api", token="t0ken", **overrides)
        transport = FakeTransport(lambda url, kw: {"data": []})
        RealtingClient(config, transport=transport).fetch_raw(FROM, TO)
        return transport.calls[0]

    def test_bearer(self):
        self.assertEqual(self._headers_for(auth_mode="bearer")["headers"]["Authorization"], "Bearer t0ken")

    def test_custom_header(self):
        call = self._headers_for(auth_mode="header", auth_header="X-Token")
        self.assertEqual(call["headers"]["X-Token"], "t0ken")

    def test_query_token(self):
        call = self._headers_for(auth_mode="query", auth_query_param="api_key")
        self.assertEqual(call["params"]["api_key"], "t0ken")
        self.assertEqual(call["headers"], {})

    def test_basic(self):
        self.assertTrue(self._headers_for(auth_mode="basic")["headers"]["Authorization"].startswith("Basic "))

    def test_none(self):
        self.assertEqual(self._headers_for(auth_mode="none")["headers"], {})


class ParamsTest(unittest.TestCase):
    def test_date_params_use_configured_names_and_format(self):
        config = RealtingConfig(
            url="https://realting.test/api",
            token="t",
            param_date_from="updated_since",
            param_date_to="updated_until",
            date_format="%Y-%m-%d",
        )
        transport = FakeTransport(lambda url, kw: {"data": []})
        RealtingClient(config, transport=transport).fetch_raw(FROM, TO)
        params = transport.calls[0]["params"]
        self.assertEqual(params["updated_since"], "2026-09-14")
        self.assertEqual(params["updated_until"], "2026-09-14")

    def test_extra_params_are_passed(self):
        config = RealtingConfig(url="https://realting.test/api", token="t", extra_params={"format": "json"})
        transport = FakeTransport(lambda url, kw: {"data": []})
        RealtingClient(config, transport=transport).fetch_raw(FROM, TO)
        self.assertEqual(transport.calls[0]["params"]["format"], "json")


class PaginationTest(unittest.TestCase):
    def test_walks_pages_until_short_page(self):
        pages = {
            1: {"data": [{"id": 1}, {"id": 2}]},
            2: {"data": [{"id": 3}]},
        }
        transport = FakeTransport(lambda url, kw: pages[kw["params"]["page"]])
        client = RealtingClient(make_config().realting, transport=transport)
        rows = client.fetch_orders(FROM, TO)
        self.assertEqual([row["id"] for row in rows], [1, 2, 3])
        self.assertEqual(len(transport.calls), 2)

    def test_empty_second_page_adds_nothing(self):
        pages = {1: {"data": [{"id": 1}, {"id": 2}]}, 2: {"data": []}}
        transport = FakeTransport(lambda url, kw: pages[kw["params"]["page"]])
        rows = RealtingClient(make_config().realting, transport=transport).fetch_orders(FROM, TO)
        self.assertEqual([row["id"] for row in rows], [1, 2])
        self.assertEqual(len(transport.calls), 2)

    def test_stops_when_server_ignores_page_param(self):
        transport = FakeTransport(lambda url, kw: {"data": [{"id": 1}, {"id": 2}]})
        client = RealtingClient(make_config().realting, transport=transport)
        rows = client.fetch_orders(FROM, TO)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(transport.calls), 2)   # друга сторінка розпізнана як повтор

    def test_pagination_disabled(self):
        config = make_config().realting
        config = type(config)(**{**config.__dict__, "pagination": "none"})
        transport = FakeTransport(lambda url, kw: {"data": [{"id": 1}, {"id": 2}]})
        rows = RealtingClient(config, transport=transport).fetch_orders(FROM, TO)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("page", transport.calls[0]["params"])


class XmlResponseTest(unittest.TestCase):
    def test_xml_export_is_parsed(self):
        xml = (
            "<orders><order><id>5</id><phone>+380671234567</phone></order>"
            "<order><id>6</id><phone>+380509998877</phone></order></orders>"
        )
        transport = FakeTransport(lambda url, kw: Response(200, xml.encode("utf-8"), "application/xml"))
        rows = RealtingClient(make_config().realting, transport=transport).fetch_orders(FROM, TO)
        self.assertEqual([row["id"] for row in rows], ["5", "6"])


if __name__ == "__main__":
    unittest.main()
