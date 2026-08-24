## v0.23.92 — 统计口径统一与自习室界面自适应

- 修复跨设备日度历史到达后仍被旧“本周累计最大值”覆盖的问题；日度、本周和月度报告统一优先使用同一批北京时间日数据，并同步校正计时器和心跳数据。
- 自习室首页、互动、专注、我的四个主选项卡改为随窗口宽度均分，自适应窄窗口且不再固定挤在左侧。
- 右键菜单移除失效的“工作记录”子菜单和手动“六毛互动”入口，工作报告改为直接入口；工作中会按低频随机节奏自动出现加油或抱抱陪伴提示。
- 增加回归测试，覆盖旧周快照污染、跨设备日数据优先级、选项卡自适应和菜单入口收敛。

## v0.23.91 — 修复报告横轴标签裁切

- 修复工作报告图表底部绘图区计算错误导致横轴日期、星期和小时标签被画到控件外并被裁切的问题。
- 周报保留周一至周日的完整日期标签；月度节律显示 00–24 时段标签，未发生日期继续以缺失值展示。
- 新增离屏回归测试，确保图表柱体与横轴标签始终位于控件可视区域内。

## v0.23.90 — 自习室窄窗口与报告日期轴修复

- 修复“我的”页面在窄窗口中被子控件最小尺寸撑宽的问题；账号按钮、表单和选项卡现在会随窗口宽度收缩，文字不再跑到可视区域外。
- 修复本周工作时长图表横轴日期被裁掉的问题，明确显示月/日与星期，并保留悬停查看完整日期和精确时长。
- 本周工作节律纵轴改为仅显示日期（如 8/24），避免日期与周几堆叠；图表保留完整 0:00–24:00 时间轴。
- 日度有效工作时间允许超过 8 小时；只有达到 24 小时这一不可能的整日值才会被限制，避免把长时间正常工作误判为异常。

## v0.23.89 — 统计纠偏与自适应工作报告

- 统一使用北京时间（Asia/Shanghai）解析专注记录，避免 UTC/本地时区导致日期和时段错位。
- 本地账号的 FocusSession 原始记录优先于旧服务器聚合快照；旧的“最大值”不再覆盖本地真实数据，也不会通过心跳反馈再次污染服务器。
- 修复计时器继承陈旧今日总时长的问题；检测到可靠本地记录时自动以记录校正当天基线。
- 周报固定展示周一至周日，月报固定展示当月完整 28/29/30/31 天；尚未到达的日期显示缺失值，不伪造 0。
- 工作图表补齐日期、星期、时间段和可读坐标轴刻度，悬停可查看具体数值；工作节律使用明确的小时区间标签。
- 修复报告图表在窄窗口中的裁切与固定宽度问题，账号页按钮随窗口宽度自适应。
- 新增 Supabase 精确纠偏迁移，允许开发端用可靠的本地日/周数据修复历史错误聚合值。

## v0.23.88 — 邮箱验证码重置与工作报告时间轴

- 忘记密码改为 Supabase Auth 六位邮箱验证码：验证码有效期 10 分钟，客户端不保存验证码；验证成功后直接设置新密码，不要求输入旧密码。
- 账号与安全、未登录“忘记密码”统一使用验证码重置流程；恢复会话仍由 Supabase Auth 管理，可直接回到自习室登录状态。
- 工作报告改用真实 FocusSession 区间：日度显示 0:00–24:00 时间轴，本周显示周一至周日 × 24 小时节律图，月度保留日历热力图并显示典型工作时段。
- 工作时长柱状图补齐日期、星期、坐标轴和易读时间刻度；悬停可查看精确时长、专注段和任务，避免把小时聚合误当成具体开始/结束时间。
- Supabase Auth 自动配置脚本同步写入 recovery 邮件验证码模板、六位长度和 600 秒过期时间。

## v0.23.87 — 三日连登娃衣与自习室账号提示修复

- 新增「三日连登搭子」娃衣：同一 Supabase 账号按北京时间连续登录 3 天后解锁，奖励状态保存在服务器，不随电脑或本地安装包混用。
- 增加幂等的 `lili_record_login` 登录记录 RPC；重复打开软件或同一天重复登录不会重复累计，登录奖励网络异常也不会把成功登录误报为失败。
- 修复已有账号重复注册时的误导：重复注册不会覆盖原密码，也不会因为账号已存在而产生新的确认邮件；界面明确引导使用原密码登录或「忘记密码」重置。
- 保留后台注册、重发确认邮件和密码重置，超时不再盲目重复提交；继续提示检查垃圾邮件，并明确说明 163/学校邮箱的投递依赖 Supabase Auth SMTP。

## v0.23.63 — 注册不再卡住，超时提示改为准确状态

