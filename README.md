# Pill★Pal R5.1.0 Development State 1

Pill★Pal R5 is a true Home Assistant custom integration. It replaces the global Pyscript/helper toggling from R4.1 with permanently separated person profiles, unique entities, and explicitly person-specific actions.

## Prerequisites

- Home Assistant 2026.8.0 or newer
- At least one person created under **Settings → People**
- HACS installed for convenient installation and updates

## Installation for Testing

### Option 1: Via HACS (Recommended)

1. Open **HACS** in your Home Assistant sidebar.
2. Click the **three dots** in the top right corner and select **Custom repositories**.
3. Paste the repository URL: `https://github.com/ToPa451/PillPal`
4. Set the Type to **Integration** and click **Add**.
5. Find **Pill★Pal** in HACS, click **Download**, and restart Home Assistant when prompted.

### Option 2: Manual Installation

1. Copy the `custom_components/pillpal` folder to `/config/custom_components/pillpal`. When performing a manual update, replace the existing folder completely rather than merging it with the new content. This ensures that no old files remain behind, particularly in `__pycache__`.
2. Restart Home Assistant completely.

### Setup & Initial Configuration

1. Open **Settings → Devices & Services → Add Integration → Pill★Pal**.
2. Select the people to include and specify whether to start with an inactive example medication or empty. For individuals with their own login, assistance by administrators can optionally be allowed. Individuals without a login are automatically assisted.
3. Open the personal dashboard **Pill★Pal** or, as an administrator, **Pill★Pal Assistance**. A Home Assistant restart is not required after the setup wizard; if your browser is already open, a single reload with `Ctrl+F5` may be necessary.

## Dashboard and Navigation

The personal and administrative dashboards are registered by the integration itself; a Lovelace resource or additional dashboard YAML is not required. On narrow screens, the menu button in the Pill★Pal header opens the Home Assistant sidebar.

Horizontal swiping in open page areas natively switches between Pill★Pal pages. Gestures starting inside dropdowns, input fields, buttons, tables, logs, or the navigation bar are not interpreted as page switches. Therefore, the `hass-swipe-navigation` extension is not required for Pill★Pal.

Modified medication and settings forms can be completely reset to the permanently saved state using **Discard Changes**. When switching pages or medications, Pill★Pal prompts for confirmation before losing unsaved inputs. Feedback on saving, refilling, and archiving appears directly next to the triggered action; a rejection remains visible there along with the inputs that can still be corrected.

In the Assistance Dashboard, every action is immutably bound to the person selected at the moment of clicking. Rapidly switching people cannot redirect an active action or its update to the new profile. Due intake slots appear above status and history in the mobile overview; in the log, system information is arranged above the longer event list.

## Data and Access Model

- There is exactly one main integration entry and one subentry along with a logical device for each onboarded person.
- Every write call to the backend requires a `person_id`. There is no globally selected profile.
- A logged-in user exclusively sees their linked person.
- The Admin Dashboard only lists people with admin assistance enabled and never the admin's own profile.
- If an HA person is removed, their profile and history are preserved; their medications are archived.
- A person created later can be added via **Add Entry** in the Pill★Pal integration entry.

## Data Security and Repair

Pill★Pal validates settings and stored profile, medication, cycle, and slot data before use. If a corrupted or partially migrated store is detected at startup, the integration first saves its unmodified contents separately under a quarantine ID. Only if this backup succeeds is a controlled, repaired state saved as the live store. The dashboard and person-specific log will subsequently indicate the quarantine. Supported older schemas receive a documented migration timestamp. A newer schema that is not supported by this version will be quarantined, but neither downgraded nor overwritten. Unknown store, profile, medication, and runtime fields are not silently imported.

The former `dashboard_path` and old output helpers for due status and daily completion are no longer active configuration. Upgrading to data schema 9 cleanly removes existing legacy values without triggering a quarantine or configuration error. Notifications use the internal dashboard path registered by the integration.

A temporarily missing or incomplete Home Assistant person state deletes neither the profile nor the user link; complete subsequent events update the name and link. Listeners and background tasks are bound to their respective load cycle, ensuring that old callbacks execute no further changes after a reload or shutdown.

The diagnostic export contains strictly structural, count, status, and configured-yes/no information: profile content for people, medications, entities, messages, logs, tokens, and quarantine is not exported.

Write operations are executed in a fixed revision-based sequence. Dashboard or service actions therefore only report success after successful persistent storage. If the commit fails, an error message is displayed and the unconfirmed change is rolled back in memory.

