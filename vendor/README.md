# vendor/

## `gamepolicyctl`

Apple's own `gamepolicyctl` binary (universal, arm64 + x86_64), vendored here so the backend can
force macOS Game Mode on for launched Wine games without requiring Xcode on the end user's
machine. It is **not built from source in this repo** — there is no source to build; it's a copy
of the system tool.

It must keep its original Apple code signature: it carries private
`com.apple.gamepolicyd.tool.*` entitlements needed to reach the `gamepolicyd` daemon, which an
ad-hoc `codesign --deep` would strip. `install.sh` and `buildapp.sh` both check for this after
signing the app bundle and restore this pristine copy if it was clobbered — see the comments
around `codesign` in either script.

If this binary ever needs to be updated, it has to be re-extracted from a macOS system with the
newer version, not rebuilt.