- 注册和“重新发送确认邮件”改为后台线程执行，SMTP 响应慢时窗口仍可操作，不会因为等待邮件服务而整個假死。
- 注册超时不再提示“可能已经创建账号”并引导用户盲目重发；现在明确说明服务端结果暂时无法确认，避免重复提交。
- 增加注册后台线程的成功、超时和密码清理回归测试。
- 说明：本版本修复客户端卡顿和误导提示；如果 Supabase Auth 的 SMTP 仍然超时，仍需管理员使用稳定的事务邮件 SMTP，并不会由桌面端替用户填写或暴露 SMTP 凭据。

## v0.23.60 — 减少 SMTP 部署配置

- v0.23.61：防止旧版内容更新覆盖 Supabase 后端配置，避免再次回退到失效的 CloudBase 地址；修复直连模式退出登录时的空中转引用。

- 自动从公开配置读取 Supabase project ref 和确认跳转地址，SMTP 端口、From 邮箱和发件人名称提供安全默认值。
- 开发者只需准备 Supabase Management Token、SMTP 主机、SMTP 账号和 SMTP 密码/授权码四个 Secrets。
- 修复旧版腾讯云 CloudBase 中转超时：生产桌面端默认只使用 Supabase Direct，不再自动切换到失效的中转地址。

## v0.23.59 — 自动配置 Supabase Auth SMTP

- 新增 `configure-supabase-auth` GitHub Actions 工作流，开发者只需在 GitHub Secrets/Variables 配置一次，工作流即可通过 Supabase Management API 写入 Auth SMTP 设置。
- SMTP 密码、Supabase Management Token 只从 Actions Secrets 读取，不进入源码、安装包或桌面客户端。
- 增加 PowerShell 配置脚本和静态安全回归测试；终端用户仍只填写邮箱和密码。

## v0.23.58 — 修复邮箱注册确认反馈与重发

- 注册接口不再把“已创建账号但等待邮箱确认”误判为失败，163、学校邮箱等需要确认的账号会明确显示待确认状态。
- 增加确认邮件重发按钮，并统一清理复制邮箱地址时可能带入的不可见字符和全角字符。
- Direct、CloudBase 和 Cloudflare 中转均支持 Supabase Auth 的 `/auth/v1/resend`；点击确认链接跳转到项目页后，回到 Lili 登录即可看到注册完成状态。
- 注意：Supabase 默认 SMTP 只向项目团队预授权邮箱投递。面向普通用户发布前，必须在 Supabase Auth 中配置自定义 SMTP。

## v0.23.57 — 修复 Windows 点击桌面后六毛消失

- 修复 Windows 点击六毛旁边的桌面空白区域后，Explorer 的 `Progman`/`WorkerW` 桌面窗口被误判为全屏，导致六毛和状态气泡被隐藏的问题。
- macOS Finder/访达桌面与 Windows Explorer 桌面现在统一不参与真实全屏判断；视频、PPT 等真实全屏仍会让位，退出后恢复。
- 增加 Windows 桌面壳层窗口类名回归测试。

## v0.23.56 — 修复 macOS 点击桌面后六毛消失

- 修复 macOS 桌面模式下点击六毛旁边的桌面空白区域后，Finder 的屏幕大小桌面窗口被误判为全屏，导致六毛和状态气泡被隐藏的问题。
- 真实视频/PPT 全屏仍会让位；退出真实全屏后继续恢复进入前的可见状态。
- 兼容英文和中文 macOS 的 Finder、Dock、控制中心、通知中心等桌面壳层名称，并增加回归测试。

## v0.23.55 — 调整串门状态位置

- 将“正在串门”小气泡放到六毛下方的状态行，并固定在“已工作”时长气泡左侧，不再覆盖待办内容。
- 拖动六毛或贴近屏幕右下角时，串门状态仍跟随六毛重新排布。
- 增加串门状态与已工作气泡横向排列、位于六毛下方的回归测试。

## v0.23.54 — 修复跨设备食物互动造型

- 发送或接受咖啡、奶茶、茶和蛋糕互动时，双方六毛都会切换到对应的物品限定造型；已装备的工作时长娃衣不会再把食物造型遮住。
- 食物互动造型不会暂停正在进行的专注计时，也不会把对方送来的食物误记入本地库存；造型结束后自动恢复正常工作/待机状态。
- 蛋糕分享的发起方会立即加入庆祝造型，接收方接受邀请后切换为同一场蛋糕造型。
- 增加“专注中 + 已装备娃衣 + 蛋糕互动”的回归测试。

## v0.23.53 — 修复蛋糕好友选择显示

- 蛋糕分享好友改用明确的复选框和选中行样式，点击后会保留可见的勾选框、边框和浅绿色选中背景。
- 避免 macOS 原生列表选中态把好友选择行刷成纯白，导致看不到边框和选中状态。
- 增加蛋糕好友选择控件的选中/取消选中回归测试。

## v0.23.52 — 修复昨日差值与全屏状态气泡

