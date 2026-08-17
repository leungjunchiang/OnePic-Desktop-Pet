# Lili v0.22.73

## 快捷坞与菜单收口

- 五个快捷入口改为跟随六毛窗口移动的轻量红黄蓝图标快捷坞，不再因动画帧边界变化抖动。
- 快捷坞默认只显示图标，设置、大小、主人称呼、更新等低频功能收进齿轮菜单。
- 右键菜单将更新与关于移动到“设置 → 更新与关于”，保留 Windows 托盘和 macOS Dock 的统一命令。
- 保留全部原始图片资源，不做有损压缩或删除。

# Lili v0.22.72

## 认证刷新、菜单与音乐状态收口

- Supabase Direct 与 CloudBase Proxy 共用 `AuthSessionManager`；同一时间只允许一个 refresh 请求，其他首页、自习室同步和后台请求加入同一个刷新结果。
- 每次刷新同时保存 access token、refresh token、过期时间和 generation；遇到 `refresh_token_already_used` 会先读取最新凭据，无法恢复时只提示“登录状态已失效，请重新登录”，不再把 Supabase JSON 直接显示给用户。
- “检测自习室网络”固定为轻量健康探针，不主动刷新登录 token；增加并发 single-flight 回归测试和脱敏刷新日志。
- 六毛右键菜单按高频、设置、系统操作分区；双击快捷口袋改为聊聊、工作、自习室、音乐、设置五个红黄蓝矢量图标，使用悬停说明，不依赖系统 Emoji。
- Windows GSMTC 与 macOS Apple Events 的真实播放器状态统一为 `MusicState`；区分“暂无播放”“播放中但无 metadata”和已读取曲目，避免实际播放却显示“暂无”。
- 打包配置只排除确认未使用的开发/数据科学依赖；保留全部公开宠物图片原始清晰度，不删除或有损压缩图片，也不打包 `user_assets`。

## 体积盘点

- 当前公开源素材：`assets/` 约 63.30 MB，其中 `assets/pet/` 约 56.29 MB、`assets/generated/` 约 7.00 MB；最大单个素材约 1.47 MB。
- v0.22.71 已发布包作为基线：Windows 安装器约 121.03 MB、Windows ZIP 约 146.80 MB、macOS arm64 DMG 约 124.26 MB、macOS x64 DMG 约 129.91 MB。
- 本次没有通过删除或降低图片质量换取体积数字；Python/PySide6 运行时是安装包的主要固定成本，后续陈楚生知识、音频和新服装仍应继续走 `content-v*` 增量包。

# Lili v0.22.71

## 统一快捷入口与跨平台菜单

- 双击六毛的快捷面板只保留聊聊、工作、搭子自习室、音乐和调整大小五个高频入口；工作按钮会根据状态显示开始工作、暂停工作或继续工作。
- 音乐控制收进二级菜单，当前播放内容改为只读状态显示，不再把多个播放器按钮平铺在面板上。
- 六毛右键菜单与 Windows 托盘菜单共用同一份菜单模型，聊天、工作、待办、自习室、音乐、互动、外观、设置和显示/隐藏状态保持一致。
- macOS 增加基于 AppKit `applicationDockMenu:` 的 Dock 右键菜单；菜单栏图标不再是访问六毛功能的唯一入口。

# Lili v0.22.70

## 聊天记录长消息显示修正

- 历史记录消息行会根据窗口宽度自动换行并调整高度，长段文字不再被列表行截断。
- 继续保留 v0.22.69 的独立窗口、可最小化、会话编辑和单条消息编辑/删除功能。

# Lili v0.22.69

## 聊天记录窗口可独立管理

- 移除聊天窗口中难以发现的“⋯ + 下拉箭头”入口，改为直接显示“聊天记录”和“新对话”。
- 聊天记录改为独立窗口，Windows 与 macOS 都有独立任务栏/Dock 项和最小化按钮，不会被聊天窗口遮住或绑定为子窗口。
- 支持编辑会话名称、编辑/删除单条消息、删除单段会话；删除当前会话会同步重置 AI 上下文，但不会删除待办、提醒或其他数据。
- 历史窗口增加“清空当前显示”和“新对话”按钮；生成回复时仍可查看记录，但编辑和删除会安全锁定。

