Run the model, transaction, and authorization tests with Python:

```sh
python -m unittest discover -s tests -v
```

The statistics refresh regression test requires Node.js, the `playwright` package, and a Playwright browser:

```sh
node tests/test_statistics_refresh.cjs
node tests/test_time_plans.cjs
```

Set `PLAYWRIGHT_CHANNEL=msedge` to use an installed Microsoft Edge browser instead of Playwright Chromium. If Playwright is installed outside the project, set `NODE_PATH` to its parent package directory.

The browser test serves the production frontend locally and simulates Home Assistant websocket responses and update events. It checks both mobile and desktop layouts, including a scrolling ancestor inside a shadow root. It does not connect to a running Home Assistant instance.

The time-plan browser test covers adding, editing, disabling, removing, discarding and saving plans, first-time holiday initialization and retained values, background refresh protection, dashboard sources and overflow at 360, 390, 768 and 1440 px. Set `PILLPAL_SCREENSHOTS` to an output directory to capture the plan forms.

The same scenarios run in both a trusted localhost context and an ordinary LAN HTTP context without `crypto.randomUUID`. This catches browser API restrictions that localhost-only tests miss. Set `PILLPAL_TEST_INSECURE=1` to run just the LAN HTTP cases.

`test_time_plans.py` exercises the pure model and production manager methods (loaded without Home Assistant): selection and validation, atomic rejection, schema-12 migration, storage/import round trips, per-person isolation, calendar identity, midnight boundaries, helper/alarm combinations, restart deduplication and Europe/Berlin DST transitions.