- 修复“较昨天”错误复用本周累计时长的问题，改为使用北京时间的昨日单日专注时长；无法确认昨日数据时显示“暂无可靠数据”。
- 修复进入全屏后，实时计时刷新又重新显示“已工作”气泡的问题；全屏期间六毛及其被隐藏的状态浮层保持让位，退出全屏后恢复进入前的可见状态。
- 增加昨日差值和全屏期间计时刷新场景的回归测试。

## v0.23.51 — 修复本周专注时间显示滞后

- 修复暂停或结束专注后，专注页的本周时长仍显示旧快照，而自习室已显示最新时长的问题。
- 渲染时补入本地 FocusSession 中尚未写入分析记录的今日专注秒数，并同步修正“较昨天”比较值。
- 增加暂停写入时序下的本周统计回归测试。

## v0.23.50 — 修复 macOS 快捷栏悬停标签

- 鼠标移动到快捷按钮时立即显示对应文字，不再需要先点击按钮；移动到另一个按钮时标签会同步切换，移出快捷栏后自动消失。
- macOS 增加应用级鼠标移动监听和定时兜底，并在显示标签前完成原生非激活窗口配置，避免首次悬停标签被 AppKit 隐藏。
- 增加快捷栏悬停切换及 macOS 首次显示顺序的回归测试。

## v0.23.49 — 全屏让位、重启清理串门与安全更新

- 检测视频/PPT 等真实全屏窗口时，暂时隐藏六毛、快捷口袋和所有状态气泡；退出全屏后恢复进入前的可见状态、位置和计时显示，并兼容 macOS 多显示器及 Retina 尺寸。
- 应用重新启动时不再复活上一次进程遗留的串门场景；本次启动后新收到的串门仍可正常显示。
- 启动后台检查到新版本时只做非打断提示，不再自动弹出更新确认或下载/退出；只有用户从托盘“更新与关于”主动检查时才进入更新流程。

## v0.23.48 — 北京时间统计、macOS 不抢焦点与六毛自习室舞台

- 今日/昨日/本周专注统计、排行榜和跨电脑合并统一按北京时间 00:00–23:59 及周一开始计算；已同步修复现有 Supabase 项目。
- macOS 桌宠、快捷口袋、已工作/串门/饮品气泡都在显示前配置为非激活面板；开始专注和点击六毛不会再把 Lili 抢到 ChatGPT/Codex 前台，输入框可以持续打字。
- 自习室成员改为紧凑的六毛舞台，显示当前娃衣、工作状态、今日/本周时长、最长连续专注和中断次数；暂停超过 10 分钟才记一次中断，房间动态只保留最近 3 条。
- 房间码改为点击“邀请好友”后才显示，增加“喊大家开工”轻互动；同一 FocusSession 继续由桌面计时器统一负责，不创建第二个计时器。
- 将用户提供的陈楚生材料整理成启动时加载、按关键词命中的短知识卡片，补充《白石洲》和“谁比谁差”的具体语境；原始研究文件和歌词正文不进入安装包、不上传、不注入回复。

## v0.23.41 — 修复 macOS 附件跳动与同账号跨电脑同步

- 快捷选项固定跟随六毛头顶，已工作计时固定放在六毛脚下；开启时自动预留底部空间，关闭时不占用该空间，拖到右下角也不再上下跳动。
- 同一账号在不同电脑之间合并同步今日/终身专注时间，采用服务端最大确认值避免覆盖和重复计算。
- 娃衣装备和专注解锁进度随账号同步；原有本地计时与旧中转服务保持兼容。

## v0.23.40 — 修复 macOS 双托盘、跨平台在线状态与自习室交互界面

- macOS 只创建原生状态栏入口，避免 Qt 托盘图标与 NSStatusItem 同时出现两个 Lili。
- 修复 CloudBase Presence upsert 缺少 PostgREST 合并头导致 Windows/macOS 状态不同步的问题；心跳单次失败也不会阻塞同一轮状态读取。
- 修复离线用户仍携带旧 working 标记时显示灰点却“正在工作”的矛盾；排行榜旧账号默认显示，明确关闭仍然有效。
- 咖啡、昂贵咖啡、奶茶、蛋糕和茶的造型仅在对应物品/互动触发时出现，不再进入普通随机动作；送补给改为统一的大按钮。
- 统一快捷栏按钮外观，并让 macOS 悬停时也显示快捷项黑色文字标签。

## v0.23.39 — 修复 macOS Codex 本机登录发现与首次启动单实例锁

- macOS 连接检测现在会识别登录 shell、常见用户级 CLI 目录，以及 `/Applications/ChatGPT.app/Contents/Resources/codex` 和用户级 ChatGPT.app 中的内置 Codex CLI；找到后始终使用绝对路径运行 `codex login status`、`exec` 和 `app-server`。
- 修复新 Mac 首次启动时 `~/Library/Application Support/Lili` 尚不存在，QLockFile 无法创建而误报“已有实例”的问题；应用会在加锁前自动创建每用户目录。
- 增加 Finder 环境、ChatGPT.app 内置 CLI 和缺失应用数据目录的跨平台回归测试。

