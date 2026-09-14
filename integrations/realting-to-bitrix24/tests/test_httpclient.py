import unittest
import urllib.error
import xml.etree.ElementTree as ET

from realting_sync.httpclient import HttpError, Response, xml_to_data


class ResponseTest(unittest.TestCase):
    def test_json_by_content_type(self):
        resp = Response(200, b'{"a": 1}', "application/json")
        self.assertEqual(resp.parsed(), {"a": 1})

    def test_json_without_content_type(self):
        resp = Response(200, b'[{"a": 1}]', "text/plain")
        self.assertEqual(resp.parsed(), [{"a": 1}])

    def test_xml_is_converted(self):
        resp = Response(200, b"<orders><order><id>1</id></order></orders>", "application/xml")
        self.assertEqual(resp.parsed(), {"orders": {"order": {"id": "1"}}}["orders"])

    def test_empty_body(self):
        self.assertIsNone(Response(200, b"", "application/json").parsed())

    def test_html_error_page_raises(self):
        with self.assertRaises(HttpError):
            Response(200, b"nonsense body", "text/plain").parsed()


class XmlToDataTest(unittest.TestCase):
    def test_repeated_tags_become_list(self):
        root = ET.fromstring("<orders><order><id>1</id></order><order><id>2</id></order></orders>")
        self.assertEqual(xml_to_data(root), {"order": [{"id": "1"}, {"id": "2"}]})

    def test_attributes_are_kept(self):
        root = ET.fromstring('<order id="5"><phone>+380</phone></order>')
        self.assertEqual(xml_to_data(root), {"id": "5", "phone": "+380"})


class RequestRetryTest(unittest.TestCase):
    """Ретраї перевіряємо, підмінивши urlopen у модулі."""

    def setUp(self):
        from realting_sync import httpclient

        self.httpclient = httpclient
        self.original = httpclient.urllib.request.urlopen

    def tearDown(self):
        self.httpclient.urllib.request.urlopen = self.original

    def _install(self, sequence):
        calls = {"n": 0}

        class FakeResponse:
            def __init__(self, body):
                self.status = 200
                self.headers = {"Content-Type": "application/json"}
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            item = sequence[calls["n"]]
            calls["n"] += 1
            if isinstance(item, Exception):
                raise item
            return FakeResponse(item)

        self.httpclient.urllib.request.urlopen = fake_urlopen
        return calls

    def test_retries_on_server_error_then_succeeds(self):
        error = urllib.error.HTTPError("url", 503, "busy", {}, None)
        error.read = lambda: b"busy"
        calls = self._install([error, b'{"ok": true}'])
        slept = []
        resp = self.httpclient.request("https://x.test", retries=3, sleep=slept.append)
        self.assertEqual(resp.json(), {"ok": True})
        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(slept), 1)

    def test_client_error_is_not_retried(self):
        error = urllib.error.HTTPError("url", 401, "unauthorized", {}, None)
        error.read = lambda: b"no access"
        calls = self._install([error])
        with self.assertRaises(HttpError) as ctx:
            self.httpclient.request("https://x.test", retries=3, sleep=lambda _: None)
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(calls["n"], 1)

    def test_network_error_exhausts_retries(self):
        calls = self._install([urllib.error.URLError("dns"), urllib.error.URLError("dns")])
        with self.assertRaises(HttpError):
            self.httpclient.request("https://x.test", retries=2, sleep=lambda _: None)
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
