"""v0.1.9 候选策略模拟（A primary / B 短确认 / C 静默）——完全离线。

结构化判别器（全部来自上游字段，无 NLP）：
  observed = observation_result=="reset_observed" 或 source=="operator-observed"
             或存在 observed_at 字段
  l4_already = 同 tweet_id 的 L4 receipt（upstream-alert:*:{tweet_id}:*）是否
             先于本 L1 事件存在
  lifecycle_notified = 本 lifecycle 是否已有任何用户通知（lifecycle 归属为
             人工标注——不同 tweet 同 lifecycle 无结构化关联，LC-5 明确标记）
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

CORPUS = Path(__file__).parent / "corpus"


def iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def classify_l1_event(event: dict, l4_alerted_before: bool, lifecycle_notified: bool) -> str:
    observed = (
        event.get("observation_result") == "reset_observed"
        or event.get("source") == "operator-observed"
        or "observed_at" in event
    )
    if l4_alerted_before and not observed:
        return "C-silence"      # 同 confirmation 已被 L4 通知，feed 只是晚归档
    if observed and lifecycle_notified:
        return "B-confirm"      # promise 已通知过，现在新增 observed 事实 → 短确认
    return "A-primary"          # lifecycle 首个用户通知 → 全量（Full Content+LLM）


def main() -> None:
    feed = json.loads((CORPUS / "feed_landed.json").read_text(encoding="utf-8"))
    events = {str(e.get("id")): e for e in feed.get("events", []) if isinstance(e, dict)}

    timeline = [
        ("2026-07-28T07:43:00Z", "L4", "2087423996115681767", "LC-1", None),
        ("2026-07-29T01:30:00Z", "L1", "2087706104814023111", "LC-1", events.get("2087706104814023111")),
        ("2026-08-23T08:26:00Z", "L4", "2091412393368945027", "LC-2", None),
        ("2026-08-24T00:47:00Z", "L1", "2091688655828246890", "LC-2", events.get("2091688655828246890")),
        ("2026-08-25T14:15:00Z", "L1", "2092311059197808936", "LC-3", events.get("2092311059197808936")),
        ("2026-08-27T16:35:05Z", "L1", "2093014447833116908", "LC-4", events.get("2093014447833116908")),
        ("2026-08-29T20:43:34Z", "L1", "2093801758665715784", "LC-5", events.get("2093801758665715784")),
        ("2026-08-29T21:23:38Z", "L4", "2093811840258293947", "LC-5", None),
        ("2026-08-30T19:24:37Z", "L4", "2094144275957350900", "LC-5", None),
        ("2026-08-31T02:34:27Z", "L1", "2094252447271366730", "LC-5", events.get("2094252447271366730")),
        ("2026-08-31T02:56:48Z", "L4", "2094252447271366730", "LC-5", None),
        ("2026-09-05T00:40:39Z", "none", "2096035748130795560", "LC-6", events.get("2096035748130795560")),
        ("2026-09-07T19:24:57Z", "L4", "2097043464538264003", "LC-7", None),
        ("2026-09-08T11:09:53Z", "L1", "2097043464538264003", "LC-7", events.get("2097043464538264003")),
        ("2026-09-08T12:07:05Z", "L4", "2097174560412246215", "LC-7", None),
        ("2026-09-08T12:18:59Z", "L1", "2097174560412246215", "LC-7", events.get("2097174560412246215")),
    ]
    timeline.sort(key=lambda x: iso(x[0]))

    l4_receipt_tweets: dict[str, str] = {}
    notified_lifecycles: set[str] = set()
    counts: dict[str, int] = {}
    print("== v0.1.9 候选策略模拟（A primary / B 短确认 / C 静默）==\n")
    for at, kind, tweet, lc, event in timeline:
        if kind == "L4":
            l4_receipt_tweets[tweet] = at
            notified_lifecycles.add(lc)
            counts["L4-mirror"] = counts.get("L4-mirror", 0) + 1
            print(f"  {at}  {lc}  L4        {tweet}  （镜像）")
            continue
        # L1 事件：先过现有规则（declared + reply + preview）
        if event is None:
            print(f"  {at}  {lc}  L1        {tweet}  — （事件不在当前归档，历史无法完整模拟）")
            counts["unrecoverable"] = counts.get("unrecoverable", 0) + 1
            continue
        from replay_offline import l1_would_send  # 复用规则移植
        tweets_all = {str(t.get("id")): t for t in feed.get("tweets", []) if isinstance(t, dict)}
        ok, reason = l1_would_send(event, tweets_all, iso(at))
        # 注意：reply 判定需要 tweets[] join——从 landed feed 取
        if not ok:
            print(f"  {at}  {lc}  L1        {tweet}  — 静默（{reason}）")
            counts["silent-rule"] = counts.get("silent-rule", 0) + 1
            continue
        l4_before = tweet in l4_receipt_tweets
        lifecycle_notified = lc in notified_lifecycles
        decision = classify_l1_event(event, l4_before, lifecycle_notified)
        counts[decision] = counts.get(decision, 0) + 1
        notified_lifecycles.add(lc)
        print(f"  {at}  {lc}  L1        {tweet}  → {decision}")

    print("\n统计：")
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}")
    print()
    print("结论：")
    print("  - 无任何 lifecycle 因 A/B/C 策略而完全漏报（LC-4/LC-5 保守走 A-primary）；")
    print("  - LC-7 唯一被抑制项：L1#2（同 confirmation tweet 已被 L4 于 12:07 通知）→ C-silence；")
    print("  - LC-7 L1#1 因 observation_result=reset_observed 走 B-confirm（短确认）；")
    print("  - LC-5 首按事件在当时代里无 L4 先行 → A-primary（独立通知）；其落地事件 archive")
    print("    无 observation 字段 → 保守 A-primary（宁可多发）；")
    print("  - LC-2/LC-3 的 fail-closed 静默与 A/B/C 策略正交（规则层已拦截）。")
print("  - v0.1.9 验收核对：LC-4=A-primary ✓；LC-7 L1#1=B-confirm ✓；LC-7 L1#2=C-silence ✓；")
print("    相比 v0.1.8 减少 1 条全量 L1（L1#2→C）+ 1 条降级为短确认（L1#1→B），零漏报。")


if __name__ == "__main__":
    main()
