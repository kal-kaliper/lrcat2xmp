#!/usr/bin/env python3
"""
lrcat2xmp: extract XMP sidecar files from a Lightroom .lrcat catalog.

A macOS/Linux-native equivalent of XmpLibeRator
(https://github.com/andyjohnson0/XmpLibeRator), which is Windows-only
(built on .NET Windows Forms). Reads each image's XMP metadata directly
from the catalog's Adobe_AdditionalMetadata table -- the same blob
Lightroom writes when you use "Metadata > Save Metadata to File" -- and
decompresses it to a sidecar file. It does not reconstruct metadata; it
extracts whatever Lightroom already stored for that image.

Usage:
    python3 lrcat2xmp.py "/path/to/Catalog.lrcat" /path/to/output_dir
    python3 lrcat2xmp.py catalog.lrcat out/ --root "2024/Italy"
    python3 lrcat2xmp.py catalog.lrcat out/ --list-folders
    python3 lrcat2xmp.py catalog.lrcat out/ --dry-run

Sidecar naming defaults to <basename>.<ext>.xmp (darktable's convention).
Pass --no-ext for classic Lightroom-style <basename>.xmp naming.
"""

import argparse
import os
import sqlite3
import sys
import zlib
from contextlib import closing
from pathlib import Path

QUERY = """
    SELECT
        AgLibraryRootFolder.name       AS root_name,
        AgLibraryFolder.pathFromRoot   AS path_from_root,
        AgLibraryFile.baseName         AS base_name,
        AgLibraryFile.extension        AS extension,
        Adobe_AdditionalMetadata.xmp   AS xmp_blob
    FROM Adobe_images
    LEFT JOIN AgLibraryFile ON AgLibraryFile.id_local = Adobe_images.rootFile
    LEFT JOIN AgLibraryFolder ON AgLibraryFolder.id_local = AgLibraryFile.folder
    LEFT JOIN AgLibraryRootFolder ON AgLibraryFolder.rootFolder = AgLibraryRootFolder.id_local
    LEFT JOIN Adobe_AdditionalMetadata ON Adobe_AdditionalMetadata.image = Adobe_images.id_local
"""

FOLDERS_QUERY = """
    SELECT AgLibraryFolder.id_local, AgLibraryRootFolder.name, AgLibraryFolder.pathFromRoot,
           COUNT(AgLibraryFile.id_local) AS file_count
    FROM AgLibraryFolder
    LEFT JOIN AgLibraryRootFolder ON AgLibraryFolder.rootFolder = AgLibraryRootFolder.id_local
    LEFT JOIN AgLibraryFile ON AgLibraryFile.folder = AgLibraryFolder.id_local
    GROUP BY AgLibraryFolder.id_local
    ORDER BY AgLibraryRootFolder.name, AgLibraryFolder.pathFromRoot
"""

# Decompressed XMP is plain-text image metadata; it should never legitimately
# be anywhere near this large. Used as a sanity cap against a corrupt or
# maliciously crafted catalog trying to zlib-bomb the process.
MAX_XMP_BYTES = 64 * 1024 * 1024


