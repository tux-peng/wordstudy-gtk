#!/usr/bin/env sh
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET="$HOME/.local/share/wordstudy-gtk-app"
APPLICATIONS="$HOME/.local/share/applications"
mkdir -p "$TARGET" "$APPLICATIONS"
cp -R "$SRC/wordstudy" "$TARGET/"
cp -R "$SRC/demo_modules" "$TARGET/"
cp "$SRC/run.sh" "$SRC/README.md" "$SRC/LICENSE" "$TARGET/"
chmod +x "$TARGET/run.sh"
sed "s|^Exec=.*|Exec=$TARGET/run.sh|" "$SRC/packaging/org.wordstudy.WordStudyGTK.desktop" > "$APPLICATIONS/org.wordstudy.WordStudyGTK.desktop"
echo "Installed WordStudy GTK at $TARGET"
echo "Launch from your applications menu or run: $TARGET/run.sh"
