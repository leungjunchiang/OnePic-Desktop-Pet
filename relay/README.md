# 六毛自习室中转服务

这里是一个可部署的 Cloudflare Worker 中转层。它只允许六毛客户端所需的固定接口，把请求转发到已有 Supabase 项目；不会暴露 service-role key，也不会接受任意目标 URL。

## 能解决什么

- 桌面端继续每 10 秒在后台发送心跳和刷新房间状态，房间切换、互动和进出房间会立即刷新。
- 客户端只需要访问 Worker 地址，不必直接访问 Supabase REST 域名。
- `/health` 可用于检查 Worker 是否在线；响应不会泄露密钥。
- 客户端仍保留最近一次房间状态，网络短暂中断时不会卡住桌宠或计时器。
- 桌面端会显示当前使用的后端地址，并把 DNS 失败、连接超时、拒绝连接、TLS、认证和服务器错误分开提示。

这套实现是“短轮询的近实时同步”，不是 WebSocket 推送。桌面端现有 Python 网络层不维护长连接；如果未来要做真正的推送，再单独接入 Supabase Realtime 或 WebSocket 客户端。

## 部署

需要一个已登录 Cloudflare 的账号，并且 Supabase 项目已经执行仓库中的全部 migrations。

```powershell
cd relay/cloudflare-worker
npm install
npx wrangler login
npx wrangler secret put SUPABASE_URL
npx wrangler secret put SUPABASE_PUBLISHABLE_KEY
npx wrangler deploy
```

部署成功后，Wrangler 会输出类似：

```text
https://lili-social-relay.<account>.workers.dev
```

仓库不内置未经实际网络验收的公网地址。`workers.dev` 地址是否能被某个地区或运营商稳定访问，必须在目标网络（包括中国大陆家庭宽带、校园网和手机热点）逐一访问 `/health` 验证；仅在开发机或开启代理时可访问，不代表国内直连已解决。

不要把 Supabase key 写进 `wrangler.toml` 或提交到仓库。`SUPABASE_PUBLISHABLE_KEY` 是 publishable/anon key；绝不能使用 `service_role`。

## 让桌面端使用中转

在启动六毛前设置 Worker 地址：

```powershell
$env:LILI_SOCIAL_API_BASE_URL = "https://lili-social-relay.<account>.workers.dev"
Start-Process .\dist\Lili\Lili.exe
```

也可以写入 `config/social_backend.json` 的 `social_api_base_url`，但发布包默认不内置未验证的公网地址。先访问 `/health` 确认服务在线，再配置客户端。

## 安全边界

- Worker 只转发固定的 `/auth/*`、`/dashboard`、`/rooms/*`、`/presence/heartbeat`、`/profile`、`/buddies/*`、`/visits/*` 和白名单 RPC。
- 登录后的请求原样携带用户 Bearer token，由 Supabase Auth 和 RLS 决定权限。
- Worker 只在组装当前用户自己的 profile/presence 路径时解析 JWT 的 `sub`；它不把 JWT 当作权限验证，真正验证仍由 Supabase 完成。
- 建议把 `ALLOWED_ORIGIN` 改成实际桌面 WebView 或管理页的来源；纯桌面 urllib 请求不带 Origin，`*` 也不会扩大 Supabase 权限。
