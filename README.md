# Lili 桌面工作搭子

一只可以在桌面跑动、休息、互动、吃喝、陪你聊天并记录工作时间的六毛形象公仔。项目仍使用 OnePic Desktop Pet 的现有代码与一图制作流程。

应用和安装文件名为“Lili”，宠物本人仍叫“六毛”。`v0.8.0` 默认站立高度为 160，并提供 100–360 像素连续滑块；六束紧密红色头发、蓝色发梢与锯齿脸、黄色连体衣和粉色爱心始终保持一致。

![六毛公仔走路预览](assets/pet/walk-preview.gif)

## 当前功能

- 透明无边框窗口、桌面置顶和多显示器 DPI 适配；
- 站立、跑动、坐下、入睡、醒来、拖拽和自拍连续动画；
- 摸头、分区点击、连续戳击、悬停注视和情绪反馈；
- 可喂苹果、小饼干、热牛奶，也可选择咖啡或热茶；
- 半透明、无红框聊天面板支持纯离线、Codex、Claude Code、DeepSeek、Kimi 和其他 OpenAI 兼容接口；macOS 字体优先苹方简体；
- 本机有 Codex/Claude Code 且已经登录时可直接复用，不需要另填 API Key；不可用或在线连接失败时自动离线回答；
- DeepSeek/Kimi 的 API 令牌只保存在系统安全凭据库，不写入设置、源码或 GitHub；
- 开始计时后六毛会敲电脑；摸头或点击电脑会直接弹出“暂停工作 / 结束工作”气泡；
- 双击六毛切换快捷口袋；选择操作后自动收起，闲置八秒也会消失，不依赖 Windows 托盘或 Mac 菜单栏；
- 能按前台应用类别切换电脑、写字、看书、耳机等动作；只识别应用类别，不读取窗口标题或文档内容；
- 可主动选择醒目的耳机、吉他和鼓；点击随机歌曲后优先打开已安装的网易云音乐或 QQ 音乐客户端，自动搜索并尝试播放，未安装时才回退到正版网页；
- 连续专注约 25 分钟会收到鼓励，约 50 分钟及更长时段会收到休息劝慰；
- 陪伴动作新增伸懒腰、一起想办法、安静陪伴和击掌庆祝，并用多段现有动画组合表现；
- 可让六毛间断性发牢骚、报时或显示受歌曲意象启发的原创短句；报时、喝水、站立休息均可单独开关；
- 六毛点击时会说“巴布达”（可关闭），并以“小小大人”的孩子视角陪伴；
- 每累计工作 1 小时解锁一套娃衣或配饰，共 10 套；第十套为戴金色皇冠和披风的“荒野国王”；
- 扩充对工作压力、自我怀疑、拖延、犯错、孤独与爱意的暖心回应；
- 跑动结束后随机站立、坐下或自拍；
- 默认 5 分钟无互动后坐下、10 分钟后入睡；
- 连续尺寸调整、暂停跑动、隐藏和退出；不同动作不会改变宠物尺寸；
- 用户可在本地放入自己的自拍成片，不提交到 Git；
- 原图登记后自动作为自拍成片，保持原始像素尺寸；
- 标准角色形象和走路 GIF 必须分别得到用户确认；
- 表情符号由程序独立绘制，换角色后仍可显示闪光、爱心、惊叹号、疑问号、怒气、Zzz 和汗滴；
- PyInstaller Windows 打包脚本。

## 喂食、对话与 AI

右键点击六毛，选择“给六毛喂食/饮品”，可以喂苹果、小饼干、热牛奶、咖啡或热茶；选择“和六毛聊聊”会打开聊天面板。本地规则可以回应工作、学习、疲惫、自我怀疑、拖延、犯错、孤独与爱意等常见话题。普通点击就是和六毛打招呼或调戏，因此不再提供单独“打招呼”菜单。

默认是“纯离线”，不需要账号或网络。右键选择“AI 与陪伴设置”后，可切换：

- `Codex`：自动检测本机 Codex CLI，复用当前登录，以临时、只读会话回答；
- `Claude Code`：自动检测本机 CLI，以一次性、无工具、无会话持久化模式回答；
- `DeepSeek`：默认使用 `https://api.deepseek.com` 与 `deepseek-v4-flash`；
- `Kimi`：默认使用中国区 `https://api.moonshot.cn/v1` 与 `kimi-k3`；
- `其他兼容 API`：填写自有 HTTPS 地址和模型名称。

