# Tibo full-push 实际投递反序取证与修复

基线与只读核验的正式 live 为 `db243132b5294142db0a84302abe7ac977929fbd`。
本轮只在 dev 修复；生产没有部署、reload、配置修改、state/receipt 修改或人工发送。

## 真实事件

取证窗口：北京时间 2026-10-08 03:19–03:23（UTC 2026-10-07 19:19–19:23）。
只读提取 Host JSONL、Core/NapCat 容器历史日志、Runner health 历史和
SQLite `mai_messages`（`mode=ro`），并重新读取公开 feed / FxTwitter。
上游重新获取的是取证时快照，不把当前点赞等可变字段当成事发时数据。

| Tweet | ID | feed `at`（UTC） | 关系 |
|---|---|---|---|
| Day 3 / Loading a banked reset | `2107913674593644711` | `2026-10-07T19:19:17.000Z` | 主帖，引用 Roundup of Day 2 |
| Will be there by EOD PST. | `2107913738791596286` | `2026-10-07T19:19:33.000Z` | `is_reply=true`、`replying_to=thsottiaux`、`in_reply_to_tweet_id=2107913674593644711` |

FxTwitter 的 `created_timestamp` 分别为 1791400757 / 1791400773，
`replying_to_status` 再次确认 self-reply 的父帖 ID。正确顺序为 Day 3 → Will be there，
两帖相差 16 秒。仓库 fixture 保留这两条真实 feed 时间、正文、回复关系以及主帖引用正文。

## Enrichment 与投递时间线

下表为原始插件日志时间加 UTC+8，排除 `<runner>` 转发副本，避免重复计数。
Provider 和 LLM 日志没有 Tweet ID；对应关系基于两条唯一正文长度、quote 分支执行次序
以及后续带明确 Tweet ID 的投递日志，属于关联取证。没有伪造 pipeline 起点或 render 耗时。

| 北京时间 | 日志 / 观察 |
|---|---|
| 03:22:23.693847 | FxTwitter full，短正文 len=25，耗时 0.76s |
| 03:22:23.801193 | FxTwitter full，Day 3 正文 len=224，耗时 0.87s |
| 03:22:24.328689 | 短正文完成 VxTwitter/feed 比较，最终采用 FxTwitter |
| 03:22:24.425135 | Day 3 完成 VxTwitter/feed 比较，最终采用 FxTwitter |
| 03:22:25.239746 | Watcher 本轮监控日志完成；后台管线仍在执行 |
| 03:22:28.777613 | LLM 成功，tokens=1456、中文 len=40，对应短帖 |
| 03:22:32.029258 | 短帖在 930652120 投递成功 |
| 03:22:32.682923 | 短帖在 858765219 投递成功 |
| 03:22:34.948310 | LLM 成功，tokens=2010、中文 len=114，对应 Day 3 主帖 |
| 03:22:36.720214 | LLM 成功，tokens=1007、中文 len=144，对应 Day 3 quote |
| 03:22:41.183171 | Day 3 在 930652120 投递成功 |
| 03:22:41.859117 | Day 3 在 858765219 投递成功 |

当时没有持久化每次 `render.html2png` 的起止时间、`render_ms` 或完整 RPC trace。
只能由现有代码确认 render 位于卡片翻译完成与发图之间；不将该区间直接算作 render_ms。
四条 NapCat 发图日志和四条数据库 `is_picture=1` 记录确认实际图片投递，不是文字 fallback。

| 群 | Tweet（实际收到顺序） | QQ message ID | 数据库消息时间（北京时间） | NapCat 发图日志时间（北京时间） |
|---|---|---|---|---|
| 930652120 | Will be there | `944012153` | 03:22:30.659393 | 03:22:30.686045 |
| 930652120 | Day 3 | `1047604985` | 03:22:38.790209 | 03:22:38.904625 |
| 858765219 | Will be there | `1490217582` | 03:22:32.095476 | 03:22:32.118383 |
| 858765219 | Day 3 | `1229400618` | 03:22:41.217678 | 03:22:41.319079 |

message ID 由这四条唯一的 Bot 出站图片数据库记录，按群及相邻发图/receipt 日志关联；
数据库记录本身没有 Tweet ID 字段。数据库时间是消息记录时间，投递完成时间以上方 receipt
日志为准，不把二者混为同一种时间。

两个群的现存 state 均包含两帖的成功 `tibo_delivered_ids`、`tibo:{id}` 和 `tweet:{id}`。
因此这是并发完成顺序导致的反序，而非缺 receipt 后的补发。
原始只读取证保存在工作区外
`/home/dev/acceptance-evidence/codex-reset.watcher/20261008-ordering/`：
`host-window.json`、`rpc-window.json`、`platform-window.json`、`upstream.json`、
`live-baseline.json` 和旧实现上的 `regression-before.txt`。
完整原始日志不放入公开仓库；这里仅列出本事件相关结果。

## 最小修复

- 仍以真实 feed `at` 判定资格/年龄；排序使用解析后的实际时间，支持不同时区表示。
  缺失或无效时间仍保守跳过，等待上游修复；不从 Tweet ID 推测时间。
- 每条 Tibo 在途尝试登记时间与逐群完成事件；仅保存在现有内存 `_inflight`，不新增 state 字段。
- Provider、主帖翻译、quote 翻译和图片生成仍在不同 Tweet 间并发。
  卡片先完成一次截图，随后各群复用该图片缓存。
- 各群独立等待已知更早 Tibo 的该群尝试完成，再进入原 `_deliver_notice()`。
  等待时不持有群投递锁，upstream / push-banked 等其它车道不加入排序屏障。
- 群投递成功、失败、异常或取消均释放该群完成事件。整条准备失败/取消也释放所有群；
  task done callback 处理尚未进入协程就被取消的情况。各群并发投递，慢群不拖住其它群。
- 较早 Tweet 跨 polling cycle 仍 inflight 时，其完成事件继续约束新发现的较新 Tweet。
- 保留原成功后 receipt、逐群 retry、去重和文字 fallback。失败帖下一轮可重试；
  不为了全局视觉顺序阻塞后续帖直到重试成功，也不能撤回已经发送的消息。
  排序适用于已发现的在途尝试，无法对已发完后才由上游补出的更早帖子作追溯重排。

回归使用 asyncio Event 控制交错，不靠睡眠猜测快慢；明确先完成 newer 的截图，
然后检查两群尚未发送，再放行 older 并断言 oldest-first。
覆盖同轮/跨轮、慢 Provider/主帖翻译/quote/截图、单群失败/异常/取消、
准备失败、启动前取消、发送中父任务取消、等待方取消、时区、未知时间、
共享截图、文字/发图失败回退、逐群重试、state 重载去重和其它车道不受阻。