## v0.23.37 — Codex 持续连接、专注统计与经济榜单一致性

- 修复 Codex 后台预热失败仍显示“已连接”的问题；现在会保留真实失败分类，首次聊天会再次尝试持久 App Server，只有真实会话失败才临时切换备用通道。
- App Server 与 exec 使用同一套 Lili HTTPS Responses 配置，macOS 继续优先复用本机 Codex 登录，不因一次启动期波动永久掉入慢通道。
- 修复重叠的历史计时检查点被重复累加，保留原始数据并按时间区间并集重建每日专注统计，避免出现不可能的“较昨天少 38 小时”。
- 自习室登录后自动幂等补传本地经济事件；富豪榜与补给站统一按本地合法创收类别统计，离线期间漏掉的收入会在下次联网时补齐。

## v0.23.25 — Codex CLI 能力探测与 Mac 在线连接修复

- App Server 改为使用最小启动命令 `codex app-server`，不再把 exec 专属参数、MCP/provider 覆盖或隔离参数传给 App Server。
- `codex exec` 改为根据当前安装版本的 `codex exec --help` 能力动态组装参数；不再硬编码 `--ignore-user-config`、`--ignore-rules` 等容易造成版本不兼容的参数。
- 启动时分别探测 `codex --version`、`codex exec --help` 和 `codex app-server --help`，诊断日志会记录版本、命令模式和不支持的参数，不记录令牌或用户提示词。
- 默认优先使用 Codex 原生登录/传输；只有用户明确设置 `LILI_CODEX_TRANSPORT=https` 时才使用 HTTPS 回退。
- 旧版 CLI 不支持 JSON 输出时自动读取普通 stdout，避免在线请求成功却被误判为无效回复。

## v0.23.24 — 修复 Mac Codex 启动与咖啡场景结束逻辑

- 修正 macOS/Windows Codex CLI 命令构造：`app-server` 不再接收 `exec` 专属参数，`codex exec` 参数改为放在子命令之后。
- CLI 参数不兼容时显示明确的启动诊断，不再误报为未登录或网络故障。
- 请搭子喝咖啡继续采用接收方主动接受；咖啡场景保存是否启动过工作计时的元数据。
- 咖啡场景到期只结束咖啡场景，不再无条件结束当前工作 Session；改为非模态“继续工作 / 结束工作”提示。
- 修正睡前文案“穿好睡意”为“穿好睡衣”。

## v0.23.23 — 工作计时状态机与昂贵咖啡连续专注奖励

- 统一工作计时为“工作中 / 手动暂停 / 10 分钟无键鼠暂停 / 锁屏暂停 / 睡眠暂停 / 播放器全屏暂停”状态；自动暂停后只提示，不会因键鼠输入、解锁或唤醒而偷偷恢复，必须由用户明确点击继续工作。
- 10 分钟阈值同时观察键盘和鼠标输入；锁屏、睡眠立即暂停；浏览器、Word、PDF、VS Code 等普通全屏不会误判，只有明确播放器全屏并持续确认后才暂停。
- 主界面、工作控制、自习室 Presence、工作记录和工资统计共用同一计时状态；暂停/继续/结束只结算新增的真实 WORKING 秒数，避免重复计薪和重复统计。
- 昂贵咖啡场景延长为最多 150 分钟；从使用时开始累计连续有效专注，超过 2 小时后幂等奖励普通咖啡 ×1，不增加钱包余额或富豪榜创收。
- 奶茶休息结束后不再自动恢复工作，用户必须明确点击继续工作；自动暂停会结束咖啡视觉场景，但不会结束工作 Session。

## v0.23.22 — 修复 macOS Codex 本机连接回退

- macOS Finder 启动的 Lili 优先复用终端中已登录的 Codex 原生传输；当本机环境不兼容时再回退到 Lili 的 HTTPS 通道，Windows 保持原有 HTTPS 优先行为。
- 读取 macOS 登录 shell 中配置的 `CODEX_HOME`，避免 Codex CLI 在终端已登录、Lili 图形应用却使用另一套凭据目录。
- Codex CLI 登录和运行失败时显示脱敏的真实诊断（登录、TLS、网络等），不再统一误报成同一句离线提示。
- App Server 与一次性 exec 使用同一套跨平台传输选择；不修改用户的 Codex 配置、令牌或 MCP 设置。
- 用户内容覆盖目录不可访问时自动回退到当前安装包资源，避免旧权限问题阻断聊天初始化。

## v0.23.19 — 成果见证、互动收件箱与六毛闹钟收口