# Lili v0.22.68

## 聊天清空、新对话与本地聊天记录

- 聊天窗口新增“聊天管理”菜单，可只清空当前显示，不影响聊天上下文、待办或提醒。
- 新增“开始新对话”，会清除本地聊天摘要并忘记 Codex App Server thread；下一句话重新建立 AI 对话，已有聊天仍保留在历史记录中。
- 新增本地聊天记录查看与“删除全部聊天记录”，最多保留 20 段会话、每段最多 120 条消息；删除聊天记录不会删除任何待办、提醒或其他应用数据。
- Windows 与 macOS 共用同一套本地数据和 Codex App Server 重置逻辑，继续支持长驻连接、增量回复和本机登录认证。

# Lili v0.22.67

## 修复中文自然语言待办的标题与明天任务显示

- 修复“你能不能给我设置一个明天3点写论文的待办”被保存成带有请求前缀的错误标题的问题。
- 明天及未来 7 天内的已安排待办会立即显示在右侧紧凑待办栏，并标注“明天/还有几天”，不再让已保存的未来任务看起来像没有添加。
- 重复提交会复用并修正此前同一日期的错误标题，不会产生重复待办；今日统计和日报仍只统计今天的任务。

# Lili v0.22.66

## Codex App Server 长驻聊天与增量回复

- Windows 与 macOS 的 Lili 聊天改用长期存活的 Codex App Server：初始化一次并持续复用 thread，每句话只启动一个新的 turn。
- 支持 `item/agentMessage/delta` 增量显示、停止按钮中断当前 turn，以及下次打开时恢复本地保存的聊天 thread；连接异常时保留原有 Codex CLI 回退路径。
- 日常聊天使用 `gpt-5.6-luna` 无推理模式，较复杂问题使用低推理模式，极复杂问题再切换 Terra；明确的日期/时间待办继续优先走本地解析与落盘。
- Windows 使用真实 Codex CLI 安装路径并隔离 App Server 配置，macOS 与 Windows 均保留 Codex 登录与 AI agent 连接能力。

# Lili v0.22.65

## Windows 聊天默认使用更快的 GPT-5.6 Luna

- Windows 与 macOS 的 Lili 对话默认使用 `gpt-5.6-luna` 和低推理模式，减少日常聊天等待时间。
- 保留 `LILI_CODEX_MODEL` 的自定义模型与 `off` 关闭覆盖选项；指定模型不可用时继续回退到 Codex 默认模型。
- 增加 Windows Codex 命令构造回归测试，确认本次选择只作用于 Lili 的一次性聊天进程。

# Lili v0.22.64

## 加快 macOS 聊天恢复并让自然语言待办真实落盘

- macOS 的 Lili 聊天使用独立临时 Codex 会话、低推理模式和 45 秒超时，跳过用户级配置，避免插件/MCP 配置拖慢本机聊天；模型不可用时自动回退到 Codex 默认模型。
- 明确的“明天/后天/具体日期 + 时间 + 待办”请求先由本地解析并写入同一套 Todo/Reminder 数据，再返回确认，不再出现 AI 口头答应但待办没有变化。
- 支持“下午三点”“晚上八点”“明天的待办事项：10点起床”等中文时间表达；普通聊天和歌曲歧义不会被待办快速通道拦截。
- 聊天动作继续复用现有 LocalActionExecutor，并通过原有刷新信号同步桌面待办条。

# Lili v0.22.63

## 修复 macOS 本机 Codex 已连接但聊天回退离线

