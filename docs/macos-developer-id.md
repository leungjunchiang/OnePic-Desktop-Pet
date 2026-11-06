# macOS Developer ID 签名与公证准备

GitHub Release 面向 Mac App Store 以外的下载。正式发行需要 Apple Developer Program 的 **Developer ID Application** 证书及其私钥，并使用 Apple 公证服务；普通 Apple ID、临时签名和自签证书不能代替它。创建证书与购买会员须由账户持有人完成。Apple 当前公布的会员费为每年 99 美元，实际以注册页面的当地价格为准。

## 账户与钥匙串

1. 在 [Apple Developer Program](https://developer.apple.com/programs/enroll/) 确认会员状态或注册。账户持有人在 [Certificates](https://developer.apple.com/account/resources/certificates/list) 创建 **Developer ID Application** 证书，并确保该证书与对应私钥都在本机钥匙串中。不要把私钥、Apple 账户密码或验证码发到聊天、提交到 Git 或存入发行包。
2. 使用 `security find-identity -v -p codesigning` 确认可见 `Developer ID Application: … (TEAMID)`。证书名称属于公开签名信息；私钥保留在钥匙串。
3. 使用 Apple 的 `notarytool` 交互式保存公证凭据，下面的占位符由账户持有人在本机输入：

   ```bash
   xcrun notarytool store-credentials lili-notary --apple-id "你的 Apple 账户" --team-id "你的 Team ID"
   ```

   工具会提示输入 App 专用密码并保存到钥匙串，不要把密码写到命令、仓库或聊天中。也可按 Apple 文档使用受保护的 App Store Connect API 密钥。

## 本地签名、公证与校验

在项目根目录使用项目的 Python 环境，填入 `security find-identity` 显示的完整证书名称：

```bash
source .venv/bin/activate
LILI_MAC_SIGNING_IDENTITY="Developer ID Application: 名称 (TEAMID)" \
LILI_MAC_NOTARY_PROFILE="lili-notary" \
bash scripts/build_macos_signed.sh
```

脚本让 PyInstaller 使用同一证书签名嵌套代码并启用 hardened runtime，验证应用签名，将应用送交公证并粘贴票据；随后创建、签名、公证并验证 DMG，再生成 SHA-256。输出为 `dist/Lili-macOS-arm64-signed.dmg` 或 `dist/Lili-macOS-x64-signed.dmg`。两种架构需要各自匹配的构建环境。公证失败、身份缺失或验证失败时构建停止；失败时可能留有中间文件，不能把这些文件作为发行结果。

首次从旧临时签名版本升级到 Developer ID 版本，macOS 可能要求重新为当前应用授权辅助功能。以后维持同一个 Apple 团队、应用 Bundle ID 和安装位置，有助于系统把更新识别为同一应用；系统权限仍由用户控制，不能保证永不再次询问。

目前 GitHub 自动发布工作流已预留另一条不公证的免费自签名路径，详见 `docs/macos-free-signing.md`；尚未接入本页的 Developer ID 公证路径。取得 Developer ID 证书后，仍需配置受保护的 CI 凭据、更新发行资产和更新器匹配规则，并在真实 Mac 上验证签名、公证、安装和升级授权后再发布。不要把签名 DMG 改名为 `unsigned` 直接覆盖旧资产。

参考：[Developer ID 证书](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/)、[Apple 公证流程](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)、[PyInstaller macOS 签名](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing)。
