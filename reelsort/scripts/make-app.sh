#!/bin/bash
# Fallback: wraps reelsort in a minimal .app bundle so macOS shows the
# Photos permission prompt for "reelsort" itself instead of Terminal.
# Use only if `swift run reelsort scan` never shows a Photos prompt.
#
# Usage (from the reelsort folder):
#   ./scripts/make-app.sh
#   open -W --stdout "$(tty)" --stderr "$(tty)" build/ReelSort.app --args scan
set -euo pipefail

cd "$(dirname "$0")/.."
swift build -c release

APP="build/ReelSort.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp .build/release/reelsort "$APP/Contents/MacOS/reelsort"
cp Sources/reelsort/Info.plist "$APP/Contents/Info.plist"
codesign --force --sign - "$APP"

echo "Built $APP"
echo "Run it with:"
echo "  open -W --stdout \"\$(tty)\" --stderr \"\$(tty)\" $APP --args scan --thumbnails 10"
