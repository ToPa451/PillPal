"""Time plans: migration, isolation, immutable cycles and production lifecycle."""
from copy import deepcopy
from collections.abc import Mapping
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "pillpal"))
import model
from test_intake_corrections import load_methods

BERLIN = ZoneInfo("Europe/Berlin")
FRIDAY = datetime(2026, 10, 23, 8, tzinfo=BERLIN)


def fixture():
    profile = model.new_profile("person.test", "Test", "person.test", "user", False, FRIDAY, create_example=False)
    profile["medications"] = {"med": model.normalize_medication({
        "id": "med", "name": "Testmedikament", "unit_singular": "Tablette", "unit_plural": "Tabletten",
        "step": 1, "pack_size": 100, "stock": 100, "doses": {slot: 1 for slot in model.SLOTS},
        "as_needed_allowed": False, "single_max": 0, "daily_max": 0,
    }, now=FRIDAY)}
    return profile


def weekend():
    plan = model.standard_time_plan()
    plan.update(id="weekend", name="Wochenende", weekdays=[5, 6])
    plan["normal"].update(wake_time="09:30", evening="21:00", night="00:30")
    plan["holiday"] = {**plan["normal"], "wake_time": "10:30"}
    return plan


class TimePlanTests(unittest.TestCase):
    def test_migration_exact_idempotent_without_second_truth(self):
        old = {"fallback_wake_time": "07:23", "morning_delay_minutes": 27,
               "lunch_window_from": "11:30", "lunch_window_to": "15:10",
               "times": {"morning": "06:00", "noon": "16:00", "evening": "22:00", "night": "00:45"}}
        settings = model.normalize_settings(old)
        values = settings["time_plans"][0]["normal"]
        self.assertEqual("07:23", values["wake_time"])
        self.assertEqual(27, values["morning_delay_minutes"])
        self.assertEqual("22:00", values["evening"])
        self.assertEqual("00:45", values["night"])
        self.assertEqual("11:30", values["lunch_window_from"])
        self.assertFalse(model.LEGACY_PLAN_KEYS & settings.keys())
        self.assertEqual(settings, model.normalize_settings(settings))

    def test_all_days_and_independent_holiday_override(self):
        settings = model.normalize_settings({})
        standard = settings["time_plans"][0]
        standard["holiday_enabled"] = True
        standard["holiday"]["wake_time"] = "09:00"
        for day in range(7):
            self.assertEqual("standard", model.resolve_time_plan(settings, date(2026, 10, 19) + timedelta(days=day), is_holiday=False)["plan_id"])
        settings["time_plans"].append(weekend())
        self.assertEqual("09:00", model.resolve_time_plan(settings, date(2026, 10, 19), is_holiday=True)["values"]["wake_time"])
        chosen = model.resolve_time_plan(settings, date(2026, 10, 24), is_holiday=True)
        self.assertEqual("09:30", chosen["values"]["wake_time"])
        settings["time_plans"][1]["holiday_enabled"] = True
        self.assertEqual("10:30", model.resolve_time_plan(settings, date(2026, 10, 24), is_holiday=True)["values"]["wake_time"])
        chosen["values"]["wake_time"] = "00:00"
        self.assertEqual("09:30", settings["time_plans"][1]["normal"]["wake_time"])

    def test_single_weekday_disable_and_conflict(self):
        settings = model.normalize_settings({})
        plan = weekend()
        plan["weekdays"] = [2]
        settings["time_plans"].append(plan)
        self.assertEqual("weekend", model.resolve_time_plan(settings, date(2026, 10, 21), is_holiday=False)["plan_id"])
        other = deepcopy(plan)
        other.update(id="other", name="Anderer", enabled=False)
        settings["time_plans"].append(other)
        model.normalize_settings(settings)
        other["enabled"] = True
        with self.assertRaisesRegex(model.PillPalError, "Mittwoch.*Wochenende.*Anderer"):
            model.normalize_settings(settings)
        settings["time_plans"] = [settings["time_plans"][0], {**plan, "enabled": False}]
        self.assertEqual("standard", model.resolve_time_plan(settings, date(2026, 10, 21), is_holiday=False)["plan_id"])

    def test_invalid_settings_rejected_atomically(self):
        profile = fixture()
        model.start_cycle(profile, FRIDAY, started_by="fallback_time")
        store = {"profiles": {profile["person_id"]: profile}}
        changes = [{"id": ""}, {"id": "standard"}, {"name": " "}, {"enabled": 1},
                   {"weekdays": []}, {"weekdays": [True]}, {"weekdays": [7]}, {"weekdays": [5, 5]}]
        for change in changes:
            with self.subTest(change=change):
                before = deepcopy(store)
                plans = [model.standard_time_plan(), {**weekend(), **change}]
                with self.assertRaises(model.PillPalError):
                    model.update_settings(store, profile["person_id"], {"time_plans": plans}, now=FRIDAY)
                self.assertEqual(before, store)
        for change in [{"wake_time": "9:00"}, {"night": "24:00"}, {"morning_delay_minutes": 121},
                       {"morning_delay_minutes": True}, {"lunch_window_from": "15:00"}]:
            plans = [model.standard_time_plan()]
            plans[0]["normal"].update(change)
            with self.subTest(change=change), self.assertRaises(model.PillPalError):
                model.update_settings(store, profile["person_id"], {"time_plans": plans}, now=FRIDAY)

    def test_standard_cannot_be_removed_disabled_or_restricted(self):
        for plans in [[], [weekend()], [{**model.standard_time_plan(), "enabled": False}],
                      [{**model.standard_time_plan(), "weekdays": [0]}]]:
            with self.subTest(plans=plans), self.assertRaises(model.PillPalError):
                model.normalize_settings({"time_plans": plans})

    def test_calendar_identity_and_current_day_only(self):
        profile = fixture()
        standard = profile["settings"]["time_plans"][0]
        standard["holiday_enabled"] = True
        profile["settings"]["holiday_calendar"] = "calendar.holidays"
        profile["runtime"].update(holiday_calendar_active=True,
            holiday_calendar_forecast={"entity_id": "calendar.previous", "closed_dates": ["2026-10-23"]})
        self.assertFalse(model.cycle_time_plan(profile, FRIDAY.date(), today=date(2026, 10, 24))["is_holiday"])
        self.assertTrue(model.cycle_time_plan(profile, FRIDAY.date(), today=FRIDAY.date())["is_holiday"])
        profile["runtime"]["holiday_calendar_forecast"]["entity_id"] = "calendar.holidays"
        self.assertTrue(model.cycle_time_plan(profile, FRIDAY.date())["is_holiday"])
        profile["settings"]["holiday_calendar"] = ""
        self.assertFalse(model.cycle_time_plan(profile, FRIDAY.date(), today=FRIDAY.date())["is_holiday"])

    def test_cycle_survives_plan_changes_deletion_calendar_and_round_trip(self):
        profile = fixture()
        profile["settings"]["time_plans"].append(weekend())
        saturday = FRIDAY + timedelta(days=1, hours=2)
        model.start_cycle(profile, saturday, started_by="awake_helper")
        runtime = deepcopy(profile["runtime"])
        store = {**model.new_store(), "profiles": {profile["person_id"]: profile}}
        model.update_settings(store, profile["person_id"], {"time_plans": [model.standard_time_plan()], "holiday_calendar": "calendar.new"}, now=saturday)
        self.assertEqual(runtime["time_plan_snapshot"], profile["runtime"]["time_plan_snapshot"])
        self.assertEqual(runtime["slots"], profile["runtime"]["slots"])
        safe, report = model.validate_store_payload(json.loads(json.dumps(store)), now=saturday)
        restored = safe["profiles"][profile["person_id"]]
        self.assertEqual(runtime["time_plan_snapshot"], restored["runtime"]["time_plan_snapshot"], report)
        self.assertEqual(runtime["slots"], restored["runtime"]["slots"])
        schedule = model.calculate_cycle_schedule(restored, saturday)
        self.assertEqual(date(2026, 10, 25), schedule["night"].date())
        self.assertEqual(30, schedule["night"].minute)

    def test_schema12_active_migration_keeps_slots_history_and_tokens(self):
        profile = fixture()
        model.start_cycle(profile, FRIDAY, started_by="fallback_time")
        model.confirm_slot({"profiles": {profile["person_id"]: profile}}, profile["person_id"], "morning", now=FRIDAY + timedelta(minutes=15))
        slots = deepcopy(profile["runtime"]["slots"])
        history = deepcopy(profile["history"])
        stock = profile["medications"]["med"]["stock"]
        profile["runtime"].pop("time_plan_snapshot")
        profile["settings"] = {"fallback_wake_time": "08:00", "times": {"evening": "20:10", "night": "23:10"}}
        raw = {**model.new_store(), "schema": 12, "profiles": {profile["person_id"]: profile}}
        safe, report = model.validate_store_payload(raw, now=FRIDAY)
        restored = safe["profiles"][profile["person_id"]]
        self.assertTrue(report["schema_migrated"])
        self.assertEqual(13, safe["schema"])
        self.assertEqual(slots, restored["runtime"]["slots"])
        self.assertEqual(history, restored["history"])
        self.assertEqual(stock, restored["medications"]["med"]["stock"])
        self.assertEqual("20:10", restored["runtime"]["time_plan_snapshot"]["values"]["evening"])

    def test_repair_preserves_valid_standard_and_other_plans(self):
        standard = model.standard_time_plan()
        standard["normal"]["wake_time"] = "06:45"
        settings, reasons = model.repair_settings({"time_plans": [weekend(), {"id": "bad"}, standard]})
        self.assertEqual(["standard", "weekend"], [plan["id"] for plan in settings["time_plans"]])
        self.assertEqual("06:45", settings["time_plans"][0]["normal"]["wake_time"])
        self.assertTrue(reasons)

    def test_profiles_isolated_and_old_write_interface_maps_standard(self):
        first, second = fixture(), fixture()
        store = {"profiles": {first["person_id"]: first}}
        model.update_settings(store, first["person_id"], {"fallback_wake_time": "07:00", "times": {"night": "00:10"}}, now=FRIDAY)
        self.assertEqual("08:00", second["settings"]["time_plans"][0]["normal"]["wake_time"])
        self.assertEqual("07:00", first["settings"]["time_plans"][0]["normal"]["wake_time"])
        self.assertNotIn("times", first["settings"])
        with self.assertRaisesRegex(model.PillPalError, "berechnet"):
            model.update_settings(store, first["person_id"], {"times": {"morning": "09:00"}}, now=FRIDAY)

    def test_alarm_validity_timezone_and_snapshot_parameters(self):
        profile = fixture()
        profile["settings"]["time_plans"][0]["normal"]["morning_delay_minutes"] = 30
        model.start_cycle(profile, FRIDAY, started_by="awake_helper")
        for alarm, source in [("2026-10-24T07:00:00+02:00", "Smartphone-Wecker"),
                              ("2026-10-24T05:00:00+00:00", "Smartphone-Wecker"),
                              ("2026-10-24T12:00:00+02:00", "Zeitplan"),
                              ("2026-10-25T07:00:00+02:00", "Zeitplan"), ("invalid", "Zeitplan"), (None, "Zeitplan")]:
            profile["runtime"]["next_alarm_at"] = alarm
            with self.subTest(alarm=alarm):
                schedule = model.calculate_cycle_schedule(profile, FRIDAY)
                self.assertEqual(source, schedule["source"])
                self.assertEqual(FRIDAY + timedelta(minutes=30), schedule["morning"])

    def test_invalid_negative_evening_rejected_before_start(self):
        profile = fixture()
        before = deepcopy(profile)
        with self.assertRaisesRegex(model.PillPalError, "vor dem Zyklusstart"):
            model.start_cycle(profile, FRIDAY.replace(hour=22), started_by="awake_helper")
        self.assertEqual(before, profile)

    def test_late_calendar_data_waits_for_next_cycle(self):
        profile = fixture()
        profile["settings"]["holiday_calendar"] = "calendar.holidays"
        standard = profile["settings"]["time_plans"][0]
        standard["holiday_enabled"] = True
        standard["holiday"]["evening"] = "21:00"
        model.start_cycle(profile, FRIDAY, started_by="fallback_time")
        before = deepcopy(profile["runtime"]["slots"])
        profile["runtime"]["holiday_calendar_forecast"].update(entity_id="calendar.holidays", closed_dates=["2026-10-23", "2026-10-24"])
        model.rebuild_schedule(profile, FRIDAY + timedelta(hours=1))
        self.assertEqual(before, profile["runtime"]["slots"])
        self.assertFalse(profile["runtime"]["time_plan_snapshot"]["holiday_applied"])
        model.end_cycle(profile, FRIDAY + timedelta(hours=16))
        model.start_cycle(profile, FRIDAY + timedelta(days=1), started_by="fallback_time")
        self.assertTrue(profile["runtime"]["time_plan_snapshot"]["holiday_applied"])

    def test_alarm_only_changes_future_slots_and_protects_due_and_completed(self):
        profile = fixture()
        model.start_cycle(profile, FRIDAY, started_by="fallback_time")
        before = deepcopy(profile["runtime"]["slots"])
        profile["runtime"]["next_alarm_at"] = "2026-10-24T07:00:00+02:00"
        model.rebuild_schedule(profile, FRIDAY.replace(hour=18))
        for slot in ("morning", "noon"):
            self.assertEqual(before[slot], profile["runtime"]["slots"][slot])
        self.assertNotEqual(before["evening"]["due_at"], profile["runtime"]["slots"]["evening"]["due_at"])
        evening = deepcopy(profile["runtime"]["slots"]["evening"])
        profile["runtime"]["next_alarm_at"] = "2026-10-24T08:00:00+02:00"
        model.rebuild_schedule(profile, FRIDAY.replace(hour=21))
        self.assertEqual(evening, profile["runtime"]["slots"]["evening"])
        profile["runtime"]["slots"]["night"]["status"] = "taken"
        night = deepcopy(profile["runtime"]["slots"]["night"])
        profile["runtime"]["next_alarm_at"] = "2026-10-24T09:00:00+02:00"
        model.rebuild_schedule(profile, FRIDAY.replace(hour=22))
        self.assertEqual(night, profile["runtime"]["slots"]["night"])

    def test_migrated_due_times_stay_frozen_until_external_alarm_changes(self):
        profile = fixture()
        model.start_cycle(profile, FRIDAY, started_by="fallback_time")
        before = deepcopy(profile["runtime"]["slots"])
        profile["runtime"].pop("time_plan_snapshot")
        profile["settings"]["time_plans"][0]["normal"]["evening"] = "21:00"
        model._migrate_runtime(profile)
        model.rebuild_schedule(profile, FRIDAY.replace(hour=9))
        self.assertEqual(before, profile["runtime"]["slots"])
        safe, _ = model.validate_store_payload({**model.new_store(), "profiles": {profile["person_id"]: profile}}, now=FRIDAY)
        restored = safe["profiles"][profile["person_id"]]
        model.rebuild_schedule(restored, FRIDAY.replace(hour=9))
        self.assertEqual(before, restored["runtime"]["slots"])

    def test_invalid_legacy_window_uses_repair_diagnostics(self):
        settings, reasons = model.repair_settings({"fallback_wake_time": "06:30", "lunch_window_from": "15:00"})
        self.assertEqual("06:30", settings["time_plans"][0]["normal"]["wake_time"])
        self.assertEqual("12:00", settings["time_plans"][0]["normal"]["lunch_window_from"])
        self.assertTrue(reasons)


