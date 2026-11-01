#!/usr/bin/env python3
"""FF7EC missing-art scanner.

Final Fantasy VII Ever Crisis has shut down. Before it did, the final asset
list swapped 3,316 event, gacha and login images for tiny placeholders. If
your game cache still holds a copy downloaded before that swap, you have the
real file on disk.

This script looks for those copies in your FF7EC cache and packs them into
one zip that you can send in. It:

  * only reads your files and never changes or deletes anything,
  * never connects to the network,
  * needs only Python 3.8 or newer (no extra packages).

Usage:
    python scanner.py                      find Steam installs automatically
    python scanner.py "D:\\Games\\FF7EC"     scan this install (or octo folder)
    python scanner.py --help               all options

The cache layout it reads:
    <install>\\octo\\v1\\3001\\<last char of id>\\<hex of "A"+id>\\<md5 of file>
Every file there is named after the MD5 of its own content, so any file whose
name differs from the placeholder's MD5 is an older version of that asset.

The global and Japanese games number their assets differently, so a cache is
only ever checked against its own region's list. The region is read from the
install (FF7EC_Data\\il2cpp_data\\Metadata\\global-metadata.dat names the
game's servers); for a bare octo folder, say which with --region.
"""

import argparse
import datetime
import hashlib
import os
import re
import sys
import zipfile
from collections import Counter

APP_ID = "3001"
GAME_FOLDER = "FF7EC"
MD5_NAME = re.compile(r"^[0-9a-f]{32}$")
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LIST_DIR = os.path.join(HERE, "data")
# The game's server hosts, as the install's metadata names them: one region's
# only, never both.
REGION_MARKERS = {"global": b"app.gl.ffviiec.com", "jp": b"app.jp.ffviiec.com"}
METADATA = os.path.join("FF7EC_Data", "il2cpp_data", "Metadata", "global-metadata.dat")


# --------------------------------------------------------------------------
# Placeholder lists
# --------------------------------------------------------------------------

class Placeholder:
    __slots__ = ("asset_id", "name", "size", "md5", "region")

    def __init__(self, asset_id, name, size, md5, region):
        self.asset_id = asset_id
        self.name = name
        self.size = size
        self.md5 = md5
        self.region = region

    @property
    def folder(self):
        """Asset folder, e.g. 'sprites/gachadetail'."""
        parts = self.name.split("/")
        return "/".join(parts[:2]) if len(parts) > 1 else parts[0]


def load_list(path):
    """Read a placeholders-<region>.tsv file."""
    base = os.path.basename(path)
    region = base[len("placeholders-"):-len(".tsv")] if base.startswith("placeholders-") else base
    items = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            cols = line.split("\t")
            if len(cols) < 4:
                raise ValueError("%s line %d: expected 4 columns" % (path, lineno))
            asset_id, name, size, md5 = cols[:4]
            items.append(Placeholder(asset_id.strip(), name.strip(),
                                     int(size), md5.strip().lower(), region))
    return items


def find_lists(paths):
    """Expand --list arguments (files or folders) into placeholder list files."""
    found = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if name.startswith("placeholders-") and name.endswith(".tsv"):
                    found.append(os.path.join(p, name))
        elif os.path.isfile(p):
            found.append(p)
        else:
            print("warning: placeholder list not found: %s" % p)
    return found


# --------------------------------------------------------------------------
# Finding installs
# --------------------------------------------------------------------------

