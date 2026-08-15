# Lili v0.22.23

- Fixed Supabase Direct presence heartbeats by adding the required PostgREST merge-duplicates preference to `lili_focus_presence` upserts. Direct clients no longer hit the primary-key duplicate error after the first heartbeat, so active buddies correctly remain online and room focus counts update.
- Added regression coverage for the direct heartbeat upsert header. CloudBase and Edge relay behavior remains unchanged.

# Lili v0.22.22

- Fixed false offline buddy status by making Supabase the authority for `last_seen` and room session timestamps. Desktop, CloudBase proxy, Edge relay, and Cloudflare relay no longer trust a skewed client clock.
- Replaced the corrupted room focus accumulator with an idempotent room-scoped focus-session ledger. The room summary now reports the total focus time accumulated in that room, including closed sessions and currently active sessions, without double-counting heartbeats or room switches.
- Reset previously contaminated room totals during the production migration and added regression contracts for server freshness and ledger-based room totals.

# Lili v0.22.21

- Fixed owner nickname synchronization by exposing the active authenticated Supabase session through the route-aware social client. Existing local owner nicknames now reach the single Supabase profile source of truth, so buddies can see the intended `{owner_nickname}瀹剁殑鍏瘺` label instead of the neutral fallback.
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

鏈鐗堟湰鎶婂叚姣涘璇濆拰鎼瓙鑷範瀹ょ殑涓ゆ潯閾捐矾閲嶆柊鎺ョǔ锛?
- 鍏瘺瀵硅瘽浣跨敤鍥哄畾瑙掕壊鐭ヨ瘑銆佹寜闇€璇濋妫€绱㈠拰鏈€杩戜笂涓嬫枃锛岃繛缁拷闂€滀粬鈥濃€滆繖棣栤€濃€滃悗闈竴鍙モ€濅笉鍐嶈劚绂婚檲妤氱敓涓栫晫瑙傦紱鏄庣‘姝岃瘝缁啓浼氭敼涓哄畨鍏ㄦ彁绀猴紝涓嶉殢鏈烘帴璇濄€?- 瀵硅瘽淇濈暀鏈満鏈夌晫鎽樿鍜屾渶杩?30 杞紝鍦ㄧ嚎璇锋眰鍙彂閫佽鑹茶瀹氥€佸懡涓殑鐩稿叧鐭ヨ瘑鍜屾湁闄愪笂涓嬫枃锛涜亰澶╄缃腑鐨勯殣绉佽鏄庝笌瀹為檯琛屼负涓€鑷淬€?- 淇鍦ㄧ嚎鑱婂ぉ鐘舵€侀噸澶嶆樉绀衡€滃凡杩炴帴鈥濈殑闂銆?- 鑷範瀹ら椤垫樉绀哄疄闄呭悗绔紙Supabase 鎴栭厤缃殑 HTTP 涓浆锛夛紝鏂板鍋ュ悍妫€鏌ャ€?- 鑷範瀹ょ綉缁滈敊璇尯鍒?DNS銆佽繛鎺ヨ秴鏃躲€佹嫆缁濊繛鎺ャ€乀LS/璇佷功銆佽璇併€丠TTP 鍜屾湇鍔″櫒閿欒锛涚綉缁滃け璐ヤ粛淇濈暀鏈€杩戞埧闂寸姸鎬侊紝涓嶉樆濉炴瀹犱笌璁℃椂銆?- Cloudflare Worker `/health` 杩斿洖鍚庣绫诲瀷涓庣煭杞鑳藉姏锛涗粨搴撲笉鍐呯疆鏈粡涓浗澶ч檰鐩爣缃戠粶瀹炴祴鐨勫叕缃戜腑杞湴鍧€銆?
# Lili v0.19.0

鏈鐗堟湰閲嶇偣淇鐐规瓕缁撴灉涓庡疄闄呮挱鏀句笉涓€鑷淬€佽杩涙瓕鎵嬩富椤典互鍙婂惈涔変笉娓呯殑鈥滄洿鏂板け璐モ€濄€?
- 鐐规瓕鏀逛负 `search 鈫?exact match 鈫?play 鈫?verify`锛屽彧鏈夊獟浣撲細璇濊繑鍥炵殑姝屽悕鍜屾瓕鎵嬮兘鍖归厤鎵嶆樉绀烘挱鏀炬垚鍔熴€?- 鎼滅储缁撴灉浠呮帴鍙楁瓕鏇茬被鍨嬶紱姝屾墜銆佷笓杈戙€丮V銆佹瓕鍗曞強姝屾墜涓嶅尮閰嶇殑缁撴灉浼氳鎷掔粷銆?- QQ 闊充箰銆佺綉鏄撲簯闊充箰銆侀叿鐙楅煶涔愩€丄pple Music銆丼potify 浣跨敤鐙珛 Provider Adapter锛屼笉鍏变韩鍥哄畾鍧愭爣鎴栭〉闈㈠竷灞€鍋囪銆?- 鍒犻櫎鎼滅储鍚庢寜鏂瑰悜閿€佸洖杞︽垨鍥哄畾鍧愭爣鎾斁绗竴鏉＄粨鏋滅殑鏃ч€昏緫锛涙寚瀹氭瓕鏇茬偣鎾笉浼氱敤鍏ㄥ眬鎾斁閿画鎾棫闃熷垪銆?- 瀹為檯姝屾洸涓嶅尮閰嶆椂鏈€澶氶噸璇曚竴娆＄簿纭挱鏀撅紝闅忓悗杩斿洖鏄庣‘缁撴灉锛涗笉鍐嶄吉瑁呮垚鎴愬姛銆?- 鍐呴儴鍖哄垎 `SEARCH_FAILED`銆乣RESULT_NOT_FOUND`銆乣PLAY_ACTION_FAILED`銆乣MEDIA_SESSION_TIMEOUT`銆乣TRACK_VERIFY_FAILED`锛屽苟璁板綍璇锋眰銆佸€欓€夊拰褰撳墠濯掍綋淇℃伅璋冭瘯鏃ュ織銆?- Windows 浣跨敤 UI Automation 瀹氫綅姝屾洸琛屼笌璇ヨ鎾斁鎸夐挳锛屽啀鐢?GSMTC 鏍￠獙锛沵acOS Apple Music 浣跨敤 Apple Events锛屽叾浠栧鎴风浣跨敤宸叉巿鏉?Accessibility Adapter銆?
鍚屾椂鍖呭惈 v0.18.1 鐨?macOS Codex CLI 缁濆璺緞妫€娴嬨€佹渶杩?30 杞亰澶╄蹇嗐€佷簲绫诲彸閿彍鍗曞拰鍥涙爣绛炬惌瀛愯嚜涔犲鏀硅繘銆?
