#!/bin/bash
# Main script for the lrcat2xmp.app droplet (built by build_app.sh).
#
# Drop one or more .lrcat catalogs onto the app. Each catalog's sidecars are
# written into a new sibling folder named "<catalog name> XMP" -- never in
# place -- so this can't collide with real photos or land in the wrong
# location. For --root subsets or writing sidecars in place next to your
# images, use lrcat2xmp.py directly from the command line (see the README).
#
# Runs with cwd set to the app bundle's Contents/Resources, where
# lrcat2xmp.py is bundled alongside this script.
set -u

for catalog in "$@"; do
    if [[ "${catalog##*.}" != "lrcat" ]]; then
        echo "Skipping (not a .lrcat file): $catalog"
        continue
    fi

    dir="$(dirname "$catalog")"
    name="$(basename "$catalog" .lrcat)"
    outdir="$dir/${name} XMP"

    echo "Catalog: $catalog"
    echo "Output:  $outdir"
    echo ""

    /usr/bin/python3 lrcat2xmp.py "$catalog" "$outdir"
    status=$?
    if [[ $status -ne 0 ]]; then
        echo "FAILED (exit $status): $catalog"
    fi
    echo ""
done

echo "All done."
