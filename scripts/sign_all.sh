#!/usr/bin/env bash
set -euo pipefail

APP="${1:?usage: sign_all.sh <path to .app>}"
APP_GROUP="${APP_GROUP_ID:-group.com.jasonquiles.terminal}"
BUNDLE_ID="${BUNDLE_ID:-com.jasonquiles.terminal}"
TEAM_ID="${TEAM_ID:-}"

if [ -z "$TEAM_ID" ]; then
  echo "error: TEAM_ID is empty (security find-identity -v -p codesigning)" >&2
  exit 1
fi
for tool in ldid codesign plutil; do
  if ! command -v "$tool" >/dev/null; then
    echo "error: missing $tool" >&2
    exit 1
  fi
done
if [ ! -d "$APP" ]; then
  echo "error: $APP not found" >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

sed -e "s/TEAMID/$TEAM_ID/g" \
    -e "s|group\.com\.jasonquiles\.terminal|$APP_GROUP|g" \
    scripts/ent.plist > "$WORK/ent.plist"

/usr/libexec/PlistBuddy -c 'Print' "$WORK/ent.plist" >&2

if ! grep -qF "$APP_GROUP" "$WORK/ent.plist"; then
  echo "error: $APP_GROUP missing from ent.plist" >&2
  exit 1
fi
if ! grep -qF "$TEAM_ID" "$WORK/ent.plist"; then
  echo "error: TEAM_ID did not substitute" >&2
  exit 1
fi

EXEC="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Info.plist")"
BIN="$APP/$EXEC"
if [ ! -f "$BIN" ]; then
  echo "error: $BIN not found" >&2
  exit 1
fi

/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $BUNDLE_ID" "$APP/Info.plist"

while IFS= read -r -d '' item; do
  echo "signing $item"
  ldid -S "$WORK/ent.plist" "$item" || exit 1
done < <(find "$APP" \( -name '*.dylib' -o -name '*.framework' \) -print0)

ldid -S "$WORK/ent.plist" "$BIN"
codesign --force --sign - --timestamp=none --entitlements "$WORK/ent.plist" "$APP"

echo "--- signed entitlements ---" >&2
codesign -d --entitlements :- "$APP" 2>/dev/null > "$WORK/verify.plist" || true
cat "$WORK/verify.plist" >&2

if grep -qF "$APP_GROUP" "$WORK/verify.plist"; then
  echo "group entitlement verified"
else
  echo "error: $APP_GROUP not present in signed entitlements" >&2
  exit 1
fi
