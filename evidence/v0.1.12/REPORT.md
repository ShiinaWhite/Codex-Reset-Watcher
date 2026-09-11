# v0.1.12 Banked forensic investigation — CONTRACT GAP, NOT A FIX CANDIDATE

调查日期：2026-09-11。基线：5122f7227d78f1b907a455d36c187048a3a23d4b。
分支：review/v0.1.12。没有修改生产逻辑、版本号、配置、Core、SDK 或既有测试。
按照任务 Phase F 的明确停止条件，本次停在取证提交，未进入修复验收。

## 1. 已证实的漏报原因及分类

v0.1.11 的 signals_from_feed 仅接受 reset_kind=banked 且
banked_state 属于 announced/arriving/available。本次事件为 banked/unknown，
因此在提取阶段就被丢弃，不会进入 Banked enrichment 或 QQ 发送管线。
离线 replay 直接调用未修改的生产函数，确认此结果。
这证明代码层面的确定性漏报条件；没有读取生产运行日志，不能声称完成了运行时逐轮追踪。

分类：D，插件将生命周期状态集合当作了告警资格，实际存在公开的独立推送决策对象，
但目前缺少足以证明其完整对应 TG 投递的契约及负例。
B 描述了本次直接过滤原因，但不能据此得出应扩大状态集合。
A 部分成立：插件没有消费 /api/push/notification 中已经可见的 Banked decision；
它是 Browser Push 的最新对象，尚不能称为完整、稳定的 TG receipt surface。
不能断言 C（全部决策不公开），因为本次 decision 确实通过 push 对象公开了。

## 2. 黄金样本完整结构

golden_2097752790177370535.json 保留本次 feed tweet/event、timeline event、
当前 official_signal、push notification 及 TG health 记录，未改写上游字段。
完整原始响应分别保存在 feed.json、timeline.json、forecast.json 等文件。

Tweet：id=2097752790177370535；kind=banked；banked_state=unknown；
classifier_version=reset-regex-v7-llm-v7；tibo_lane=reset_related；
explicit_reset_claim=false；is_reply=false；conversation_id 等于自身 ID；
replying_to/in_reply_to_tweet_id=null；at/declared_at=2026-09-09T18:23:34.000Z；
inferred_at=2026-09-09T18:23:34.197Z。完整原文在 fixture 中。

Event（feed 与 timeline 的该记录相同）：type/group=credits；reset_kind=banked；
banked_state=unknown；scope=global；preview=false；confidence=medium；source=live；
source_label=Live radar feed；announcement_state=none；observation_result=unknown；
reset_verification_status=pending；audience/incident_links/reason_tags/observation_sources=[]；
effective_at/official_window=null。无 tags 字段；无 alert_selected/receipt/delivery ID 字段。

当前 /api/forecast.official_signal=null，latest_alert 为 2097174560412246215 的 Global Reset。
本地历史数据结束于事故前，无法证明本次 Tweet 从未短暂进入 official_signal。
不要把当前 null 写成完整历史结论。

codex-reset.com/api/reset/current 返回 HTTP 404。
插件配置使用的另一个上游 tibo.modelyard.dev/api/reset/current 返回 CONFIRMED /
DIRECT_VERIFIED，resetSourceUrl 指向黄金 Tweet；这是独立来源，不是 Codex Reset TG decision。
v0.1.10 已退役该路径的用户通知，不建议重新启用。

## 3. 真实公开告警面与可验证边界

https://t.me/codexresetalerts/70 是本次 TG 消息。公开频道预览验证了题述标题、原文及链接。
本机 curl 获取 TG 遇到 TLS 失败；web 工具成功读取公开频道并解析出消息 70。
更早分页通过 web 工具读取失败，浏览器补充尝试也超时；没有伪造完整 TG 日志。

https://codex-reset.com/api/push/notification 当前返回：

    alert.id = 2097752790177370535
    alert.kind = banked
    alert.title = 🎟️ Banked reset update
    alert.at = 2026-09-09T18:23:34.000Z
    alert.url = /

body 与题述 Tibo 原文对应。无 event_id、receipt_id、TG message ID、cursor 或历史数组。
注意 at 是原帖时刻，不是 TG 发送时刻。

公开 sw.js 的 buildNotification 实际调用此 endpoint。其注释区分了“实际推送记录”
与 /api/push/latest 的重新选择展示结果；notificationFromAlert 接受 reset/forecast/banked。
本次 /api/push/latest 返回旧 Global Reset 2097174560412246215，不能代替 notification。

公开 release-notes.html 记录：Alert v3 管理 regular Reset / strong Watch；
pure Banked 保留独立 Telegram/Discord/Browser Push 路由和 receipts。
这与主页文字、Service Worker 和本次响应相互吻合，说明路径独立。
但是公开前端只消费结果，不包含服务器 TG 发送函数或完整 Banked eligibility predicate。
一次 GitHub/网页搜索没有找到该站服务端源码；不能以同名的其他项目代替。

