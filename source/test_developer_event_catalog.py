"""V3 event registry regression tests (no Qt or game process required)."""
import json
import unittest
from developer_event_catalog import (load_catalog, filter_catalog,
                                     make_fixture, inspect_capture)


class EventCatalogTests(unittest.TestCase):
    def test_all_developer_rows_are_present(self):
        events = load_catalog()
        self.assertEqual(len(events), 357)
        self.assertEqual(len({e.name for e in events}), 357)
        self.assertEqual(len({e.category for e in events}), 14)
        self.assertEqual(sum(e.uncertain_id for e in events), 47)
        self.assertEqual(sum(e.networked for e in events), 29)
        self.assertEqual(sum(e.kept for e in events), 59)

    def test_representative_event_metadata(self):
        events = {e.name: e for e in load_catalog()}
        self.assertEqual(events["local_highlight"].event_id, "0x37df5768")
        self.assertTrue(events["effect_applied"].uncertain_id)
        self.assertEqual(events["weapon_shoot"].counts, (1490, 1359, 1359))
        self.assertTrue(events["player_downed"].networked)
        self.assertIn(("damage", "float"), events["player_damage"].fields)

    def test_filter_and_fixture_validation(self):
        sample = filter_catalog("damage", recorder="Kept by recorder")
        self.assertTrue(sample)
        self.assertTrue(all(e.kept for e in sample))
        event = next(e for e in load_catalog() if e.name == "local_highlight")
        fixture = make_fixture(event, {"active": "true", "entityid": "0x12"})
        self.assertTrue(fixture["fields"]["active"])
        self.assertEqual(fixture["fields"]["entityid"], "0x12")
        self.assertTrue(fixture["fixture_only"])
        self.assertEqual(fixture["unfilled_fields"], [])
        with self.assertRaises(ValueError):
            make_fixture(event, {"active": "not-a-bool"})
        with self.assertRaises(ValueError):
            make_fixture(next(e for e in load_catalog()
                              if e.name == "ui_damage_number"),
                         {"damage": "nan"})

    def test_inspect_json_and_jsonl(self):
        for source in ('[{"event":"weapon_shoot"}, {"id":"0x4031de01"},'
                       ' {"event":"not_in_catalog"}]',
                       '{"event":"weapon_shoot"}\n{"event_id":"0x4031de01"}\n'):
            result = inspect_capture(source)
            self.assertEqual(result["matched"], 2)
            self.assertEqual(result["counts"]["weapon_shoot"], 1)
        with self.assertRaises(ValueError):
            inspect_capture("[]", max_bytes=1)


if __name__ == "__main__":
    unittest.main()
