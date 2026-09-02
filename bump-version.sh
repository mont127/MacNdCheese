#!/bin/bash
# Bump the app version in Sources/Info.plist, wich is the single source of truth for
# both CFBundleShortVersionString and CFBundleVersion (they are always kept equal).
#
# Numbering is an ODOMETER: every component past the major is a single digit that
# rolls over into the one to its left instead of growing past 9.
#
#   small commit:  10.8.8 -> 10.8.9
#                  10.8.9 -> 10.9.0     (patch hit 9, so it wraps and minor moves up)
#                  10.9.9 -> 11.0.0     (minor hit 9 too, so major moves up)
#
# The major is NOT capped -- it just counts (10 -> 11 -> 12 ...).
#
# Usage:
#   ./bump-version.sh              small commit, the default: bump patch w/ rollover
#   ./bump-version.sh minor        deliberate feature bump: minor +1 (rolls), patch 0
#   ./bump-version.sh major        deliberate big bump: major +1, minor and patch 0
#   ./bump-version.sh 11.2.0       set an exact version
#   ./bump-version.sh --dry-run    print what would happen, change nothing
#
# Prints "OLD -> NEW" and leaves the edit unstaged so you can review it.
set -euo pipefail

cd "$(dirname "$0")"
PLIST=Sources/Info.plist
[ -f "$PLIST" ] || { echo "no $PLIST here" >&2; exit 1; }

DRY=0
LEVEL=patch
EXPLICIT=""
for arg in "$@"; do
    case "$arg" in
        --dry-run)          DRY=1 ;;
        patch|minor|major)  LEVEL="$arg" ;;
        [0-9]*.[0-9]*.[0-9]*) EXPLICIT="$arg" ;;
        *) echo "unknown argument: $arg" >&2; exit 1 ;;
    esac
done

# Current version = the string right after the CFBundleShortVersionString key.
CUR="$(awk '/<key>CFBundleShortVersionString<\/key>/ { getline; \
             gsub(/.*<string>|<\/string>.*/, ""); print; exit }' "$PLIST")"
case "$CUR" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) echo "could not read a X.Y.Z version out of $PLIST (got '$CUR')" >&2; exit 1 ;;
esac

if [ -n "$EXPLICIT" ]; then
    NEW="$EXPLICIT"
else
    MAJOR="${CUR%%.*}"
    REST="${CUR#*.}"
    MINOR="${REST%%.*}"
    PATCH="${REST#*.}"

    case "$LEVEL" in
        patch)
            PATCH=$((PATCH + 1))
            # the rollover: a digit never goes past 9, it wraps and carrys left
            if [ "$PATCH" -gt 9 ]; then
                PATCH=0
                MINOR=$((MINOR + 1))
            fi
            if [ "$MINOR" -gt 9 ]; then
                MINOR=0
                MAJOR=$((MAJOR + 1))
            fi
            ;;
        minor)
            PATCH=0
            MINOR=$((MINOR + 1))
            if [ "$MINOR" -gt 9 ]; then
                MINOR=0
                MAJOR=$((MAJOR + 1))
            fi
            ;;
        major)
            PATCH=0
            MINOR=0
            MAJOR=$((MAJOR + 1))
            ;;
    esac
    NEW="$MAJOR.$MINOR.$PATCH"
fi

echo "$CUR -> $NEW"
[ "$DRY" = 1 ] && exit 0
[ "$CUR" = "$NEW" ] && { echo "alredy at $NEW, nothing to do"; exit 0; }

# Rewrite only the <string> lines that directly follow the two version keys, so the
# rest of the plist (and its formating) is untouched.
awk -v new="$NEW" '
    prev ~ /<key>(CFBundleShortVersionString|CFBundleVersion)<\/key>/ {
        sub(/<string>[^<]*<\/string>/, "<string>" new "</string>")
    }
    { print; prev = $0 }
' "$PLIST" > "$PLIST.tmp" && mv "$PLIST.tmp" "$PLIST"

# Belt and braces: both keys must actually read back as the new version.
for key in CFBundleShortVersionString CFBundleVersion; do
    got="$(awk -v k="$key" '$0 ~ "<key>" k "</key>" { getline; \
            gsub(/.*<string>|<\/string>.*/, ""); print; exit }' "$PLIST")"
    [ "$got" = "$NEW" ] || { echo "failed to set $key (reads '$got')" >&2; exit 1; }
done
echo "updated $PLIST (CFBundleShortVersionString + CFBundleVersion)"