/api/health 的 telegram_alerts.last_banked_delivery 为：
type=banked，destination=alerts，at=2026-09-09T18:24:31.000Z，status=sent。
Discord 对应记录一致。但二者都不包含 tweet/event ID。
web_push.last_delivery 包含 alert_id=黄金 Tweet、alert_kind=banked，
at=2026-09-09T19:36:31.000Z，attempted=0/sent=0/failed=0。
所以不能把 Browser Push 当前对象解释为“所有订阅者实际成功收到”的回执，
也不能通过时间相邻自动 join 出任意 TG message 与 Tweet 的关系。

banked-state.js 的 HOME_BANKED_STATES 包含 unknown，但那是展示筛选。
selectBankedUpdates 甚至接受 group=credits 或 kind=banked；不是 TG 发送条件。
没有将这些前端函数当作告警 contract。

## 4. 历史 replay 与负例缺口

扫描 live/ 和 archive/historical-workspace/ 下可解析的 283 份 JSON，
historical_inventory.json 记录完整 unknown 对象及出现路径。
其中黄金样本出现次数为 0；没有事故当天的 forecast 或 delivery 历史。
历史 unknown 只有两个唯一事件，二者 reset_kind 均为 null，且发生在已归档 TG 窗口之前。
“窗口之外”不等于“TG 没发”。不能将它们标成任务要求的真实负例。

下表是发生时的结构提取结果，不代表今天会补发；48h/baseline/receipt 仍会另外抑制发送。
TG sent 取自本次公开频道或既有 live/forensic_0908/replay/corpus/telegram_channel_log.md。
旧归档的部分日期与 tweet 时间不一致，因此这里只利用其 ID/消息对应证据，不扩展时间结论。

| event/tweet ID | TG 证据 | 旧插件提取 | 新插件 | 原因 |
|---|---|---|---|---|
| 2097752790177370535 | 已发，message 70 | 静默 | 未实现 | banked/unknown 被旧状态白名单排除；当前 push 明确选中 |
| 2096035437299237298 | 已发 | eligible | 未实现 | banked/arriving |
| 2095979536043401428 | 已发 | eligible | 未实现 | banked/arriving |
| 2095651088502591861 | 已发 | eligible | 未实现 | banked/announced |
| 2090964822422949999 | 未知 | eligible | 未实现 | banked/available；没有该 ID 的 TG 证据 |
| 2090947196107764189 | 已发 | eligible | 未实现 | banked/arriving |
| 2090766694897619318 | 已发 | eligible | 未实现 | banked/announced |
| 2076735790567338203 | 未知，归档未覆盖 | 静默 | 未实现 | unknown，reset_kind=null |
| 2076418567143408112 | 未知，归档未覆盖 | 静默 | 未实现 | unknown，reset_kind=null |

运行：PYTHONPATH=test_env python evidence/v0.1.12/replay.py。
输出 replay.json。脚本对黄金样本的旧提取漏报及 push 实际选中执行断言。
没有新增会把旧漏报固化为预期行为的 pytest 回归测试。

## 5. 为什么没有直接修复

第一优先路径已有重要线索：直接消费 /api/push/notification 的 kind=banked 对象，
可以处理当前黄金样本而不扩大所有 unknown。但尚有以下未满足验收的缺口：

1. 对象是跨类型的最新值；缺少 Banked 历史/cursor，无法恢复在两次轮询或故障期间被更新值替换的 Banked。
   上游自己的公开说明也承认 Web Push Events 可合并为最新状态。
2. 未取得服务器代码或接口契约来证明每个 TG Banked decision 都会进入该对象，
   以及各通道失败、无订阅者、重试时的更新顺序与保留语义。
3. TG 的现有 health receipt 无事件 ID，不能做稳定关联；当前 push.id 是 tweet ID，不是 TG delivery ID。
4. 没有具备实际 TG 未发送证据的历史 unknown 负例，不能完成 Phase E/F 的强制验收。

第二优先路径也不成立：没有足以重建 TG eligibility 的已验证 feed 字段组合。
不得新增 unknown、按补偿关键词判断、按 source/live/preview/scope 猜测，或伪造 TG negative label。

继续工作需要：上游 Banked selection/delivery 的服务端实现或维护者明确契约，
最好是带稳定 event/tweet ID、选择状态及保留/分页语义的 Banked alert API；
以及至少一个真实 unknown 未发送样本的覆盖时间段日志/receipt 证据。
不要求用户提供 token、账户凭据或私有聊天内容，也没有联系上游或向频道发送消息。

## 6. 验证与交付状态

完整运行 PYTHONPATH=test_env python -m pytest test_watcher.py test_replay_corpus.py
（追加 --tb=short -q 只改变输出），203 passed in 3.47s，Python 3.13.3。
这是未修改 v0.1.11 生产逻辑的基线验证，不是新 Banked 规则或新 pipeline 的验收。
完整黄金正例/负例及新规则可靠性测试尚未实现；既有可靠性测试随原测试集通过。

本提交仅取证、fixture、replay 与报告。没有修复 commit，不声明版本升级至 0.1.12。
没有 merge、部署、tag、GitHub Release 或 push。远端只读核实 main 仍为指定基线，
review/v0.1.12 远端分支不存在。最终 commit/tree/diff 信息在最终回复中提供。