### Backup, Restore, and Complete Removal

The authoritative backup is a full Home Assistant backup. Before upgrading, rolling back to an older Pill★Pal version, or removing the integration, such a backup should be created and tested for restorability. Reloading or temporarily disabling the integration retains all application data. In contrast, confirming the **removal of the entire Pill★Pal integration entry** permanently deletes its live store and quarantine storage; recovery is then only possible from a previous Home Assistant backup. Removing only a person subentry continues to archive their application data and is not a complete deletion. Prior to archiving or full deletion, Pill★Pal cleans up all known profile-related mobile notifications. If a saved notify service is unavailable when a person is removed, the exact deletion job is preserved and retried once the service returns.

## Automation Entities and Actions

For each person, entities such as due status, next intake, adherence, reorders, and buttons for confirming, snoozing, and skipping are created. Additionally, services are available under `pillpal.*`. Services always expect a `person_id`, keeping automations unambiguous.

`pillpal.adjust_stock` adjusts a medication's stock relatively. The delta must be non-zero, at most ±10,000, and a multiple of the specified minimum step size. An overly large negative correction is capped at 0 stock in a traceable manner. Event, log, and action results contain both requested and actually applied changes; success is returned only after persistent saving.

## Medication Management and Buttons

The minimum step size applies server-side to stock, package size, regular doses, maximum doses, refills, PRN (as-needed) logging, stock correction, and the amount per button press. The PRN dialog uses plus/minus steps exclusively; "Half" at 0.5 per button press remains explicitly supported.

An expiration date can be entered from the previous year up to five years after the current year. The date field is visible only when expiration checking is enabled. Archived medications appear in the management selection only when the archive filter is active; a future open slot is restored after reactivation, whereas doses already in the past continue not to be retroactively created.

A medication-specific input button confirms the matching regular slot first for a medication usable both regularly and PRN. Only pure PRN medications are logged as PRN via this button. Attribute changes and duplicate identical button events do not trigger a log entry. A rejected press is logged and briefly displayed on the mobile device with the reason and the next regular intake time.

## Notifications

A currently registered `notify.mobile_app_…` service can be selected directly as a notification target. Pill★Pal updates this selection upon later registration or removal of a service. A valid notify service **or** the active native person-specific entity **Intake Due** suffices as a reminder channel. A warning appears only if both channels are missing for regular medication; dashboard, entity, and log use the exact same logic.

Critical reminders use the alarm default values from R4.0.17 again and list each medication with a bullet point on its own line. Successful helper/button loggings receive a non-alarming 10-second mobile confirmation. Result and rejection messages after a companion action do not have a forced short display duration. Logging directly within the personal or administrative dashboard clears the alarm, but intentionally produces no additional mobile confirmation.

After `TAKE` or `SKIP` via a companion action, the feedback response states the next open intake slot or the complete daily cycle. If a subsequent slot is already due, its newly bound actions are directly available within this response. `SKIP` additionally confirms that stock remained unchanged. Upon automatically transitioning to "Missed", Pill★Pal stops repeating and clears the exact old slot notification.

If this feedback temporarily cannot be delivered after a successful application commit, the action still remains successful. Pill★Pal stores only the missing feedback side-effect and retries it later, even across a cycle change. The intake action itself is never repeated. Sent time, retry anchor, and the visible success log of a reminder are set only after a confirmed notify call.

The action identifier is bound not only to person, cycle, and slot, but also to the specific notify device using an opaque single-use token. After a target change, actions from old or copied notifications are clearly rejected without altering intake status or stock. After `SNOOZE`, `TAKE`, `SNOOZE`, and `SKIP` remain available in the persistent, non-alarming notification response. Re-snoozing extends the existing end time and binds all actions to a new single-use token.

If an open medication schedule or time plan changes, Pill★Pal removes an already visible old reminder before resending and rotates its action tokens. Explicit snooze accepts only an actually due or already snoozed slot from the current cycle. A repeated valid `TAKE` executes without a second stock deduction, but retries clearing the old notification. A temporarily missing notify service does not consume a reminder slot and is addressed immediately upon re-registration. The complete notification state is also reconciled once directly after startup or reload; a delivery merely reserved in the old process is not falsely considered delivered.

Upon an actual change of the notify target, Pill★Pal clears the old endpoint best-effort and publishes still-active intake, stock, and expiration notices to the new target. Stock alerts are saved as delivered only after successful transmission and react to any visible detail changes.

