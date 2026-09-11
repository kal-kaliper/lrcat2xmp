# lrcat2xmp

Extract XMP sidecar files from an Adobe Lightroom Classic (`.lrcat`) catalog,
without Lightroom installed. A dependency-free, macOS/Linux-native tool for
the same job [XmpLibeRator](https://github.com/andyjohnson0/XmpLibeRator)
does on Windows — inspired by it, and independently implemented after
reading its public source to understand the catalog schema and blob format
(no code copied; see [License and attribution](#license-and-attribution)).
XmpLibeRator itself is Windows-only, built on .NET Windows Forms, which has
no macOS/Linux runtime.

## What it does

Lightroom's catalog stores each image's metadata (ratings, keywords, color
labels, IPTC, and any Develop adjustments) as a compressed XMP blob in the
`Adobe_AdditionalMetadata` table. `lrcat2xmp` reads that blob directly and
decompresses it to a `.xmp` sidecar file, preserving the catalog's folder
hierarchy under whatever output directory you give it. It does not
reconstruct or infer metadata — it extracts the same bytes Lightroom itself
would write via *Metadata > Save Metadata to File*, straight from the
catalog, without needing Lightroom running. It does not verify that content
against the actual image file, and it can't tell you whether a given image's
stored metadata is stale relative to Lightroom's live in-memory state (e.g.
an edit made but not yet flushed to the catalog on disk).

This is useful for migrating a Lightroom library to darktable, digiKam, or
any other tool that reads XMP sidecars, especially for images where you
never manually triggered a metadata save. It says nothing about whether
another tool will *render* those Develop settings the same way Lightroom
does — process-specific adjustments (masks, profiles, camera-matching
curves) are not guaranteed to translate 1:1 between applications.

## Requirements

Python 3.8+, standard library only. No install step.

## GUI: drag-and-drop app

For a no-terminal option, `gui/build_app.sh` builds `lrcat2xmp.app`, a small
macOS app you drop a `.lrcat` catalog onto. It extracts sidecars into a new
sibling folder named `<catalog name> XMP` (never in place, so it can't
collide with real photos or land in the wrong location) and streams progress
live in the app window.

```bash
cd gui
./build_app.sh      # builds lrcat2xmp.app in this folder
```

The build uses [Platypus](https://sveinbjorn.org/platypus) (MIT-licensed) to
wrap `droplet.sh`, which calls `lrcat2xmp.py`. If the `platypus` command-line
tool isn't already installed, the script downloads Platypus temporarily to
use its bundled CLI -- nothing is installed system-wide. Since the resulting
app is built locally rather than downloaded, macOS won't quarantine it, so it
should launch immediately with no Gatekeeper prompt.

The GUI always extracts an entire catalog with default sidecar naming; use
`lrcat2xmp.py` directly from the command line for `--root` subsets,
`--no-ext` naming, `--dry-run` previews, or writing sidecars in place.

## Usage

```bash
# Preview what would be written, without creating any directories or files
python3 lrcat2xmp.py "/path/to/Catalog.lrcat" /path/to/output --dry-run

# Extract everything
python3 lrcat2xmp.py "/path/to/Catalog.lrcat" /path/to/output

# Extract just one folder subtree (see --list-folders for exact paths)
python3 lrcat2xmp.py "/path/to/Catalog.lrcat" /path/to/output --root "2024/Italy"

# List catalog folders with image counts, to find --root values
# (output_dir is a required positional argument even here, though unused)
python3 lrcat2xmp.py "/path/to/Catalog.lrcat" /path/to/output --list-folders
```

`--root` matches by whole path component, so `--root "2024/Italy"` matches
`2024/Italy/...` but not `2024/ItalyOther/`.

Sidecars are named `<basename>.<ext>.xmp` by default, matching darktable's
convention (it always keeps the source extension in the sidecar name, for
every file type — not just RAW). Pass `--no-ext` for classic Lightroom-style
`<basename>.xmp` naming instead — note this makes a RAW+JPEG pair with the
same basename collide onto one sidecar (see Known limitations).

### Writing sidecars in place

To match darktable's own layout, point `output_dir` at the *parent* of the
catalog's root folder name, so the script's `<root>/<path-from-root>/`
structure lands exactly on top of where your images already live. Check the
folder's root name and path with `--list-folders` first — getting this one
level off will nest an extra folder and put every sidecar next to the wrong
copy of your images.

### A note on "HasSettings"

Lightroom sets `crs:HasSettings="True"` on an image's XMP as soon as it's
been through *any* Develop-module processing — including automatic ones like
an HDR or panorama merge, which always bake in a full (but often untouched)
settings snapshot. `HasSettings="True"` alone is not proof you actually
edited that image. To find genuinely edited photos, check whether the
specific tonal keys (`crs:Exposure2012`, `Contrast2012`, `Highlights2012`,
`Shadows2012`, `Whites2012`, `Blacks2012`, `Clarity2012`, `Vibrance`,
`Saturation`), HSL/color-grading values, or crop bounds actually differ from
their defaults.

## Safety

- The catalog is opened read-only (SQLite `mode=ro`) and is never written to.
- Folder names, filenames, and extensions read from the catalog are
  sanitized before being used to build output paths (no absolute paths, no
  `..` traversal), with a containment check as a second layer of defense
  against a corrupt or malformed catalog.
- Sidecars are written with exclusive create (never silently overwriting an
  existing file or following a symlink at the destination).
- If two different catalog records would land on the same output path
  (duplicate root-folder names, virtual copies, a same-run collision from
  `--no-ext`, or a case-insensitive filesystem), the second one is skipped
  and reported as a collision rather than silently overwriting the first.
- `--dry-run` creates no directories and writes no files, including the
  report (which prints to stdout instead).

## Known limitations

- Virtual copies and duplicate catalog entries that resolve to the same
  output path are skipped (reported as collisions), not merged.
- Decompressed XMP is capped at 64 MB as a sanity check against a corrupt or
  adversarially crafted catalog; genuine XMP metadata is always far smaller.
- No XML validation is performed on the extracted bytes beyond the zlib
  decompression succeeding — a truly malformed-but-decompressible blob would
  still be written out as-is.
- Tested against real Lightroom Classic catalogs on macOS (schema version
  `3-2-2`, LR versions 12–13). Older/newer catalog schema versions haven't
  been verified.

## How it works

The catalog is a standard SQLite database. For each image, the query joins
`Adobe_images` → `AgLibraryFile` → `AgLibraryFolder` → `AgLibraryRootFolder`
to get its folder path, and `Adobe_AdditionalMetadata` for its `xmp` blob.
That blob is a 4-byte uncompressed-length prefix followed by a zlib-compressed
stream of the raw XMP XML — skip the first 4 bytes, `zlib.decompress()` the
rest, and you have the sidecar contents.

## License and attribution

MIT, see [LICENSE](LICENSE). Inspired by XmpLibeRator (also MIT); no code
from it is included here — this is an independent implementation written
after reading its public source to understand the Lightroom catalog schema
and the blob's compression format.
