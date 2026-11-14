#!/bin/sh
# CI build of the injected dylib. No local paths, no signing.
set -eu

SDK="$(xcrun --sdk iphoneos --show-sdk-path)"
MIN="$(/usr/libexec/PlistBuddy -c 'Print :MinimumOSVersion' work/Terminal.app/Info.plist 2>/dev/null || echo 15.0)"

SRC="src/TerminalSBX.m"
[ -f "$SRC" ] || { echo "missing $SRC"; exit 1; }

# ARC only if the source has no manual retain/release calls.
ARC=""
if ! grep -qE '\b(release|retain|autorelease)\]' "$SRC"; then
  ARC="-fobjc-arc"
  echo "source looks ARC: enabling -fobjc-arc"
else
  echo "manual retain/release found: MRC build"
fi

INCS=""
for d in src include vendor Headers; do
  [ -d "$d" ] && INCS="$INCS -I$d" && echo "include dir: $d"
done

FW=""
for f in UIKit Foundation CoreGraphics CoreFoundation QuartzCore; do
  FW="$FW -framework $f"
done

mkdir -p out
set -x
xcrun --sdk iphoneos clang \
  -arch arm64 \
  -miphoneos-version-min="$MIN" \
  -isysroot "$SDK" \
  -dynamiclib \
  $ARC -O2 -Wall \
  $INCS \
  $FW \
  -Wl,-undefined,dynamic_lookup \
  -install_name @executable_path/Frameworks/TerminalSBX.dylib \
  -o out/TerminalSBX.dylib \
  "$SRC"
set +x

file out/TerminalSBX.dylib
otool -D out/TerminalSBX.dylib
echo "dylib built: $(du -h out/TerminalSBX.dylib | cut -f1)"