class LifecycleTests(unittest.TestCase):
    def manager(self, state=None, alarm=None):
        namespace = {"datetime": datetime, "date": date, "timedelta": timedelta, "timezone": timezone,
                     "dt_util": SimpleNamespace(as_local=lambda dt: dt.astimezone(BERLIN), parse_datetime=lambda value: datetime.fromisoformat(value) if value else None)}
        namespace.update({name: getattr(model, name) for name in ("cycle_is_active", "cycle_time_plan", "calculate_cycle_schedule", "start_cycle", "end_cycle")})
        cls = load_methods("manager.py", "PillPalManager", {"_state_available", "_reconcile_cycle_lifecycle", "_apply_dynamic_context", "_calendar_event_dates"}, namespace)
        manager = cls()
        states = {}
        if state is not None:
            states["input_boolean.awake"] = SimpleNamespace(state=state, attributes={}, last_changed=FRIDAY)
        if alarm:
            states["sensor.alarm"] = SimpleNamespace(state=alarm, attributes={})
        manager.hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
        return manager, states

    def test_all_helper_alarm_combinations(self):
        for helper in (None, "on"):
            for alarm in (None, "2026-10-24T07:00:00+02:00"):
                with self.subTest(helper=helper, alarm=alarm):
                    manager, _ = self.manager(helper, alarm)
                    profile = fixture()
                    profile["settings"].update(awake_helper="input_boolean.awake", next_alarm_entity="sensor.alarm")
                    self.assertTrue(manager._reconcile_cycle_lifecycle(profile, FRIDAY, allow_helper_recovery=True))
                    self.assertEqual("awake_helper" if helper else "fallback_time", profile["runtime"]["cycle_started_by"])
                    self.assertEqual("Smartphone-Wecker" if alarm else "Zeitplan", profile["runtime"]["schedule_source"])
                    self.assertFalse(manager._reconcile_cycle_lifecycle(profile, FRIDAY + timedelta(hours=1), allow_helper_recovery=True))

    def test_helper_off_blocks_and_unavailable_allows_fallback(self):
        for state, starts in [("off", False), ("unknown", True), ("unavailable", True), (None, True)]:
            manager, _ = self.manager(state)
            profile = fixture()
            profile["settings"]["awake_helper"] = "input_boolean.awake"
            self.assertEqual(starts, manager._reconcile_cycle_lifecycle(profile, FRIDAY))

    def test_new_day_uses_own_wake_time_without_duplicate_starts(self):
        manager, _ = self.manager()
        profile = fixture()
        profile["settings"]["time_plans"].append(weekend())
        self.assertTrue(manager._reconcile_cycle_lifecycle(profile, FRIDAY))
        friday_id = profile["runtime"]["cycle_id"]
        self.assertFalse(manager._reconcile_cycle_lifecycle(profile, FRIDAY + timedelta(days=1, hours=1)))
        self.assertEqual(friday_id, profile["runtime"]["cycle_id"])
        self.assertTrue(manager._reconcile_cycle_lifecycle(profile, FRIDAY + timedelta(days=1, hours=2)))
        self.assertTrue(profile["runtime"]["cycle_started_at"].startswith("2026-10-24T09:30"))
        self.assertEqual("weekend", profile["runtime"]["time_plan_snapshot"]["plan_id"])
        self.assertFalse(manager._reconcile_cycle_lifecycle(profile, FRIDAY + timedelta(days=1, hours=3)))
        self.assertTrue(manager._reconcile_cycle_lifecycle(profile, FRIDAY + timedelta(days=3)))
        self.assertEqual("standard", profile["runtime"]["time_plan_snapshot"]["plan_id"])
        self.assertTrue(profile["runtime"]["cycle_started_at"].startswith("2026-10-26T08:00"))

    def test_recovered_helper_does_not_restart_completed_day(self):
        manager, states = self.manager("unavailable")
        profile = fixture()
        profile["settings"]["awake_helper"] = "input_boolean.awake"
        manager._reconcile_cycle_lifecycle(profile, FRIDAY)
        cycle_id = profile["runtime"]["cycle_id"]
        states["input_boolean.awake"].state = "off"
        self.assertTrue(manager._reconcile_cycle_lifecycle(profile, FRIDAY + timedelta(hours=1)))
        states["input_boolean.awake"].state = "on"
        self.assertFalse(manager._reconcile_cycle_lifecycle(profile, FRIDAY + timedelta(hours=2), allow_helper_recovery=True))
        self.assertEqual(cycle_id, profile["runtime"]["cycle_id"])

    def test_restart_before_after_wake_and_no_backfill(self):
        manager, _ = self.manager()
        profile = fixture()
        self.assertFalse(manager._reconcile_cycle_lifecycle(profile, FRIDAY.replace(hour=7)))
        self.assertTrue(manager._reconcile_cycle_lifecycle(profile, FRIDAY.replace(hour=12)))
        self.assertEqual(FRIDAY.isoformat(), profile["runtime"]["cycle_started_at"])
        self.assertTrue(manager._reconcile_cycle_lifecycle(profile, FRIDAY + timedelta(days=4, hours=4)))
        self.assertEqual("2026-10-27", profile["runtime"]["cycle_date"])
        self.assertEqual(2, sum(event["type"] == "cycle_started" for event in profile["events"]))

    def test_dst_nonexistent_and_repeated_wake_time(self):
        for day in (date(2026, 3, 29), date(2026, 10, 25)):
            manager, _ = self.manager()
            profile = fixture()
            profile["settings"]["time_plans"][0]["normal"]["wake_time"] = "02:30"
            now = datetime.combine(day, datetime.min.time(), BERLIN).replace(hour=4)
            self.assertTrue(manager._reconcile_cycle_lifecycle(profile, now))
            started = datetime.fromisoformat(profile["runtime"]["cycle_started_at"])
            self.assertEqual(3 if day.month == 3 else 2, started.hour)
            self.assertFalse(manager._reconcile_cycle_lifecycle(profile, now + timedelta(hours=1)))

    def test_restart_during_second_dst_hour_catches_first_occurrence(self):
        manager, _ = self.manager()
        profile = fixture()
        profile["settings"]["time_plans"][0]["normal"]["wake_time"] = "02:30"
        now = datetime(2026, 10, 25, 2, 15, tzinfo=BERLIN, fold=1)
        self.assertTrue(manager._reconcile_cycle_lifecycle(profile, now))
        self.assertEqual("2026-10-25T02:30:00+02:00", profile["runtime"]["cycle_started_at"])
        self.assertFalse(manager._reconcile_cycle_lifecycle(profile, now.replace(minute=45)))

    def test_morning_delay_counts_elapsed_minutes_across_dst_gap(self):
        profile = fixture()
        start = datetime(2026, 3, 29, 1, 55, tzinfo=BERLIN)
        model.start_cycle(profile, start, started_by="awake_helper")
        schedule = model.calculate_cycle_schedule(profile, start)
        self.assertEqual("2026-03-29T03:10:00+02:00", schedule["morning"].isoformat())

    def test_calendar_multiday_exclusive_end_and_timed_events(self):
        manager, _ = self.manager()
        self.assertEqual(["2026-10-23", "2026-10-24"], manager._calendar_event_dates({"start": "2026-10-23", "end": "2026-10-25"}))
        self.assertEqual(["2026-10-23"], manager._calendar_event_dates({"start": "2026-10-23T10:00:00+02:00", "end": "2026-10-23T11:00:00+02:00"}))


