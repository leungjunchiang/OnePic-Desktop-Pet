#!/usr/bin/env bash

# 只在用户本机生成并保护免费签名密钥，通过 gh 标准输入写入仓库加密密钥。
set -euo pipefail
set +x
mode="${1:-upload}"
if [[ "$mode" != 'upload' && "$mode" != '--prepare-only' ]]; then
    echo '参数只支持 --prepare-only。' >&2
    exit 1
fi
if [[ "$mode" == 'upload' ]]; then
    if ! command -v gh >/dev/null 2>&1; then
        echo '需要 GitHub CLI（gh）；也可使用 --prepare-only 准备后在 GitHub 网页配置。' >&2
        exit 1
    fi
    if ! gh auth status >/dev/null 2>&1; then
        echo '请先在终端运行 gh auth login 完成 GitHub 登录。' >&2
        exit 1
    fi
fi

repo="${LILI_GITHUB_REPOSITORY:-leungjunchiang/OnePic-Desktop-Pet}"
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
identity_dir="${LILI_SELF_SIGN_DIR:-$project_root/.local-lili-signing}"
p12="$identity_dir/lili-selfsign.p12"
passfile="$identity_dir/lili-selfsign-password"
cert="$identity_dir/lili-selfsign-certificate.pem"
bundle="$identity_dir/lili-selfsign-github-secret.json"
pin_file="${LILI_SELF_SIGN_PIN_PATH:-$project_root/packaging/macos/selfsign-certificate.sha256}"
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
printf '%s' "$fingerprint" > "$identity_dir/lili-selfsign-fingerprint"
chmod 600 "$identity_dir/lili-selfsign-fingerprint"
if [[ -f "$pin_file" ]]; then
    pinned="$(tr -d '\r\n :' < "$pin_file" | tr '[:lower:]' '[:upper:]')"
    [[ "$pinned" == "$fingerprint" ]] || { echo '现有签名证书与项目固定指纹不同，已停止。' >&2; exit 1; }
else
    printf '%s\n' "$fingerprint" > "$pin_file"
fi
python3 - "$p12" "$passfile" "$bundle" <<'PY'
import base64, json, os, pathlib, sys
p12, password, output = map(pathlib.Path, sys.argv[1:])
data = {"p12": base64.b64encode(p12.read_bytes()).decode("ascii"), "password": password.read_text()}
fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, separators=(",", ":"))
PY
chmod 600 "$bundle"

if [[ "$mode" == '--prepare-only' ]]; then
    echo "签名身份已准备在 ${identity_dir}；私钥和密码未显示，也未上传。"
    exit 0
fi

# 不打印私钥、P12 或密码；固定证书指纹随代码一同审查。
gh secret set LILI_MAC_SELF_SIGN_BUNDLE --repo "$repo" < "$bundle"
echo 'GitHub 加密密钥已配置。请妥善保管本机签名身份备份。'