def connect_readonly(catalog_path):
    # Build a proper file: URI rather than interpolating the path as a bare
    # string, so a path containing '?', '#', or other URI-meaningful
    # characters can't be misread as query parameters.
    uri = Path(catalog_path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def list_folders(conn):
    return conn.execute(FOLDERS_QUERY).fetchall()


def decompress_xmp(blob):
    """Decompress a stored XMP blob.

    Returns the decompressed bytes on success, None if there was nothing
    stored, or False if what was stored could not be safely decompressed
    (corrupt data, wrong type, or implausibly large).
    """
    if blob is None:
        return None
    if not isinstance(blob, (bytes, bytearray)):
        # A corrupt catalog row could have this column as TEXT/INTEGER.
        return False
    if len(blob) <= 4:
        return None
    # First 4 bytes are the uncompressed length prefix; the rest is a
    # zlib-compressed stream of the raw XMP XML. We don't need the length
    # value itself, only to skip past it.
    try:
        decompressor = zlib.decompressobj()
        data = decompressor.decompress(blob[4:], MAX_XMP_BYTES + 1)
        if len(data) > MAX_XMP_BYTES or decompressor.unconsumed_tail:
            return False
        data += decompressor.flush()
        if len(data) > MAX_XMP_BYTES:
            return False
        return data
    except zlib.error:
        return False


def sanitize_component(name):
    """Sanitize a single path segment (a root folder name or filename) taken
    from catalog data. Never returns something that could be interpreted as
    empty, '.', '..', or containing a path separator."""
    if not name:
        return "_"
    name = str(name).replace("\\", "/").split("/")[-1]
    if name in ("", ".", ".."):
        return "_"
    return name


def sanitize_relpath(path_str):
    """Sanitize a multi-segment relative path (pathFromRoot) taken from
    catalog data into a safe relative Path: drop empty/'.'/'..' segments so
    the result can never point outside wherever it's joined onto."""
    parts = []
    for part in (path_str or "").replace("\\", "/").split("/"):
        if part in ("", ".", ".."):
            continue
        parts.append(part)
    return Path(*parts) if parts else Path()


def is_within(path, root):
    path = os.path.normpath(str(path))
    root = os.path.normpath(str(root))
    return path == root or path.startswith(root + os.sep)


def path_matches_prefix(candidate, prefix):
    """Component-wise prefix match, so '2024/Italy' matches '2024/Italy/x'
    but not '2024/ItalyOther/'."""
    candidate_parts = [p for p in candidate.strip("/").split("/") if p]
    prefix_parts = [p for p in prefix.strip("/").split("/") if p]
    if not prefix_parts:
        return True
    return candidate_parts[: len(prefix_parts)] == prefix_parts


def extract(catalog_path, output_dir, folder_ids=None, include_ext=True, dry_run=False):
    output_root = Path(output_dir).resolve()

    written = 0
    skipped_existing = []
    skipped_no_xmp = []
    skipped_corrupt_xmp = []
    skipped_collision = []
    skipped_no_file_record = 0
    written_paths = {}  # normalized destination -> report_key of the row that claimed it this run
    processed = 0
    PROGRESS_INTERVAL = 250  # rows between progress lines, for long-running extracts

    with closing(connect_readonly(catalog_path)) as conn:
        query = QUERY
        params = []
        if folder_ids is not None:
            if not folder_ids:
                # An explicitly empty selection means "extract nothing",
                # not "no filter" -- avoid accidentally matching everything.
                return 0, [], [], [], 0
            placeholders = ",".join("?" for _ in folder_ids)
            query += f" WHERE AgLibraryFolder.id_local IN ({placeholders})"
            params = list(folder_ids)
        query += " ORDER BY AgLibraryFolder.pathFromRoot, AgLibraryFile.baseName"

        for root_name, path_from_root, base_name, extension, xmp_blob in conn.execute(query, params):
            processed += 1
            if processed % PROGRESS_INTERVAL == 0:
                verb = "checked" if dry_run else "written"
                print(f"...{processed} processed, {written} {verb}", flush=True)

            if not base_name:
                # Adobe_images row with no matching AgLibraryFile (orphaned catalog entry).
                skipped_no_file_record += 1
                continue

            safe_root = sanitize_component(root_name)
            safe_rel = sanitize_relpath(path_from_root)
            safe_base = sanitize_component(base_name)
            safe_ext = sanitize_component(extension) if extension else ""

            out_dir = output_root / safe_root / safe_rel
            ext_part = f".{safe_ext}" if include_ext and safe_ext else ""
            out_path = out_dir / f"{safe_base}{ext_part}.xmp"
            report_key = f"{root_name}/{path_from_root or ''}{base_name}.{extension or ''}"

            # Defense in depth: sanitization above should already guarantee
            # this, but never write anywhere outside output_root.
            if not is_within(out_path, output_root):
                skipped_corrupt_xmp.append(report_key)
                continue

            xmp_bytes = decompress_xmp(xmp_blob)
            if xmp_bytes is False:
                skipped_corrupt_xmp.append(report_key)
                continue
            if xmp_bytes is None:
                skipped_no_xmp.append(report_key)
                continue

            dest_key = str(out_path)
            if dest_key in written_paths:
                # A different catalog record maps to the same destination
                # path within this run (duplicate root names, virtual
                # copies, a RAW+JPEG pair colliding under --no-ext, or a
                # case-insensitive filesystem). Report it rather than
                # silently dropping one image's metadata.
                skipped_collision.append((report_key, written_paths[dest_key]))
                continue

            if dry_run:
                if out_path.exists():
                    skipped_existing.append(report_key)
                    continue
                written_paths[dest_key] = report_key
                written += 1
                continue

            out_dir.mkdir(parents=True, exist_ok=True)
            try:
                with open(out_path, "xb") as f:
                    f.write(xmp_bytes)
            except FileExistsError:
                skipped_existing.append(report_key)
                continue

            written_paths[dest_key] = report_key
            written += 1

    return written, skipped_existing, skipped_no_xmp, skipped_corrupt_xmp, skipped_collision, skipped_no_file_record


def resolve_root_filter(conn, root_prefix):
    folder_ids = [
        row[0] for row in list_folders(conn)
        if path_matches_prefix(f"{row[1]}/{row[2] or ''}", root_prefix)
        or path_matches_prefix(row[2] or "", root_prefix)
    ]
    return folder_ids


def format_report(written, skipped_existing, skipped_no_xmp, skipped_corrupt_xmp,
                   skipped_collision, skipped_no_file_record, dry_run):
    verb = "Would write" if dry_run else "Wrote"
    lines = [f"{verb} {written} XMP sidecar file(s).", ""]
    lines.append(f"{len(skipped_existing)} file(s) skipped (output already existed):")
    lines += [f"    {p}" for p in skipped_existing]
    lines.append("")
    lines.append(f"{len(skipped_no_xmp)} file(s) skipped (no XMP metadata stored in catalog):")
    lines += [f"    {p}" for p in skipped_no_xmp]
    lines.append("")
    lines.append(f"{len(skipped_corrupt_xmp)} file(s) skipped (unreadable/unsafe XMP data -- "
                  "possible catalog corruption):")
    lines += [f"    {p}" for p in skipped_corrupt_xmp]
    lines.append("")
    lines.append(f"{len(skipped_collision)} file(s) skipped (destination collided with another "
                  "catalog record in this run -- e.g. a virtual copy, duplicate root folder name, "
                  "or a filename that only differs by extension under --no-ext):")
    lines += [f"    {a}  (collided with {b})" for a, b in skipped_collision]
    lines.append("")
    lines.append(f"{skipped_no_file_record} catalog entr(ies) skipped (orphaned, no matching file record).")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("catalog", help="Path to the Lightroom .lrcat catalog file")
    ap.add_argument("output_dir", help="Directory to write XMP sidecar files into "
                                        "(required even with --list-folders, though unused there)")
    ap.add_argument(
        "--no-ext", action="store_true",
        help="Name sidecars <basename>.xmp instead of <basename>.<ext>.xmp "
             "(darktable expects the extension included; this is the classic "
             "Lightroom-style naming, use only if you don't need darktable to find them)",
    )
    ap.add_argument(
        "--root",
        help="Only extract images whose Lightroom folder path starts with this "
             "prefix, matched by whole path component (e.g. '2024/Italy' matches "
             "'2024/Italy/...' but not '2024/ItalyOther/'). Default: all folders.",
    )
    ap.add_argument(
        "--list-folders", action="store_true",
        help="List catalog folders with IDs and file counts (respects --root), then exit",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be written, without creating any directories or files",
    )
    args = ap.parse_args()

    if not os.path.exists(args.catalog):
        sys.exit(f"error: catalog not found: {args.catalog}")

    lock_path = args.catalog + ".lock"
    if os.path.exists(lock_path):
        print(
            f"warning: {lock_path} exists -- Lightroom may have this catalog open. "
            "Reading is safe, but close Lightroom first if you hit odd errors.",
            file=sys.stderr,
        )

    try:
        with closing(connect_readonly(args.catalog)) as conn:
            if args.list_folders:
                folder_ids = resolve_root_filter(conn, args.root) if args.root else None
                for folder_id, root_name, path_from_root, file_count in list_folders(conn):
                    if folder_ids is not None and folder_id not in folder_ids:
                        continue
                    print(f"{folder_id}\t{file_count}\t{root_name}/{path_from_root}")
                return

            folder_ids = None
            if args.root:
                folder_ids = resolve_root_filter(conn, args.root)
                if not folder_ids:
                    sys.exit(f"error: no folders matched --root {args.root!r} (try --list-folders)")
    except sqlite3.DatabaseError as e:
        sys.exit(f"error: could not read catalog: {e}")

    if not args.dry_run:
        os.makedirs(args.output_dir, exist_ok=True)

    try:
        (written, skipped_existing, skipped_no_xmp, skipped_corrupt_xmp,
         skipped_collision, skipped_no_file_record) = extract(
            args.catalog, args.output_dir,
            folder_ids=folder_ids,
            include_ext=not args.no_ext,
            dry_run=args.dry_run,
        )
    except (sqlite3.DatabaseError, OSError) as e:
        sys.exit(f"error: extraction failed: {e}")

    report = format_report(written, skipped_existing, skipped_no_xmp, skipped_corrupt_xmp,
                            skipped_collision, skipped_no_file_record, args.dry_run)

    if args.dry_run:
        print(report, end="")
        return

    report_path = os.path.join(args.output_dir, "lrcat2xmp report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"Wrote {written} XMP sidecar file(s).")
    print(
        f"{len(skipped_existing)} skipped (already existed), "
        f"{len(skipped_no_xmp)} skipped (no stored XMP), "
        f"{len(skipped_corrupt_xmp)} skipped (unreadable XMP data), "
        f"{len(skipped_collision)} skipped (destination collision), "
        f"{skipped_no_file_record} skipped (orphaned catalog entries)."
    )
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