- 成果见证改为提交者手动指定两名搭子，使用 `PENDING / ACCEPTED / REJECTED` 状态；允许且仅允许一轮替补，服务端幂等结算固定 `+200 吉他拨片`。
- 成果见证每月最多发起 4 次、成功奖励 3 次；申请进入自习室“互动”页待处理，不再由后台随机寻找见证人。
- 自习室“聊天”页改名为“互动”，搭子卡删除重复的“戳一下/递奶茶”，收拢为“串门、加油、送补给”。
- 待办中心增加“闹钟”页并嵌入现有 AlarmManager；闹钟编辑增加唯一的“启用此闹钟”总开关，启用状态、声音状态和调度状态统一持久化。
- 闹钟管理继续保持普通非模态窗口，不抢其他应用焦点；旧系统菜单入口和待办中心入口共用同一份闹钟数据。

## v0.23.18 — 六毛补给与轻量快捷入口

- 咖啡壶改为每天补给普通咖啡 1 杯，每天最多一杯；保留旧库存，不新增皮肤或娃衣。
- 小蛋糕改为自由使用的庆祝食物，移除“重要 Todo”领取门槛；咖啡继续直接开启工作场景，食物卡不再要求选择 Todo。
- 成果见证固定奖励 200 吉他拨片，仍需两名不同搭子确认且每月最多 3 次；补给站增加独立“成果见证”入口。
- 双击快捷栏加入轻量“喂食”口袋并移出低频“设置”，补给图标沿用现有红黄蓝快捷栏风格。
- 设置中心不再显示失效的“键鼠无操作自动暂停”开关；无操作不会暂停，睡眠/锁屏仍按既有规则处理。

## v0.22.92 — 六毛钱袋生活经济重构

- 统一经济底层：真实专注工资、成果/外快、消费、库存、家当、生活事件、图鉴和称号共用一个原子化本地账本。
- 钱袋首页改为“六毛钱袋”，区分当前余额、本月创收、本月花费和本月身份；工资条与账本从同一数据源计算。
- 新增荒野小卖部与小仓库：普通咖啡、昂贵咖啡、奶茶、小蛋糕，以及不涉及皮肤/娃衣的六毛家当；购买和使用分开，双击操作可幂等。
- 保留早鸟免费昂贵咖啡和旧版余额、历史工资、昂贵咖啡库存；不新增皮肤、不改变现有皮肤成长规则。
- 新增生活图鉴、称号、状态和六毛点评；消费不会制造专注时间、任务完成或虚假收入。
- 荒野国王富豪榜改为按本月合法创收排名，消费、收到礼物和库存转移不增加排名收入。
- 新增经济账本字段与非破坏式 Supabase 迁移，搭子可见范围和富豪榜退出资格保持原有隐私规则。

## v0.22.91 — 待办近期视图与重要日期分离

- “即将到来”改为近期时间线：普通未来待办正常显示，倒计时和纪念日只有进入各自提前提醒窗口后才显示。
- “重要日期”集中管理全部倒计时与纪念日，避免与近期时间线重复。
- 重要日期不再显示普通待办勾选框；仍可双击编辑、修改提醒窗口和删除。
- 保持 TodoCenter、CompactTodo、聊天待办共用同一个 TimeMemory 数据源。

## v0.22.90 — 统一待办中心

- 新增完整「待办中心」：今天、即将到来、倒计时·纪念日、已完成四个视图。
- 待办快捷图标现在会打开并置前完整待办中心，不再只是高亮 CompactTodo。
- 待办、提醒、倒计时和纪念日继续复用同一个 TimeMemory 本地数据容器，不复制生成第二套待办。
- 支持在同一窗口中新建、编辑、完成、恢复和删除四类事项；桌面 CompactTodo 保持轻量显示。
- 本次没有新增 Supabase 表或 migration。

# Lili v0.22.89

## macOS 自习室登录恢复

- macOS Lili.app 的本地运行状态统一使用 ~/Library/Application Support/Lili；Session 凭据继续只保存在系统 Keychain。
- Supabase Session 刷新改为进程内 single-flight，并增加跨进程刷新锁；刷新前重读完整 Session，刷新成功后原子写回 access token、refresh token、过期时间和 generation。
- 临时网络、TLS 或 Keychain 读取异常不会直接清空登录凭据；只有确认不可恢复的认证失败才显示“重新登录”入口。
- 自习室认证错误只保留顶部状态条和“重新登录”按钮，不再同时弹出重复系统窗口。
- 增加脱敏认证诊断日志，不记录任何 access token 或 refresh token。

# Lili v0.22.88

## 夜间限定造型

- 每天本地时间 00:30–06:30，六毛进程在线时从夜间限定素材池稳定随机选择当天造型；当前首个限定造型为“夜读六毛”。
- 造型只作为临时活动覆盖层显示，不写入永久娃衣装备；用户手动动作仍可短暂优先，临时动作结束后回到夜间限定造型。
- 06:30（不含）后立即失效并恢复普通状态，重启应用也不会把过期造型自动穿回；该机制不增加专注、毛币或称号奖励，不鼓励熬夜。
- 用户提供的夜间图片已转换为透明 1024×1024 RGBA 素材并保留清晰度，新增独立资源目录，不删除或覆盖历史动作图片。

