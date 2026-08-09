# 一图桌宠（OnePic Desktop Pet）

上传一张角色图片，让 Agent 帮助生成、配置并优化一个可以在 Windows 桌面上跑动、休息、互动和自拍的桌面宠物。

当前 `v0.1.0` 是从已经可运行的 Python + PySide6 桌宠整理出的开源候选版本。仓库暂时保留一套演示角色动作，后续将继续完善“一张图到完整动作”的 Agent 自动执行流程。

## 当前功能

- 透明无边框窗口、桌面置顶和多显示器 DPI 适配；
- 站立、跑动、坐下、入睡、醒来、拖拽和自拍连续动画；
- 摸头、分区点击、连续戳击、悬停注视和情绪反馈；
- 跑动结束后随机站立、坐下或自拍；
- 默认 5 分钟无互动后坐下、10 分钟后入睡；
- 右键尺寸调整、暂停跑动、隐藏和退出；
- 用户可在本地放入自己的自拍成片，不提交到 Git；
- 原图登记后自动作为自拍成片，保持原始像素尺寸；
- 标准角色形象和走路 GIF 必须分别得到用户确认；
- 表情符号由程序独立绘制，换角色后仍可显示闪光、爱心、惊叹号、疑问号、怒气、Zzz 和汗滴；
- PyInstaller Windows 打包脚本。

## 最快体验

未来正式 Release 会提供可直接运行的 Windows 版本，不需要安装 Python。当前本地候选版请先执行：

```powershell
.\scripts\check_environment.ps1
.\scripts\setup_environment.ps1
.\scripts\run.ps1
```

环境脚本只在项目内创建 `.venv` 并安装依赖，不会自动安装 Python、Git，不会修改系统环境变量，也不会申请管理员权限。缺少 Python 3.12 时会停止并给出提示。

## 从一张图片开始

完成环境安装后，先登记最初上传的图片：

```powershell
.\scripts\start_onepic.ps1 -SourceImage "图片的完整路径"
```

该命令会在 `user_assets/source/` 保留原始文件副本，并生成同分辨率的 `user_assets/selfie.png`。原图、自拍图和流程状态全部被 Git 忽略。

接下来先选择生成风格：`preserve_original`（保留原画风，默认）、`light_chibi`（轻度 Q 版）或 `full_chibi`（完整 Q 版）。Agent 只能先生成一张标准角色形象，登记人物特色并交给用户确认：

```powershell
.\.venv\Scripts\python.exe .\tools\onepic_workflow.py character-candidate `
  --image "标准角色候选图路径" `
  --style preserve_original `
  --feature "有辨识度的脸型和眼型" `
  --feature "原图中的发型、服装和标志性配饰"
```

随后必须打开确认窗口。只有用户亲自查看候选图并点击“符合，这就是我要的角色”后，动作门禁才会通过：

```powershell
.\.venv\Scripts\python.exe .\tools\onepic_workflow.py approve-character
```

生成动作后还必须生成并查看走路 GIF：

```powershell
.\.venv\Scripts\python.exe .\tools\onepic_workflow.py walk-review
.\.venv\Scripts\python.exe .\tools\onepic_workflow.py approve-walk --yes
```

没有完成两个确认，程序不会加载私有角色，个人版本打包也会被阻止。

更换角色后可以生成八种表情符号联系表进行视觉检查：

```powershell
.\.venv\Scripts\python.exe .\tools\render_emotion_preview.py
```

## 自定义自拍照片

把照片命名为下列任意一种形式：

```text
user_assets/selfie.png
user_assets/selfie.jpg
user_assets/selfie.jpeg
```

通常不需要手动复制：`start_onepic.ps1` 会自动把最初上传的原图转换为全分辨率 `selfie.png`。`user_assets/` 中的图片默认被 Git 忽略。没有提供原图时，自拍动作仍会播放，但不会用生成动画末帧冒充原照片。

## 测试与打包

```powershell
.\scripts\test.ps1
.\scripts\build.ps1
```

默认打包生成不含任何 `user_assets/` 的公开演示版本。只有角色和走路均确认后，才能显式构建个人版本：

```powershell
.\scripts\build.ps1 -IncludeUserAssets
```

打包结果位于：

```text
dist/OnePicDesktopPet/OnePicDesktopPet.exe
```

## 公开下载与 macOS 版本

公开安装包由 GitHub Actions 在对应操作系统上构建，发布在仓库的 Releases 页面：

- `OnePicDesktopPet-Windows-x64.zip`：解压后双击 `OnePicDesktopPet.exe`；
- `OnePicDesktopPet-macOS-arm64-unsigned.dmg`：适用于 Apple 芯片 Mac（M1/M2/M3/M4 等）；
- `OnePicDesktopPet-macOS-x64-unsigned.dmg`：适用于 Intel Mac。打开 DMG 后运行 `OnePicDesktopPet.app`。

公开安装包始终使用仓库中的演示角色，不包含 `user_assets/`、用户原图、自拍照片、候选图或私人动作。macOS DMG 目前没有 Apple Developer ID 签名与公证；首次启动时可能需要在 Finder 中按住 Control 点击应用并选择“打开”。

## 一图制作流程

Agent 应先检查环境，再建立项目、处理原图、生成动作、检查多头多腿和裁切问题、接入行为状态机、运行测试，最后在用户验收后打包。详细流程见：

- [Agent 执行入口](agent-guide/AGENT_GUIDE.md)
- [一图桌宠执行说明书](agent-guide/一图桌宠执行说明书.md)
- [素材规范](docs/素材规范.md)
- [角色与走路验收清单](docs/角色与走路验收清单.md)
- [隐私说明](docs/隐私说明.md)
- [GitHub 发布清单](docs/发布清单.md)

## 当前公开状态

源码已发布到 [Taylor154/OnePic-Desktop-Pet](https://github.com/Taylor154/OnePic-Desktop-Pet)，GitHub Actions 测试已经通过。[v0.1.0 Release](https://github.com/Taylor154/OnePic-Desktop-Pet/releases/tag/v0.1.0) 已提供 Windows ZIP，并已完成公开下载、SHA-256 核对、解压和独立启动验证。

## 授权

- 程序代码和项目文档：MIT License；
- `assets/` 中的公开演示美术素材：CC BY-NC 4.0；
- `user_assets/`：不属于公开仓库内容，除非素材所有者另行明确授权。

详细范围和署名方式见 [素材授权说明](ASSETS_LICENSE.md)。
