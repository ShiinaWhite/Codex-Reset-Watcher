# Telegram @codexresetalerts 投递记录（频道最后 20 条，2026-09-08 21:30Z 抓取）

来源：t.me/s/codexresetalerts 公开预览（web_reader 抓取）。这是投递侧 ground truth。
时区：消息时间为 UTC（频道显示）。

| # | 时间 (UTC) | 类别 | 内容摘要 | 关联推文 | 备注 |
|---|---|---|---|---|---|
| 1 | 07-27 00:30(edited) | ⚠️ Reset-related reply | "Usage limits have been reset for all paid... Happy Monday" | 2086972933566857393 | **reply 型 ⚠️ 确实存在**（"Interpretation:" 行） |
| 2 | 07-27 00:34 | ✅ Reset confirmed | "Hi. It is done." | 2086972802457063486 | |
| 3 | 07-28 07:43 | ⚠️ Official reset claim | 承诺"每 1M 用户 reset" | 2087423996115681767 | legacy ⚠️ 子型 |
| 4 | 07-29 01:30 | ✅ Codex reset confirmed | 15M / "Landing in the next hour or so, go /fast" | 2087706104814023111 | |
| 5 | 08-08(edited 11:44) | 🎟️ Banked reset announced | 20M milestone banked | 2090766694897619318 | 与 feed banked_state=announced 1:1 |
| 6 | 08-09 23:52 | ✅ Banked reset confirmed | by 8 PM PT；"does not automatically refresh your current quota" | 2090947196107764189 | 与 feed banked arriving 对应 |
| 7 | 08-23 08:26 | ⚠️ >80% chance | "Reset will land around 14pm PST tomorrow" | 2091412393368945027 | |
| 8 | 08-24 00:47 | ✅ Codex reset confirmed | "Good Sunday... propagated" | 2091688655828246890 | |
| 9 | 08-25(edited 01:07) | （编辑声明，非告警） | Quiet reset confirmed；**"No ✅ alert fired for this one... fell outside the alert freshness window. That's by design"** | 2092311059197808936 | 频道含非告警编辑内容；告警有 freshness 门槛 |
| 10 | 08-26(edited 10:31) | ⚠️ 50% chance | tease 推文（"20 years since I've pressed the reset button... tomorrow"） | 2092862554632826968 | **50% 也投递 → tier 不是投递门槛** |
| 11 | 08-27 16:35 | ✅ Codex reset confirmed | "Never slept better and feeling reseted" | 2093014447833116908 | |
| 12 | 08-29(edited 05:20) | ⚠️ >85% chance by Aug 30 18:00 UTC | "There is a place and a time for resets. Soon, but not today" + Update 附第二条推文 | 2093551005711679557 + 2093573991965557198 | **同一告警消息合并两条推文、原地编辑追加** |
| 13 | 08-29 20:43 | ✅ Codex reset confirmed | "We are reseting usage for all paid users" | 2093801758665715784 | feed 同推文 announced（现在时） |
| 14 | 08-29(edited 20:47) | ⚠️ 93% chance by Aug 31 07:00 UTC | "This celebration is moved to tomorrow" + X 卡片显示 reply "Landing 2:30pm PST" | 2093811840258293947（卡为 2093801838504186008） | |
| 15 | 08-30 19:25(edited) | ⚠️ 93% chance | "Your Codex and ChatGPT Work reset will land at 6pm PST" | 2094144275957350900 | **feed 判 claim=false/state=none 的同推文，Telegram 投递了** |
| 16 | 08-31 02:30(edited) | ✅ Codex reset confirmed | 25M landed（"we have now reset usage"） | 2094251180121854309（feed 事件为相邻 2094252447271366730） | 落地确认链接的推文与 feed 事件 id 不同 |
| 17 | 09-01 23:13 | 🎟️ Banked reset announced | Astra 补偿 banked | 2095651088502591861 | |
| 18 | 09-04 00:15 | 🎟️ Banked reset landing (some users) | | 2095979536043401428 | |
| 19 | 09-04 00:41(edited) | 🎟️ Banked reset landing (all) | | 2096035437299237298 | |
| — | （9-04 00:41 → 9-07 19:25 **空窗**） | | **9-05 00:40 reply 内部误报（forecast latest_alert=reset/confirmed）无任何投递** | 2096035748130795560 | latest_alert 回声 ≠ 投递 |
| 20 | 09-07 19:25 | ⚠️ 83% chance | "Never gonna give you up..."（本次事故推文） | 2097043464538264003 | 385 views @21:30Z；feed 判 none/claim=false |

要点：
- 三个 emoji 家族：⚠️（watch/chance，含 50/80/85/93/83%）、✅（confirmed，含 banked 确认）、🎟️（banked announced/landing）。
- 8-29→8-31 周期投递序列：✅(msg13) → ⚠️93%(msg14) → ⚠️93%(msg15) → ✅(msg16)，与 Aug-31 02:56Z forecast 的 alert_event_id 换锚序列（…947→…900→…730:likely）对应。
- 告警消息普遍存在 edited 标记：上游对同告警演化偏好原地编辑。
- 频道描述："resets only: Tibo's explicit reset signals and verified global resets"。
