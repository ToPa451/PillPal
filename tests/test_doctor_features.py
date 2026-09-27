"""Regression tests for doctor-specific planning and statistics."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "pillpal"
sys.path.insert(0, str(INTEGRATION_DIR))

import model  # noqa: E402


NOW = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


def _profile() -> dict:
    profile = model.new_profile(
        "person.test",
        "Testperson",
        "person.test",
        "user-test",
        False,
        NOW,
        create_example=False,
    )
    profile["settings"].update(
        {
            "order_warning_days": 10,
            "practice_lead_days": 5,
            "low_stock_window_days": 7,
        }
    )
    return profile


def _doctor(doctor_id: str, name: str, closures: list[dict] | None = None) -> dict:
    return model.normalize_doctor(
        {
            "id": doctor_id,
            "name": name,
            "practice_closures": closures or [],
        },
        now=NOW,
    )


def _medication(
    medication_id: str,
    name: str,
    *,
    doctor_id: str = "",
    stock: int = 30,
    as_needed: bool = False,
) -> dict:
    return model.normalize_medication(
        {
            "id": medication_id,
            "name": name,
            "doctor_id": doctor_id,
            "unit_singular": "Tablette",
            "unit_plural": "Tabletten",
            "step": 1,
            "pack_size": 30,
            "stock": stock,
            "doses": {
                "morning": 0 if as_needed else 1,
                "noon": 0,
                "evening": 0,
                "night": 0,
            },
            "as_needed_allowed": as_needed,
            "single_max": 1 if as_needed else 0,
            "daily_max": 3 if as_needed else 0,
        },
        now=NOW,
    )


class DoctorOrderPlanningTests(unittest.TestCase):
    def test_closure_only_changes_assigned_doctor_projection(self) -> None:
        profile = _profile()
        profile["doctors"] = {
            "doctor-a": _doctor(
                "doctor-a",
                "Praxis A",
                [{"start": "2026-10-08", "end": "2026-10-12"}],
            )
        }
        profile["medications"] = {
            "assigned": _medication(
                "assigned", "Mit Arzt", doctor_id="doctor-a", stock=17
            ),
            "unassigned": _medication("unassigned", "Ohne Arzt", stock=17),
        }

        projections = {
            item["medication_id"]: item for item in model.order_plan(profile, NOW)["projections"]
        }

        self.assertEqual("practice_closure_advanced", projections["assigned"]["reason"])
        self.assertLess(
            projections["assigned"]["effective_order_date"],
            projections["assigned"]["normal_order_date"],
        )
        self.assertEqual("normal_threshold", projections["unassigned"]["reason"])
        self.assertEqual(
            projections["unassigned"]["effective_order_date"],
            projections["unassigned"]["normal_order_date"],
        )

    def test_co_order_window_never_crosses_doctors(self) -> None:
        profile = _profile()
        profile["doctors"] = {
            "doctor-a": _doctor(
                "doctor-a",
                "Praxis A",
                [{"start": "2026-10-08", "end": "2026-10-12"}],
            ),
            "doctor-b": _doctor("doctor-b", "Praxis B"),
        }
        profile["medications"] = {
            "due-a": _medication("due-a", "Fällig A", doctor_id="doctor-a", stock=17),
            "later-a": _medication(
                "later-a", "Später A", doctor_id="doctor-a", stock=24
            ),
            "later-b": _medication(
                "later-b", "Später B", doctor_id="doctor-b", stock=24
            ),
        }

        result = model.order_plan(profile, NOW)
        item_ids = {item["medication_id"] for item in result["items"]}

        self.assertIn("due-a", item_ids)
        self.assertIn("later-a", item_ids)
        self.assertNotIn("later-b", item_ids)
        self.assertEqual(
            {"doctor-a"},
            {item["doctor_id"] for item in result["items"]},
        )

    def test_practice_status_lists_each_doctor_independently(self) -> None:
        profile = _profile()
        profile["doctors"] = {
            "doctor-a": _doctor(
                "doctor-a",
                "Praxis A",
                [{"start": "2026-10-01", "end": "2026-10-02"}],
            ),
            "doctor-b": _doctor("doctor-b", "Praxis B"),
        }

        result = model.practice_status(profile, NOW)
        statuses = {item["doctor_id"]: item for item in result["doctors"]}

        self.assertFalse(statuses["doctor-a"]["open"])
        self.assertEqual("practice_closure", statuses["doctor-a"]["reason"])
        self.assertTrue(statuses["doctor-b"]["open"])
        self.assertEqual("doctor_statuses", result["reason"])

    def test_doctor_with_assigned_medication_cannot_be_deleted(self) -> None:
        profile = _profile()
        profile["doctors"] = {"doctor-a": _doctor("doctor-a", "Praxis A")}
        profile["medications"] = {
            "med-a": _medication("med-a", "Medikament A", doctor_id="doctor-a")
        }
        store = model.new_store()
        store["profiles"][profile["person_id"]] = profile

        with self.assertRaisesRegex(model.PillPalError, "Medikament A"):
            model.delete_doctor(store, profile["person_id"], "doctor-a", now=NOW)


class DoctorStatisticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = _profile()
        self.profile["doctors"] = {
            "doctor-a": _doctor("doctor-a", "Praxis A"),
            "doctor-b": _doctor("doctor-b", "Praxis B"),
        }
        self.profile["medications"] = {
            "med-a": _medication("med-a", "Medikament A", doctor_id="doctor-a"),
            "med-b": _medication("med-b", "Medikament B", doctor_id="doctor-b"),
            "prn": _medication("prn", "Bedarf ohne Arzt", as_needed=True),
        }
        self.profile["history"] = {
            "daily": {
                "2026-10-01": {
                    "slots": {
                        "cycle-1:morning": {
                            "slot_id": "cycle-1:morning",
                            "cycle_id": "cycle-1",
                            "cycle_date": "2026-10-01",
                            "slot": "morning",
                            "due_at": "2026-10-01T08:00:00+00:00",
                            "status": "taken",
                            "completed_at": "2026-10-01T08:05:00+00:00",
                            "medications": [
                                {
                                    "medication_id": "med-a",
                                    "name": "Medikament A",
                                    "quantity": 1,
                                    "doctor_id": "doctor-a",
                                    "doctor_name": "Praxis A",
                                },
                                {
                                    "medication_id": "med-b",
                                    "name": "Medikament B",
                                    "quantity": 1,
                                    "doctor_id": "doctor-b",
                                    "doctor_name": "Praxis B",
                                },
                            ],
                        }
                    }
                }
            }
        }
        self.profile["events"] = [
            {
                "id": "prn-1",
                "type": "as_needed",
                "date": "2026-10-01",
                "timestamp": "2026-10-01T12:00:00+00:00",
                "medication_id": "prn",
                "medication_name": "Bedarf ohne Arzt",
                "doctor_id": "",
                "doctor_name": "",
                "quantity": 1,
            }
        ]

    def test_doctor_filter_keeps_matching_medication_snapshot(self) -> None:
        result = model.statistics(self.profile, 1, NOW, doctor_id="doctor-a")

        self.assertEqual(1, result["planned"])
        self.assertEqual(1, result["taken"])
        self.assertEqual(0, result["as_needed_bookings"])
        event = result["day_details"]["events"][0]
        self.assertEqual(["med-a"], [item["medication_id"] for item in event["medications"]])
        self.assertEqual(
            {"doctor-a", "doctor-b"},
            {item["doctor_id"] for item in result["available_doctors"]},
        )

    def test_unassigned_doctor_filter_includes_unassigned_prn_only(self) -> None:
        result = model.statistics(self.profile, 1, NOW, doctor_id="__none__")

        self.assertEqual(0, result["planned"])
        self.assertEqual(1, result["as_needed_bookings"])
        self.assertEqual(1, result["as_needed_quantity"])
        self.assertEqual("prn", result["day_details"]["events"][0]["medication_id"])

    def test_medication_and_doctor_filters_must_match_same_snapshot(self) -> None:
        result = model.statistics(
            self.profile,
            1,
            NOW,
            medication_id="med-a",
            doctor_id="doctor-b",
        )

        self.assertEqual(0, result["planned"])
        self.assertEqual([], result["day_details"]["events"])


if __name__ == "__main__":
    unittest.main()
