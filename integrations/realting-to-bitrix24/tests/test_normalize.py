import unittest

from realting_sync.normalize import (
    Lead,
    SkippedOrder,
    clean_email,
    clean_phone,
    extract_rows,
    load_field_map,
    normalize,
    normalize_all,
    split_name,
)


class ExtractRowsTest(unittest.TestCase):
    def test_plain_list(self):
        self.assertEqual(extract_rows([{"id": 1}, {"id": 2}]), [{"id": 1}, {"id": 2}])

    def test_common_wrappers(self):
        for key in ("data", "items", "orders", "result", "rows"):
            with self.subTest(key=key):
                self.assertEqual(extract_rows({key: [{"id": 7}]}), [{"id": 7}])

    def test_nested_xml_shape(self):
        payload = {"orders": {"order": {"id": "5"}}}
        self.assertEqual(extract_rows(payload), [{"id": "5"}])

    def test_single_object(self):
        self.assertEqual(extract_rows({"id": 3, "phone": "+380"}), [{"id": 3, "phone": "+380"}])

    def test_dict_of_orders(self):
        payload = {"1": {"id": 1}, "2": {"id": 2}}
        self.assertEqual(extract_rows(payload), [{"id": 1}, {"id": 2}])

    def test_empty(self):
        self.assertEqual(extract_rows(None), [])
        self.assertEqual(extract_rows([]), [])

    def test_empty_wrapper_is_not_treated_as_an_order(self):
        # порожня сторінка пагінації: {"data": []} — це нуль заявок, а не одна
        self.assertEqual(extract_rows({"data": []}), [])
        self.assertEqual(extract_rows({"orders": [], "meta": {"page": 2}}), [])

    def test_wrapper_with_non_dict_items_is_ignored(self):
        self.assertEqual(extract_rows({"data": ["сміття", 5]}), [])


class CleanersTest(unittest.TestCase):
    def test_phone_keeps_plus_and_digits(self):
        self.assertEqual(clean_phone(" +38 (067) 123-45-67 "), "+380671234567")
        self.assertEqual(clean_phone("067 123 45 67"), "0671234567")

    def test_phone_rejects_garbage(self):
        self.assertEqual(clean_phone("—"), "")
        self.assertEqual(clean_phone("12345"), "")
        self.assertEqual(clean_phone(""), "")

    def test_email_validation(self):
        self.assertEqual(clean_email(" Ivan@Example.COM "), "ivan@example.com")
        self.assertEqual(clean_email("не пошта"), "")
        self.assertEqual(clean_email("a@b"), "")

    def test_split_name(self):
        self.assertEqual(split_name("Іван Петренко"), ("Іван", "Петренко"))
        self.assertEqual(split_name("Іван  Петро Петренко"), ("Іван", "Петро Петренко"))
        self.assertEqual(split_name("  "), ("", ""))


class NormalizeTest(unittest.TestCase):
    def test_typical_json_order(self):
        lead = normalize({
            "id": 42,
            "created_at": "2026-09-14 10:00:00",
            "name": "Іван Петренко",
            "phone": "+38 (067) 123-45-67",
            "email": "IVAN@example.com",
            "message": "Цікавить ця квартира",
            "object_url": "https://realting.com/object/1",
            "lang": "uk",
        })
        self.assertEqual(lead.external_id, "42")
        self.assertEqual(lead.first_name, "Іван")
        self.assertEqual(lead.last_name, "Петренко")
        self.assertEqual(lead.phone, "+380671234567")
        self.assertEqual(lead.email, "ivan@example.com")
        self.assertEqual(lead.language, "uk")
        self.assertTrue(lead.has_contact)

    def test_nested_client_object(self):
        lead = normalize({"order_id": "a-1", "client": {"name": "Ольга", "phone": "0501112233"}})
        self.assertEqual(lead.external_id, "a-1")
        self.assertEqual(lead.first_name, "Ольга")
        self.assertEqual(lead.phone, "0501112233")

    def test_explicit_first_last_name_wins(self):
        lead = normalize({"id": 1, "first_name": "Анна", "last_name": "Коваль", "name": "ігнор", "phone": "0501112233"})
        self.assertEqual((lead.first_name, lead.last_name), ("Анна", "Коваль"))
        self.assertEqual(lead.full_name, "Анна Коваль")

    def test_missing_id_is_skipped(self):
        with self.assertRaises(SkippedOrder) as ctx:
            normalize({"phone": "0501112233"})
        self.assertIn("ідентифікатор", str(ctx.exception))

    def test_missing_contacts_is_skipped(self):
        with self.assertRaises(SkippedOrder):
            normalize({"id": 5, "name": "Хтось"})

    def test_email_only_order_passes(self):
        lead = normalize({"id": 5, "email": "a@b.com"})
        self.assertTrue(lead.has_contact)
        self.assertEqual(lead.phone, "")

    def test_xml_style_value_dict(self):
        lead = normalize({"id": {"value": "77"}, "phone": {"value": "+380671234567"}})
        self.assertEqual(lead.external_id, "77")
        self.assertEqual(lead.phone, "+380671234567")


class NormalizeAllTest(unittest.TestCase):
    def test_counts_and_dedup_inside_batch(self):
        rows = [
            {"id": 1, "phone": "0501112233"},
            {"id": 1, "phone": "0501112233"},   # дубль у тій самій відповіді
            {"id": 2},                            # без контактів
            {"phone": "0501112233"},              # без id
        ]
        leads, skipped = normalize_all(rows)
        self.assertEqual([lead.external_id for lead in leads], ["1"])
        self.assertEqual(len(skipped), 2)


class FieldMapTest(unittest.TestCase):
    def test_custom_map_overrides_defaults(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            path.write_text(json.dumps({"external_id": ["zayavka_nomer"], "phone": ["kontakt.tel"]}), encoding="utf-8")
            fmap = load_field_map(path)
            lead = normalize({"zayavka_nomer": "z-9", "kontakt": {"tel": "0501112233"}, "id": "ignored"}, fmap)
            self.assertEqual(lead.external_id, "z-9")
            self.assertEqual(lead.phone, "0501112233")


if __name__ == "__main__":
    unittest.main()
