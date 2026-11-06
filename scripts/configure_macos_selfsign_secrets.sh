#!/usr/bin/env bash

# 只在用户本机生成并保护免费签名密钥，通过 gh 标准输入写入仓库加密密钥。
set -euo pipefail
set +x
if ! command -v gh >/dev/null 2>&1; then
    echo '需要 GitHub CLI（gh）；请先安装并完成 gh auth login。' >&2
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    echo '请先在终端运行 gh auth login 完成 GitHub 登录。' >&2
    exit 1
fi

repo="${LILI_GITHUB_REPOSITORY:-leungjunchiang/OnePic-Desktop-Pet}"
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
identity_dir="${LILI_SELF_SIGN_DIR:-$project_root/.local-lili-signing}"
p12="$identity_dir/lili-selfsign.p12"
passfile="$identity_dir/lili-selfsign-password"
cert="$identity_dir/lili-selfsign-certificate.pem"
temp_dir=""
verify_dir=""
cleanup() {
    status=$?
    trap - EXIT
    if [[ -n "$temp_dir" ]]; then rm -rf "$temp_dir"; fi
    if [[ -n "$verify_dir" ]]; then rm -rf "$verify_dir"; fi
    exit "$status"
}
trap cleanup EXIT
umask 077
mkdir -p "$identity_dir"
chmod 700 "$identity_dir"

if [[ -e "$p12" || -e "$passfile" || -e "$cert" ]]; then
    if [[ ! -f "$p12" || ! -f "$passfile" || ! -f "$cert" ]]; then
        echo '已有签名身份不完整；为避免破坏旧版身份，脚本不会覆盖。' >&2
        exit 1
    fi
    echo '复用已有签名身份，保持后续版本代码身份一致。'
else
    temp_dir="$(mktemp -d "$identity_dir/.identity-tmp.XXXXXXXX")"
    cat > "$temp_dir/openssl.cnf" <<'CONFIG'
[req]
prompt=no
distinguished_name=dn
x509_extensions=ext
[dn]
CN=Lili Desktop Pet Free Signing
[ext]
basicConstraints=critical,CA:TRUE
keyUsage=critical,digitalSignature,keyCertSign
extendedKeyUsage=codeSigning
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid:always
CONFIG
    openssl req -x509 -newkey rsa:3072 -nodes -keyout "$temp_dir/private-key.pem" \
        -out "$temp_dir/certificate.pem" -days 3650 -config "$temp_dir/openssl.cnf" -extensions ext >/dev/null 2>&1
    password="$(openssl rand -hex 32)"
    printf '%s' "$password" > "$temp_dir/password"
    unset password
    if openssl version | grep -q '^OpenSSL 3'; then
        openssl pkcs12 -export -legacy -inkey "$temp_dir/private-key.pem" \
            -in "$temp_dir/certificate.pem" -out "$temp_dir/identity.p12" \
            -name 'Lili Desktop Pet Free Signing' -passout "file:$temp_dir/password"
    else
        openssl pkcs12 -export -inkey "$temp_dir/private-key.pem" \
            -in "$temp_dir/certificate.pem" -out "$temp_dir/identity.p12" \
            -name 'Lili Desktop Pet Free Signing' -passout "file:$temp_dir/password"
    fi
    mv "$temp_dir/identity.p12" "$p12"
    mv "$temp_dir/password" "$passfile"
    mv "$temp_dir/certificate.pem" "$cert"
fi
chmod 600 "$p12" "$passfile" "$cert"
fingerprint="$(openssl x509 -in "$cert" -noout -fingerprint -sha256 | sed 's/^[^=]*=//' | tr -d ':' | tr '[:lower:]' '[:upper:]')"
[[ "$fingerprint" =~ ^[A-F0-9]{64}$ ]]
verify_dir="$(mktemp -d "$identity_dir/.verify-tmp.XXXXXXXX")"
if ! openssl pkcs12 -in "$p12" -clcerts -nokeys -passin "file:$passfile" -out "$verify_dir/cert.pem" >/dev/null 2>&1; then
    openssl pkcs12 -legacy -in "$p12" -clcerts -nokeys -passin "file:$passfile" -out "$verify_dir/cert.pem" >/dev/null
fi
p12_fingerprint="$(openssl x509 -in "$verify_dir/cert.pem" -noout -fingerprint -sha256 | sed 's/^[^=]*=//' | tr -d ':' | tr '[:lower:]' '[:upper:]')"
[[ "$p12_fingerprint" == "$fingerprint" ]] || { echo 'P12 与证书备份不匹配，已停止。' >&2; exit 1; }

# 不打印私钥、P12 或密码；只有完成全部写入后才打开签名开关。
base64 < "$p12" | tr -d '\n' | gh secret set LILI_MAC_SELF_SIGN_P12_BASE64 --repo "$repo"
gh secret set LILI_MAC_SELF_SIGN_P12_PASSWORD --repo "$repo" < "$passfile"
gh variable set LILI_MAC_SELF_SIGN_CERT_SHA256 --repo "$repo" --body "$fingerprint"
gh variable set LILI_MAC_SELF_SIGNING_ENABLED --repo "$repo" --body true
echo 'GitHub 加密密钥与证书指纹已配置。请妥善保管本机签名身份备份。'
