"""Мапінг реального формату відповіді realting.com.

Зразок узятий з бойової відповіді `/api/orders/export` (контакти в ньому
замасковані самою платформою — саме так вона віддає заявки, не прийняті в роботу).
"""

from __future__ import annotations

import unittest

from helpers import FakeTransport, make_config
from realting_sync.bitrix import BitrixClient, lead_comment
from realting_sync.normalize import extract_rows, is_masked, normalize, normalize_all
from realting_sync.realting import RealtingClient
from test_realting import FROM, TO

MASKED_ORDER = {
    "id": 162275,
    "status_id": 1,
    "status_title": "Request not accepted to work",
    "reason": "",
    "name": "Ник***",
    "phone": "+790******21",
    "email": "n***@gmail.com",
    "message": "Акт***",
    "region": "Russia",
    "lang_code": None,
    "lang_title": "",
    "object": {
        "id": 3865039,
        "title": "58 m² | 2 bedroom apartment",
        "type_id": 0,
        "type_title": "Properties",
        "url": "https://realting.com/poland/property/3865039",
        "price": "$305 536",
    },
    "stay": None,
    "guests": None,
    "utm": {"source": "", "medium": "", "campaign": "", "content": "", "term": ""},
    "created_at": "2026-09-01 00:15:15",
    "received_at": "2026-09-04 07:50:15",
}

OPEN_ORDER = {
    "id": 162300,
    "status_id": 2,
    "status_title": "In work",
    "name": "Олена Ковальчук",
    "phone": "+38 (067) 123-45-67",
    "email": "Olena@Example.com",
    "message": "Цікавить ця квартира, передзвоніть",
    "region": "Ukraine",
    "lang_code": "uk",
    "lang_title": "Українська",
    "object": {
        "id": 1702254,
        "title": "160 m² | Townhouse 4 bedrooms",
        "type_id": 0,
        "type_title": "Properties",
        "url": "https://realting.com/poland/property/1702254",
        "price": "$312 455",
    },
    "utm": {"source": "google", "medium": "cpc", "campaign": "poland", "content": "", "term": ""},
    "created_at": "2026-09-14 10:00:00",
    "received_at": "2026-09-14 10:05:00",
}


def envelope(rows, page=1, pages=1, total=None):
    return {
        "success": True,
        "meta": {"page": page, "limit": 200, "total": total if total is not None else len(rows), "pages": pages},
        "data": rows,
    }


class EnvelopeTest(unittest.TestCase):
    def test_orders_are_taken_from_data(self):
        rows = extract_rows(envelope([MASKED_ORDER, OPEN_ORDER]))
        self.assertEqual([row["id"] for row in rows], [162275, 162300])

    def test_meta_only_envelope_yields_nothing(self):
        self.assertEqual(extract_rows(envelope([], total=0)), [])


class MaskDetectionTest(unittest.TestCase):
    def test_masked_values_are_recognised(self):
        self.assertTrue(is_masked("Ник***"))
        self.assertTrue(is_masked("", "+790******21"))
        self.assertTrue(is_masked("n***@gmail.com"))
        self.assertFalse(is_masked("Олена Ковальчук", "+380671234567", "olena@example.com"))

    def test_masked_order_is_flagged_not_crashed(self):
        lead = normalize(MASKED_ORDER)
        self.assertTrue(lead.masked)
        self.assertEqual(lead.external_id, "162275")

    def test_masked_order_is_skipped_by_default(self):
        leads, skipped = normalize_all([MASKED_ORDER])
        self.assertEqual(leads, [])
        self.assertIn("замасковані", skipped[0].reason)

    def test_masked_order_can_be_imported_deliberately(self):
        leads, skipped = normalize_all([MASKED_ORDER], skip_masked=False)
        self.assertEqual(len(leads), 1)
        self.assertEqual(skipped, [])

    def test_masked_phone_never_reaches_bitrix_as_a_number(self):
        lead = normalize(MASKED_ORDER)
        self.assertEqual(lead.phone, "")          # '+790******21' — не телефон

    def test_open_order_is_not_flagged(self):
        self.assertFalse(normalize(OPEN_ORDER).masked)