Reorder and expiration notices have distinct, person-specific titles. A single shared icon applies to all message types; Pill★Pal assigns technical tags stably internally and therefore does not offer them in the editor. Expiration dates appear with localized dates, a dedicated line per medication, and an indication of whether the preparation expires today, in how many days, or expired how many days ago.

## Reordering, Expiration, and Medical Practice Planning

For each active regular medication, Pill★Pal calculates the expected depletion date from current stock and daily dosage. This generates the standard order date and the effective order date. If the standard date falls within a contiguous block of weekends, public holidays, or stored practice closures, Pill★Pal checks whether enough actual opening days remain before depletion; otherwise, the reminder is advanced by the configured number of open practice days.

The joint reorder window includes additional preparations whose depletion occurs shortly after an already due medication. The reorder suggestion contains package sizes, costs/copayments, a copyable order text, and a warning if costs are incomplete. The same data is available as machine-readable attributes on the person-specific **Reorders** entity and within the dashboard.

A connected holiday calendar is read ahead once daily as well as immediately following selection or state changes via `calendar.get_events`. A temporarily unsynchronized calendar is automatically fetched again without generating a diagnostic error or user warning. Technical details of successful fetches and real errors appear in the log, while the Practice page displays only compact status along with active or future closure periods; past periods are no longer displayed or calculated.

## Statistics, History, and Diagnostic Log

At the start of a daily cycle, Pill★Pal stores a domain snapshot for every scheduled slot containing cycle, target time, medication, amount, and unit at that time. Pending status changes are updated until completion; completed historical slots are not rewritten by subsequent changes to schedule, name, or unit. Older data lacking such a snapshot is supplemented exclusively from its terminal events at that time, never from today's medication plan.

Dashboard, native statistic entities, `pillpal.statistics`, and the read-only statistics WebSocket use the same model calculation. Timeframe, custom From/To dates, medication, intake time, and selected day filter metrics, heatmap, and daily list together. In addition to planned, taken, skipped, and missed, pending intakes are also reported; PRN shows log count and total amount separately.

The person-specific diagnostic log contains all events from the last rolling 48 hours without a 500-entry cap. Changes to medications and settings state field, old value, and new value clearly. Rejected actions, technical errors, and uncaught errors from owner-bound background tasks are made visible in the appropriate Pill★Pal profile in addition to the Home Assistant system log.

## Native Entities and Actions

Each person profile provides four stable slot sensors for morning, noon, evening, and night alongside due status and next intake. Cycle ID and date, due time, loggability, snooze time, completion time, as well as medications, amounts, and units stem from the same profile state across all entities. A dedicated practice status entity details the reason and next open day. The adherence entity contains a 30-day history with a heatmap and daily details; planned, taken, skipped, missed, and PRN intakes are additionally provided as separate counters.

Public actions select the Pill★Pal person profile as a device and display the four intake times with localized labels. Medication actions accept a unique visible medication name; technical IDs remain compatible for existing automations. Every action can return a machine-readable result and additionally updates the person-specific **Action Result** entity as well as the `pillpal_action_result` event with `pending`, `success`, or `error`. Single-use tokens are never published in the entity or event.

`pillpal.statistics` supplies freely filterable timeframes, medications, intake times, heatmaps, and daily details as an action response. `pillpal.recalculate` forces a person-specific recalculation and retries failed intake calendar outputs. If an intake calendar is configured, confirmed, skipped, automatically missed, and PRN intakes generate exactly one structured calendar entry with medications in individual bullet points. Removing a person subentry cleans up only its entity and device registration entries; archived Pill★Pal application data is retained.

## R4.1 Data

R4.1 and R5 must not process intakes simultaneously. Due to profile mixing observed during testing, there is no silent import. The service `pillpal.import_r410` is visibly named **Controlled Import of R4.1 Medications** and requires a validated JSON file alongside an explicit mapping of old profile IDs to new person IDs. It imports medications exclusively. Settings, interfaces, daily cycles, loggings, statistics, and logs are intentionally not imported.

## Beta Notice

This release contains the new architecture and the first complete user interface. Prior to daily production use, it should be tested on a test instance with realistic person, notification, and automation configurations. Medication decisions must not rely exclusively on Home Assistant.

Source code and installation package are byte-matched using `tools/release_package.py`. The complete automated and physical verification sequence is detailed in `RELEASE_CHECKLIST.md`; intentional deviations from R4 are documented in `CHANGE_SCOPE.md`.