- 修正 `codex exec` 的非交互调用：完整提示词现在作为命令参数传递，不再把 `-` 当成提示词并仅写入 stdin。
- Finder 启动的 macOS App 会显式补齐用户 `HOME`、`CODEX_HOME`、Shell PATH 与 UTF-8 环境，继续使用绝对路径调用 Codex CLI。
- 保留 Codex GUI、CLI、登录状态三者的独立诊断；Codex CLI 执行失败会记录安全的错误摘要，不记录聊天提示词、令牌或密码。
- 自习室仍然保持 Supabase Direct 优先；本次只修复 AI 聊天调用，不会把 CloudBase 变成主后端或第二数据库。

# Lili v0.22.62

## 固化 macOS TLS 修复并保持 Supabase 优先

- macOS 自习室 HTTPS 请求现在优先使用系统可信 CA（`/etc/ssl/cert.pem` 等），找不到或不可用时回退到应用内置 certifi；始终保持证书校验和主机名校验，不使用 `verify=False`。
- 增加安全 TLS 诊断字段，记录实际使用的 CA 来源，便于区分系统证书、应用证书与网络问题。
- 保持 Supabase 为唯一主后端和首选线路：每次启动都从 Supabase Direct 开始，旧的 CloudBase 线路只作为记录提示；连续网络级失败后才切换 CloudBase Proxy，恢复检查成功后自动切回。
- 业务错误（401/403/参数错误等）不会触发线路切换，也不会把 CloudBase 当作第二数据库。

# Lili v0.22.60

## 修正 Qt/DPI 安全边距测试

- 保留 v0.22.59 的待办操作列安全边距修复。
- 修正 Windows Qt 实际布局边界的回归断言，确保测试验证的是按钮确实没有贴到面板底部，而不是强制要求未必一致的逻辑 padding 数值。

# Lili v0.22.59

## 修复紧凑待办 `+` 按钮底部裁切

- 为操作列增加明确的上下安全边距，确保 `⋯` 和 `+` 在 Windows DPI 缩放下不会触碰宿主窗口边界。
- 面板高度现在同时包含操作列安全边距和原生按钮绘制余量。
- 增加操作列与面板底部留白的回归断言。

# Lili v0.22.58

## 修复程序更新检查的 GitHub 403

- GitHub API 返回 403/429 时自动切换到 Release 网页重定向线路，不再把网页登录和 API 限制混为一谈。
- 更新检查结果缓存 5 分钟，减少重复点击导致的匿名 API 限流。
- Release 网页线路会继续使用官方 GitHub 安装包和 SHA-256 校验文件，不在客户端内置个人 Token。
- 增加 API 限流回退、版本解析和检查缓存测试。

# Lili v0.22.57

## 修复待办文字下半段裁切

- 按真实字体字形高度和行间距重新计算 TodoRow 文本高度，并增加 DPI/抗锯齿安全余量。
- 清除待办文本控件的隐式内外边距，避免中文下沉部和时间末尾字符被裁切。
- 增加回归测试，确保短文本和多行待办的完整字形区域都落在控件与行容器内。

# Lili v0.22.55

## 窗口最小化行为

- “我的时光”和详细待办改为无父窗口的普通非模态窗口。
- 点击系统最小化按钮后进入 Windows 任务栏，不再变成桌面上的附属小框。
- 保留桌宠对窗口的打开、恢复、隐藏和生命周期管理。

# Lili v0.22.54

## 待办与时间记忆管理

- 修复长待办文字、时间和 `⋯` 操作按钮被宠物层或窗口边界遮挡的问题。
- 宠物与待办面板使用硬性不重叠布局，并保留可点击的完整操作区域。
- 倒计时、纪念日支持编辑/删除；时光轴支持删除。
- 增加待办与时间记忆管理的回归测试。

# Lili v0.22.53

## 待办条多行布局

- 长待办在达到内容宽度上限后自动换行，最多显示两行，只有超出两行才使用省略号。
- 每条待办独立拥有 `⋯` 操作按钮，`+` 移到列表末尾，按钮不会被六毛或窗口边界裁切。
- 待办行高根据真实字体测量结果增长，复核了短文本、中等文本和超长文本的布局边界。

# Lili v0.22.52

## 离开检测改为自动分类

