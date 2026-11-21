#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD=$(mktemp -d)
trap 'rm -rf "$BUILD"' EXIT
PACKAGE="$BUILD/wordstudy-gtk"
DEST="$PACKAGE/usr/share/wordstudy-gtk"
mkdir -p "$PACKAGE/DEBIAN" "$DEST" "$PACKAGE/usr/bin" "$PACKAGE/usr/share/applications" "$PACKAGE/usr/share/doc/wordstudy-gtk"
cp -R "$ROOT/wordstudy" "$ROOT/demo_modules" "$DEST/"
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} +
cp "$ROOT/README.md" "$ROOT/LICENSE" "$DEST/"
cp "$ROOT/README.md" "$PACKAGE/usr/share/doc/wordstudy-gtk/README.md"
cp "$ROOT/LICENSE" "$PACKAGE/usr/share/doc/wordstudy-gtk/copyright"
cat > "$PACKAGE/DEBIAN/control" <<'CONTROL'
Package: wordstudy-gtk
Version: 0.3.0-1
Architecture: all
Maintainer: WordStudy GTK contributors <noreply@example.invalid>
Section: education
Priority: optional
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0
Description: Native Linux Bible study with MySword and MyBible support
 Independent GTK 3/Python Bible study app with parallel translations,
 commentaries, dictionaries, study books, search, notes and bookmarks.
 Includes font, wrapping, Strong's, morphology and red-letter preferences.
 Supports common unencrypted MySword and MyBible SQLite modules.
CONTROL
cat > "$PACKAGE/usr/bin/wordstudy-gtk" <<'LAUNCH'
#!/bin/sh
cd /usr/share/wordstudy-gtk
exec /usr/bin/python3 -m wordstudy "$@"
LAUNCH
chmod +x "$PACKAGE/usr/bin/wordstudy-gtk"
cat > "$PACKAGE/usr/bin/wordstudy-gtk-demo" <<'DEMO'
#!/bin/sh
cd /usr/share/wordstudy-gtk
exec /usr/bin/python3 -m wordstudy.sample --bundled --install
DEMO
chmod +x "$PACKAGE/usr/bin/wordstudy-gtk-demo"
sed 's|^Exec=.*|Exec=wordstudy-gtk|' "$ROOT/packaging/org.wordstudy.WordStudyGTK.desktop" > "$PACKAGE/usr/share/applications/org.wordstudy.WordStudyGTK.desktop"
dpkg-deb --build --root-owner-group "$PACKAGE" "$ROOT/../wordstudy-gtk_0.3.0-1_all.deb"
