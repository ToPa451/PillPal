"""Historical corrections: accounting, audit, statistics and durable transactions."""
from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from test_doctor_features import model, _profile, _medication, NOW


def fixture(status="taken", *, event=True):
    profile = _profile()
    profile["medications"] = {"med": _medication("med", "Historisches Medikament", stock=9 if status == "taken" else 10)}
    meds = [{"medication_id": "med", "name": "Historisches Medikament", "quantity": 1,
             "unit_singular": "Tablette", "unit_plural": "Tabletten", "doctor_id": "old-doctor"}]
    if status == "taken":
        meds[0].update(stock_before=10, stock_after=9)
    original = model.add_event(profile, f"regular_{status}", now=NOW, cycle_id="cycle",
                               cycle_date=NOW.date().isoformat(), slot="morning", medications=meds)
    model._migrate_terminal_event_history(profile)
    if not event:
        profile["events"] = []
    return profile


class IntakeCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.profile = fixture()
        self.revision = ""

    def correct(self, status, now=NOW):
        result = model.correct_intake(self.profile, "cycle:morning", status, self.revision,
                                      actor="Assistenz", actor_user_id="user-1", now=now)
        self.revision = result.get("correction_id", self.revision)
        return result

    def stats(self, **kwargs):
        return model.statistics(self.profile, 1, NOW, **kwargs)

    def test_all_state_transitions_and_repeated_corrections(self):
        for original in ("taken", "skipped", "missed"):
            for target in ("taken", "skipped", "discarded"):
                with self.subTest(original=original, target=target):
                    self.profile = fixture(original)
                    self.revision = ""
                    self.correct(target)
                    self.assertEqual(9 if target == "taken" else 10, self.profile["medications"]["med"]["stock"])
                    stats = self.stats()
                    self.assertEqual(int(target != "discarded"), stats["planned"])
                    self.assertEqual(int(target == "taken"), stats["taken"])
                    self.assertEqual(int(target == "skipped"), stats["skipped"])
                    self.assertEqual(0, stats["pending"])
                    self.assertEqual(target, stats["day_details"]["events"][0]["status"])

    def test_round_trip_keeps_star_and_immutable_history(self):
        events, history = deepcopy(self.profile["events"]), deepcopy(self.profile["history"])
        self.correct("skipped")
        self.correct("taken")
        self.assertEqual(9, self.profile["medications"]["med"]["stock"])
        self.assertEqual(history, self.profile["history"])
        self.assertEqual(events, self.profile["events"][:len(events)])
        booking = self.stats()["day_details"]["events"][0]
        self.assertTrue(booking["corrected"])
        self.assertEqual("taken", booking["original_status"])
        self.assertEqual(100, self.stats()["adherence"])
        self.assertEqual("manual_correction", self.profile["events"][-1]["source"])
        self.assertEqual("user-1", self.profile["events"][-1]["actor_user_id"])

    def test_discard_skip_and_restore_only_change_stock_once(self):
        for status, stock in (("discarded", 10), ("skipped", 10), ("discarded", 10), ("taken", 9)):
            self.correct(status)
            self.assertEqual(stock, self.profile["medications"]["med"]["stock"])

    def test_discard_excludes_denominator_and_heatmap(self):
        self.correct("discarded")
        stats = self.stats()
        self.assertEqual((0, 0, 0, 0), tuple(stats[k] for k in ("planned", "taken", "skipped", "missed")))
        self.assertEqual("no_data", stats["heatmap"][0]["heatmap_status"])
        self.assertEqual(1, len(stats["day_details"]["events"]))
        self.assertEqual("regular_discarded", stats["day_details"]["events"][0]["type"])

    def test_historical_quantity_after_plan_change(self):
        self.profile["medications"]["med"]["doses"]["morning"] = 5
        self.correct("skipped")
        self.correct("taken")
        self.assertEqual(9, self.profile["medications"]["med"]["stock"])

    def test_actual_debit_not_nominal_quantity_is_reversed(self):
        self.profile["events"][0]["medications"][0]["quantity"] = 5
        self.correct("discarded")
        self.assertEqual(10, self.profile["medications"]["med"]["stock"])

    def test_atomic_rejection_for_insufficient_stock(self):
        self.profile = fixture("skipped")
        self.profile["medications"]["med"]["stock"] = 0
        before = deepcopy(self.profile)
        with self.assertRaises(model.PillPalError):
            self.correct("taken")
        self.assertEqual(before, self.profile)

    def test_validates_all_medications_before_mutating(self):
        self.profile = fixture("skipped")
        self.profile["medications"]["other"] = _medication("other", "Leer", stock=0)
        extra = {**self.profile["events"][0]["medications"][0], "medication_id": "other"}
        self.profile["history"]["daily"][NOW.date().isoformat()]["slots"]["cycle:morning"]["medications"].append(extra)
        before = deepcopy(self.profile)
        with self.assertRaises(model.PillPalError):
            self.correct("taken")
        self.assertEqual(before, self.profile)

    def test_duplicate_and_stale_requests_never_rebook(self):
        self.correct("skipped")
        before = deepcopy(self.profile)
        with self.assertRaises(model.PillPalError):
            model.correct_intake(self.profile, "cycle:morning", "skipped", "", now=NOW)
        self.assertEqual(before, self.profile)
        self.assertTrue(self.correct("skipped")["unchanged"])
        self.assertEqual(before, self.profile)
        self.correct("taken")
        with self.assertRaises(model.PillPalError):
            model.correct_intake(self.profile, "cycle:morning", "discarded", "", now=NOW)
        self.assertEqual(9, self.profile["medications"]["med"]["stock"])

    def test_correction_date_does_not_move_original_statistic(self):
        self.correct("discarded", NOW + timedelta(days=90))
        self.assertEqual(0, self.stats()["planned"])
        future = model.statistics(self.profile, 1, NOW + timedelta(days=90))
        self.assertEqual([], future["day_details"]["events"])

    def test_persistence_normalization_keeps_correction(self):
        self.correct("discarded")
        store = {"profiles": {self.profile["person_id"]: json.loads(json.dumps(self.profile))}}
        self.profile = model.ensure_profile(store, person_id=self.profile["person_id"],
                                            name="Testperson", person_entity_id="person.test",
                                            user_id="user-test", admin_assistance=False, now=NOW)
        self.assertEqual(0, self.stats()["planned"])
        self.assertTrue(self.stats()["day_details"]["events"][0]["corrected"])
        self.correct("taken")
        self.assertEqual(9, self.profile["medications"]["med"]["stock"])

    def test_snapshot_only_missed_entry_can_be_corrected(self):
        self.profile = fixture("missed", event=False)
        self.correct("taken")
        self.assertEqual(1, self.stats()["taken"])

    def test_event_only_legacy_entry_and_missing_debit(self):
        self.profile["history"] = {"daily": {}}
        self.correct("skipped")
        self.assertEqual(1, self.stats()["skipped"])
        self.profile["events"][0]["medications"][0].pop("stock_before")
        before = deepcopy(self.profile)
        with self.assertRaises(model.PillPalError):
            self.correct("taken")
        self.assertEqual(before, self.profile)

    def test_changed_unit_is_rejected_without_partial_update(self):
        self.profile["medications"]["med"]["unit_singular"] = "ml"
        before = deepcopy(self.profile)
        with self.assertRaises(model.PillPalError):
            self.correct("discarded")
        self.assertEqual(before, self.profile)

    def test_historical_filters_and_full_medication_display(self):
        self.correct("skipped")
        self.assertEqual(1, self.stats(doctor_id="old-doctor")["skipped"])
        self.assertEqual(0, self.stats(doctor_id="new-doctor")["planned"])
        self.assertEqual(1, len(self.stats(medication_id="med")["day_details"]["events"][0]["all_medications"]))

    def test_archived_medication_can_be_reversed(self):
        self.profile["medications"]["med"]["archived"] = True
        self.correct("skipped")
        self.assertEqual(10, self.profile["medications"]["med"]["stock"])

    def test_fractional_historical_quantities_do_not_drift(self):
        self.profile = fixture("skipped")
        stored = self.profile["history"]["daily"][NOW.date().isoformat()]["slots"]["cycle:morning"]
        stored["medications"][0]["quantity"] = 0.5
        for _ in range(5):
            self.correct("taken")
            self.assertEqual(9.5, self.profile["medications"]["med"]["stock"])
            self.correct("skipped")
            self.assertEqual(10, self.profile["medications"]["med"]["stock"])

    def test_aggregated_historical_debit_is_validated_before_booking(self):
        self.profile = fixture("skipped")
        self.profile["medications"]["med"]["stock"] = 1
        stored = self.profile["history"]["daily"][NOW.date().isoformat()]["slots"]["cycle:morning"]
        stored["medications"].append(deepcopy(stored["medications"][0]))
        before = deepcopy(self.profile)
        with self.assertRaises(model.PillPalError):
            self.correct("taken")
        self.assertEqual(before, self.profile)

    def test_unknown_and_prn_entries_are_rejected(self):
        for intake_id in ("", "other:morning", "as_needed"):
            with self.assertRaises(model.PillPalError):
                model.correct_intake(self.profile, intake_id, "taken", "", now=NOW)

    def test_status_displays_use_effective_state_without_reopening_cycle(self):
        self.profile["runtime"].update(cycle_id="cycle", cycle_date=NOW.date().isoformat(),
            slots={"morning": {"status": "taken", "confirmed_at": NOW.isoformat(),
                                "items": [], "due_at": NOW.isoformat()}})
        before = deepcopy(self.profile["runtime"])
        self.correct("discarded")
        self.assertEqual("discarded", model.slot_detail(self.profile, "morning", NOW)["status"])
        self.assertEqual("discarded", model.snapshot(self.profile, NOW)["runtime"]["slots"]["morning"]["status"])
        self.assertEqual("discarded", model.current_and_upcoming(self.profile, NOW)["past"][0]["status"])
        self.assertEqual(
            {k: v for k, v in before.items() if k != "last_activity"},
            {k: v for k, v in self.profile["runtime"].items() if k != "last_activity"},
        )