- 默认无操作宽限时间改为 10 分钟，支持 5/10/15/20 分钟或 5～120 分钟自定义；宽限内不会暂停、记录或提示。
- 超过宽限后只计算超出的时长，例如无操作 54 分 40 秒只处理 44 分 40 秒。
- 根据锁屏/会话状态、前台应用类别、全屏状态和媒体播放做本地自动分类；高置信度直接记录，低置信度默认记休息。
- 低置信度只显示一次不抢焦点的轻提示，6.5 秒自动收起，可点击“改成专注”；不再闪烁、置顶抢焦点或反复弹出二选一窗口。
- 纠正结果按前台应用保存为本地规则，并保留最近 200 条自动分类记录，便于后续复核。

## 自习室状态容错

- 短暂同步失败时保留最近确认的搭子状态，不再立即错误显示为离线；只有缓存超过宽限期才显示离线缓存。
- 房间连接进入降级状态时，UI 会明确提示正在恢复，而不是覆盖实时状态。

# Lili v0.22.51

## 临近事件自动进入待办

- 倒计时和纪念日现在会在距离目标不超过默认 7 天时自动出现在六毛旁边的统一待办视图中，不再复制成第二条 Todo 数据。
- 支持每条事件自定义提前出现天数；AI 创建/修改倒计时和纪念日时也可以传入 `show_before_days`。
- 一次性倒计时到期后显示“已过期”，年度纪念日会按下一次发生日期重新计算；确认纪念日只隐藏当前年度，不会删除长期记录。
- 今日小纸条、紧凑待办条和聊天操作共用同一个事件投影层，勾选事件会真实完成倒计时或确认纪念日并写入本地数据。

# Lili v0.22.50

## 自习室双时间指标

- 自习室现在明确显示“今日共同专注”和“累计共同专注”两项指标。
- 今日值按北京时间计算当天与房间专注账本相交的时长，累计值来自同一个房间账本的历史总时长。
- 保留旧的 `shared_focus_seconds` 兼容字段，旧客户端仍可读取累计值。

# Lili v0.22.49

- 新增程序更新下载进度条，显示百分比和已下载/总大小。
- 下载器在未知文件大小时显示动态进度，并在校验完成后关闭进度窗口。
- 保留 SHA-256 校验，校验失败不会启动安装程序。

# Lili v0.22.48

## Compact Todo geometry

- Recomputed the pet-attached Todo panel from the real row/action-column layout so long titles elide only the text area.
- Reserved the complete vertical action rail and native companion-window safety space, keeping `⋯` and `＋` fully visible at 100%/125%/150% DPI.
- Added debug geometry logging for the panel, rows, action rail, buttons, host pet window, and monitor bounds.

## Program update feedback

- Fixed tray `QAction` signal wiring so “检查程序更新” is treated as an explicit manual check instead of a silent background check.
- Added update-chain logging, immediate checking feedback, explicit no-Release messaging, and strict semantic-version validation.

# Lili v0.22.47

## Compact Todo action column

- Rebuilt the right-side controls as a fixed 40px action column with complete 32px `⋯` and `＋` buttons.
- Long task titles now elide only the content area; the action buttons remain fully visible and clickable.
- The panel height accounts for the vertical action rail, including an 8px gap between `⋯` and `＋`.

## Program update feedback

- Added a shared `UpdateManager` for content-only and full-program updates.
- “检查程序更新” now immediately shows a checking state and reports latest/current versions, available releases, network failures, malformed metadata, and download verification failures.
- Added release-note previews, semantic version comparisons, and a visible program version in AI and companion settings.

# Lili v0.22.46

## Compact Todo action column

- Compact Todo rows now use fixed segments: checkbox, elided task text, and a 30px `⋯` button.
- The add button is aligned in the same right-side action column below `⋯`; long titles can no longer clip or push either control out of the panel.
- Added geometry tests for the short, long, multi-row, and repositioned pet-attached layouts.

## Full-program automatic updates

