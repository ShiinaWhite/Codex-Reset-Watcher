# Quoted poll：真实 schema 与展示边界

基线：`ca679b80674fc8d69603659bc65fcfb2adf10c1e`。先通过实际生产投递日志定位
主帖 `2107578625419866469`，两个群于 2026-10-07 05:11 UTC+8 自然收到通知。
主帖正文为 “Four updates or a reset. Or both. How was day 2.”；引用帖为
`2107576143285219799`，作者 Tibo / `thsottiaux`，正文 `Vote`。

2026-10-07 18:13 UTC+8 从真实 Host 容器网络只读取得如下响应。
未经改写的 Fx / Vx 响应与 feed 目标条目保存于
[`tests/fixtures/quoted-poll`](../tests/fixtures/quoted-poll)。完整 feed 原始响应保存于独立验收 evidence。

| 来源 | 引用字段 | Poll 字段 | 选项字段 | 票数 / 百分比 | 总票数 / 截止状态 |
|---|---|---|---|---|---|
| [FxTwitter](https://api.fxtwitter.com/status/2107578625419866469) | `tweet.quote`：`id/text/author/created_timestamp` | `tweet.quote.poll` | `choices[]` | `label/count/percentage` 直接提供 | `total_votes=74565`，`ends_at=2026-10-07T00:58:03Z`，`time_left_en=Final results` |
| [VxTwitter](https://api.vxtwitter.com/thsottiaux/status/2107578625419866469) | `qrt`：`tweetID/text/user_name/user_screen_name/date_epoch` | `qrt.pollData` | `options[]` | `name/votes/percent` 直接提供 | 没有总票数、截止时间、duration 或 closed 字段；全部 option votes 求和为 4012 |
| [Feed](https://codex-reset.com/api/feed) | 当前目标条目没有 quote / qrt / quoted_tweet | 没有 | 没有 | 没有 | 没有 |

Fx 快照：`👌(good day)` 17,902 票 / 24%；`🫨 (needs a reset)` 56,663 票 / 76%。
Vx 快照：相同选项，989 / 3023 票，24.65% / 75.35%；`fetched_on=1791320910`
表明它缓存了更早的响应。两份快照不能混搭。
两个公开 Provider 均没有 viewer-selected / checked 等用户选择字段；不展示 ✓。
Fx 没有独立 duration 或 closed 字段；结束状态来自其明确的 Final results 和带时区的截止时间。

## 归一化与降级

引用 dict 下的 `poll` 是独立结构：`options[{label,votes,percentage}]`、`total_votes`、
`ends_at`、`closed`。Provider parsing 层归一化；没有伪造 option 或拼入 `quote.text`。
Vx 总数只在至少两个有效选项且全部有 votes 时求和；percentage 优先使用 Provider
直接值，缺失时只根据可靠 counts / total 计算。无效数字成为未知值，不显示伪造结果。
带时区的截止时间可以确定状态；无可靠时间 / closed 信息显示“状态未知”。

主帖正文仍按既有优先级选择；图片模式独立补引用与 poll。已取得的 poll 随主帖候选切换
保留，完整选项 / 状态 / 总数更充分的整份快照优先。明确不同引用 ID 不合并。
feed fallback 支持相同结构；本次真实 feed 不含 poll，fallback 回归用例明确属于
基于真实 Fx 结构构造的测试变体，不声称它来自本次 feed 响应。

至少两个有效选项展示 poll；选项不足只显示“投票内容暂不可用”，其中 Vote / 投票占位正文
被隐藏。完全无法确认 poll 时维持普通 quote 正文。每个投票选项首行显示原文及括号内中文译文，
例如 `🤌 good day（美好的一天）`；原文大小写保持上游原样，emoji 只显示一次；仅在展示层去掉包住整个英文选项的外层括号，
例如 `👌 (good day)` 显示为 `👌 good day`，句内括号保留，Provider 原始数据不变。
有 poll 的短引用标题（原文与译文合计不超过 40 字符、无换行）合为 `Vote（投票）` 一行，
无有效译文时仅显示原文。长标题、多行正文与普通非 poll 引用继续整块中英展示。
次行显示比例条与一位小数百分比，比例条保留原始快照精度；底部票数、状态和截止时间保持不变。
数字、票数、状态和截止时间不进入 LLM；译文失败、重复或为空时只展示原文，不留空括号。
整个选项翻译共用已有配置的翻译时间预算，无新增配置。普通 quote、文字模式和文字 fallback
的来源链接与发送行为保持不变。

## 验证与预览

`test_quoted_poll.py` 使用两份真实 Provider fixture，另明确构造 active、缺字段、
feed fallback、失败、跨 Provider 补全等变体。覆盖两项百分比、票数、作者/时间、
无 viewer 标记、可选译文、普通 quote、正文择优、快照原子性、state / dedup 独立性。
`test_card_browser.py` 验证闭合/进行中投票真实 Chromium 截图、比例条宽度、离线字体、
左右布局、无独立来源 URL、无横向溢出。不会发送 QQ 或读正式 state。

`tools/preview_cards.py` 使用 `card.json` 的真实 Fx poll snapshot；手工译文只用于预览/验收。
[本地成果图](preview/tweet-quote-poll.png)。Host 验收应只在独立 helper 中使用同一 presentation
fixture，硬限制测试群 `611817038`；正式 Watcher 与其两群不参与测试。

本轮本地验证：完整 pytest **359 passed**；投票 / Tibo / delivery identity targeted
**131 passed**；真实 Chromium **6 passed**；Ruff、format、diff 检查通过。
在用户目录补齐 Chromium 所需 NSS/NSPR/ALSA 运行库和与 Host 一致的 Noto Color Emoji
fallback，未更换插件字体资产或改动生产环境。

选项合行展示的后续验证：完整 pytest **369 passed**；投票 targeted **32 passed**；
真实 Chromium **6 passed**，并校验中英标签同一行、比例条及百分比在下一行、emoji 不重复。
Ruff、format、diff 检查通过；本地重新生成投票预览，未操作生产或发送 QQ 消息。

外层括号与短标题合行后续验证：完整 pytest **382 passed**；投票 targeted **45 passed**；
真实 Chromium **6 passed**，包括 `Vote（投票）` 同行、英文选项外层括号去除与普通引用不变。
Ruff、format、diff 检查通过；仅更新本地投票预览，未部署或发送 QQ 消息。
