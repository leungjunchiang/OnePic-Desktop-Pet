# 免费 macOS 自签名与更新授权

此方案使用固定的自签名证书，适用于不购买 Apple Developer Program 的 Lili 发行。它不是 Apple Developer ID，也不能通过 Apple 公证。macOS 可能在首次打开或安装新版本时要求用户在系统设置中允许打开；辅助功能仍需用户授权，首次从旧临时签名版切换时可能要重新授权。即使代码身份保持一致，也要在实际 Mac 上验证授权是否保留。

## 签名身份与 GitHub 加密密钥

在一台可信 Mac 上使用已登录本仓库的 GitHub CLI（`gh`），从项目根目录运行：

```bash
bash scripts/configure_macos_selfsign_secrets.sh
```

脚本仅在身份不存在时生成一张用于代码签名的自签证书，随后复用同一证书。证书及加密私钥备份保存在 Git 忽略的 `.local-lili-signing/` 目录，目录权限为 700、文件权限为 600。P12 与密码作为一个组合值，通过标准输入写入 GitHub Actions 加密密钥 `LILI_MAC_SELF_SIGN_BUNDLE`。公钥证书的 SHA-256 指纹保存在项目的 `packaging/macos/selfsign-certificate.sha256`，用于阻止意外更换证书。这些私钥和密码不要贴到聊天、日志或提交到 Git。

如果暂时没有 GitHub CLI，可以先运行 `bash scripts/configure_macos_selfsign_secrets.sh --prepare-only`。它只在本机准备文件，不上传。然后在仓库的 Settings → Secrets and variables → Actions 中，由仓库管理员自行配置：

1. 运行 `pbcopy < .local-lili-signing/lili-selfsign-github-secret.json`，只将组合值复制到本机剪贴板，不显示在终端。
2. 粘贴到新建的 Repository secret `LILI_MAC_SELF_SIGN_BUNDLE` 并保存。
3. 保存后可运行 `pbcopy </dev/null` 清空。剪贴板可能被其他本机程序读取；确保在可信设备上操作。不要把组合值发送到聊天。

妥善备份该目录及其密码；GitHub 不会显示已保存的密钥值。若丢失或更换签名私钥，后续版本身份会变化，用户可能必须重新授权。不要为了省事把私钥放入仓库或发行包。设置脚本在密钥部分缺失、证书和 P12 不匹配时停止，避免悄悄生成新身份。

## 自动构建与首次安装

包含此改动的发布工作流会在一次性 GitHub macOS runner 的隔离钥匙串中导入身份，检查固定证书指纹并签名整个应用；两个架构的代码身份必须一致，才可发布 `Lili-macOS-arm64-selfsigned.dmg`、`Lili-macOS-x64-selfsigned.dmg` 及校验文件。未配置密钥、证书不匹配或签名验证失败会让发布失败；本地开发构建仍可使用旧临时签名入口。

旧版 v0.23.355 的更新器只认识 `-unsigned.dmg`，首次切换需要用户从新 Release 手动下载对应的 `-selfsigned.dmg`，拖入「应用程序」，按照 macOS 提示打开，并在「系统设置 → 隐私与安全性 → 辅助功能」允许当前 Lili。此后新版更新器优先识别自签名包。更新前后都使用同一签名身份、Bundle ID 和应用副本。

不要把证书加入用户电脑的系统信任列表，也不要指导用户关闭 Gatekeeper。可按 [Apple 的说明](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unidentified-developer-mh40616/mac)逐次处理 macOS 的打开提示。

## 验证边界

一次性 GitHub Mac 构建机已用临时证书签名两个不同版本和完整 Lili 应用；两个版本的代码身份可互相验证，完整应用启动冒烟通过。该测试没有授予真实用户的辅助功能权限，也未模拟真实用户升级；首次正式发行仍需在真实 Mac 上验证跨版本授权、下载和打开体验。Apple 不认可自签证书为 Developer ID，无法为该包提供公证服务。[Apple 代码签名说明](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/Procedures/Procedures.html)
