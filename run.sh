#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
# Distro-provided /usr/bin/python3 normally knows where PyGObject is installed.
exec /usr/bin/python3 -m wordstudy "$@"
