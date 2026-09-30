# 2026-09-30 重复通知与非 Tweet 观测事件调查

调查基线：main `2b1bc1bdc88cf5a666194a53bd17ca9263d4b6b7`，插件 0.1.12。
本次独立读取代码历史、生产日志、持久 state 与公开上游；没有生产发送、部署、state 编辑、Core 修改或 marketplace Issue 操作。

## 结论与证据边界

三组重复的共同机制是跨 lane 投递身份未协调，包含两种 Global 顺序和一组 Banked 顺序。Banked 空正文另有独立的数据模型错误：上游事件被无条件当作 Tweet，观测通知内容又被 Tweet 内容规则排除。

日志能确定每次实际发送路径及 receipt identity；当前 state 同时保留各 lane 的独立键。不是同一去重键在 reload 后消失、再次发送。9 月 25 日以来的插件日志未见读取/写入 state 失败或插件加载/卸载记录；Core StartedAt 为 `2026-09-14T17:32:27.010398223Z`、RestartCount=0。9 月 29 日 18:51 有群配置保存与新增群 baseline，与 26/27 日重复无关。

生产 `plugin.py` SHA-256 为 `9f4a770518665b900e156419a310d2247f7daf8e2827026635459a09bea19cec`，与基线源文件一致。代码路径和运行版本已经交叉核实。

本次公开 API 快照抓取于 `2026-09-30T15:16:41Z` 附近，见 [events.json](events.json)（完整 feed 的三个 ID 子集）和 [push_notification.json](push_notification.json)（原始响应字节）。**它们不是六个发送时刻的历史 HTTP 响应。** 历史每次轮询的完整 forecast/feed 响应未取得，无法证明指针何时变动、当时每个可选字段的精确值，也不能用当前快照倒填历史。

原始生产日志/state 留在本地 `private/forensic/2026-09-30-duplicates/`，未上传含群号的文件。日志下列摘录省略群号，未改变时间、路径或键。

## 三组实际发送路径

时间均为北京时间（UTC+8）。

| Event ID | 第一次实际成功发送 | 第二次实际成功发送 |
|---|---|---|
| `2103637477760311522` | 09-26 08:12:05，`Feed 通知已发送`，`global-declared:2103637477760311522` | 09-26 09:04:44，`Upstream 告警已镜像`，`upstream-alert:signal:2103637477760311522:likely` |
| `2103911959544610829` | 09-27 02:19:42，`Upstream 告警已镜像`，`upstream-alert:signal:2103911959544610829:likely` | 09-27 07:47:52，`Feed 通知已发送`，`global-declared:2103911959544610829` |
| `2105008012525769969` | 09-30 12:32:45，`Push Banked 已镜像`，`push-banked:2105008012525769969` | 09-30 15:19:45，`Feed 通知已发送`，`banked:2105008012525769969:available` |

Banked 两次均对当时配置的两个群发送；两个群各有上述两种 receipt。第二个群是在 9 月 29 日加入。

### 26 日：feed 完整通知在前，L4 在后

实际调用链为 `_process_feed_signals` → `_dispatch_declared_signal` → `_l1_primary_pipeline` → `_send_group_text`，随后 `_process_upstream_alert` → `_alert_pipeline` → `_send_group_text`。

旧模型只实现了部分 L4 → L1 处理，L4 调度只读 `upstream_alert_keys`，从不查询已成功的 feed 完整通知。因此 feed 已投递不影响首次 L4，同一原文再次 enrichment、翻译、发送。

08:12:00 与 09:04:40 两次 FxTwitter 均返回 full、len=217；08:12:05 和 09:04:44 各有一次 LLM 翻译成功，译文长度 105/102。不同译文是两条管线重新翻译的结果。

### 27 日：L4 在前，feed 后来再次走 A-primary

L4 02:19:42 成功且记录了结构化 Tweet receipt。旧 L1 仅在狭窄指纹成立时静默：已收同 Tweet 的 L4，并且 `source=live`、`explicit_reset_claim=True`、`announced_at=tweet.at`。已收相同来源的事实没有直接成为去重依据。

07:47:52 发的是完整通知，不是 B-confirm。该执行结果证明当时未走 observed/B 分支，也未满足 C 指纹；不需要假定 receipt 丢失。当前快照中该 event 已是 `source=archive`，对应 Tweet 为 `explicit_reset_claim=false`、`tibo_lane=reset_related`，这些字段均会阻止旧 C 判定。历史确切字段未保留，因此只把这些当前字段作为可复现的 schema 证据。

02:19:39 与 07:47:49 两次 FxTwitter 均返回 full、len=66；两次 LLM 都成功，译文长度均为 22。body 重复不是 sender 自行重放同一消息，而是 L4 与 L1 独立生成。

### 30 日：push-banked 与 feed/available，以及空正文

第一次走 `_process_push_banked` → `_push_banked_pipeline`；第二次走 `_process_feed_signals` → `_dispatch_banked_signal` → `_banked_pipeline`。两条管线各自检查自己的 receipt，互不协调。

两次 FxTwitter 查询 `/status/2105008012525769969` 均 HTTP 404；两次 VxTwitter 均返回 HTML、JSON 解码失败；日志均明确写出“无可用的真实推文文本，仅发送标题/原帖”。没有 LLM 成功记录，正文在 LLM 之前就已消失。

