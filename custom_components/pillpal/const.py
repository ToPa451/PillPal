"""Constants for the Pill★Pal integration."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

DOMAIN = "pillpal"
NAME = "Pill★Pal"
VERSION = "5.1.0-dev.9"
MIN_HA_VERSION = "2026.8.0"

PLATFORMS = ["sensor", "binary_sensor", "button"]
PERSON_SUBENTRY_TYPE = "person"

CONF_PERSON_ID = "person_id"
CONF_PERSON_ENTITY_ID = "person_entity_id"
CONF_PERSON_NAME = "person_name"
CONF_USER_ID = "user_id"
CONF_ADMIN_ASSISTANCE = "admin_assistance"
CONF_INCLUDED_PERSON_IDS = "included_person_ids"
CONF_ASSISTED_PERSON_IDS = "assisted_person_ids"
CONF_CREATE_EXAMPLE = "create_example_medication"

STORAGE_KEY = "pillpal.profiles"
STORAGE_VERSION = 1
DATA_SCHEMA_VERSION = 9

SIGNAL_DATA_UPDATED = "pillpal_data_updated"
SIGNAL_PROFILE_UPDATED = "pillpal_profile_updated_{}"

PANEL_URL = "pillpal"
ADMIN_PANEL_URL = "pillpal-admin"
PANEL_COMPONENT = "pillpal-panel-5100-21"
STATIC_URL = "/pillpal_static_5100_21"
FRONTEND_DIR = Path(__file__).parent / "frontend"

SCHEDULER_INTERVAL = timedelta(seconds=30)
DIAGNOSTIC_RETENTION_HOURS = 48

SLOTS = ("morning", "noon", "evening", "night")
SLOT_LABELS = {
    "morning": "Morgens",
    "noon": "Mittags",
    "evening": "Abends",
    "night": "Zur Nacht",
}

DEFAULT_SETTINGS = {
    "notify_target": "",
    "confirm_helper": "",
    "awake_helper": "",
    "next_alarm_entity": "",
    "holiday_calendar": "",
    "intake_calendar": "",
    "early_minutes": 30,
    "morning_delay_minutes": 15,
    "fallback_wake_time": "08:00",
    "snooze_minutes": 15,
    "repeat_minutes": 5,
    "order_warning_days": 10,
    "practice_lead_days": 5,
    "low_stock_window_days": 7,
    "expiry_warning_days": 14,
    "alarm_window_from": "04:00",
    "alarm_window_to": "11:00",
    "lunch_window_from": "12:00",
    "lunch_window_to": "14:00",
    "bedtime_offset_hours": 8.25,
    "evening_before_bedtime_hours": 2,
    "notification_title": "Einnahme fällig",
    "notification_intro": "Folgende Medikamente müssen genommen werden.",
    "action_take": "Sammeleinnahme",
    "action_snooze": "Snooze",
    "action_skip": "Überspringen",
    "notification_sticky": True,
    "notification_persistent": False,
    "notification_alert_once": False,
    "notification_critical": True,
    "notification_channel": "alarm_stream",
    "notification_group": "Medikation",
    "notification_tag": "Medikation",
    "notification_icon": "mdi:medication-outline",
    "order_notification_title": "Nachbestellung",
    "order_notification_tag": "Medikamentenbestellung",
    "order_notification_icon": "mdi:medication-outline",
    "expiry_notification_title": "Haltbarkeit prüfen",
    "expiry_notification_tag": "MedikamentenMHD",
    "expiry_notification_icon": "mdi:medication-outline",
    "notification_color": "",
    "notification_vibration_pattern": "",
    "notification_led_color": "",
    "notification_sound": "alarm.caf",
    "notification_importance": "high",
    "notification_priority": "high",
    "notification_visibility": "private",
    "notification_ttl": 0,
    "notification_timeout": 0,
    "ios_interruption_level": "critical",
    "ios_volume": 1,
    "ios_badge": 0,
    "ios_presentation_options": "alert, badge, sound",
    "currency": "€",
    "show_archived": False,
    "statistics_show_archived": False,
    "times": {
        "morning": "08:00",
        "noon": "13:00",
        "evening": "20:00",
        "night": "23:00",
    },
}

SERVICE_CONFIRM_SLOT = "confirm_slot"
SERVICE_SNOOZE_SLOT = "snooze_slot"
SERVICE_SKIP_SLOT = "skip_slot"
SERVICE_BOOK_AS_NEEDED = "book_as_needed"
SERVICE_SAVE_MEDICATION = "save_medication"
SERVICE_ARCHIVE_MEDICATION = "archive_medication"
SERVICE_REACTIVATE_MEDICATION = "reactivate_medication"
SERVICE_REFILL = "refill"
SERVICE_ADJUST_STOCK = "adjust_stock"
SERVICE_UPDATE_SETTINGS = "update_settings"
SERVICE_UPDATE_PRACTICE_CLOSURES = "update_practice_closures"
SERVICE_ACKNOWLEDGE_ERRORS = "acknowledge_errors"
SERVICE_RECALCULATE = "recalculate"
SERVICE_STATISTICS = "statistics"
SERVICE_IMPORT_R410 = "import_r410"
