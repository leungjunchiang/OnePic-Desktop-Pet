#!/usr/bin/env bash

# 正式签名入口必须同时配置证书和公证档案，不能回退到临时签名。
set -euo pipefail
: "${LILI_MAC_SIGNING_IDENTITY:?需要设置 Developer ID Application 签名身份}"
: "${LILI_MAC_NOTARY_PROFILE:?需要设置钥匙串中的公证配置名称}"
exec bash "$(dirname "$0")/build_macos.sh"