# Lili v0.22.87

## 六毛钱包、工资条与荒野王国

- 双击六毛快捷坞新增“待办”，直接打开现有 Todo 系统，与桌面待办、聊天创建的待办共用同一份数据。
- 真实专注时间按每日 8 小时上限计入工资，每小时 6 毛币；完成待办可获得小额任务绩效，避免单纯挂机刷钱。
- 每天首次在 10 点前开始并完成至少 30 分钟有效专注，可获得“昂贵咖啡”；钱包支持登记论文录用等稿费/偶然所得、消费和月末工资条。
- 自习室首页增加可选择加入的“荒野王国富豪榜”；仅展示已接受搭子中明确参与的成员，Supabase migration 启用 RLS 并限制榜单范围。
- Windows/macOS 共用同一套本地账本与好友榜单协议，榜单 migration 尚未部署时不影响离线钱包和自习室基础功能。

# Lili v0.22.86

## macOS Codex 聊天连接修复

- 修正 macOS CI 中 Codex 登录 shell 路径探测测试对 subprocess 调用顺序的错误假设，确保 ARM 与 Intel 构建都能稳定验证 Finder 启动场景。
- 保留 v0.22.85 的核心修复：Finder 启动时补齐 Codex 登录 shell PATH、固定 HOME/CODEX_HOME/SQLite 运行目录，并使用绝对 Codex CLI 路径。
- Lili 专用 Codex App Server 与 exec 默认使用独立 HTTPS Responses provider 并关闭 WebSocket；App Server 失败时自动走同一 HTTPS exec 回退，模型不可用时回退到 Codex 默认模型。

# Lili v0.22.85

## 修复 macOS Codex 聊天连接与 HTTPS 回退

- 修复 macOS Finder 启动的 Lili 缺少登录 shell PATH，导致 Codex CLI 能检测到但实际聊天子进程无法启动的问题；现在会补齐 zsh、nvm、pnpm、Bun、Volta 等路径，并固定 HOME、CODEX_HOME 与 SQLite 运行目录。
- Lili 专用 Codex App Server 和 exec 通道默认使用独立的 HTTPS Responses provider，禁用 WebSocket，避免网络环境导致的多次 WebSocket 超时；不修改用户自己的 Codex 配置。
- App Server 失败时继续自动切换到同样的 HTTPS exec 通道；模型在当前账号不可用时自动回退到 Codex 默认模型，不再直接落到离线陪伴。
- 保留本机登录、只读沙箱、上下文隔离和敏感信息脱敏日志；Windows 与 macOS 共用同一套修复逻辑。

# Lili v0.22.84

## 工作控制与状态栏菜单修复

- 工作进行中或暂停时，工作入口按当前状态提供“暂停/继续工作”和“结束工作”；结束后保留本轮时长并回到非工作状态。
- “结束工作”在六毛工作控制条中使用低强调样式，悬停时再以浅红色提示，避免与继续操作争抢视觉焦点。
- 任务栏/状态栏/Dock 菜单把高频操作、工作记录/换装与待办、设置和系统操作分成独立分组。
- 修复六毛未先双击、隐藏或切换到其它应用后状态栏菜单大面积灰掉的问题；菜单不再继承六毛窗口的激活状态，并在原菜单对象内刷新动态状态。

# Lili v0.22.83

## 修复程序更新 TLS 证书校验

- 程序更新检查、Release 页面回退、安装包下载和 SHA-256 文件下载统一使用系统 CA 或内置 certifi 证书。
- 保持主机名校验和证书校验开启，不使用 `verify=False` 或 `CERT_NONE`。
- 修复 macOS Finder 启动旧版应用时常见的 `CERTIFICATE_VERIFY_FAILED` / `unable to get local issuer certificate`。

# Lili v0.22.82

## 修复旧内容覆盖层遮盖新版动作素材

- 修复本地旧版 `content_updates` 覆盖层优先级错误：旧内容版本不再覆盖新版安装包内置素材。
- 保留并继续使用“修改后”文件夹对应的五张新版动作图：拿保温杯、恶魔毛毛、饕餮一餐、捡贝壳、钓螃蟹。
- 即使内容更新网络暂时失败，升级到新版程序后也会直接显示新版内置造型。

# Lili v0.22.81

## 搭子私人备注与新版动作素材

- 自习室“我的搭子”支持设置仅自己可见的私人备注；公开昵称不变，不广播、不通知对方。
- 私人备注使用独立 Supabase 表和 RLS，跨设备同步时仍只返回当前用户自己的备注。
- 更新五个对应的六毛动作素材：热饮、恶魔、盛宴、捡贝壳和钓鱼；保持 1024×1024 透明画布、完整边距和高分辨率主体。

