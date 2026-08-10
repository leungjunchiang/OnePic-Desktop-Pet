#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

icon_source="assets/icons/pet.png"
icon_root="build/macos"
iconset="$icon_root/pet.iconset"
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
dmg_file="dist/SixHairWorkmate-macOS-${release_arch}-unsigned.dmg"

mkdir -p "$icon_root"
mkdir -p "$iconset"
rm -f "$iconset"/*.png "$icon_file"

sips -z 16 16 "$icon_source" --out "$iconset/icon_16x16.png" >/dev/null
sips -z 32 32 "$icon_source" --out "$iconset/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$icon_source" --out "$iconset/icon_32x32.png" >/dev/null
sips -z 64 64 "$icon_source" --out "$iconset/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$icon_source" --out "$iconset/icon_128x128.png" >/dev/null
sips -z 256 256 "$icon_source" --out "$iconset/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$icon_source" --out "$iconset/icon_256x256.png" >/dev/null
sips -z 512 512 "$icon_source" --out "$iconset/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$icon_source" --out "$iconset/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$icon_source" --out "$iconset/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$iconset" -o "$icon_file"

ONEPIC_INCLUDE_USER_ASSETS=0 python -m PyInstaller --noconfirm --clean OnePicDesktopPet.spec
test -d "dist/SixHairWorkmate.app"

# The public build is intentionally unsigned because no Apple Developer ID is available.
codesign --force --deep --sign - "dist/SixHairWorkmate.app"
rm -f "$dmg_file"
hdiutil create \
    -volname "六毛工作搭子" \
    -srcfolder "dist/SixHairWorkmate.app" \
    -ov \
    -format UDZO \
    "$dmg_file"

echo "Built $project_root/$dmg_file"
