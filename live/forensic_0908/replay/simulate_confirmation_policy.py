"""v0.1.9 Confirmation Lane replay 模拟——直接调用生产分类器（无第二实现）。

判定完全复用生产 `classify_declared_signal` /
`is_observed_declaration` / `is_duplicate_live_confirmation`；lifecycle
标签仅用于报告分组，不参与判定。完全离线：只读 corpus fixtures。

运行：本脚本需 maibot_sdk 可导入（与 test_watcher.py 相同要求），例如：
    PYTHONPATH="<repo>/test_env;<repo>" python simulate_confirmation_policy.py
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent.parent.parent
CORPUS = Path(__file__).resolve().parent / "corpus"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "test_env"))

from codex_reset_watcher.plugin import (  # noqa: E402
    classify_declared_signal,
    is_duplicate_live_confirmation,
    is_observed_declaration,
)


def iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> None:
    feed = json.loads((CORPUS / "feed_landed.json").read_text(encoding="utf-8"))
    events = {str(e.get("id")): e for e in feed.get("events", []) if isinstance(e, dict)}
    tweets = {str(t.get("id")): t for t in feed.get("tweets", []) if isinstance(t, dict)}

    # 时间线：(UTC 时刻, 类型, tweet_id, lifecycle)
    # - L4 时刻 = 该 alert 实际观测/投递时刻（Wayback 快照 / 生产日志 / TG 记录）
    # - L1 时刻 = declared 事件在 feed 出现/翻转 announced 的时刻
    #   （LC-7 为生产日志实测；archive 类事件以 announced_at 近似，见报告）
    timeline = [
        ("2026-07-28T07:43:00Z", "L4", "2087423996115681767", "LC-1"),
        ("2026-07-29T01:30:00Z", "L1", "2087706104814023111", "LC-1"),
        ("2026-08-23T08:26:00Z", "L4", "2091412393368945027", "LC-2"),
        ("2026-08-24T00:47:00Z", "L1", "2091688655828246890", "LC-2"),
        ("2026-08-25T14:15:00Z", "L1", "2092311059197808936", "LC-3"),
        ("2026-08-27T16:35:05Z", "L1", "2093014447833116908", "LC-4"),
        ("2026-08-29T20:43:34Z", "L1", "2093801758665715784", "LC-5"),
        ("2026-08-29T21:23:38Z", "L4", "2093811840258293947", "LC-5"),
        ("2026-08-30T19:24:37Z", "L4", "2094144275957350900", "LC-5"),
        ("2026-08-31T02:34:27Z", "L1", "2094252447271366730", "LC-5"),
        ("2026-08-31T02:56:48Z", "L4", "2094252447271366730", "LC-5"),
        ("2026-09-07T19:24:57Z", "L4", "2097043464538264003", "LC-7"),
        ("2026-09-08T11:09:53Z", "L1", "2097043464538264003", "LC-7"),
        ("2026-09-08T12:07:05Z", "L4", "2097174560412246215", "LC-7"),
        ("2026-09-08T12:18:59Z", "L1", "2097174560412246215", "LC-7"),
    ]
    timeline.sort(key=lambda x: iso(x[0]))

    l4_receipt_tweets: dict[str, str] = {}           # tweet_id -> 首次 receipt 时刻
    notified_lifecycles_report: dict[str, int] = {}  # 仅报告分组，不参与判定
    counts: dict[str, int] = {}
    print("== v0.1.9 Historical Replay（生产 classify_declared_signal）==\n")
    for at, kind, tweet_id, lc in timeline:
        if kind == "L4":
            l4_receipt_tweets[tweet_id] = at
            notified_lifecycles_report[lc] = notified_lifecycles_report.get(lc, 0) + 1
            counts["L4-mirror"] = counts.get("L4-mirror", 0) + 1
            print(f"  {at}  {lc}  L4  {tweet_id}  （镜像）")
            continue
        event = events.get(tweet_id) or {}
        observed = is_observed_declaration(
            event.get("observation_result"),
            event.get("source"),
            event.get("observed_at"),
        )
        l4_already = tweet_id in l4_receipt_tweets
        duplicate = is_duplicate_live_confirmation(
            source=event.get("source"),
            explicit_reset_claim=tweets.get(tweet_id, {}).get("explicit_reset_claim"),
            announced_at=iso(event["announced_at"]) if event.get("announced_at") else None,
            tweet_at=iso(tweets[tweet_id]["at"]) if tweet_id in tweets and tweets[tweet_id].get("at") else None,
            l4_already=l4_already,
        )
        decision = classify_declared_signal(
            observed=observed,
            l4_already=l4_already,
            duplicate_confirmation=duplicate,
        )
        counts[decision] = counts.get(decision, 0) + 1
        notified_lifecycles_report[lc] = notified_lifecycles_report.get(lc, 0) + 1
        print(f"  {at}  {lc}  L1  {tweet_id}  → {decision}")

    print("\n统计：")
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}")
    print("\nlifecycle 通知分布（仅报告分组）：")
    for key, value in sorted(notified_lifecycles_report.items()):
        print(f"  {key}: {value}")
    print()
    print("验收核对：")
    print("  - LC-4 → A_PRIMARY；")
    print("  - LC-7 L1#1（promise 翻转 observed）→ B_CONFIRM；")
    print("  - LC-7 L1#2（确认推文 live + claim + L4 receipt）→ C_SILENCE；")
    print("  - 缺 observed/duplicate 字段（archive）→ 保守 A_PRIMARY，零漏报；")
    print("  - LC-6（Sep-5 FP）不在 declared 时间线 → 生产规则本身静默。")


if __name__ == "__main__":
    main()
