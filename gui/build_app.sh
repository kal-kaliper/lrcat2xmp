#!/bin/bash
# Builds gui/lrcat2xmp.app -- a drag-and-drop macOS wrapper around
# ../lrcat2xmp.py, using Platypus (https://sveinbjorn.org/platypus, MIT).
#
# If the `platypus` command-line tool isn't already installed (Platypus.app's
# "Install Command Line Tool" menu item), this downloads Platypus.app
# temporarily to use its bundled CLI -- nothing is installed system-wide.
set -euo pipefail
cd "$(dirname "$0")"

PLATYPUS_VERSION=5.5.0
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

EXTRA_ARGS=()
if command -v platypus >/dev/null 2>&1; then
    PLATYPUS_CLT="$(command -v platypus)"
else
    echo "platypus CLI not found on PATH; downloading Platypus $PLATYPUS_VERSION to use its bundled CLI..."
    curl -sL -o "$WORKDIR/platypus.zip" \
        "https://github.com/sveinbjornt/Platypus/releases/download/v${PLATYPUS_VERSION}/platypus${PLATYPUS_VERSION}.zip"
    unzip -q "$WORKDIR/platypus.zip" -d "$WORKDIR"
    RESOURCES="$WORKDIR/Platypus.app/Contents/Resources"
    PLATYPUS_CLT="$RESOURCES/platypus_clt"
    chmod +x "$PLATYPUS_CLT"
    # The CLI tool's default ScriptExec path only exists after running
    # Platypus.app's own installer into /usr/local; decode it into our temp
    # dir instead and point --executable-path at it, so nothing gets
    # installed system-wide just to build this app.
    base64 -d -i "$RESOURCES/ScriptExec.b64" > "$WORKDIR/ScriptExec"
    chmod +x "$WORKDIR/ScriptExec"
    cp -r "$RESOURCES/MainMenu.nib" "$WORKDIR/MainMenu.nib"
    EXTRA_ARGS+=(--executable-path "$WORKDIR/ScriptExec" --nib-path "$WORKDIR/MainMenu.nib")
fi

rm -rf "lrcat2xmp.app"

"$PLATYPUS_CLT" \
    --name "lrcat2xmp" \
    --interface-type "Droplet" \
    --interpreter "/bin/bash" \
    --droppable \
    --suffixes "lrcat" \
    --bundled-file "../lrcat2xmp.py" \
    --author "Kal" \
    --overwrite \
    "${EXTRA_ARGS[@]}" \
    droplet.sh \
    lrcat2xmp.app

echo "Built gui/lrcat2xmp.app -- drag a .lrcat file onto it."