def steam_roots():
    """Steam install folders from the registry and the usual places."""
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            keys = [
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            ]
            for hive, key, value in keys:
                try:
                    with winreg.OpenKey(hive, key) as k:
                        roots.append(os.path.normpath(winreg.QueryValueEx(k, value)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.append(os.path.join(os.environ[env], "Steam"))
    else:
        home = os.path.expanduser("~")
        roots += [os.path.join(home, ".steam", "steam"),
                  os.path.join(home, ".local", "share", "Steam")]
    return unique_existing(roots)


def steam_libraries():
    """All Steam library folders listed in libraryfolders.vdf."""
    libs = []
    for root in steam_roots():
        libs.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', text):
            libs.append(os.path.normpath(m.group(1).replace("\\\\", "\\")))
    return unique_existing(libs)


def unique_existing(paths):
    seen, out = set(), []
    for p in paths:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen and os.path.isdir(p):
            seen.add(key)
            out.append(p)
    return out


def resolve_cache(path):
    """Turn an install, octo, v1 or 3001 folder into the '3001' cache folder."""
    path = os.path.abspath(path)
    candidates = [
        os.path.join(path, "octo", "v1", APP_ID),
        os.path.join(path, "v1", APP_ID),
        os.path.join(path, APP_ID),
        path,
    ]
    for c in candidates:
        if os.path.basename(c) == APP_ID and os.path.isdir(c):
            return c
    return None


def auto_caches():
    caches = []
    for lib in steam_libraries():
        game = os.path.join(lib, "steamapps", "common", GAME_FOLDER)
        cache = resolve_cache(game)
        if cache:
            caches.append(cache)
    return caches


def detect_region(cache):
    """'global' or 'jp' from the install around a '3001' cache folder, or None
    when there is no install (a bare octo folder) or it names neither."""
    install = os.path.dirname(os.path.dirname(os.path.dirname(cache)))  # 3001 -> v1 -> octo -> install
    path = os.path.join(install, METADATA)
    hits = set()
    overlap = max(len(m) for m in REGION_MARKERS.values())
    try:
        with open(path, "rb") as f:
            tail = b""
            for chunk in iter(lambda: f.read(1 << 22), b""):
                block = tail + chunk
                for region, marker in REGION_MARKERS.items():
                    if marker in block:
                        hits.add(region)
                tail = block[-overlap:]
    except OSError:
        return None
    return hits.pop() if len(hits) == 1 else None


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def asset_dir(cache, asset_id):
    return os.path.join(cache, asset_id[-1], ("A" + asset_id).encode("ascii").hex())


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Found:
    __slots__ = ("ph", "md5", "size", "path")

    def __init__(self, ph, md5, size, path):
        self.ph = ph
        self.md5 = md5
        self.size = size
        self.path = path


def scan_cache(cache, placeholders, stats):
    """Yield every original (non-placeholder) version found in one cache."""
    for ph in placeholders:
        d = asset_dir(cache, ph.asset_id)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        stats["folders"] += 1
        for name in names:
            low = name.lower()
            if low == ph.md5 or not MD5_NAME.match(low):
                continue  # the placeholder itself, or .meta and other files
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            try:
                actual = file_md5(path)
                size = os.path.getsize(path)
            except OSError as e:
                print("  could not read %s: %s" % (path, e))
                stats["unreadable"] += 1
                continue
            if actual != low:
                stats["damaged"] += 1  # incomplete or corrupted download
                continue
            yield Found(ph, low, size, path)


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def output_path(directory):
    stamp = datetime.date.today().isoformat()
    path = os.path.join(directory, "FF7EC-art-%s.zip" % stamp)
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, "FF7EC-art-%s-%d.zip" % (stamp, n))
        n += 1
    return path


def write_zip(path, found):
    lines = ["region\tasset_id\tasset_name\tmd5\tsize\tplaceholder_size\tlarger"]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in found:
            arc = "%s/%s/%s" % (f.ph.region, f.ph.asset_id, f.md5)
            z.write(f.path, arc)
            lines.append("\t".join([
                f.ph.region, f.ph.asset_id, f.ph.name, f.md5, str(f.size),
                str(f.ph.size), "yes" if f.size > f.ph.size else "no",
            ]))
        z.writestr("manifest.tsv", "\n".join(lines) + "\n")


def write_missing(path, placeholders, found_keys):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("region\tasset_id\tfolder\tasset_name\n")
        for ph in placeholders:
            if (ph.region, ph.asset_id) not in found_keys:
                f.write("%s\t%s\t%s\t%s\n" % (ph.region, ph.asset_id, ph.folder, ph.name))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Find original FF7EC event/gacha/login art in your game "
                    "cache and pack it into one zip. Read only, no network.")
    ap.add_argument("paths", nargs="*",
                    help="game install, octo folder or backup of it "
                         "(default: look in your Steam libraries)")
    ap.add_argument("--list", action="append", metavar="PATH",
                    help="placeholder list (.tsv) or folder of them "
                         "(default: the data folder next to this script)")
    ap.add_argument("--out", metavar="DIR", default=HERE,
                    help="where to write the zip (default: next to this script)")
    ap.add_argument("--missing", metavar="FILE",
                    help="also write a list of the images still not found")
    ap.add_argument("--region", choices=sorted(REGION_MARKERS),
                    help="which game the given paths are (global or jp); needed "
                         "for a bare octo folder, otherwise read from the install")
    ap.add_argument("--no-steam", action="store_true",
                    help="don't search Steam libraries, only the given paths")
    ap.add_argument("--no-pause", action="store_true",
                    help="don't wait for Enter before closing")
    args = ap.parse_args(argv)

    # Double-clicking the script opens a console that closes at once; keep it.
    pause = (argv is None and len(sys.argv) == 1 and sys.platform == "win32"
             and not args.no_pause)
    try:
        return run(args)
    finally:
        if pause:
            try:
                input("\nPress Enter to close.")
            except EOFError:
                pass