class CalendarForecastTests(unittest.IsolatedAsyncioTestCase):
    def manager(self, profile, response=None, error=None):
        namespace = {"Mapping": Mapping, "date": date, "timedelta": timedelta, "deepcopy": deepcopy,
                     "PillPalError": model.PillPalError, "append_log": model.append_log,
                     "dt_util": SimpleNamespace(now=lambda: FRIDAY),
                     "_empty_holiday_forecast": lambda: model._new_runtime()["holiday_calendar_forecast"],
                     "_format_german_date": str}
        cls = load_methods("manager.py", "PillPalManager", {"_async_refresh_holiday_forecast", "_calendar_event_dates", "_holiday_calendar_retry_pending", "_remove_transient_calendar_log_entries"}, namespace)
        manager = cls()
        manager._lock = asyncio.Lock()
        manager.profile = lambda _: profile
        manager._dispatch = lambda _: None
        async def save():
            pass
        async def call(*args, **kwargs):
            if error:
                raise RuntimeError(error)
            return response
        manager._async_save = save
        manager.hass = SimpleNamespace(services=SimpleNamespace(async_call=call))
        return manager

    async def test_known_dates_survive_failure_for_same_calendar(self):
        profile = fixture()
        profile["settings"]["holiday_calendar"] = "calendar.holidays"
        profile["runtime"]["holiday_calendar_forecast"].update(entity_id="calendar.holidays", closed_dates=["2026-10-23"])
        manager = self.manager(profile, error="Network failure")
        await manager._async_refresh_holiday_forecast(profile["person_id"], force=True)
        self.assertTrue(model.cycle_time_plan(profile, FRIDAY.date())["is_holiday"])
        self.assertFalse(model.cycle_time_plan(profile, date(2026, 10, 24))["is_holiday"])
        self.assertEqual("Network failure", profile["runtime"]["holiday_calendar_forecast"]["last_error"])

    async def test_calendar_switch_never_uses_previous_dates_on_failure(self):
        profile = fixture()
        profile["settings"]["holiday_calendar"] = "calendar.new"
        profile["runtime"]["holiday_calendar_forecast"].update(entity_id="calendar.old", closed_dates=["2026-10-23"])
        manager = self.manager(profile, error="Network failure")
        await manager._async_refresh_holiday_forecast(profile["person_id"], force=True)
        self.assertFalse(model.cycle_time_plan(profile, FRIDAY.date())["is_holiday"])
        self.assertEqual([], profile["runtime"]["holiday_calendar_forecast"]["closed_dates"])

    async def test_success_and_missing_calendar(self):
        profile = fixture()
        profile["settings"]["holiday_calendar"] = "calendar.holidays"
        manager = self.manager(profile, response={"calendar.holidays": {"events": [{"start": "2026-10-23", "end": "2026-10-25"}]}})
        await manager._async_refresh_holiday_forecast(profile["person_id"], force=True)
        self.assertTrue(model.cycle_time_plan(profile, date(2026, 10, 24))["is_holiday"])
        profile["settings"]["holiday_calendar"] = ""
        await manager._async_refresh_holiday_forecast(profile["person_id"], force=True)
        self.assertEqual([], profile["runtime"]["holiday_calendar_forecast"]["closed_dates"])


if __name__ == "__main__":
    unittest.main()