# Lili v0.22.80

## 悬浮提示文字对比度优化

- 图标 hover 标签改为浅色底配黑色文字，避免白色桌面背景下文字不清晰。
- 保持悬浮标签轻量、短暂和不改变快捷坞布局。

# Lili v0.22.79

## 移除左击工作控制条

- 左击六毛不再弹出“开始工作”“暂停工作”“结束工作”浮动按钮。
- 左击继续执行摸头、戳戳、自拍等宠物互动；工作操作保留在快捷栏和任务栏/Dock 菜单。
- 工作控制组件保留给显式工作入口和兼容调用，不再由普通宠物点击触发。

# Lili v0.22.78

## 快捷图标悬停提示

- 五个快捷图标继续默认只显示图形，鼠标悬停时显示统一风格的六毛轻量标签。
- 音乐图标 hover 明确显示“音乐”，同时保留原生 tooltip 和无障碍名称作为系统兜底。
- 动态工作入口会同步更新悬停文案，不改变快捷栏的紧凑布局。

# Lili v0.22.77

## 快捷坞与宠物互动菜单优化

- 五个快捷图标改为统一的轻量胶囊托盘，弱化单个按钮边框和高亮，增加留白与统一阴影。
- 六毛本体右键新增“六毛互动”，集中提供抱抱、加油、提醒休息、查看状态，以及喂食与饮品。
- 食物入口复用现有离线陪伴模型，不改变饱食、精力和动作反馈逻辑。

# Lili v0.22.76

## 六毛本体右键菜单收口

- 右键六毛本体只保留“换动作”“换娃衣”“换装与外观”和“隐藏六毛”。
- 动作菜单复用完整动作素材分组，并保留随机动作；娃衣菜单复用现有解锁状态与装备逻辑。
- 聊聊、工作、自习室、音乐、待办、AI 设置、更新等程序级功能不再重复出现在六毛本体右键菜单。
- 任务栏/Dock 继续提供完整程序菜单，双击六毛继续提供五个快捷图标。

# Lili v0.22.75

## 右键六毛改为工作控制

- 右键六毛本体只弹出当前工作状态控制条，不再弹出完整程序菜单。
- 工作状态严格按 IDLE / FOCUSING / PAUSED 显示“开始工作”或“暂停/继续工作 + 结束工作”。
- 控制条默认位于六毛头顶上方，顶部空间不足时自动翻到下方，并跟随六毛窗口移动。
- 点击操作、再次右键或点击其它 Qt 窗口后自动收起控制条。
- 任务栏/Dock 继续使用完整菜单，双击六毛继续打开五个快捷图标。

# Lili v0.22.74

## 音乐与工作控件收口

- 移除快捷栏和音乐菜单中的“当前播放”、歌曲名称、歌手名称和常驻媒体状态轮询。
- 音乐入口只保留播放/暂停、上一首、下一首和随机播放陈楚生；实际点击后仅给轻量操作反馈。
- 工作中的暂停/继续与结束按钮移动到六毛下方，跟随六毛窗口位置，不再归属于待办区域。
- 快捷坞默认位于六毛头顶上方并保留留白，屏幕边缘空间不足时自动避让。
- 保留所有图片资源和音乐控制能力，不改变图片清晰度。

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
## v0.23.30 — 聊天当前轮优先、待办日期语义、闹钟保留与自习室状态一致性

- 普通聊天不再因为上一轮陈楚生歌曲内容和“人生”等宽泛词触发人物经历；本轮意图优先，知识检索不再拼接上一轮长回答，并按意图缩短历史与知识预算。
- 将陈楚生用户资料整理为运行时短知识卡，原始长文不进入安装包或聊天提示；普通问题默认不检索这些卡片。
- 待办增加 `date_explicit` 语义，创建日期不再冒充事项日期；无日期待办主列表只显示标题，明确事项时间仍正常显示。
- 闹钟关闭、错过、Todo 提醒模式变化改为保留并标记状态，只有明确删除才移除记录；跨天和重启后仍可查看。
- Codex App Server 在后台预热，失败自动回到现有 HTTPS exec 兼容路径；新增不泄露正文的响应性能日志。
- 自习室首页自己的今日专注优先读取共享本地 FocusSession；后台 payload 缺少 leaderboard 时保留上次榜单，不再误清空。
## v0.23.31 — 单实例与 Codex 连接诊断收口

- 应用 bootstrap 增加进程内唯一 ownership；聊天信号使用唯一连接，避免重复启动/重复绑定造成第二只六毛或同一消息多次提交。
- Codex App Server 恢复 thread 时校验 provider/transport；不兼容、恢复失败或新旧 CLI 状态迁移会清除本地旧指针后新建 thread，不删除服务器历史。
- warm-up 和实际请求失败会保留安全的错误分类与阶段信息；状态栏显示版本不兼容、登录、网络、超时、线程配置等真实原因，不把内部命令、system prompt、令牌或完整异常泄漏到普通界面。
- 保留现有 HTTPS exec 兼容路径；App Server 不可用时仍可继续尝试兼容连接。
## v0.23.34 — 修复 Codex 掉线后的错误降级与重复聊天

