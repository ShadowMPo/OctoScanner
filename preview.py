#!/usr/bin/env python3
"""FF7EC art preview: turn recovered cache files into PNG images.

Works on either
  * a zip written by scanner.py (FF7EC-art-<date>.zip), or
  * a game install / octo folder: the originals scanner.py would find there.

It writes one PNG per image into a folder next to the input (or --out), named
after the asset, so you can see what a scan recovered before sending it in.

Usage:
    python preview.py FF7EC-art-2026-11-01.zip
    python preview.py "D:\\SteamLibrary\\steamapps\\common\\FF7EC"
    python preview.py "E:\\Backups\\octo" --region jp

Needs UnityPy (pip install UnityPy), which reads the game's Unity bundles.
scanner.py itself does not need it. Like the scanner, this only reads your
files and never connects to the network.

The cache files are Unity asset bundles whose bytes are lightly scrambled
with a key made from the asset's name (see unscramble below); after undoing
that, UnityPy reads the textures.
"""

import argparse
import os
import sys
import zipfile
from collections import Counter

import scanner


def key_bytes(name):
    """The scrambling key for an asset name: its bytes mirrored, then mixed
    with a running value so every name gives a different key."""
    data = [ord(ch) & 0xFF for ch in name]
    n = len(data)
    kb = bytearray(2 * n)
    for i, c in enumerate(data):
        kb[2 * i] = c
        kb[2 * n - 1 - 2 * i] = c
    h = 0xAB
    for b in kb:
        h = (((h & 1) << 7) | (h >> 1)) ^ b
    return bytes(b ^ (h & 0xFF) for b in kb)


def unscramble(data, name):
    """A cache file's bytes as a plain Unity bundle. The first five bytes are
    the 'Unity' signature; every byte after that is XORed with the key."""
    body = bytearray(data)
    if len(body) < 5:
        return bytes(body)
    body[0:5] = b"Unity"
    kb = key_bytes(name)
    for pos in range(5, len(body)):
        body[pos] ^= kb[pos % len(kb)]
    return bytes(body)


def images(data, name):
    """(texture name, PIL image) for every texture in a cache file."""
    import UnityPy
    env = UnityPy.load(unscramble(data, name))
    out = []
    for obj in env.objects:
        if obj.type.name == "Texture2D":
            tex = obj.read()
            out.append((tex.m_Name, tex.image))
    return out


def safe(part):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in part)


def png_path(out_dir, region, asset_name, md5, texture, multiple):
    """<out>/<region>/<folders of the asset>/<file>[_<texture>]_<md5 start>.png"""
    parts = asset_name.split("/")
    folder = os.path.join(out_dir, region, *[safe(p) for p in parts[:-1]])
    base = parts[-1]
    if base.endswith(".a"):
        base = base[:-2]
    if multiple:
        base += "_" + safe(texture)
    return os.path.join(folder, "%s_%s.png" % (safe(base), md5[:8]))


def from_zip(path):
    """(region, asset name, md5, bytes) for every file in a scanner zip."""
    with zipfile.ZipFile(path) as z:
        rows = z.read("manifest.tsv").decode("utf-8").splitlines()[1:]
        for row in rows:
            cols = row.split("\t")
            if len(cols) < 4:
                continue
            region, asset_id, name, md5 = cols[:4]
            yield region, name, md5, z.read("%s/%s/%s" % (region, asset_id, md5))


def from_caches(args):
    """(region, asset name, md5, bytes) for every original scanner.py would
    find in the given installs or octo folders."""
    placeholders = []
    for path in scanner.find_lists(args.list or [scanner.DEFAULT_LIST_DIR]):
        placeholders += scanner.load_list(path)
    for p in args.paths:
        cache = scanner.resolve_cache(p)
        if not cache:
            print("warning: no FF7EC cache (octo\\v1\\%s) under %s" % (scanner.APP_ID, p))
            continue
        region = scanner.detect_region(cache) or args.region
        if region is None:
            print("skipped %s: can't tell the global from the Japanese game; "
                  "pass --region global or --region jp" % cache)
            continue
        mine = [ph for ph in placeholders if ph.region == region]
        print("Reading %s (%s game)" % (cache, region))
        stats = Counter()
        for found in scanner.scan_cache(cache, mine, stats):
            with open(found.path, "rb") as f:
                yield region, found.ph.name, found.md5, f.read()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Turn recovered FF7EC cache files (a scanner.py zip, or an "
                    "install / octo folder) into PNG images. Needs UnityPy.")
    ap.add_argument("paths", nargs="+", help="scanner zip, game install or octo folder")
    ap.add_argument("--out", metavar="DIR",
                    help="where to write the PNGs (default: <input>-preview next to the input)")
    ap.add_argument("--region", choices=sorted(scanner.REGION_MARKERS),
                    help="which game a bare octo folder came from")
    ap.add_argument("--list", action="append", metavar="PATH",
                    help="placeholder list (.tsv) or folder of them (default: data/)")
    args = ap.parse_args(argv)

    try:
        import UnityPy  # noqa: F401
    except ImportError:
        print("preview.py needs UnityPy to read the game's bundles:\n"
              "    python -m pip install UnityPy\n"
              "(scanner.py does not need it.)")
        return 2

    zips = [p for p in args.paths if p.lower().endswith(".zip") and os.path.isfile(p)]
    folders = [p for p in args.paths if p not in zips]
    # A zip's previews go beside it; a cache's next to this script, never
    # inside the game folder.
    if args.out:
        out_dir = os.path.abspath(args.out)
    elif zips:
        out_dir = os.path.splitext(os.path.abspath(zips[0]))[0] + "-preview"
    else:
        out_dir = os.path.join(scanner.HERE, "FF7EC-art-preview")

    def entries():
        for z in zips:
            print("Reading %s" % z)
            yield from from_zip(z)
        if folders:
            args.paths = folders
            yield from from_caches(args)

    written = failed = 0
    for region, name, md5, data in entries():
        try:
            textures = images(data, name)
        except Exception as e:  # a file UnityPy can't read
            print("  could not read %s (%s): %s" % (name, md5, e))
            failed += 1
            continue
        for texture, image in textures:
            path = png_path(out_dir, region, name, md5, texture, len(textures) > 1)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            image.save(path)
            written += 1
            print("  %s  %dx%d" % (os.path.relpath(path, out_dir), image.width, image.height))

    if not written and not failed:
        print("\nNothing to preview.")
        return 0
    print("\nWrote %d image(s) to %s" % (written, out_dir))
    if failed:
        print("%d file(s) could not be read." % failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
