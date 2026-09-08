"""Historical Replay pytest 门禁：真实 corpus fixture → 生产规则判定。

数据：live/forensic_0908/replay/corpus/ 里的真实公开 API fixture
（2026-09-08 落地后归档 + 历史快照序列）。完全离线，无网络请求。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_reset_watcher.plugin import (
    classify_declared_signal,
    is_duplicate_live_confirmation,
    is_observed_declaration,
    signals_from_feed,
)

CORPUS = Path(__file__).resolve().parent / "live/forensic_0908/replay/corpus"


def _landed() -> dict:
    return json.loads((CORPUS / "feed_landed.json").read_text(encoding="utf-8"))


def _event(tweet_id: str) -> dict:
    return next(e for e in _landed()["events"] if str(e.get("id")) == tweet_id)


def _tweet(tweet_id: str) -> dict:
    return next(t for t in _landed()["tweets"] if str(t.get("id")) == tweet_id)


def test_lc7_confirmation_tweet_duplicate_fingerprint_c_silence():
    """09-08 L1#2：live 确认推文 + 同 tweet L4 receipt → C_SILENCE。"""
    event = _event("2097174560412246215")
    tweet = _tweet("2097174560412246215")
    observed = is_observed_declaration(
        event.get("observation_result"), event.get("source"), event.get("observed_at")
    )
    assert observed is False
    duplicate = is_duplicate_live_confirmation(
        source=event.get("source"),
        explicit_reset_claim=tweet.get("explicit_reset_claim"),
        announced_at=event.get("announced_at"),
        tweet_at=tweet.get("at"),
        l4_already=True,
    )
    assert duplicate is True
    assert classify_declared_signal(
        observed=False, l4_already=True, duplicate_confirmation=duplicate
    ) == "C_SILENCE"


def test_lc7_promise_flip_observed_b_confirm():
    """09-08 L1#1：promise 推文被 operator 回写 observed → B_CONFIRM。"""
    event = _event("2097043464538264003")
    assert event.get("source") == "operator-observed"
    assert event.get("observation_result") == "reset_observed"
    assert event.get("observed_at")
    assert is_observed_declaration(
        event.get("observation_result"), event.get("source"), event.get("observed_at")
    ) is True
    assert classify_declared_signal(
        observed=True, l4_already=True, duplicate_confirmation=False
    ) == "B_CONFIRM"


def test_lc4_declared_event_stays_a_primary():
    """LC-4（Aug-27 declaration）：无 L4 receipt → A_PRIMARY（不漏报）。"""
    event = _event("2093014447833116908")
    tweet = _tweet("2093014447833116908")
    observed = is_observed_declaration(
        event.get("observation_result"), event.get("source"), event.get("observed_at")
    )
    duplicate = is_duplicate_live_confirmation(
        source=event.get("source"),
        explicit_reset_claim=tweet.get("explicit_reset_claim"),
        announced_at=event.get("announced_at"),
        tweet_at=tweet.get("at"),
        l4_already=False,
    )
    assert duplicate is False
    assert classify_declared_signal(
        observed=observed, l4_already=False, duplicate_confirmation=duplicate
    ) == "A_PRIMARY"


@pytest.mark.parametrize(
    "tweet_id",
    ["2092311059197808936", "2094252447271366730"],
)
def test_archive_missing_observation_fields_never_c_silence(tweet_id: str):
    """archive 事件缺 observation 字段 → 保守 A_PRIMARY，不得误 C_SILENCE。
    tweet 可能已滚出归档（missing → 结构化字段全缺，同样不构成 duplicate）。"""
    event = _event(tweet_id)
    tweets_by_id = {str(t.get("id")): t for t in _landed().get("tweets", [])}
    tweet = tweets_by_id.get(tweet_id)
    observed = is_observed_declaration(
        event.get("observation_result"), event.get("source"), event.get("observed_at")
    )
    duplicate = is_duplicate_live_confirmation(
        source=event.get("source"),
        explicit_reset_claim=(tweet or {}).get("explicit_reset_claim"),
        announced_at=event.get("announced_at"),
        tweet_at=(tweet or {}).get("at"),
        l4_already=True,
    )
    assert observed is False
    assert duplicate is False
    assert classify_declared_signal(
        observed=False, l4_already=True, duplicate_confirmation=duplicate
    ) == "A_PRIMARY"


def test_lc2_event_rolled_out_archive_reply_fail_closed():
    """LC-2（Aug-24）：tweet 滚出归档、event 无 is_reply → reply unknown
    fail-closed → 不产生 declared 信号（历史真实行为）。"""
    event = _event("2091688655828246890")
    assert event.get("announcement_state") == "announced"
    assert event.get("is_reply") is None
    signals = signals_from_feed(_landed())
    assert all(s.event_id != "2091688655828246890" for s in signals if s.lane == "global_declared")


def test_sep5_false_positive_produces_no_declared_signal():
    """Sep-5 内部误报：reply 推文不产生 declared 信号（生产规则静默）。"""
    feed = json.loads(
        (Path(__file__).resolve().parent / "live/feed_0905.json").read_text(encoding="utf-8")
    )
    signals = signals_from_feed(feed)
    declared_ids = {s.event_id for s in signals if s.lane == "global_declared"}
    assert "2096035748130795560" not in declared_ids
