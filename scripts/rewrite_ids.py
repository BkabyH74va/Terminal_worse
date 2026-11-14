#!/usr/bin/env python3
"""
Rewrite the bundle id and app-group id inside an .app.

Old values are read from the app itself unless --old-* / OLD_* are given,
so no configuration is required.  New values are forced in unconditionally:
a key that exists is overwritten, a key that is missing is added.

Plists are rewritten structurally.  Mach-O files get a byte-level replace
that only fires when the new string fits the old slot; the remainder is
NUL-padded so no offsets move.  A too-long replacement warns instead of
truncating.
"""

import argparse
import os
import plistlib
import shutil
import sys

DEFAULT_NEW_GROUP = "group.com.jasonquiles.terminal"
DEFAULT_NEW_BUNDLE = "com.jq.terminal"

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


def read_key(plist_path, key):
    try:
        with open(plist_path, "rb") as handle:
            plist = plistlib.load(handle)
    except Exception:  # noqa: BLE001
        return ""
    value = plist.get(key) if isinstance(plist, dict) else None
    return value if isinstance(value, str) else ""


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
        warn(f"{path}: '{old}' -> '{new}' too long "
             f"({len(new_bytes)} > {len(old_bytes)}), skipped")
        return 0
    count = data.count(old_bytes)
    data = data.replace(old_bytes,
                        new_bytes + b"\x00" * (len(old_bytes) - len(new_bytes)))
    try:
        with open(path, "wb") as handle:
            handle.write(data)
    except OSError as error:
        warn(f"{path}: {error}")
        return 0
    changed.append(f"{path} ({count} string occurrence(s))")
    return count


def rewrite_plist(path, old_group, new_group, old_bundle, new_bundle, force):
    try:
        with open(path, "rb") as handle:
            plist = plistlib.load(handle)
    except Exception as error:  # noqa: BLE001
        warn(f"{path}: unreadable plist ({error})")
        return 0
    if not isinstance(plist, dict):
        return 0

    edits = 0

    def put(key, new_value, old_value):
        nonlocal edits
        current = plist.get(key)
        if force:
            if current != new_value:
                plist[key] = new_value
                edits += 1
            return
        if isinstance(current, str) and old_value and current == old_value:
            plist[key] = new_value
            edits += 1

    for key in BUNDLE_KEYS:
        put(key, new_bundle, old_bundle)
    for key in GROUP_KEYS:
        put(key, new_group, old_group)

    for key in ENTITLEMENT_GROUP_KEYS:
        groups = plist.get(key)
        if not isinstance(groups, list):
            continue
        rebuilt = []
        for entry in groups:
            if isinstance(entry, str) and entry == old_group:
                if new_group not in rebuilt:
                    rebuilt.append(new_group)
                    edits += 1
            elif entry not in rebuilt:
                rebuilt.append(entry)
        if force and new_group not in rebuilt:
            rebuilt.append(new_group)
            edits += 1
        if rebuilt != groups:
            plist[key] = rebuilt
            edits += 1

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
                                             old_bundle, new_bundle, True)
                continue
            if is_macho(path):
                binary_edits += patch_macho(path, old_bundle, new_bundle)
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
    info = os.path.join(app, "Info.plist")
    if not os.path.isdir(app):
        print(f"error: {app} is not a directory", file=sys.stderr)
        return 2
    if not os.path.isfile(info):
        print(f"error: {info} missing", file=sys.stderr)
        return 2

    new_group, new_bundle = args.new_group.strip(), args.new_bundle.strip()
    if not new_group or not new_bundle:
        print("error: --new-bundle and --new-group are required", file=sys.stderr)
        return 2

    old_bundle = args.old_bundle.strip()
    old_group = args.old_group.strip()

    # Auto-detect when not supplied: the app already knows its own identity.
    if not old_bundle:
        old_bundle = read_key(info, "CFBundleIdentifier")
        if old_bundle:
            print(f"detected bundle id: {old_bundle}")
    if not old_group:
        old_group = read_key(info, "AppGroupIdentifier")
        if old_group:
            print(f"detected app group: {old_group}")

    if old_bundle and old_bundle == new_bundle:
        print("bundle id already matches")
    if old_bundle and len(new_bundle) > len(old_bundle):
        print(f"note: new bundle id is {len(new_bundle)} chars, old is "
              f"{len(old_bundle)}; Mach-O patch will be skipped, plist still rewritten")

    print(f"app       : {app}")
    print(f"bundle id : {old_bundle or '(none)'} -> {new_bundle}")
    print(f"app group : {old_group or '(none)'} -> {new_group}")

    if not args.dry_run:
        backup = app.rstrip("/") + ".orig"
        if not os.path.exists(backup):
            shutil.copytree(app, backup, symlinks=True)
            print(f"backup    : {backup}")

    plist_edits, binary_edits = walk(app, old_group, new_group, old_bundle, new_bundle)
    print(f"\nplist keys : {plist_edits}\nbinary hits: {binary_edits}")
    for entry in changed:
        print(f"  {entry}")
    if plist_edits == 0:
        print("error: no plist key changed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
