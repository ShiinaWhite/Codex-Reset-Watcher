# Tibo full-push 实际投递反序取证与修复

基线与只读核验的正式 live 为 `db243132b5294142db0a84302abe7ac977929fbd`。
0.2.0 保留下述排序和逐群可靠性规则；原始生产日志归档于仓库外。

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