def run(args):
    lists = find_lists(args.list or [DEFAULT_LIST_DIR])
    if not lists:
        print("No placeholder list found. Put placeholders-*.tsv in %s "
              "or pass --list." % DEFAULT_LIST_DIR)
        return 2
    placeholders = []
    for path in lists:
        items = load_list(path)
        print("Loaded %d placeholders from %s" % (len(items), os.path.basename(path)))
        placeholders += items

    caches = []
    for p in args.paths:
        c = resolve_cache(p)
        if c:
            caches.append(c)
        else:
            print("warning: no FF7EC cache (octo\\v1\\%s) under %s" % (APP_ID, p))
    if not args.paths and not args.no_steam:
        caches += auto_caches()
    caches = unique_existing(caches)
    if not caches:
        print("\nNo FF7EC cache found. Pass the game folder, e.g.\n"
              '  python scanner.py "D:\\SteamLibrary\\steamapps\\common\\FF7EC"')
        return 1

    regions = {ph.region for ph in placeholders}
    found, seen = [], set()
    stats = Counter()
    scanned = Counter()
    for cache in caches:
        print("\nScanning %s" % cache)
        detected = detect_region(cache)
        region = detected or args.region
        if detected and args.region and detected != args.region:
            print("  note: the install is the %s game; --region %s ignored for it" % (detected, args.region))
        if region is None:
            print("  skipped: can't tell whether this is the global or the Japanese game\n"
                  "  (no install around it). Run again with --region global or --region jp.")
            continue
        if region not in regions:
            print("  skipped: %s game, but no placeholders-%s.tsv was loaded" % (region, region))
            continue
        # The two games number their assets differently: checking a cache
        # against the other region's list "finds" the unrelated asset that
        # happens to carry each number.
        mine = [ph for ph in placeholders if ph.region == region]
        print("  %s game: checking %d placeholders" % (region, len(mine)))
        scanned[region] += 1
        before = len(found)
        for f in scan_cache(cache, mine, stats):
            key = (f.ph.region, f.ph.asset_id, f.md5)
            if key not in seen:  # the same file in two installs counts once
                seen.add(key)
                found.append(f)
        print("  %d new original file(s)" % (len(found) - before))

    if not scanned:
        print("\nNothing was scanned.")
        return 1
    # Only the regions actually scanned count as checked.
    placeholders = [ph for ph in placeholders if ph.region in scanned]
    found_keys = {(f.ph.region, f.ph.asset_id) for f in found}
    print("\nAsset folders present in cache: %d of %d" % (stats["folders"], len(placeholders)))
    if stats["damaged"]:
        print("Skipped %d damaged file(s) (content does not match name)" % stats["damaged"])
    if stats["unreadable"]:
        print("Skipped %d unreadable file(s)" % stats["unreadable"])

    if args.missing:
        write_missing(args.missing, placeholders, found_keys)
        print("Wrote the list of images still missing to %s" % os.path.abspath(args.missing))

    if not found:
        print("\nNo original art found in this cache. Thanks for checking!")
        return 0

    total = Counter(ph.folder for ph in placeholders)
    got = Counter({(f.ph.region, f.ph.asset_id): f.ph.folder for f in found}.values())
    print("\nFound originals for %d image(s):" % len(found_keys))
    for folder, n in got.most_common():
        print("  %-28s %5d of %d" % (folder, n, total[folder]))

    os.makedirs(args.out, exist_ok=True)
    out = output_path(os.path.abspath(args.out))
    write_zip(out, found)
    size_mb = os.path.getsize(out) / 1e6
    print("\nWrote %s (%d file(s), %.1f MB)." % (out, len(found), size_mb))
    print("Please send this zip in. Your cache was not changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
