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

