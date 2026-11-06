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
signing_identity="${LILI_MAC_SIGNING_IDENTITY:-}"
notary_profile="${LILI_MAC_NOTARY_PROFILE:-}"
selfsign_identity="${LILI_MAC_SELF_SIGN_IDENTITY:-}"
selfsign_keychain="${LILI_MAC_SELF_SIGN_KEYCHAIN:-}"
if [[ -n "$selfsign_identity" ]]; then
    if [[ -n "$signing_identity" || -n "$notary_profile" || -z "$selfsign_keychain" || ! "$selfsign_identity" =~ ^[0-9A-F]{40}$ ]]; then
        echo "免费签名需要唯一的证书指纹及隔离钥匙串，不能与 Developer ID 配置混用。" >&2
        exit 1
    fi
    if ! security find-identity -v -p codesigning "$selfsign_keychain" | grep -F -- "$selfsign_identity" >/dev/null; then
        echo "指定钥匙串中没有可用的免费签名身份及私钥。" >&2
        exit 1
    fi
    dmg_file="dist/Lili-macOS-${release_arch}-selfsigned.dmg"
elif [[ -n "$selfsign_keychain" ]]; then
    echo "已设置免费签名钥匙串，但没有设置签名身份。" >&2
    exit 1
elif [[ -n "$signing_identity" ]]; then
    if [[ "$signing_identity" != "Developer ID Application: "* || -z "$notary_profile" ]]; then
        echo "正式签名需要 Developer ID Application 身份和 LILI_MAC_NOTARY_PROFILE。" >&2
        exit 1
    fi
    if ! security find-identity -v -p codesigning | grep -F -- "\"$signing_identity\"" >/dev/null; then
        echo "钥匙串中没有可用的 Developer ID Application 身份及私钥。" >&2
        exit 1
    fi
    xcrun --find notarytool >/dev/null
    xcrun --find stapler >/dev/null
    dmg_file="dist/Lili-macOS-${release_arch}-signed.dmg"
else
    if [[ -n "$notary_profile" ]]; then
        echo "已设置公证配置，但没有设置 LILI_MAC_SIGNING_IDENTITY。" >&2
        exit 1
    fi
    dmg_file="dist/Lili-macOS-${release_arch}-unsigned.dmg"
fi
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

if [[ -n "$selfsign_identity" ]]; then
    # This is a free, non-notarized identity; re-sign all bundled code with
    # the same key and verify the app's certificate-anchored identity.
    codesign --force --deep --timestamp=none --keychain "$selfsign_keychain" --sign "$selfsign_identity" "dist/Lili.app"
    codesign --verify --deep --strict "dist/Lili.app"
    codesign -dv --verbose=4 "dist/Lili.app" 2>&1 | grep -Fx 'Identifier=io.github.leungjunchiang.lili' >/dev/null
    requirement="$(codesign --display -r - "dist/Lili.app" 2>&1 | sed -n 's/^designated => //p')"
    [[ -n "$requirement" && "$requirement" == *anchor* && "$requirement" != *cdhash* ]]
    codesign --verify --strict -R "=$requirement" "dist/Lili.app"
    test ! -e "dist/Lili.app/Contents/Resources/user_assets"
    test ! -e "dist/Lili.app/Contents/Frameworks/user_assets"
elif [[ -n "$signing_identity" ]]; then
    # PyInstaller signs nested executables and the app with the same identity.
    # Its bundle-signing warning is not fatal, so explicitly verify the result.
    codesign --verify --deep --strict "dist/Lili.app"
    codesign -dv --verbose=4 "dist/Lili.app" 2>&1 | grep -Fx -- "Authority=$signing_identity" >/dev/null
    test ! -e "dist/Lili.app/Contents/Resources/user_assets"
    test ! -e "dist/Lili.app/Contents/Frameworks/user_assets"
    app_zip="$icon_root/Lili-notarize.zip"
    ditto -c -k --keepParent "dist/Lili.app" "$app_zip"
    xcrun notarytool submit "$app_zip" --keychain-profile "$notary_profile" --wait --output-format json > "$icon_root/app-notary.json"
    python -c 'import json,sys; result=json.load(open(sys.argv[1])); print("App notarization:", result.get("status")); sys.exit(0 if result.get("status") == "Accepted" else 1)' "$icon_root/app-notary.json"
    xcrun stapler staple "dist/Lili.app"
    xcrun stapler validate "dist/Lili.app"
    rm -f "$app_zip"
else
    # Existing release builds are ad-hoc signed when no Developer ID is configured.
    codesign --force --deep --sign - "dist/Lili.app"
fi
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

if [[ -n "$signing_identity" ]]; then
    codesign --force --sign "$signing_identity" --timestamp "$dmg_file"
    xcrun notarytool submit "$dmg_file" --keychain-profile "$notary_profile" --wait --output-format json > "$icon_root/dmg-notary.json"
    python -c 'import json,sys; result=json.load(open(sys.argv[1])); print("DMG notarization:", result.get("status")); sys.exit(0 if result.get("status") == "Accepted" else 1)' "$icon_root/dmg-notary.json"
    xcrun stapler staple "$dmg_file"
    xcrun stapler validate "$dmg_file"
    codesign --verify --deep --strict "dist/Lili.app"
    codesign --verify "$dmg_file"
    (cd dist && shasum -a 256 "$(basename "$dmg_file")" > "$(basename "$dmg_file").sha256")
elif [[ -n "$selfsign_identity" ]]; then
    hdiutil verify "$dmg_file"
    (cd dist && shasum -a 256 "$(basename "$dmg_file")" > "$(basename "$dmg_file").sha256")
fi

echo "Built $project_root/$dmg_file"
