# Contributing to MacNCheese

Thanks for wanting to help.

This project is a small GUI wrapper around existing tools to make running Windows games on macOS easier.

## Quick rules

Keep it simple.
If you change behavior. explain why in the pull request.
If you add a new feature. add a small note to the README.
If you add a new dependency. justify it.

## What to work on

Good first contributions.
Fixing bugs and edge cases.
Better game exe detection.
Making Intel support clearer and safer.
Improving error messages so users can self fix.
UI cleanup for Simplified UI and Dev UI modes.
Docs and wiki pages.

Please avoid.
Big refactors with no user visible gain.
New backends without a clear test case.
Anything that downloads unknown binaries without a pinned source.

## Development setup

You need.
macOS.
Xcode (for the Swift toolchain, codesign, iconutil). xcode-select --install at minimum.
Homebrew and Wine, only once you want to test actual game-launching flows end to end.

Clone.
git clone https://github.com/mont127/MacNdCheese
cd MacNdCheese

Build and install for local testing.
bash install.sh

This builds the Swift app in release mode, bundles backend_server.py alongside it, and installs
straight into /Applications/MacNdCheese Launcher.app, then codesigns it. Re-run it after every
change you want to test. It is the fast local loop, no .dmg involved.

backend_server.py only uses the Python standard library, so there is nothing to pip install to
build or run the app. requirements.txt (PyQt6, pyobjc, pypresence) belongs to
MacNdCheeseARM-OLDER.py, a legacy pre-SwiftUI prototype kept for reference. It is not part of
the current build.

To produce a distributable .dmg instead (this is what CI runs).
bash buildapp.sh arm64
Or x86_64, or universal.

Do not run installer.sh directly during development. install.sh and buildapp.sh both copy it
into the built app's Contents/Resources. It is meant to be run BY the app at runtime, to install
Wine, DXVK, Mesa and so on for end users, not by you as a dev script.

See ARCHITECTURE.md for how the Swift UI and the Python backend talk to each other, and for a
map of which file owns what.

## Testing

There is no automated test suite yet. Adding CI is tracked separately in #103. Until then you
must test manually.

Before you open a pull request. verify at least these flows.
App opens and UI renders.
Simplified UI toggle works.
Dev UI toggle works.
Install tools works or fails with a readable log.
Install wine works or fails with a readable log.
DXVK build 64 works.
DXVK build 32 works.
Mesa install works and creates ~/mesa/x64 with expected dlls.
Prefix init works.
Steam install works.
Steam launch works.
Scan games finds installed titles.
Launch game works for at least one title.
Log output is readable and saved where expected.

If your change touches a specific backend. test that backend with at least one real game.

## Pull request checklist

Before submitting.
Keep changes focused.
Run the app and verify the main flows above.
Make sure the log output still shows what the app is doing.
Do not add secrets or personal paths to commits.
Update docs if behavior changes.

In your PR description include.
What changed.
Why it changed.
How to test it.
Your macOS version and whether Apple Silicon or Intel.

## Style

Keep code readable over clever.
Prefer small functions.
Prefer clear names.
Keep UI text short and practical.
Avoid unnecessary abstractions.

## Security and safety

Do not add hidden network calls.
Any download must be optional and clearly visible to users.
Use pinned URLs and prefer official release sources.
If you add a new download. document it in the README.

## Reporting issues

If you are opening an issue. include.
Mac model.
Apple Silicon or Intel.
macOS version.
What you tried.
Log output from the app.
Wine log file path if available.
DXVK logs if using DXVK.

## License

By contributing. you agree your contributions are licensed under the same license as the project.