当前 feed 同 ID 事件明确包含：`source=observed`、`source_label=Observed on our monitoring accounts`、`reset_kind=banked`、`banked_state=available`、`observed_variant=fresh`、`url=null`，无同 ID `tweets[]` 条目。`text` 与 `summary` 为上游监测账户的观测说明。push 同 ID 有正文、`url=/`；push `at` 与 event `announced_at` 均为 `2026-09-29T18:53:14.000Z`。

因此这是上游观测事件，不能仅因 ID 为数字就视为 Tibo 推文。旧代码强制拼 `x.com/thsottiaux/status/{event_id}`，向 Tweet providers 查询一个没有证据成立的身份，同时无条件排除 event text/push body，导致两次标题加未经证实的“原帖”链接。

## 修复设计

实现放在独立分支，候选版本 0.1.13。触发契约保留，LLM 不参与告警判定。

1. 将 lane 已处理键与实际投递证据分离。新增按群 `delivered_notice_keys`，只有成功完整通知或有明确成功覆盖的 alias 才记录。baseline、过期记录不能制造该字段。保留原 lane receipt 以处理上游同 ID 幂等。
2. Global 普通完整通知关联到 `global:{event_id}`。L4 → feed 用结构化同 Tweet 的成功 receipt，去掉依赖文本分类字段的 C 指纹门槛。feed → L4 只合并已知初始 `signal:{tweet_id}:likely` 且有明确 `tweet_id` 的身份。首次 strong、未知 opaque ID、后续 L4 ID 变化继续独立投递，不把整个 Tweet 永久封禁。observed 的短确认保留。
3. Banked 按 `event_id + phase` 关联。push 自带合法 `banked_state` 可直接关联；没有该字段时，仅可将同 ID、同 occurrence time 的 `source=observed` / `available` 事件绑定为同一次观测。旧 Tweet push 不能借用后来变化的 feed 阶段。缺少关联证据时独立发送；生命周期 announced/arriving/available 不互相屏蔽。
4. 四条完整通知管线与短确认共享按群投递锁，覆盖 receipt 重查、QQ send、receipt 落盘。Provider/LLM 在锁外；state mutation 锁仍不跨网络。失败/取消不记成功，发送期间移除群后不重建其 receipt。
5. 先确定内容来源。明确 X URL 或同 ID `tweets[]` join 才查询 Tweet providers。缺少推文身份时，push body 作为明确标注的上游通知展示、翻译；feed text/summary 则仅对 observed 事件采用，标作「上游通知」，链接标作「来源」。既不虚构推文链接，也不把上游正文标作 Tibo 原文。没有可用正文或 provider 失败仍照常发送通知。

新增状态字段通过 v6 白名单携带，配置结构和能力清单不变。旧 `upstream_alert_tweet_ids` 可作为可靠成功覆盖；旧 feed `notified_keys` 无法区分真实投递与 baseline/age/C，不能倒推成功。旧 Banked push receipt 不包含阶段，也不倒填。迁移时宁可有一次保守重复，不能用猜测制造漏报。

此次没有解决上游 latest-only push 的离线/被覆盖恢复问题。没有完整历史/cursor 时仍不承诺恢复。QQ 接受后、state 成功落盘前发生进程退出或文件写入失败，仍可能跨重启重发；这是 at-least-once 边界，本次未声称 exactly-once。

若 30 日第一次采样时 feed 尚未暴露可关联事件，修复会保持 push 的独立投递；以后 feed 出现时仍保守发送。我们没有当时的响应来判定这一点。能保证的是：有上述结构化关联时只发一次，缺证据时不吞告警，观测正文不再因虚构 Tweet 查询而丢失。

## 验证

基线两个完整测试文件：220 passed。新增 `test_delivery_identity.py`：23 项，覆盖两组 Global/两种顺序、reload、Banked 来源/正文/翻译、并发 send-await、逐群失败重试、baseline 不当作成功、legacy L4 receipt、strong/未知 ID、阶段变化/缺关联、取消、发送期间移除群、畸形来源 URL/阶段字段等。

完整测试命令（本地 SDK + fake ctx，网络密闭，不发生产消息）：

```powershell
$env:PYTHONPATH='test_env'
python -m pytest -q test_watcher.py test_replay_corpus.py test_delivery_identity.py
```

结果：**243 passed**。六项核心顺序回归在原 main 代码下全部失败；测试中的 reconstructed L4 最小输入及 26 日 announced 状态已明确标注，不冒充历史 HTTP fixture。

原测试有两处必要调整：完整 golden 测试显式传入真实 feed 以证明 Tweet join；最小无来源对象改为来源页而非虚构 X permalink。并发 state 测试补齐后台任务等待，验证真实 receipt 落盘完成。

## 原始证据 SHA-256

下表引用本地保留的原始字节，可用于后续核对；公开 JSON 子集与私有完整 feed 的哈希不同。

| 文件 | SHA-256 |
|---|---|
| 完整 feed.json | `f73e6c864b5db0c89cd24734e33e9bddbe7eb7316eb0255c67bc87d46f21a9ac` |
| forecast.json | `906feeab5550571ad3d95cc77b51ad98a5ad63b28fe9f3ddd296b1d231ea8c26` |
| push_notification.json | `53a997375e87a488505fb05c3ec33cf2584624be218689937dcc25387368e1c6` |
| state.json | `ac07e9f488fc1c796039b3ae274bcba6ee01d3e67e4d4a45531b49638223f2b0` |
| watcher_logs.txt | `ba1302b8a340b1a803023dd895c9d7d8d5d784d450ad955ea4a15bca0289117b` |
