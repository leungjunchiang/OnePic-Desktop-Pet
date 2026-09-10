#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

icon_source="assets/icons/pet.png"
icon_root="build/macos"
icon_file="$icon_root/pet.icns"
machine_arch="${ONEPIC_MAC_ARCH:-$(uname -m)}"
case "$machine_arch" in
    arm64) release_arch="arm64" ;;
    x64|x86_64) release_arch="x64" ;;
    *)
        echo "Unsupported macOS architecture: $machine_arch" >&2
        exit 1
        ;;
esac
dmg_file="dist/Lili-macOS-${release_arch}-unsigned.dmg"
dmg_root="$icon_root/dmg-root"

mkdir -p "$icon_root"
rm -f "$icon_file"
# macOS 15's iconutil rejects otherwise valid iconsets generated from small
# PNGs on some runner images.  sips performs the same conversion directly and
# produces a valid ICNS without changing the public asset.
sips -s format icns "$icon_source" --out "$icon_file" >/dev/null
test -s "$icon_file"

ONEPIC_INCLUDE_USER_ASSETS=0 python -m PyInstaller --noconfirm --clean OnePicDesktopPet.spec
test -d "dist/Lili.app"

# The public build is intentionally unsigned because no Apple Developer ID is available.
codesign --force --deep --sign - "dist/Lili.app"
rm -f "$dmg_file"
rm -rf "$dmg_root"
mkdir -p "$dmg_root"
cp -R "dist/Lili.app" "$dmg_root/Lili.app"
ln -s /Applications "$dmg_root/Applications"
hdiutil create \
    -volname "Lili" \
    -srcfolder "$dmg_root" \
    -ov \
    -format UDZO \
    "$dmg_file"

echo "Built $project_root/$dmg_file"