设置页的“检测是否连接”不会发送聊天内容：Codex 使用官方 `codex login status` 检查登录；Claude Code 检查本机认证状态；API 通道使用只读的模型列表请求验证地址和令牌。

在线模式会把当前消息与最近少量上下文发送给所选服务。聊天记录只放在当前进程内存里，关闭 Lili 后不会落盘。令牌由 Windows 凭据管理器或 macOS 钥匙串保存。不要把 API Key 发到 Issue、聊天记录或截图里。

OpenAI 当前公开资料没有提供让第三方独立程序直接接管 Codex 内置宠物的接口。因此本版本采用受支持的 `codex exec` 联动方式；Lili 仍是独立桌宠。若要在 Codex/ChatGPT 桌面应用里使用 Lili 外观，需要在该应用的“设置 → Pets”中另行创建并选择自定义宠物。

## 工作计时与陪伴动作

右键点击六毛，进入“工作计时”开始或继续。开始后暂停与结束不会藏在菜单中：六毛会摆出电脑，摸他的头或点击电脑即可看到两个控制气泡。计时跨应用重启保留当天累计与终身累计，到第二天只重置“今日”数字。自然退出程序时会自动暂停，关机或未运行的时间不会被算作工作。

“六毛陪伴动作”中可以主动选择专注、加油、抱抱、庆祝、安慰、休息、伸展、想办法、安静陪伴和击掌。音乐动作另提供耳机、吉他和鼓。

程序没有打包陈楚生歌词或音频。它只保存歌曲标题与原创化的意象短句。Windows 客户端调用会在用户明确点击后短暂聚焦音乐软件，通过搜索框尝试播放第一条结果；不同客户端版本若没有响应，会保留搜索结果供用户点击。

工作记录只在本机应用数据目录保存日期和累计秒数，不保存任务名称、输入内容或聊天历史，也不会上传到 GitHub 或任何网络服务。

## 最快体验

Releases 页面提供可直接运行的 Windows 和 macOS 版本，不需要安装 Python。本地开发版可执行：

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
dist/Lili/Lili.exe
```

## 公开下载与 macOS 版本

公开安装包由 GitHub Actions 在对应操作系统上构建，发布在仓库的 Releases 页面：

- `Lili-Windows-x64.zip`：解压后双击 `Lili.exe`；
- `Lili-macOS-arm64-unsigned.dmg`：适用于 Apple 芯片 Mac（M1/M2/M3/M4 等）；
- `Lili-macOS-x64-unsigned.dmg`：适用于 Intel Mac。打开 DMG 后运行 `Lili.app`。

公开安装包使用仓库中的六毛公仔动作，不包含 `user_assets/`、用户原图、自拍照片、候选图、私人动作或聊天记录。macOS DMG 目前没有 Apple Developer ID 签名与公证；首次启动时可能需要在 Finder 中按住 Control 点击应用并选择“打开”。

## 一图制作流程

Agent 应先检查环境，再建立项目、处理原图、生成动作、检查多头多腿和裁切问题、接入行为状态机、运行测试，最后在用户验收后打包。详细流程见：

- [Agent 执行入口](agent-guide/AGENT_GUIDE.md)
- [一图桌宠执行说明书](agent-guide/一图桌宠执行说明书.md)
- [素材规范](docs/素材规范.md)
- [角色与走路验收清单](docs/角色与走路验收清单.md)
- [隐私说明](docs/隐私说明.md)
- [GitHub 发布清单](docs/发布清单.md)

## 当前公开状态

源码维护在 [leungjunchiang/OnePic-Desktop-Pet](https://github.com/leungjunchiang/OnePic-Desktop-Pet)。`v0.8.0` 提供 Lili 的 Windows ZIP、Apple 芯片 Mac DMG、Intel Mac DMG 及各自的 SHA-256 校验文件；旧版本仍保留在 Releases 页面。

## 授权

- 程序代码和项目文档：MIT License；
- `assets/` 中的公开演示美术素材：CC BY-NC 4.0；
- `user_assets/`：不属于公开仓库内容，除非素材所有者另行明确授权。

详细范围和署名方式见 [素材授权说明](ASSETS_LICENSE.md)。
