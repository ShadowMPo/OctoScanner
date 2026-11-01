# FF7EC missing-art scanner

Before Final Fantasy VII Ever Crisis shut down, 3,316 event, gacha and login
images were replaced with tiny placeholders. If your game cache still has the
originals, this finds them so they can be restored in the
[NeverCrisis](https://github.com/ShadowMPo/NeverCrisis) offline server.

Read only, no network, Python 3.8+ with no extra packages. Global and Japanese
games supported.

## Use

1. Install [Python](https://www.python.org/downloads/).
2. Download this repo (**Code** > **Download ZIP**) and extract it.
3. Double-click `scanner.py`, or run:

   ```
   python scanner.py                                  # finds Steam installs
   python scanner.py "D:\Games\FF7EC"                 # another install
   python scanner.py "E:\Backups\octo" --region jp    # bare octo backup
   ```

4. If anything is found, it writes `FF7EC-art-<date>.zip`. Post it on
   **[issue #29](https://github.com/ShadowMPo/NeverCrisis/issues/29)**
   (over 25 MB: upload elsewhere and post the link). Don't rename the files
   inside.

"No original art found" means your cache was already updated. Old `octo`
backups or an unused second PC are the best places to look.

## Options

| Option | |
| --- | --- |
| `--region global\|jp` | Required for a bare `octo` folder |
| `--out DIR` | Where to write the zip |
| `--missing FILE` | List the images still not found |
| `--list PATH` | Use another placeholder list |
| `--no-steam` | Only scan the given folders |
| `--no-pause` | Don't wait for Enter at the end |

## Preview (optional)

See the recovered images as PNGs:

```
python -m pip install UnityPy
python preview.py FF7EC-art-2026-11-01.zip
```

It also accepts an install or `octo` folder, like the scanner.

## How it works

Cache files live in `<install>\octo\v1\3001\<last digit of id>\<hex of "A"+id>\`
and are named after the MD5 of their content. For each placeholder in
`data/placeholders-<region>.tsv`, any valid file in its folder with a
different MD5 is an older original. The global and Japanese games number
assets differently, so each install is checked against its own list (region
read from `global-metadata.dat`).

## License

MIT