- Added automatic release checks with a persistent settings switch.
- Users are asked before downloading; the matching Windows installer or macOS DMG is downloaded from GitHub Releases and verified with SHA-256 before launch.
- Windows can launch the installer and restart Lili automatically; macOS opens the verified DMG for the standard app replacement flow.
- Manual entry is available from the tray menu: `检查程序更新`.

# Lili v0.22.45

## Chat actions and one Todo store

- Chat JSON actions now survive nested `tasks` objects, execute against the same local TodoStore as the desktop panel, and emit a refresh event immediately.
- Added real Todo create/update/complete/delete/query handling, separate `due_at` and `remind_at` fields, source tracking, similar-task merging, and idempotent local reminder synchronization.
- Removed user-facing duplicate “便利贴” entries; chat is named “和六毛聊聊”, and the desktop/tray entry is unified as “待办”.
- Failed local actions now report failure instead of leaving an AI confirmation that was never saved.

## User-controlled content updates

- Added a persistent setting for automatic supplement-content checks (knowledge/configuration/assets only).
- Turning it off disables silent startup checks while keeping the explicit tray action available.

## Content-only online updates

- Added an optional manifest-based content updater. It downloads only changed files under `assets/`, `config/`, and `resources/`, verifies each SHA-256, stages a complete version, and atomically switches one active pointer.
- Added a quiet startup check and a tray action for manual checks. Network failures are silent at startup and never block the pet or replace the running executable.
- Successful content patches clear the local knowledge/song/story caches and refresh pet assets when possible; code/EXE updates remain a separate release operation.

## Pet-attached Todo sizing

- Compact Todo width now hugs the longest visible task while staying between 156px and 320px; labels ellipsize only when the bounded width is reached and keep the full task in a tooltip.
- Repositioned the pet-attached Todo strip immediately after its width changes so it remains anchored to 六毛 rather than leaving a stale gap.

# Lili v0.22.43

## Pet-attached Todo placement

- Repositioned the compact Todo strip with the pet as its anchor: left side first, right side when needed, and below only as a fallback, with an 8px horizontal gap and screen-edge clamping.
- Tightened the row and panel dimensions so a single task remains a small accessory instead of a detached mini application.
- Made each `⋯` action a clear 26×26 button with a visible background, hover/pressed feedback, and the existing real task menu hit area.
- Updated regression coverage for left/right/below placement decisions and the compact action button.

## Test harness correction

- Kept the compact Todo interaction regression test non-blocking while still exercising the real `⋯` button click and request-signal path.
- Updated the pet-following regression assertion to use the visible character mask, matching the accessory layout's 8px gap instead of the transparent window rectangle.

## Compact Todo attachment and accessory layout

- Fixed collapsed `CompactTodoPanel` so it always keeps the highest-priority Todo visible; an empty list shows only the small add affordance.
- Replaced the competing collapse controls with one real expand/collapse button, and kept the `⋯` action as a mouse-enabled `QToolButton` whose menu remains clickable after refreshes.
- Prioritized the current task, pinned tasks, nearest scheduled time, and creation time for the compact row.
- Reflowed the speech bubble, compact Todo strip, and detailed 便利贴 from the pet as one anchor; the Todo strip stays below the pet and the detailed note moves to the available side with screen-edge fallback.
- Added regression coverage for one-row collapse, three-row expansion, action-button hit testing, and pet-following placement.

- Added a local-first Chen Chusheng song understanding layer with 117 public song cards containing titles, themes, emotions, imagery, and 六毛 usage hints.
- The app can match a user-selected local lyric TXT in memory to recognize a song or a user-provided lyric fragment, without putting lyric lines into prompts, replies, build artifacts, or the GitHub Release.
- Added copyright-safe song context to online AI prompts and offline fallback replies; ambiguous bare phrases remain ordinary conversation instead of being forced into a song match.
- Added regression coverage for public-card safety, explicit song disambiguation, local fragment recognition, and non-continuation behavior.

# Lili v0.22.37

