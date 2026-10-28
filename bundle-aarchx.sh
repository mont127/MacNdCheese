#!/bin/bash
# usage: bundle-aarchx.sh <app Resources dir> <target arch: arm64|x86_64|universal>
#
# Puts AArchX into the app at Resources/aarchx/ocerz. AArchX is the experimental
# x86-64 -> arm64 translator a bottle can pick instead of Rosetta (Bottle settings ->
# x86 translation); Rosetta stays the default for every bottle. Source code and license:
# https://github.com/mont127/AArchX (LGPL-2.1-or-later; its LICENSE ships beside the binary).
#
# Where the binary comes from, first match wins:
#   MNC_AARCHX_BIN  a built ocerz binary, copied as is
#   MNC_AARCHX_SRC  an AArchX checkout, built here (this is what the release workflows do)
#   ../AArchX/ocerz a sibling dev checkout's existing build, for the local install.sh loop
# With MNC_AARCHX_SRC, MNC_AARCHX_FEX names a FEXCore build (AArchX's tools/fex-build.sh) to
# link in, which gives ocerz -fex (Bottle settings -> FEX translator); without it, or when the
# build with it fails, ocerz is built without -fex and the backend greys the setting out.
# None of them = a build without AArchX. That is fine: the backend then reports it as
# unavailable, the setting is greyed out and every bottle runs on Rosetta as before.
#
# AArchX's native mode (Bottle settings -> AArchX mode) loads two more things from
# runtime/ beside the binary: the API databases `make apis` generates from the SDK, and
# the x86 C++ runtime tools/build_guest_cxx.sh builds. They are built with the binary
# from MNC_AARCHX_SRC and copied from any source that has them; a build without them
# ships cache mode only, and the backend greys native out.
set -eu

RES="$1"
ARCH="${2:-arm64}"

if [ "$ARCH" = "x86_64" ]; then
    echo "AArchX: skipped for an x86_64 build (it translates for Apple Silicon only)"
    exit 0
fi

BIN=""
SRC=""
if [ -n "${MNC_AARCHX_BIN:-}" ]; then
    BIN="$MNC_AARCHX_BIN"
    SRC="$(cd "$(dirname "$BIN")" && pwd)"
elif [ -n "${MNC_AARCHX_SRC:-}" ]; then
    SRC="$MNC_AARCHX_SRC"
    # The app supports macOS 12, so the translator must at least load there; AArchX's
    # own Makefile targets the machine it is built on.
    echo "AArchX: building $SRC ($(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo 'not a git checkout'))"
    if [ -n "${MNC_AARCHX_FEX:-}" ] && ! make -C "$SRC" -j"$(sysctl -n hw.ncpu)" ocerz FEX="$MNC_AARCHX_FEX" \
            ARCHFLAGS="-arch arm64 -mmacosx-version-min=12.0" >"$SRC/.mnc-build.log" 2>&1; then
        tail -30 "$SRC/.mnc-build.log" >&2
        echo "::warning::AArchX failed to build with FEXCore; building it without -fex"
        rm -f "$SRC/src/fex.o" "$SRC/src/main.o"
        MNC_AARCHX_FEX=""
    fi
    if [ -z "${MNC_AARCHX_FEX:-}" ] && ! make -C "$SRC" -j"$(sysctl -n hw.ncpu)" ocerz \
            ARCHFLAGS="-arch arm64 -mmacosx-version-min=12.0" >"$SRC/.mnc-build.log" 2>&1; then
        # Experimental and optional: a translator that fails to compile on this toolchain
        # must not cost the release. Say so loudly and ship the launcher without it.
        tail -30 "$SRC/.mnc-build.log" >&2
        echo "::warning::AArchX failed to build; this build ships without it (Rosetta only)"
        exit 0
    fi
    BIN="$SRC/ocerz"
    # Native mode's runtime. Optional like the binary: a failure leaves cache mode only.
    echo "AArchX: generating the native-mode API databases"
    if ! make -C "$SRC" apis >"$SRC/.mnc-apis.log" 2>&1; then
        tail -20 "$SRC/.mnc-apis.log" >&2
        echo "::warning::AArchX's API databases failed to build; this build ships cache mode only"
    fi
    echo "AArchX: building the guest C++ runtime"
    if ! bash "$SRC/tools/build_guest_cxx.sh" >"$SRC/.mnc-guestcxx.log" 2>&1; then
        tail -20 "$SRC/.mnc-guestcxx.log" >&2
        echo "::warning::AArchX's guest C++ runtime failed to build; this build ships cache mode only"
    fi
elif [ -x "../AArchX/ocerz" ]; then
    SRC="$(cd ../AArchX && pwd)"
    BIN="$SRC/ocerz"
fi

if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
    echo "AArchX: not bundled (set MNC_AARCHX_BIN or MNC_AARCHX_SRC to include it)"
    exit 0
fi
if ! lipo -archs "$BIN" 2>/dev/null | grep -qw arm64; then
    echo "AArchX: $BIN has no arm64 slice, not bundling it" >&2
    exit 1
fi

mkdir -p "$RES/aarchx"
cp "$BIN" "$RES/aarchx/ocerz"
chmod +x "$RES/aarchx/ocerz"
# Since AArchX 0.9.1 ocerz links libSystem's parts weakly and, on a macOS where one of
# its imports is missing, starts ocerz-umbrella from beside itself instead; `make ocerz`
# builds both. Without it that fallback can only tell the user to reinstall AArchX.
rm -f "$RES/aarchx/ocerz-umbrella"
if [ -x "$(dirname "$BIN")/ocerz-umbrella" ]; then
    cp "$(dirname "$BIN")/ocerz-umbrella" "$RES/aarchx/ocerz-umbrella"
    chmod +x "$RES/aarchx/ocerz-umbrella"
fi
if [ -f "$SRC/LICENSE" ]; then
    cp "$SRC/LICENSE" "$RES/aarchx/LICENSE"
fi
echo "AArchX: bundled $BIN -> $RES/aarchx/ocerz"
rm -rf "$RES/aarchx/runtime"
if [ -d "$SRC/runtime/apis/macos" ] && [ -f "$SRC/runtime/guest/usr/lib/libc++.1.dylib" ]; then
    mkdir -p "$RES/aarchx/runtime"
    cp -R "$SRC/runtime/apis" "$SRC/runtime/guest" "$RES/aarchx/runtime/"
    echo "AArchX: bundled native mode's runtime ($(du -sh "$RES/aarchx/runtime" | cut -f1))"
else
    echo "AArchX: no native-mode runtime beside $BIN; this build offers cache mode only"
fi