class RealFieldsTest(unittest.TestCase):
    def setUp(self):
        self.lead = normalize(OPEN_ORDER)

    def test_contacts(self):
        self.assertEqual(self.lead.phone, "+380671234567")
        self.assertEqual(self.lead.email, "olena@example.com")
        self.assertEqual((self.lead.first_name, self.lead.last_name), ("Олена", "Ковальчук"))

    def test_nested_object_block(self):
        self.assertEqual(self.lead.object_id, "1702254")
        self.assertEqual(self.lead.object_title, "160 m² | Townhouse 4 bedrooms")
        self.assertEqual(self.lead.object_url, "https://realting.com/poland/property/1702254")
        self.assertEqual(self.lead.object_price, "$312 455")
        self.assertEqual(self.lead.object_type, "Properties")

    def test_language_comes_from_lang_code(self):
        self.assertEqual(self.lead.language, "uk")

    def test_language_falls_back_to_title_when_code_is_null(self):
        row = {**OPEN_ORDER, "lang_code": None, "lang_title": "Русский"}
        self.assertEqual(normalize(row).language, "Русский")

    def test_region_and_status(self):
        self.assertEqual(self.lead.region, "Ukraine")
        self.assertEqual(self.lead.status, "In work")

    def test_nested_utm_block(self):
        self.assertEqual(self.lead.utm_source, "google")
        self.assertEqual(self.lead.utm_medium, "cpc")
        self.assertEqual(self.lead.utm_campaign, "poland")

    def test_empty_utm_block_does_not_break_anything(self):
        lead = normalize({**OPEN_ORDER, "utm": {"source": "", "medium": "", "campaign": ""}})
        self.assertEqual(lead.utm_source, "")

    def test_comment_carries_the_context_a_manager_needs(self):
        text = lead_comment(self.lead)
        for expected in ("Сообщение: Цікавить ця квартира", "Объект: 160 m² | Townhouse",
                         "Цена: $312 455", "Регион клиента: Ukraine", "Статус на Realting: In work",
                         "realting.com/poland/property/1702254", "Realting ID: 162300"):
            self.assertIn(expected, text)

    def test_bitrix_fields_of_a_real_order(self):
        fields = BitrixClient(make_config().bitrix, transport=FakeTransport(lambda u, k: {"result": 1})).lead_fields(self.lead)
        self.assertEqual(fields["UF_CRM_REALTING_ID"], "162300")
        self.assertEqual(fields["PHONE"], [{"VALUE": "+380671234567", "VALUE_TYPE": "WORK"}])
        self.assertEqual(fields["EMAIL"], [{"VALUE": "olena@example.com", "VALUE_TYPE": "WORK"}])
        self.assertEqual(fields["UTM_SOURCE"], "google")
        self.assertIn("160 m² | Townhouse", fields["TITLE"])


class MetaPaginationTest(unittest.TestCase):
    def test_single_page_stops_immediately(self):
        transport = FakeTransport(lambda url, kw: envelope([OPEN_ORDER], pages=1))
        rows = RealtingClient(make_config().realting, transport=transport).fetch_orders(FROM, TO)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(transport.calls), 1)   # meta.pages=1 — другої сторінки не просимо

    def test_walks_all_pages_reported_by_meta(self):
        pages = {
            1: envelope([{**OPEN_ORDER, "id": 1}], page=1, pages=3, total=3),
            2: envelope([{**OPEN_ORDER, "id": 2}], page=2, pages=3, total=3),
            3: envelope([{**OPEN_ORDER, "id": 3}], page=3, pages=3, total=3),
        }
        transport = FakeTransport(lambda url, kw: pages[kw["params"]["page"]])
        rows = RealtingClient(make_config().realting, transport=transport).fetch_orders(FROM, TO)
        self.assertEqual([row["id"] for row in rows], [1, 2, 3])

    def test_meta_wins_over_short_page_heuristic(self):
        # сторінка коротша за limit, але meta каже, що є ще одна
        pages = {
            1: envelope([{**OPEN_ORDER, "id": 1}], page=1, pages=2, total=2),
            2: envelope([{**OPEN_ORDER, "id": 2}], page=2, pages=2, total=2),
        }
        transport = FakeTransport(lambda url, kw: pages[kw["params"]["page"]])
        rows = RealtingClient(make_config().realting, transport=transport).fetch_orders(FROM, TO)
        self.assertEqual(len(rows), 2)


class MixedBatchTest(unittest.TestCase):
    def test_only_open_orders_reach_the_crm(self):
        rows = extract_rows(envelope([MASKED_ORDER, OPEN_ORDER, {"id": 3, "name": "Порожня"}]))
        leads, skipped = normalize_all(rows)
        self.assertEqual([lead.external_id for lead in leads], ["162300"])
        self.assertEqual(len(skipped), 2)


if __name__ == "__main__":
    unittest.main()
