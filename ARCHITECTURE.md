# Architecture

MacNCheese is two processes wearing one app icon.

## The two halves

- **`Sources/`** — a SwiftUI app (`Package.swift`, macOS 12+). This is everything the user sees:
  game grid, settings, onboarding, the Epic/Steam library views, the Discord-fed Game Showcase
  tab, App Intents/Siri support (macOS 14+ only — see `Sources/AppIntents/` and
  `Sources/CompatShims.swift` for the cross-version shims the rest of the UI leans on).
- **`backend_server.py`** — a single long-running Python process, standard library only. It does
  the actual work: creating Wine prefixes, installing DXVK/VKD3D/DXMT/GPTK, detecting and
  launching games, managing bottles, driving the Steam/Epic integrations, forcing Game Mode, etc.
  (Mesa install still exists in `installer.sh` for old configs, but it's been dropped from backend
  auto-detection — `_mesa_available()` is hardcoded `False` — and isn't offered as a launch backend.)

The Swift app launches `backend_server.py` as a subprocess and talks to it over a line-delimited
JSON-RPC protocol on stdin/stdout — one JSON object per line each way (see the docstring at the
top of `backend_server.py`). `Sources/BackendClient.swift` is the only file on the Swift side that
speaks this protocol; every view calls through it rather than shelling out on its own.

If you're adding a new backend capability: add a `cmd` handler in `backend_server.py`, then call
it from `BackendClient.swift`. If you're changing something the user sees and it doesn't need new
backend logic, you're almost certainly only touching files under `Sources/`. The `COMMANDS` dict
near the bottom of `backend_server.py` is a full index of every handler name — grep there first if
you're looking for where a specific action is implemented.

## Where things live (Swift side)

| File | Owns |
|---|---|
| `MacNCheeseApp.swift` | App entry point, top-level state |
| `ContentView.swift` / `SidebarView.swift` | Main window shell and navigation |
| `GameGridView.swift` / `GameDetailView.swift` | Game library grid and per-game detail |
| `GameLaunchSheet.swift` | Launch-time backend picker/options |
| `CreateBottleSheet.swift` / `OpenExeSheet.swift` | Bottle creation, running an arbitrary .exe |
| `SettingsSheet.swift` | App settings — the largest view, mostly independent sub-sections |
| `StoreSheet.swift` / `GameShowcaseView.swift` | In-app store tab and the Discord-fed showcase (see `discord-showcase-bot/`) |
| `EpicLibraryView.swift` / `EpicLandingView.swift` / `EpicLogo.swift` | Epic Games integration |
| `OnboardingView.swift` | First-run flow |
| `BackendClient.swift` | JSON-RPC bridge to `backend_server.py` — see above |
| `Models.swift` | Shared data types decoded from the backend's JSON responses |
| `Theme.swift` | Shared colors/fonts/styling constants |
| `InstallRunner.swift` | Drives the install/repair flows (Wine, DXVK, DXMT, etc.) from the UI side |
| `Localization.swift` | UI string translations |
| `AppIntents/` | Siri/Shortcuts support |

(Not exhaustive — the rest of `Sources/*.swift` follows the same one-file-per-screen pattern and
is straightforward to match up by filename.)

## Build & install scripts

Three scripts, three different jobs — they are not interchangeable:

- **`install.sh`** — local dev loop. Builds the Swift release binary, bundles
  `backend_server.py` + `installer.sh` + `vendor/gamepolicyctl`, installs to
  `/Applications/MacNdCheese Launcher.app`, and codesigns it. Run this to test a change.
- **`buildapp.sh [arm64|x86_64|universal]`** — release builder. Same bundling, but outputs into
  `build/MacNCheese.app` and packages a distributable `.dmg`. Also extracts App Intents metadata
  (Siri/Shortcuts), same as `install.sh`. Two workflows call this directly (no more separate
  `.github/scripts/build-macos.sh` copy — that used to drift out of sync with this script, which
  is how the official prebuilt DMGs ended up missing the Epic logo and Game Mode support that
  source builds via `install.sh` had; see #81):
  - `.github/workflows/build-universal.yml` — `workflow_dispatch`, builds arm64 and x86_64 as two
    separate DMGs.
  - `.github/workflows/nightly.yml` — runs on every push to `main`, builds one true universal
    (`buildapp.sh universal`, arm64+x86_64 in one binary via `lipo`) DMG and publishes it as a
    GitHub prerelease. See #104.

  `.github/workflows/ci.yml` is a separate, lighter workflow: a plain `swift build` (no icon, no
  signing, no `.dmg`) plus syntax checks on `backend_server.py` and the shell scripts, run on
  every PR to fail fast on build breaks.
- **`installer.sh`** — *not* a script you run yourself. It's copied into the built app's
  `Contents/Resources/` and is what the app itself shells out to at runtime, to install Wine,
  DXVK, VKD3D, DXMT, etc. onto an end user's machine (plus Mesa, kept only for old configs — see
  above).

## `vendor/gamepolicyctl`

A compiled, Apple-signed `gamepolicyctl` binary, vendored (not built from source in this repo) so
the backend can force macOS Game Mode on for launched Wine games without requiring Xcode on the
end user's machine. It must keep its **original** Apple signature — it carries private
`com.apple.gamepolicyd.tool.*` entitlements that an ad-hoc re-sign would strip. Both `install.sh`
and `buildapp.sh` check for this after codesigning and restore the pristine binary if it was
touched. Don't try to rebuild or re-sign it yourself; see `vendor/README.md`.

## `macndcheese` — the CLI

A standalone Python script (symlinked as `mnc`) that speaks the exact same JSON-RPC protocol as
`Sources/BackendClient.swift`, over its own `backend_server.py` subprocess — a second, independent
client of the backend, not a wrapper around the Swift app. Run with no arguments for an
interactive shell (keeps one backend alive across commands, so `use <bottle>` sticks); run with a
subcommand for one-shot/scriptable use (`macndcheese bottles list`, `macndcheese engines status`).
`macndcheese raw <cmd> key=value ..` calls any backend command directly, which is how it can always
technically reach every command — but that is not the same as a *friendly, discoverable* command
for it. See CONTRIBUTING.md's "CLI parity" section: because this and `BackendClient.swift` are two
independent clients of the same backend, they drift unless a change to one is deliberately mirrored
in the other, and CI only backstops the mechanically-detectable half of that.

## `discord-showcase-bot/`

Not game-launching logic — this syncs the project's Discord showcase forum channel into
`showcase.json` on the `showcase-data` branch, which `Sources/GameShowcaseView.swift` reads to
render the in-app Game Showcase tab. It runs as a scheduled GitHub Actions job
(`.github/workflows/showcase-sync.yml`), not something you run locally. See
`discord-showcase-bot/README.md`.

## `MacNdCheeseARM-OLDER.py`

A legacy, pre-SwiftUI prototype of the app built with PyQt6 (matches `requirements.txt`). Kept
for reference; it is not part of the current build and nothing in `install.sh`, `buildapp.sh`,
or CI touches it.