- 事实问题（包括“广东省会是哪吗”）在离线降级时不再套用无关陪伴模板；失败气泡保留脱敏后的连接诊断，并提供重新连接入口。
- Codex App Server 遇到当前账号不可用的指定模型时，在同一条常驻连接中去掉模型参数重试 CLI 默认模型，不重新连接、不修改 macOS 的 Codex 环境或 transport。
- 聊天发送边界增加短时幂等保护，避免失败/重复点击把同一句用户消息和离线回复重复写入当前会话。
- 增加“是哪/哪个/哪一项”等中文事实问法的确定性分类与 App Server 模型兼容回归测试。

## v0.23.33 — Codex exec 增量读取与常驻连接保持

- 常驻 App Server 继续复用已完成握手的进程和 thread，不在每条消息前重新连接。
- 兼容 `codex exec --json` 在进程运行期间输出的 assistant JSONL 增量会立即进入聊天窗口；不支持该事件格式的 CLI 自动保留原完整返回路径。
- 增量读取不改变 macOS 的 Codex executable、CODEX_HOME、登录状态、命令参数或 transport 选择；App Server 失败仍只在当前会话内降级到既有兼容路径。
- 增加 JSONL snapshot 去重测试，避免流式片段与最终完整答案重复显示。

## v0.23.32 — 聊聊增量显示与兼容连接输出优化

- App Server 的真实 delta 继续即时转发；CLI/HTTPS 等完整返回的兼容路径也拆成短片段，避免回复一次性整段出现。
- 聊天窗口改为约 25ms 的小批次刷新，接近逐字显示，同时避免每个 token 都重建全文 HTML。
- 增加兼容 transport 增量显示和权威最终文本收口测试，不改变原有 Codex fallback、待办、计时和单实例逻辑。
## v0.23.36 — 恢复 App Server 主通道与预热失败自愈

- 启动期 App Server warm-up 失败只记录为 `warmup_failed`，不再把整个运行周期永久切换到较慢的 `codex exec`。
- 首次真实聊天仍会重新尝试 App Server；成功后继续复用同一个进程和 thread，macOS 原有 executable、登录态与 transport 不变。
- 只有真实 turn 生命周期失败才进入 60 秒 exec 冷却；冷却结束后由 AgentManager 后台重新预热 App Server。
- 增加预热失败后首条真实消息成功、后续消息复用同一 App Server，以及后台恢复调度回归测试。
## v0.23.46 — 日报定时、Mac 快捷键悬停与每日蛋糕分享

- Mac 快捷口袋的六个快捷项统一显示悬停简称：聊聊、开始/暂停工作、待办、搭子自习室、音乐、喂食；移开鼠标后自动消失，不激活其他应用。
- 工作日报改为设置中心里的每日时间生成，默认开启并默认为 18:00；支持鼠标滚轮选择时间，也可以关闭，不再按满 8 小时或退出程序自动生成。
- 小蛋糕改为每日免费补给：每天最多获得 1 个、库存最多 1 个，不能独享；每人每天最多发起 1 场，必须邀请 1～3 位搭子，好友可异步接受。
- 分享蛋糕支持“今天庆祝什么？”留言；对方在刷新首页前也能通过后台短轮询收到“请你一起吃蛋糕”的非模态处理框，发起方可看到各位搭子接受进度。
- 新增 Supabase 群体分享 RPC、数据库幂等/权限校验和 relay 路由，保持同一账号在 Mac/Windows 间使用同一份服务端状态。

## v0.23.47 — Mac 悬浮提示、互动造型与日报入口

- Mac 快捷口袋现在按真实鼠标悬浮即时显示当前按钮简称，移到其他按钮会立即切换，离开快捷口袋后自动消失。
- 请奶茶/咖啡后，发送方和接收方都会在对应时间内显示饮品限定造型，不暂停正在进行的专注计时；串门改为跟随六毛的小型状态标签，不再弹出大串门窗口。
- 工作日报默认改为每天 22:30 生成，并在“工作记录”中增加“设置工作报告时间…”入口，继续支持滚轮选择时间和关闭自动生成。

## v0.23.62
构建验证：修复认证超时处理的旧后端兼容性，并确保注册/重发确认邮件超时不会重复提交凭据。

- 放宽注册与重发确认邮件的客户端等待时间，适配 Supabase SMTP 响应较慢的情况。
- 注册超时后不再把密码请求盲目重放到其他后端；界面会保留邮箱并显示“重新发送确认邮件”入口，避免重复创建账号。
- 增加注册超时回归测试，并保留 Supabase 直连与旧内容覆盖层隔离修复。
