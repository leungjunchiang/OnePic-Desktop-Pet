#!/usr/bin/env bash

# 仅在一次性 GitHub macOS runner 中导入用户授权的免费签名私钥。
set -euo pipefail
set +x
: "${GITHUB_ACTIONS:?仅供 GitHub Actions 使用}"
[[ "$GITHUB_ACTIONS" == "true" && "${RUNNER_OS:-}" == "macOS" ]]
: "${RUNNER_TEMP:?缺少 runner 临时目录}"
: "${GITHUB_ENV:?缺少 GitHub 环境文件}"
: "${LILI_MAC_SELF_SIGN_P12_BASE64:?缺少签名身份密钥}"
: "${LILI_MAC_SELF_SIGN_P12_PASSWORD:?缺少签名身份密码}"
: "${LILI_MAC_SELF_SIGN_CERT_SHA256:?缺少固定证书指纹}"

umask 077
scratch="$RUNNER_TEMP/lili-selfsign"
mkdir -p "$scratch"
p12="$scratch/identity.p12"
cert="$scratch/certificate.pem"
keychain="$scratch/signing.keychain-db"
ready=0
cleanup_failed_setup() {
    if [[ "$ready" != 1 ]]; then
        security delete-keychain "$keychain" >/dev/null 2>&1 || true
        rm -f "$p12"
    fi
}
trap cleanup_failed_setup EXIT

printf '%s' "$LILI_MAC_SELF_SIGN_P12_BASE64" | base64 -D > "$p12"
if ! openssl pkcs12 -in "$p12" -clcerts -nokeys -passin env:LILI_MAC_SELF_SIGN_P12_PASSWORD -out "$cert" >/dev/null 2>&1; then
    openssl pkcs12 -legacy -in "$p12" -clcerts -nokeys -passin env:LILI_MAC_SELF_SIGN_P12_PASSWORD -out "$cert" >/dev/null
fi
expected="$(printf '%s' "$LILI_MAC_SELF_SIGN_CERT_SHA256" | tr -d ':' | tr '[:lower:]' '[:upper:]')"
[[ "$expected" =~ ^[A-F0-9]{64}$ ]]
actual="$(openssl x509 -in "$cert" -noout -fingerprint -sha256 | sed 's/^[^=]*=//' | tr -d ':' | tr '[:lower:]' '[:upper:]')"
[[ "$actual" == "$expected" ]] || { echo '签名证书指纹与固定值不一致。' >&2; exit 1; }
identity="$(openssl x509 -in "$cert" -noout -fingerprint -sha1 | sed 's/^[^=]*=//' | tr -d ':' | tr '[:lower:]' '[:upper:]')"
[[ "$identity" =~ ^[A-F0-9]{40}$ ]]

keychain_password="$(openssl rand -hex 32)"
echo "::add-mask::$keychain_password"
security create-keychain -p "$keychain_password" "$keychain"
security unlock-keychain -p "$keychain_password" "$keychain"
security list-keychains -d user -s "$keychain"
security import "$p12" -k "$keychain" -P "$LILI_MAC_SELF_SIGN_P12_PASSWORD" -T /usr/bin/codesign
security set-key-partition-list -S apple-tool:,apple: -s -k "$keychain_password" "$keychain" >/dev/null
# GitHub 托管 runner 完成任务后销毁；信任范围限定为代码签名。
sudo -n security add-trusted-cert -d -r trustRoot -p codeSign -k /Library/Keychains/System.keychain "$cert"
security find-identity -v -p codesigning "$keychain" | grep -F -- "$identity" >/dev/null
printf 'LILI_MAC_SELF_SIGN_IDENTITY=%s\nLILI_MAC_SELF_SIGN_KEYCHAIN=%s\n' "$identity" "$keychain" >> "$GITHUB_ENV"
rm -f "$p12"
ready=1
echo '免费签名身份已导入隔离的构建钥匙串。'
