"""PRN discard: immutable audit records, stock credits and effective daily totals."""
from copy import deepcopy
import json
import unittest

from test_doctor_features import model, _profile, _medication, NOW
import test_intake_corrections as correction_tests


def fixture():
    profile = _profile()
    profile["medications"] = {"prn": _medication("prn", "Example PRN", stock=10, as_needed=True)}
    store = {"profiles": {profile["person_id"]: profile}}
    event = model.book_as_needed(store, profile["person_id"], "prn", 1,
                                actor="Test", actor_user_id="user-test", now=NOW)
    return store, profile, event


class PrnDiscardTests(unittest.TestCase):
    def setUp(self):
        self.store, self.profile, self.event = fixture()
        self.intake_id = f"as_needed:{self.event['id']}"

    def discard(self, revision=""):
        return model.correct_intake(self.profile, self.intake_id, "discarded", revision,
                                   actor="Test", actor_user_id="user-test", now=NOW)

    def test_discard_credits_stock_and_retains_immutable_original(self):
        original = deepcopy(self.profile["events"])
        self.discard()
        self.assertEqual(10, self.profile["medications"]["prn"]["stock"])
        self.assertEqual(original, self.profile["events"][:len(original)])
        correction = self.profile["events"][-1]
        self.assertEqual(self.event["id"], correction["original_event_id"])
        self.assertEqual("manual_correction", correction["source"])
        self.assertEqual("user-test", correction["actor_user_id"])

    def test_statistics_and_heatmap_exclude_discarded_prn_but_show_audit(self):
        before = model.statistics(self.profile, 1, NOW)
        self.assertEqual(1, before["as_needed_bookings"])
        self.assertEqual("manual_only", before["heatmap"][0]["heatmap_status"])
        self.discard()
        stats = model.statistics(self.profile, 1, NOW)
        self.assertEqual(0, stats["as_needed_bookings"])
        self.assertEqual(0, stats["as_needed_quantity"])
        self.assertEqual("no_data", stats["heatmap"][0]["heatmap_status"])
        audit = stats["day_details"]["events"][0]
        self.assertEqual("as_needed_discarded", audit["type"])
        self.assertEqual("discarded", audit["status"])
        self.assertTrue(audit["corrected"])
        self.assertEqual(self.intake_id, audit["slot_id"])

    def test_only_discard_is_allowed_including_after_discard(self):
        for after_discard in (False, True):
            if after_discard:
                self.discard()
            before = deepcopy(self.profile)
            revision = model.intake_corrections(self.profile).get(self.intake_id, {}).get("id", "")
            for status in ("taken", "skipped"):
                with self.assertRaises(model.PillPalError):
                    model.correct_intake(self.profile, self.intake_id, status, revision, now=NOW)
                self.assertEqual(before, self.profile)

    def test_duplicate_requests_never_credit_twice(self):
        result = self.discard()
        before = deepcopy(self.profile)
        with self.assertRaises(model.PillPalError):
            self.discard()
        self.assertTrue(self.discard(result["correction_id"])["unchanged"])
        self.assertEqual(before, self.profile)

    def test_legacy_quantity_and_historical_plan_changes(self):
        for key in ("stock_before", "stock_after"):
            self.profile["events"][0].pop(key)
        self.profile["medications"]["prn"].update(step=0.5, single_max=3, archived=True)
        self.discard()
        self.assertEqual(10, self.profile["medications"]["prn"]["stock"])

    def test_new_bookings_use_actual_recorded_stock_debit(self):
        self.profile["events"][0]["quantity"] = 3
        self.discard()
        self.assertEqual(10, self.profile["medications"]["prn"]["stock"])

    def test_changed_units_or_missing_medication_reject_atomically(self):
        for changed_unit in (True, False):
            self.store, self.profile, self.event = fixture()
            self.intake_id = f"as_needed:{self.event['id']}"
            if changed_unit:
                self.profile["medications"]["prn"]["unit_singular"] = "ml"
            else:
                self.profile["medications"].clear()
            before = deepcopy(self.profile)
            with self.assertRaises(model.PillPalError):
                self.discard()
            self.assertEqual(before, self.profile)

    def test_daily_maximum_and_frontend_totals_use_effective_state(self):
        self.profile["medications"]["prn"]["daily_max"] = 1
        self.discard()
        result = model.book_as_needed(self.store, self.profile["person_id"], "prn", 1,
                                     actor="Test", actor_user_id="user-test", now=NOW)
        self.assertEqual("as_needed", result["type"])
        events = model.snapshot(self.profile, NOW)["events"]
        self.assertEqual(1, sum(e.get("quantity", 0) for e in events if e["type"] == "as_needed"))

    def test_clear_statistics_keeps_prn_discard_effective(self):
        self.discard()
        model.clear_statistics(self.store, self.profile["person_id"], actor="Test", now=NOW)
        self.assertEqual(0, model.statistics(self.profile, 1, NOW)["as_needed_bookings"])
        with self.assertRaises(model.PillPalError):
            self.discard()
        self.assertEqual(10, self.profile["medications"]["prn"]["stock"])

    def test_persistence_and_medication_filter(self):
        self.discard()
        self.store = json.loads(json.dumps(self.store))
        self.profile = model.ensure_profile(self.store, person_id=self.profile["person_id"],
            name="Test", person_entity_id="person.test", user_id="user-test",
            admin_assistance=False, now=NOW)
        stats = model.statistics(self.profile, 1, NOW, medication_id="prn", slot="as_needed")
        self.assertEqual(0, stats["as_needed_bookings"])
        self.assertEqual(1, len(stats["day_details"]["events"]))


class PrnTransactionTests(unittest.IsolatedAsyncioTestCase):
    def manager(self):
        manager = correction_tests.CorrectionTransactionTests().manager()
        _, profile, event = fixture()
        manager.data = {"profile": profile}
        self.intake_id = f"as_needed:{event['id']}"
        return manager

    # Exercise the same production transaction methods with a PRN booking.
    async def test_prn_storage_failure_rolls_back(self):
        manager = self.manager()
        before = deepcopy(manager.data)
        async def fail():
            raise OSError("storage unavailable")
        manager._async_save = fail
        with self.assertRaises(OSError):
            await manager.async_correct_intake("person", self.intake_id, "discarded", "",
                                              actor="Test", actor_user_id="user-test")
        self.assertEqual(before, manager.data)

    async def test_prn_concurrent_discard_only_credits_once(self):
        import asyncio
        manager = self.manager()
        results = await asyncio.gather(*[
            manager.async_correct_intake("person", self.intake_id, "discarded", "",
                                         actor="Test", actor_user_id="user-test") for _ in range(2)
        ], return_exceptions=True)
        self.assertEqual(1, sum(isinstance(r, model.PillPalError) for r in results))
        self.assertEqual(10, manager.profile("")["medications"]["prn"]["stock"])

if __name__ == "__main__":
    unittest.main()
