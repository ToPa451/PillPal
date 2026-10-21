Run the model, transaction, and authorization tests with Python:

```sh
python -m unittest discover -s tests -v
```

The statistics refresh regression test requires Node.js, the `playwright` package, and a Playwright browser:

```sh
node tests/test_statistics_refresh.cjs
```

Set `PLAYWRIGHT_CHANNEL=msedge` to use an installed Microsoft Edge browser instead of Playwright Chromium. If Playwright is installed outside the project, set `NODE_PATH` to its parent package directory.

The browser test serves the production frontend locally and simulates Home Assistant websocket responses and update events. It checks both mobile and desktop layouts, including a scrolling ancestor inside a shadow root. It does not connect to a running Home Assistant instance.
