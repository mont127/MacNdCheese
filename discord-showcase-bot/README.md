# discord-showcase-bot

Not part of the app itself — this is the data pipeline behind the in-app **Game Showcase** tab
(`Sources/GameShowcaseView.swift`).

`showcase_sync.py` reads the project's Discord showcase forum channel via the Discord REST API,
mirrors screenshots (Discord's CDN URLs expire after ~24h), and writes `showcase.json` + `media/`.
It runs as a scheduled job, `.github/workflows/showcase-sync.yml`, which commits the output to
the `showcase-data` branch. The shipped app fetches that branch directly from
`raw.githubusercontent.com`; nothing here ships inside the app bundle.

You don't need to run this to build or test MacNCheese locally. See the script's own docstring
for the full input/output contract (env vars, `showcase.json` schema, rate limits).
