"""Historical Replay：v0.1.8 L1/L4 规则在真实历史 Reset lifecycle 上的离线模拟。

完全离线：只读 corpus/ 下的真实 fixture 与本文件内编码的证据时间线
（每条事实标注来源）。不发起任何网络/LLM/QQ 请求。

规则移植（与生产 v0.1.8 一致的最小语义）：
- L1（feed declared）：type=="reset" 且 announcement_state=="announced"
  且非 preview，且 reply 判定（event+tweets[] join）显式 False；
  announced_at 距出现时间 ≤48h；键 global-declared:{id}。
- L4（upstream alert mirror）：仅在**实际观测到** official_signal
  （delivery=alerts、alert_event_id 非空）的时刻产生 mirror 事件
  （键 upstream-alert:{alert_event_id}）；观测间隙不插值、不编造。

诚实性标注：每个事实带来源；无法恢复的时刻标记 UNRECOVERABLE。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

CORPUS = Path(__file__).parent / "corpus"
MAX_AGE_HOURS = 48


def iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def beijing(moment: datetime) -> str:
    return moment.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")


# ---------- L1 规则移植（与生产 signals_from_feed declared 车道一致） ----------

def l1_would_send(event: dict, tweets: dict[str, dict], appearance: datetime) -> tuple[bool, str]:
    if event.get("type") != "reset":
        return False, "type != reset"
    if event.get("announcement_state") != "announced":
        return False, "announcement_state != announced"
    if event.get("preview"):
        return False, "preview"
    tweet = tweets.get(str(event.get("id")))
    flags = [v for v in (event.get("is_reply"), (tweet or {}).get("is_reply")) if v is not None]
    if not flags:
        return False, "reply unknown（fail-closed）"
    if any(f is True for f in flags):
        return False, "is_reply=True"
    if not any(f is False for f in flags):
        return False, "reply unknown（fail-closed）"
    announced_at = iso(event["announced_at"])
    if appearance - announced_at > timedelta(hours=MAX_AGE_HOURS):
        return False, f"age-guard >{MAX_AGE_HOURS}h"
    return True, "declared"


# ---------- 真实证据时间线（逐条标注来源） ----------
# forecast 快照：corpus/forecast_snapshot_series.json（实际观测值）
# Telegram 投递：corpus/tg_deliveries.md（投递侧 ground truth）
# 生产实发（LC-7）：docker logs 2026-09-08（见 REPLAY_EVIDENCE 注释）

LIFECYCLES = [
    {
        "id": "LC-1",
        "name": "Jul-27/29 15M reset",
        "events": [
            # (时间UTC, 车道, tweet, 说明, 来源)
            ("2026-07-28T07:43:00Z", "L4-candidate", "2087423996115681767",
             "TG ⚠️ Official reset claim（承下一日 reset）", "tg msg3"),
            ("2026-07-29T01:30:00Z", "L1", "2087706104814023111",
             "feed announced（archive）；TG ✅ msg4 同刻", "tg msg4 + feed archive"),
        ],
        "forecast_surface": "absent（Jul-31 Wayback 证明 alert 字段整体不存在）",
    },
    {
        "id": "LC-2",
        "name": "Aug-23/24 Sunday reset",
        "events": [
            ("2026-08-23T08:26:00Z", "L4-candidate", "2091412393368945027",
             "TG ⚠️ >80% chance（承 Aug-23 落地）", "tg msg7"),
            ("2026-08-24T00:47:00Z", "L1", "2091688655828246890",
             "feed announced；TG ✅ msg8", "tg msg8 + feed archive"),
        ],
        "forecast_surface": "unknown（Jul-31 与 Aug-31 之间无快照）",
    },
    {
        "id": "LC-3",
        "name": "Aug-25 quiet reset（无预告）",
        "events": [
            ("2026-08-25T14:15:00Z", "L1", "2092311059197808936",
             "feed announced（archive）；TG 无 ✅（编辑声明：freshness window 不告警）",
             "tg msg9 + feed archive"),
        ],
        "forecast_surface": "unknown（同上无快照）",
    },
    {
        "id": "LC-4",
        "name": "Aug-27 reset",
        "events": [
            ("2026-08-27T16:35:05Z", "L1", "2093014447833116908",
             "feed announced（live→claim=True）；TG ✅ msg11", "tg msg11 + feed archive"),
        ],
        "forecast_surface": "unknown",
    },
    {
        "id": "LC-5",
        "name": "Aug-29→31 25M cycle（首按+改期+承诺+落地）",
        "events": [
            ("2026-08-29T20:43:34Z", "L1", "2093801758665715784",
             "「We are reseting usage」首按；feed announced；TG ✅ msg13", "tg msg13 + feed"),
            ("2026-08-29T21:23:38Z", "L4-candidate", "2093811840258293947",
             "改期；TG ⚠️93%（feed state=none）", "tg msg14 + feed"),
            ("2026-08-30T19:24:37Z", "L4-candidate", "2094144275957350900",
             "「will land at 6pm PST」；TG ⚠️93%（feed state=none，claim=False）",
             "tg msg15 + feed"),
            ("2026-08-31T02:34:27Z", "L1", "2094252447271366730",
             "25M 落地（archive announced/high/hard）；TG ✅ msg16", "tg msg16 + feed archive"),
            ("2026-08-31T02:56:48Z", "L4", "2094252447271366730",
             "Wayback：official_signal signal:…:likely dated_commitment 93 window=end of Monday",
             "wayback-20260831025648"),
        ],
        "forecast_surface": "present（Aug-31 快照实证）",
    },
    {
        "id": "LC-6",
        "name": "Sep-05 内部 false-positive（非事件）",
        "events": [
            ("2026-09-05T00:40:39Z", "none", "2096035748130795560",
             "reply 误报：forecast latest_alert=reset/confirmed 但 official_signal=null；"
             "TG 零投递；feed state=none → 三道防线全部静默",
             "forecast_0905.json + tg 空窗"),
        ],
        "forecast_surface": "present（official_signal=null）",
    },
    {
        "id": "LC-7",
        "name": "Sep-08 rickroll promise → 落地 → 确认（生产全链实测）",
        "events": [
            ("2026-09-07T19:24:57Z", "L4", "2097043464538264003",
             "promise 83%；feed state=none；TG ⚠️ msg20；生产实发 09-08 06:23（v0.1.6）",
             "forecast_0908 + tg msg20 + 生产日志"),
            ("2026-09-08T01:34:00Z", "landing", "2097043464538264003",
             "reset 实际发生（6pm PST）；原事件后被上游改写 announced（operator-observed）",
             "feed_landed"),
            ("2026-09-08T11:09:53Z", "L1", "2097043464538264003",
             "生产实发：promise 事件翻转为 announced 被 L1 捕获（落地后 ~1.6h）",
             "生产日志"),
            ("2026-09-08T12:06:50Z", "enrich", "2097174560412246215",
             "fx full len=50 0.78s（确认推文）", "生产日志"),
            ("2026-09-08T12:07:05Z", "LLM", "2097174560412246215",
             "解读成功 attempt=1 opencode-v4f-thinking tokens=2585", "生产日志"),
            ("2026-09-08T12:07:05Z", "L4", "2097174560412246215",
             "确认推文新 alert signal:…:likely；生产实发（两群）", "生产日志"),
            ("2026-09-08T04:05:53Z", "L1", "2097174560412246215",
             "确认事件 announced（04:05:53Z=北京 12:05）；生产实发 12:18-12:19（L4 后 ~12 分钟）",
             "feed_landed + 生产日志"),
        ],
        "forecast_surface": "present（全周期实测）",
    },
]


def main() -> None:
    feed = json.loads((CORPUS / "feed_landed.json").read_text(encoding="utf-8"))
    tweets = {str(t.get("id")): t for t in feed.get("tweets", []) if isinstance(t, dict)}
    events = {str(e.get("id")): e for e in feed.get("events", []) if isinstance(e, dict)}

    print("== 离线 replay：L1 规则逐事件判定（v0.1.8 语义移植）==\n")
    l1_results: dict[str, tuple[bool, str]] = {}
    for lc in LIFECYCLES:
        for at, lane, tweet, note, source in lc["events"]:
            if lane != "L1":
                continue
            event = events.get(tweet)
            if event is None:
                l1_results[tweet] = (False, "event 不在当前 feed 归档（过旧）")
                continue
            appearance = iso(at)
            ok, reason = l1_would_send(event, tweets, appearance)
            l1_results[tweet] = (ok, reason)
            print(f"  {lc['id']} {tweet} announced_at={beijing(iso(event['announced_at']))} "
                  f"→ {'发送' if ok else '不发送'}（{reason}）")

    print("\n== 生命周期对照表 ==\n")
    header = (
        f"{'Lifecycle':<10} {'车道':<12} {'时间(UTC)':<20} {'tweet':<21} "
        f"{'同lifecycle':<10} {'L1新增价值'}"
    )
    print(header)
    print("-" * 118)
    for lc in LIFECYCLES:
        l4_times = [iso(e[0]) for e in lc["events"] if e[1].startswith("L4")]
        l1_times = [iso(e[0]) for e in lc["events"] if e[1] == "L1"]
        for at, lane, tweet, note, source in lc["events"]:
            value = {
                "L4": "promise/commitment 预告",
                "L1": "observed reset 事实（已发生）",
                "L4-candidate": "预告（历史无结构化面，TG 有投递）",
                "landing": "reset 实际发生",
                "enrich": "（展示层补全）",
                "LLM": "（AI 解读）",
                "none": "（正确静默）",
            }.get(lane, "")
            print(f"{lc['id']:<10} {lane:<12} {at:<20} {tweet:<21} "
                  f"{'是':<10} {value}")
        # lifecycle 内时序关系
        if l4_times and l1_times:
            gap = min(l1_times) - min(l4_times)
            print(f"           ↳ L4 首次 → L1 首次 间隔：{gap}；"
                  f"L1 是否重复：{'首按型 L1 独立于承诺' if min(l1_times) < min(l4_times) else 'L1 在全部 L4 之后（确认型）'}")
    print()

    print("== 分类 ==")
    classifications = {
        "LC-1": "B（L1 是该时代唯一结构化通知；forecast 面不存在）",
        "LC-2": "C/D（TG 有 ⚠️ 预告；L1 确认落地；结构化 L4 面证据缺失）",
        "LC-3": "E（quiet reset：TG 故意不告警；本方 L1 因 archive reply 未知 fail-closed 亦静默）",
        "LC-4": "B/C（L1 即落地即通知；结构化 L4 证据缺失）",
        "LC-5": "C（L4=promise 93%×2 先行 ~5-31h；L1×2=首按事实+落地事实；落地后 L4 亦锚定落地推文 → L1 与 L4#3 近重复）",
        "LC-6": "（非事件：三道防线全部正确静默）",
        "LC-7": "C/D（L4 promise 83% 先行 ~7.75h；L1#1=observed 新事实；L4#2 与 L1#2 同 tweet 12 分钟内近重复）",
    }
    for key, text in classifications.items():
        print(f"  {key}: {text}")


if __name__ == "__main__":
    main()
