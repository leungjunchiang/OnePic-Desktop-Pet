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
