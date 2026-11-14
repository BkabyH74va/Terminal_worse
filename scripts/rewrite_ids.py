#!/usr/bin/env python3
"""
Rewrite the previous build's bundle id and app-group id to yours.

All of these must agree or worse_query bails at -1 before it reaches
containermanager:

  * Info.plist, commandDictionary.plist, any other bundle plist
  * .com.apple.mobile_container_manager.metadata.plist inside containers
  * Mach-O string tables

Plists are rewritten structurally.  Mach-O files get a byte-level replace
that only fires when the new string fits the old slot; the remainder is
NUL-padded so no offsets move.  A too-long replacement warns instead of
truncating.

Source values: --old-* or OLD_GROUP / OLD_BUNDLE.
Destination values: --new-* or NEW_GROUP / NEW_BUNDLE, defaulting to
group.com.jasonquiles.terminal and com.jasonquiles.terminal.

Re-running is safe: migrated files no longer match and are skipped.
"""

import argparse
import os
import plistlib
import shutil
import sys

DEFAULT_NEW_GROUP = "group.com.jasonquiles.terminal"
DEFAULT_NEW_BUNDLE = "com.jasonquiles.terminal"

BUNDLE_KEYS = ("CFBundleIdentifier", "AppGroupIdentifier", "WKAppBundleIdentifier")
ENTITLEMENT_BUNDLE_KEYS = ("application-identifier",)
ENTITLEMENT_GROUP_KEYS = ("com.apple.security.application-groups",)
GROUP_KEYS = ("AppGroupIdentifier",)

MACHO_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe")
SKIP_DIRS = {"_CodeSignature", "SC_Info", "Frameworks"}
SKIP_NAMES = {"embedded.mobileprovision"}

warnings = []
changed = []


def warn(message):
    warnings.append(message)
    print(f"  warn: {message}", file=sys.stderr)


def is_macho(path):
    try:
        with open(path, "rb") as handle:
            return handle.read(4) in MACHO_MAGICS
    except OSError:
        return False


def patch_macho(path, old, new):
    if not old or not new or old == new:
        return 0
    old_bytes = old.encode("utf-8")
    new_bytes = new.encode("utf-8")
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as error:
        warn(f"{path}: {error}")
        return 0
    if old_bytes not in data:
        return 0
    if len(new_bytes) > len(old_bytes):
        warn(f"{path}: '{old}' -> '{new}' too long ({len(new_bytes)} > "
             f"{len(old_bytes)}), skipped")
        return 0
    count = data.count(old_bytes)
    data = data.replace(old_bytes, new_bytes + b"\x00" * (len(old_bytes) - len(new_bytes)))
    try:
        with open(path, "wb") as handle:
            handle.write(data)
    except OSError as error:
        warn(f"{path}: {error}")
        return 0
    changed.append(f"{path} ({count} string occurrence(s))")
    return count


def rewrite_plist(path, old_group, new_group, old_bundle, new_bundle):
    try:
        with open(path, "rb") as handle:
            plist = plistlib.load(handle)
    except Exception as error:  # noqa: BLE001
        warn(f"{path}: unreadable plist ({error})")
        return 0
    if not isinstance(plist, dict):
        return 0

    edits = 0
    for key in BUNDLE_KEYS:
        value = plist.get(key)
        if isinstance(value, str) and old_bundle and value == old_bundle:
            plist[key] = new_bundle
            edits += 1

    for key in GROUP_KEYS:
        value = plist.get(key)
        if isinstance(value, str) and old_group and value == old_group:
            plist[key] = new_group
            edits += 1

    for key in ENTITLEMENT_GROUP_KEYS:
        groups = plist.get(key)
        if isinstance(groups, list):
            rebuilt = []
            for entry in groups:
                if isinstance(entry, str) and old_group and entry == old_group:
                    rebuilt.append(new_group)
                    edits += 1
                elif entry not in rebuilt:
                    rebuilt.append(entry)
            if new_group not in rebuilt:
                rebuilt.append(new_group)
                edits += 1
            plist[key] = rebuilt

    for key in ENTITLEMENT_BUNDLE_KEYS:
        value = plist.get(key)
        if isinstance(value, str) and old_bundle and old_bundle in value:
            plist[key] = value.replace(old_bundle, new_bundle)
            edits += 1

    if edits == 0:
        return 0
    try:
        with open(path, "wb") as handle:
            plistlib.dump(plist, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)
    except Exception as error:  # noqa: BLE001
        warn(f"{path}: write failed ({error})")
        return 0
    changed.append(f"{path} ({edits} key(s))")
    return edits


def walk(app, old_group, new_group, old_bundle, new_bundle):
    plist_edits = binary_edits = 0
    for root, dirs, files in os.walk(app):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name in SKIP_NAMES:
                continue
            path = os.path.join(root, name)
            if name.endswith(".plist"):
                plist_edits += rewrite_plist(path, old_group, new_group,
                                             old_bundle, new_bundle)
                continue
            if is_macho(path):
                if old_bundle:
                    binary_edits += patch_macho(path, old_bundle, new_bundle)
                if old_group:
                    binary_edits += patch_macho(path, old_group, new_group)
    return plist_edits, binary_edits


def main():
    parser = argparse.ArgumentParser(description="Rewrite ids in an .app bundle.")
    parser.add_argument("--app", required=True)
    parser.add_argument("--old-group", default=os.environ.get("OLD_GROUP", "").strip())
    parser.add_argument("--new-group",
                        default=os.environ.get("NEW_GROUP", DEFAULT_NEW_GROUP).strip())
    parser.add_argument("--old-bundle", default=os.environ.get("OLD_BUNDLE", "").strip())
    parser.add_argument("--new-bundle",
                        default=os.environ.get("NEW_BUNDLE", DEFAULT_NEW_BUNDLE).strip())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    app = os.path.abspath(args.app)
    if not os.path.isdir(app):
        print(f"error: {app} is not a directory", file=sys.stderr)
        return 2
    if not os.path.isfile(os.path.join(app, "Info.plist")):
        print(f"error: {app}/Info.plist missing", file=sys.stderr)
        return 2

    old_group, new_group = args.old_group.strip(), args.new_group.strip()
    old_bundle, new_bundle = args.old_bundle.strip(), args.new_bundle.strip()

    if not old_group and not old_bundle:
        print("error: nothing to rewrite", file=sys.stderr)
        return 2
    if old_group and old_group == new_group:
        print(f"error: old group equals new group ({new_group})", file=sys.stderr)
        return 2
    if old_bundle and old_bundle == new_bundle:
        print(f"error: old bundle equals new bundle ({new_bundle})", file=sys.stderr)
        return 2

    print(f"app       : {app}")
    print(f"bundle id : {old_bundle or '(unchanged)'} -> {new_bundle}")
    print(f"app group : {old_group or '(unchanged)'} -> {new_group}")

    if not args.dry_run:
        backup = app.rstrip("/") + ".orig"
        if not os.path.exists(backup):
            shutil.copytree(app, backup, symlinks=True)
            print(f"backup    : {backup}")

    plist_edits, binary_edits = walk(app, old_group, new_group, old_bundle, new_bundle)
    print(f"\nplist keys : {plist_edits}\nbinary hits: {binary_edits}")
    for entry in changed:
        print(f"  {entry}")
    if plist_edits == 0 and binary_edits == 0:
        print("nothing matched; wrong --old-* or already migrated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