- Replaced the compact Todo QDialog with a frameless `CompactTodoPanel` that sits directly below 六毛 and follows pet movement across screens and DPI changes.
- Compact mode now shows only checkbox Todo rows, optional `· HH:MM`, a tiny `⋯` task menu, a small `＋` entry, and a bounded overflow control; no title bar, note text, focus totals, room state, or statistics are rendered.
- Kept standalone 便利贴 as a separate detailed free-form window. Right-click menus now distinguish `待办` from `便利贴`, and switching display modes routes to the correct surface.
- Added compact-panel regression coverage for one task, three tasks, collapse state, completion preview, frameless flags, and movement coupling to the pet.

# Lili v0.22.36

- Separated the compact window from the 便利贴 presentation: compact mode now renders only today's checkable Todo rows and a small `＋` add entry. It no longer shows note text, focus totals, task counts, room status, or other dashboard statistics.
- Renamed the former 今日小纸条 UI to 便利贴 and added a small, locally persisted free-form text area in the detailed view. The note is stored separately in `sticky_note.json` and is never rendered in compact mode.
- Removed the custom folded mini-bar behavior. The window keeps the normal Windows minimize button and minimizes to the taskbar; compact mode is an explicit display choice, not a substitute for minimizing.
- Added checkbox completion handling, restart-safe sticky-note storage, and offscreen UI regression coverage for task-only compact rendering and normal minimization flags.

# Lili v0.22.35

- Added three clear 今日小纸条 modes: detailed, compact, and fully hidden. The compact note is a small 280px pet-style panel that follows below 六毛; the detailed mode adapts to its content and caps the task list height.
- Kept the note lightweight: the header and bottom row now focus on the selected task, with `▶ 开始`, `✓ 完成`, and `＋`; folding, display settings, time memory, check-out, and rest day live under `···`.
- Selecting a task no longer hides or removes it. Completed tasks stay in today's list with a check mark and strike-through; edit/delete are available from the more menu and each task's context menu, with optional hide-completed display.
- Added persistent `today_note_mode` settings and regression coverage for the three-mode configuration. Hidden notes stay closed on startup but can still be opened temporarily from 六毛's menu.

# Lili v0.22.34

- Rebuilt 今日小纸条 as a compact 360px floating note with adaptive height capped at 420px; oversized controls moved into the `···` menu and task overflow stays inside the list.
- Clicking a task now only selects it. Starting and completing are explicit actions; completed tasks remain visible with a check mark, muted text, and strike-through, with an optional hide-completed setting.
- Reduced the core action row to `▶ 开始`, `✓ 完成`, and `＋`, while keeping check-out, rest day, display settings, and time memory available from the more menu.
- Added regression coverage confirming completed tasks remain in today's data and the selected task remains selected after completion.

# Lili v0.22.33

- Added a local-first time-memory system for today's note, Todo items, reminders, work-session attribution, daily check-out records, weekly/monthly statistics, and rest days.
- Added local Countdown, Anniversary, and curated Timeline modules with shared Beijing/local-time date calculations, restart-safe atomic JSON persistence, and idempotent milestone records.
- Added a small 今日小纸条 window with show/hide/fold/task-start/task-complete/check-out/rest-day actions, plus a non-resident 我的时光 review window for countdowns, anniversaries, and timeline events.
- Connected AI chat to real local time-memory context and explicit structured actions; the model can interpret task/countdown/anniversary/timeline requests, while local code performs all persistence, date math, reminders, and queries.
- Connected focus sessions to selected tasks and daily records. Normal application exit now persists the final running segment before pausing the shared timer.
- Added 19 pure-Python regression tests covering persistence, date boundaries, task attribution, reminders, check-in/check-out, statistics, countdowns, anniversaries, timeline idempotency, structured actions, and restart behavior.

# Lili v0.22.25

- Fixed idle recovery duration: the five-minute setting is only the automatic-pause threshold; the recovery dialog now reports the full elapsed absence.
- Kept idle recovery as one reusable, one-shot decision window instead of repeatedly opening dialogs.
- Added a local, block-indexed Chen Chusheng knowledge base with relationship and song catalogs.
- Added high-confidence story triggers with cooldowns and calmer, slower Lili companion replies.
- Injected the short Lili persona on every AI request and only retrieved relevant knowledge blocks.
- Added regression tests for idle duration, selective retrieval, ten story conversations, and story cooldown behavior.

