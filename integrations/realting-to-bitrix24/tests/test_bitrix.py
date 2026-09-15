import unittest

from helpers import FakeTransport, json_response, make_config
from realting_sync.bitrix import BitrixClient, BitrixError, lead_comment, lead_title
from realting_sync.normalize import Lead


def sample_lead(**overrides) -> Lead:
    data = {
        "external_id": "42",
        "first_name": "Іван",
        "last_name": "Петренко",
        "phone": "+380671234567",
        "email": "ivan@example.com",
        "comment": "Цікавить обʼєкт",
        "object_title": "Квартира в Батумі",
        "object_url": "https://realting.com/object/1",
    }
    data.update(overrides)
    return Lead(**data)


class LeadFieldsTest(unittest.TestCase):
    def setUp(self):
        self.client = BitrixClient(make_config().bitrix, transport=FakeTransport(lambda url, kw: {"result": 1}))

    def test_fields_contain_contacts_and_external_id(self):
        fields = self.client.lead_fields(sample_lead())
        self.assertEqual(fields["PHONE"], [{"VALUE": "+380671234567", "VALUE_TYPE": "WORK"}])
        self.assertEqual(fields["EMAIL"], [{"VALUE": "ivan@example.com", "VALUE_TYPE": "WORK"}])
        self.assertEqual(fields["UF_CRM_REALTING_ID"], "42")
        self.assertEqual(fields["NAME"], "Іван")
        self.assertEqual(fields["UTM_SOURCE"], "realting.com")

    def test_missing_contact_channel_is_omitted(self):
        fields = self.client.lead_fields(sample_lead(email=""))
        self.assertNotIn("EMAIL", fields)

    def test_empty_name_gets_placeholder(self):
        fields = self.client.lead_fields(sample_lead(first_name="", last_name=""))
        self.assertEqual(fields["NAME"], "Без имени")

    def test_title_includes_object_and_is_bounded(self):
        self.assertEqual(lead_title(sample_lead()), "Realting #42 — Квартира в Батумі")
        self.assertLessEqual(len(lead_title(sample_lead(object_title="д" * 400))), 255)

    def test_comment_collects_known_details(self):
        text = lead_comment(sample_lead(language="uk", created_at="2026-09-14"))
        self.assertIn("Цікавить обʼєкт", text)
        self.assertIn("https://realting.com/object/1", text)
        self.assertIn("Realting ID: 42", text)

    def test_texts_stay_within_the_basic_plane(self):
        """Bitrix24 мовчки обнуляє TITLE, якщо в ньому є символ поза BMP (емодзі)."""
        lead = sample_lead(masked=True, object_title="48 m² | 1 bedroom apartment")
        for text in (lead_title(lead), lead_comment(lead), lead_title(sample_lead())):
            self.assertTrue(all(ord(ch) < 0x10000 for ch in text), f"емодзі в тексті: {text!r}")

    def test_masked_lead_title_says_so_in_words(self):
        title = lead_title(sample_lead(masked=True))
        self.assertIn("контакты скрыты", title)
        self.assertNotIn("контакты скрыты", lead_title(sample_lead()))


class CallTest(unittest.TestCase):
    def test_result_is_unwrapped(self):
        transport = FakeTransport(lambda url, kw: {"result": [{"ID": "7"}]})
        client = BitrixClient(make_config().bitrix, transport=transport)
        self.assertEqual(client.call("crm.lead.list", {}), [{"ID": "7"}])
        self.assertTrue(transport.calls[0]["url"].endswith("/crm.lead.list.json"))

    def test_business_error_raises(self):
        transport = FakeTransport(lambda url, kw: {"error": "INVALID_CREDENTIALS", "error_description": "bad token"})
        client = BitrixClient(make_config().bitrix, transport=transport)
        with self.assertRaises(BitrixError) as ctx:
            client.call("crm.lead.add", {})
        self.assertEqual(ctx.exception.code, "INVALID_CREDENTIALS")

    def test_rate_limit_error_is_retried(self):
        responses = [
            {"error": "QUERY_LIMIT_EXCEEDED", "error_description": "too fast"},
            {"result": 15},
        ]
        transport = FakeTransport(lambda url, kw: responses.pop(0))
        slept: list[float] = []
        client = BitrixClient(make_config().bitrix, transport=transport, sleep=slept.append, monotonic=lambda: 0.0)
        self.assertEqual(client.call("crm.lead.add", {}), 15)
        self.assertEqual(len(transport.calls), 2)
        self.assertTrue(slept)

    def test_throttle_waits_between_calls(self):
        transport = FakeTransport(lambda url, kw: {"result": 1})
        slept: list[float] = []
        clock = iter([0.0, 0.0, 0.1, 0.1])
        config = make_config().bitrix
        config = type(config)(**{**config.__dict__, "min_interval": 0.5})
        client = BitrixClient(config, transport=transport, sleep=slept.append, monotonic=lambda: next(clock))
        client.call("crm.lead.add", {})
        client.call("crm.lead.add", {})
        self.assertTrue(any(value > 0 for value in slept))


class CrmOperationsTest(unittest.TestCase):
    def test_find_lead_by_external_id(self):
        transport = FakeTransport(lambda url, kw: {"result": [{"ID": "101"}]})
        client = BitrixClient(make_config().bitrix, transport=transport)
        self.assertEqual(client.find_lead_by_external_id("42"), "101")
        body = transport.calls[0]["json_body"]
        self.assertEqual(body["filter"], {"UF_CRM_REALTING_ID": "42"})

    def test_find_lead_returns_none_when_absent(self):
        client = BitrixClient(make_config().bitrix, transport=FakeTransport(lambda url, kw: {"result": []}))
        self.assertIsNone(client.find_lead_by_external_id("42"))

    def test_find_duplicate_checks_phone_then_email(self):
        def responder(url, kwargs):
            if kwargs["json_body"]["type"] == "PHONE":
                return {"result": {}}
            return {"result": {"LEAD": [55]}}

        transport = FakeTransport(responder)
        client = BitrixClient(make_config().bitrix, transport=transport)
        self.assertEqual(client.find_duplicate(sample_lead()), "55")
        self.assertEqual(len(transport.calls), 2)

    def test_add_lead_returns_id(self):
        transport = FakeTransport(lambda url, kw: {"result": 777})
        client = BitrixClient(make_config().bitrix, transport=transport)
        self.assertEqual(client.add_lead(sample_lead()), "777")
        self.assertEqual(transport.calls[0]["json_body"]["params"], {"REGISTER_SONET_EVENT": "N"})


if __name__ == "__main__":
    unittest.main()
