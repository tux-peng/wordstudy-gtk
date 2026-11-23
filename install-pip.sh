#!/usr/bin/env bash
# Install WordStudy GTK using pipx or an isolated pip virtual environment.
# Requires system GTK 3/PyGObject (e.g. on Artix: pacman -S python-gobject gtk3).
set -euo pipefail

METHOD="${1:-pipx}"
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-/usr/bin/python3}"
VENV_DIR="${VENV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/wordstudy-gtk-venv}"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

usage() {
    printf 'Usage: %s [pip|pipx] [--no-desktop]\n' "$0"
    printf '  pip   Install into a private venv under %%s\n' "$VENV_DIR"
    printf '  pipx  Install as a pipx-managed application (default)\n'
    exit 2
}

[[ "$METHOD" == pip || "$METHOD" == pipx ]] || usage
[[ "${2:-}" == "" || "${2:-}" == "--no-desktop" ]] || usage
[[ -x "$PYTHON" ]] || { echo "Python not found: $PYTHON" >&2; exit 1; }

if ! "$PYTHON" -c '
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GdkPixbuf
' 2>/dev/null; then
    cat >&2 <<'ERROR'
System GTK 3 / PyGObject bindings are unavailable for the selected Python.
Artix/Arch: sudo pacman -S python python-gobject gtk3
Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0 python3-venv
Fedora: sudo dnf install python3-gobject gtk3
Use a system Python that can import gi (normally /usr/bin/python3).
ERROR
    exit 1
fi

mkdir -p "$BIN_DIR"
if [[ "$METHOD" == "pipx" ]]; then
    command -v pipx >/dev/null || {
        echo "pipx not installed. On Artix/Arch: sudo pacman -S python-pipx" >&2
        exit 1
    }
    # --system-site-packages makes distro-provided gi visible in the venv.
    pipx install --python "$PYTHON" --system-site-packages --force "$SOURCE_DIR"
    LAUNCH="$BIN_DIR/wordstudy-gtk"
else
    "$PYTHON" -m venv --system-site-packages "$VENV_DIR"
    "$VENV_DIR/bin/python" -m pip install --upgrade "$SOURCE_DIR"
    LAUNCH="$VENV_DIR/bin/wordstudy-gtk"
    # A private wrapper avoids writing to an externally managed system Python.
    cat > "$BIN_DIR/wordstudy-gtk" <<EOF
#!/bin/sh
exec "$LAUNCH" "\$@"
EOF
    chmod +x "$BIN_DIR/wordstudy-gtk"
fi

if [[ "${2:-}" != "--no-desktop" ]]; then
    mkdir -p "$APPLICATIONS_DIR"
    sed "s|^Exec=.*|Exec=$LAUNCH|" \
        "$SOURCE_DIR/packaging/org.wordstudy.WordStudyGTK.desktop" \
        > "$APPLICATIONS_DIR/org.wordstudy.WordStudyGTK.desktop"
fi
printf '\nInstalled WordStudy GTK via %s.\nLaunch: %s\n' "$METHOD" "$LAUNCH"
printf 'Ensure %s is included in your PATH for the wordstudy-gtk command.\n' "$BIN_DIR"