def load_methods(filename, class_name, names, namespace):
    """Run production transaction methods without installing Home Assistant."""
    path = Path(__file__).parents[1] / "custom_components" / "pillpal" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    cls.body = [n for n in cls.body if getattr(n, "name", "") in names]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[class_name]


class CorrectionTransactionTests(unittest.IsolatedAsyncioTestCase):
    def manager(self):
        cls = load_methods("manager.py", "PillPalManager", {"_mutate", "async_correct_intake"},
                           {"deepcopy": deepcopy, "correct_intake": model.correct_intake,
                            "dt_util": SimpleNamespace(now=lambda: NOW)})
        manager = cls()
        manager.data = {"profile": fixture()}
        manager.profile = lambda _: manager.data["profile"]
        manager._lock = asyncio.Lock()
        manager.dispatched = []
        manager._dispatch = lambda person_id: manager.dispatched.append(person_id)
        async def save():
            await asyncio.sleep(0)
            manager.persisted = deepcopy(manager.data)
        manager._async_save = save
        return manager

    async def test_simultaneous_surfaces_only_one_correction_commits(self):
        manager = self.manager()
        results = await asyncio.gather(*[
            manager.async_correct_intake("person", "cycle:morning", "skipped", "", actor="A", actor_user_id="a")
            for _ in range(2)], return_exceptions=True)
        self.assertEqual(1, sum(isinstance(r, model.PillPalError) for r in results))
        self.assertEqual(10, manager.profile("")["medications"]["med"]["stock"])
        self.assertEqual(manager.data, manager.persisted)
        self.assertEqual(["person"], manager.dispatched)

    async def test_save_failure_rolls_back_stock_and_history(self):
        manager = self.manager()
        before = deepcopy(manager.data)
        async def fail():
            raise OSError("disk unavailable")
        manager._async_save = fail
        with self.assertRaises(OSError):
            await manager.async_correct_intake("person", "cycle:morning", "skipped", "", actor="A", actor_user_id="a")
        self.assertEqual(before, manager.data)
        self.assertEqual([], manager.dispatched)


class CorrectionAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_rejects_other_profile_before_mutation(self):
        await self.check_access(user_id="stranger", is_admin=False, admin_mode=False, assisted=False, allowed=False)

    async def test_own_profile_can_be_corrected(self):
        await self.check_access(user_id="user-test", is_admin=False, admin_mode=False, assisted=False, allowed=True)

    async def test_assistance_requires_admin_and_enabled_assistance(self):
        await self.check_access(user_id="admin", is_admin=True, admin_mode=True, assisted=True, allowed=True)
        await self.check_access(user_id="admin", is_admin=True, admin_mode=True, assisted=False, allowed=False)
        await self.check_access(user_id="stranger", is_admin=False, admin_mode=True, assisted=True, allowed=False)

    async def check_access(self, *, user_id, is_admin, admin_mode, assisted, allowed):
        namespace = {}
        cls = load_methods("manager.py", "PillPalManager", {"active_profiles", "person_id_for_user",
                           "_self_service_person_id", "manageable_profiles", "is_authorized"}, namespace)
        manager = cls()
        profile = fixture()
        profile["admin_assistance"] = assisted
        manager.data = {"profiles": {profile["person_id"]: profile}}
        calls = []
        async def record(*args, **kwargs):
            pass
        manager.async_record_action_result = record
        async def run(*args):
            calls.append(args)
            return {"status": "skipped"}
        class HAError(Exception):
            pass
        namespace.update(_manager=lambda _: manager, _run_action=run,
                         PillPalError=model.PillPalError, HomeAssistantError=HAError)
        api_path = Path(__file__).parents[1] / "custom_components/pillpal/api.py"
        tree = ast.parse(api_path.read_text(encoding="utf-8"))
        methods = [n for n in tree.body if getattr(n, "name", "") in {"_authorize", "websocket_action"}]
        for method in methods:
            method.decorator_list = []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *methods], type_ignores=[])), str(api_path), "exec"), namespace)
        errors, results = [], []
        connection = SimpleNamespace(user=SimpleNamespace(id=user_id, is_admin=is_admin, name="Test"),
                                     send_error=lambda *args: errors.append(args),
                                     send_result=lambda *args: results.append(args))
        await namespace["websocket_action"](None, connection,
            {"id": 1, "person_id": profile["person_id"], "action": "correct_intake", "admin_mode": admin_mode})
        self.assertEqual(allowed, bool(calls))
        self.assertEqual(allowed, bool(results))
        self.assertEqual(not allowed, bool(errors))


if __name__ == "__main__":
    unittest.main()