# Lili v0.22.24

- Room activity timestamps are now rendered consistently in Beijing time (UTC+8), regardless of the computer's local timezone.
- Shared task countdowns and room ritual checks use the same Beijing-time reference.
- Added regression coverage for converting UTC Supabase timestamps to Beijing time.

# Lili v0.22.23

- Fixed Supabase Direct presence heartbeats by adding the required PostgREST merge-duplicates preference to `lili_focus_presence` upserts. Direct clients no longer hit the primary-key duplicate error after the first heartbeat, so active buddies correctly remain online and room focus counts update.
- Added regression coverage for the direct heartbeat upsert header. CloudBase and Edge relay behavior remains unchanged.

# Lili v0.22.22

- Fixed false offline buddy status by making Supabase the authority for `last_seen` and room session timestamps. Desktop, CloudBase proxy, Edge relay, and Cloudflare relay no longer trust a skewed client clock.
- Replaced the corrupted room focus accumulator with an idempotent room-scoped focus-session ledger. The room summary now reports the total focus time accumulated in that room, including closed sessions and currently active sessions, without double-counting heartbeats or room switches.
- Reset previously contaminated room totals during the production migration and added regression contracts for server freshness and ledger-based room totals.

# Lili v0.22.21

- Fixed owner nickname synchronization by exposing the active authenticated Supabase session through the route-aware social client. Existing local owner nicknames now reach the single Supabase profile source of truth, so buddies can see the intended `{owner_nickname}家的六毛` label instead of the neutral fallback.
- Fixed offline dashboard messaging: local focus started without selecting a study room no longer appears as a failed study-room connection. A selected room still reports a real room outage and keeps the cached state visible.
- Added regression coverage for the active-session bridge and the no-room local-focus state.

# Lili v0.22.20

- Startup and sign-in now probe Supabase Direct first instead of trusting a stale saved CloudBase route.
- CloudBase fallback is selected only after two network-level Direct failures; authentication and business errors never trigger a route switch.
- Login credentials are submitted once on Direct and, only after a network failure, once on the CloudBase proxy. Failed account switches no longer leave an old user session active.
- Direct recovery checks run every minute while using the proxy, so changing VPN/network conditions is reflected promptly.
- Updated route regression tests and release metadata to avoid garbled release titles and notes.

# Lili v0.22.19

- Supabase remains the only authentication, PostgreSQL/RLS, room, focus and interaction source of truth.
- Added a single BackendRouteManager: Supabase Direct is preferred; CloudBase is only a restricted mainland HTTP proxy fallback and never a second business database.
- Health checks are lightweight and isolated: one request, no dashboard/presence/listener initialization. Visible room snapshots refresh about every 30 seconds; presence writes are limited to state changes and about 90-second heartbeats.
- Network failures retry once before fallback; authentication and business errors do not switch routes. Two spaced recovery probes return the client to direct Supabase.
- Kept explicit room selection, exclusive room membership, room-scoped events/interactions, live server-time rendering and room-scoped cumulative focus totals.
- Added route/proxy contract tests and updated the CloudBase function source to forward only allowlisted Supabase Auth/REST/RPC requests without service-role credentials in the desktop app.

# Lili v0.22.12

- Fixed the deployed Supabase Edge Function relay to normalize both gateway-prefixed and function-prefixed request paths.
- Added safe route-not-found diagnostics without logging authentication tokens.
- Redeployed `lili-social-relay-v2` as active version 3; `/auth/*`, `/health`, dashboard, presence, room, buddy, visit, and RPC routes now share the same path normalization.

# Lili v0.22.11

- Fixed Supabase Edge Function route normalization so hosted `/functions/v1/<function>/...` request paths correctly reach authentication, dashboard, presence, and study-room routes instead of returning `Study-room route not found`.
- Redeployed `lili-social-relay-v2` as an active version and added a route contract assertion.

# Lili v0.22.9

- The published desktop configuration now routes study-room traffic through the deployed `lili-social-relay-v2` Supabase Edge Function instead of calling the Supabase REST API directly.
- Added stable relay routes for authentication, presence heartbeat, dashboard, room state, room events, interactions, and the allowlisted study-room RPCs.
- The relay validates the current user's Bearer token upstream and never ships a `service_role` key to the desktop client.
- Added contract tests for the default relay configuration and the Edge Function route/security boundary.
- The Edge Function is deployed and healthy in Supabase; Mainland China no-VPN reachability still requires a real target-network `/health` test.

# Lili v0.22.7

本次版本把六毛对话和搭子自习室的两条链路重新接稳：

- 六毛对话使用固定角色知识、按需话题检索和最近上下文，连续追问“他”“这首”“后面一句”不再脱离陈楚生世界观；明确歌词续写会改为安全提示，不随机接话。
- 对话保留本机有界摘要和最近 30 轮，在线请求只发送角色设定、命中的相关知识和有限上下文；聊天设置中的隐私说明与实际行为一致。
- 修复在线聊天状态重复显示“已连接”的问题。
- 自习室首页显示实际后端（Supabase 或配置的 HTTP 中转），新增健康检查。
- 自习室网络错误区分 DNS、连接超时、拒绝连接、TLS/证书、认证、HTTP 和服务器错误；网络失败仍保留最近房间状态，不阻塞桌宠与计时。
- Cloudflare Worker `/health` 返回后端类型与短轮询能力；仓库不内置未经中国大陆目标网络实测的公网中转地址。

# Lili v0.19.0

本次版本重点修复点歌结果与实际播放不一致、误进歌手主页以及含义不清的“更新失败”。

- 点歌改为 `search → exact match → play → verify`，只有媒体会话返回的歌名和歌手都匹配才显示播放成功。
- 搜索结果仅接受歌曲类型；歌手、专辑、MV、歌单及歌手不匹配的结果会被拒绝。
- QQ 音乐、网易云音乐、酷狗音乐、Apple Music、Spotify 使用独立 Provider Adapter，不共享固定坐标或页面布局假设。
- 删除搜索后按方向键、回车或固定坐标播放第一条结果的旧逻辑；指定歌曲点播不会用全局播放键续播旧队列。
- 实际歌曲不匹配时最多重试一次精确播放，随后返回明确结果；不再伪装成成功。
- 内部区分 `SEARCH_FAILED`、`RESULT_NOT_FOUND`、`PLAY_ACTION_FAILED`、`MEDIA_SESSION_TIMEOUT`、`TRACK_VERIFY_FAILED`，并记录请求、候选和当前媒体信息调试日志。
- Windows 使用 UI Automation 定位歌曲行与该行播放按钮，再由 GSMTC 校验；macOS Apple Music 使用 Apple Events，其他客户端使用已授权 Accessibility Adapter。

同时包含 v0.18.1 的 macOS Codex CLI 绝对路径检测、最近 30 轮聊天记忆、五类右键菜单和四标签搭子自习室改进。
## v0.22.61

- 修复 macOS Finder 启动的 `.app` 找不到 CA 根证书导致 CloudBase 自习室 TLS 连接失败的问题：发布包显式携带 certifi CA bundle，并在 macOS 社交请求中使用正常证书校验的 SSL context。
- TLS 失败日志增加平台、Python/OpenSSL、certifi 路径和系统验证路径诊断；不关闭证书校验，不使用 `verify=False`。
- 修复 macOS 图形应用与终端环境 PATH 不同导致 Codex CLI 找不到的问题，继续使用绝对路径和 `codex login status` 独立检测。
- macOS 自习室与 Codex 聊天保持独立连接状态；一个服务失败不会把另一个服务误判为离线。
- macOS App 的 Bundle 版本号改为从项目版本动态生成，避免安装包仍显示旧版本。

