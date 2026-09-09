"""Codex 额度重置提醒插件验证（pytest）。

v0.1.4：feed 主源车道（Banked 生命周期 + Global 已宣告）+ Tibo 精确时间
辅助源（已确认/时间更新/预估预告）+ 多群通知（per-group receipt：发送
成功才记录对应群状态，失败群独立重试；legacy 单群配置一次性迁移）+
age-guard 日志仅对首次遇到的过期信号打印一次（已见 key 静默跳过）。
真实 fixture 优先（2026-09 Astra banked 链、2026-08 announced→arriving
→available 全链、已知误报 reply 事件）；synthetic 数据只覆盖真实数据中
不存在的纯内部边界（unknown 枚举、发送失败）。不依赖 MaiBot Core 与
外网（fake ctx + 真实 schema 样本）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_reset_watcher.plugin as plugin_module  # noqa: E402
from codex_reset_watcher.plugin import (  # noqa: E402
    BANKED_NOTICE_TITLE,
    CONTRACT_PROMPT,
    CodexResetWatcher,
    CodexResetWatcherConfig,
    Conclusion,
    GLOBAL_CONFIRMED_TITLE,
    GLOBAL_NOTICE_TITLE,
    TRANSLATION_MAX_CHARS,
    _display_source,
    _display_tibo_status,
    _display_verification_status,
    _format_title,
    _format_tibo_observation_log,
    _parse_iso,
    _reason_from_tags,
    _status_id,
    _validate_llm_translation,
    _content_from_provider_payload,
    _extract_json_object,
    build_confirmed_message,
    build_full_notice,
    conclusion_from_tibo_current,
    signals_from_feed,
    upstream_alert_event_id,
)

PROBE_DIR = Path(__file__).resolve().parent

BEIJING = ZoneInfo("Asia/Shanghai")


def _future_iso(hours: float = 3.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _tibo_current(**overrides: object) -> dict:
    """真实 schema 形状：SCHEDULED 倒计时路径实际字段。

    注意不带 confirmation——前端 SCHEDULED 路径不读它，无真实样本
    证明 SCHEDULED 一定带 confirmation，因此它不是必需字段。
    """
    payload: dict = {
        "status": "SCHEDULED",
        "expectedResetAt": _future_iso(),
        "expectedResetTimeText": "6pm PT",
        "isApproximate": False,
        "resetSource": "thsottiaux",
        "resetSourceUrl": "https://x.com/thsottiaux/status/999",
        "verificationStatus": "DIRECT_VERIFIED",
    }
    payload.update(overrides)
    return payload


# ===== 真实 schema 判定 =====


def test_scheduled_exact_confirms():
    conclusion = conclusion_from_tibo_current(_tibo_current())
    assert conclusion is not None
    assert conclusion.kind == "scheduled"
    assert conclusion.stable_id == "https://x.com/thsottiaux/status/999"


def test_time_changed_confirms_as_update_kind():
    conclusion = conclusion_from_tibo_current(_tibo_current(status="TIME_CHANGED"))
    assert conclusion is not None
    assert conclusion.kind == "time_changed"


def test_none_with_future_time_is_silent():
    assert conclusion_from_tibo_current(_tibo_current(status="NONE")) is None


def test_confirmed_with_future_time_is_silent():
    # CONFIRMED 是完成态，不是未来计划。
    assert conclusion_from_tibo_current(_tibo_current(status="CONFIRMED")) is None


def test_due_and_confirming_are_silent():
    assert conclusion_from_tibo_current(_tibo_current(status="DUE")) is None
    assert conclusion_from_tibo_current(_tibo_current(status="CONFIRMING")) is None


def test_approximate_scheduled_yields_estimated_conclusion():
    """v0.1.2：isApproximate 不再一票否决，改为"预估"结论。"""
    conclusion = conclusion_from_tibo_current(_tibo_current(isApproximate=True))
    assert conclusion is not None
    assert conclusion.approximate is True


def test_untrusted_verification_is_silent():
    assert (
        conclusion_from_tibo_current(_tibo_current(verificationStatus="INDEXED_ONLY"))
        is None
    )


def test_tibo_current_has_no_numeric_confidence_gate():
    """feed 主源化后，Tibo /api/events 数值 confidence 兜底门槛已删除。"""
    assert conclusion_from_tibo_current(_tibo_current()) is not None


def test_event_confidence_gate_removed():
    """旧 event_confidence_for_url 门槛函数已随 feed 主源化删除。"""
    assert not hasattr(plugin_module, "event_confidence_for_url")


def test_reason_compensation_tag():
    assert _reason_from_tags(["compensation"]) == "补偿"
    assert _reason_from_tags(["unknown-tag"]) == "未说明"
    assert _reason_from_tags(None) == "未说明"


def test_past_time_is_silent():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert conclusion_from_tibo_current(_tibo_current(expectedResetAt=past)) is None


def test_missing_source_url_still_confirms_conservatively():
    conclusion = conclusion_from_tibo_current(_tibo_current(resetSourceUrl=""))
    assert conclusion is not None
    assert conclusion.stable_id.startswith("no-source:")


# ===== 真实 probe 样本回归（走 _fetch_conclusion 封装，不绕过） =====


def _probe(name: str) -> dict:
    return json.loads((PROBE_DIR / name).read_text(encoding="utf-8"))


def _wire_fetch(plugin: CodexResetWatcher, current: dict, events: dict | None, feed: dict | None):
    async def fake_get_json(url: str, timeout_seconds: int):
        if "/api/reset/current" in url:
            return current
        if "/api/events" in url:
            return events
        if "/api/feed" in url:
            return feed
        return None

    plugin._get_json = fake_get_json  # type: ignore[method-assign]


def _wired_conclusion(plugin: CodexResetWatcher, current, events, feed):
    _wire_fetch(plugin, current, events, feed)

    async def scenario():
        return await plugin._fetch_conclusion()

    return asyncio.run(scenario())


def _tibo_payload(status: str, expected_reset_at, source_url: str = "https://x.com/thsottiaux/status/999") -> dict:
    """真实 /api/reset/current schema 形状（字段与 probe/live 一致）。"""
    return {
        "status": status,
        "expectedResetAt": expected_reset_at,
        "expectedResetTimeText": "6pm PT",
        "isApproximate": False,
        "resetSource": "thsottiaux",
        "resetSourceUrl": source_url,
        "verificationStatus": "DIRECT_VERIFIED",
    }


def _stage_payload(plugin: CodexResetWatcher, payload: dict, events: dict | None = None):
    """经 _get_json 桩 + _check_once 走完整生产路径（含去重/通知/状态写盘）。"""
    current = payload

    async def fake_get(url: str, timeout_seconds: int):
        if "api/reset/current" in url:
            return current
        if "api/events" in url:
            if events is not None:
                return events
            return {"data": [{"confidence": 0.95, "source_url": current.get("resetSourceUrl")}]}
        if "api/feed" in url:
            return None
        return None

    plugin._get_json = fake_get  # type: ignore[method-assign]
    asyncio.run(plugin._check_once())


def test_full_lifecycle_through_production_path(tmp_path):
    """v0.1.10 退役回归：SCHEDULED → DUE → CONFIRMING → TIME_CHANGED →
    CONFIRMED 完整生命周期全部经 _check_once 生产路径，QQ 通知为 0——
    Tibo /api/reset/current 用户侧退役（只读观察），独立重置预告/时间
    更新不再出现；_maybe_notify 旧路径仅作死代码保留。"""
    plugin = _make_plugin(tmp_path)
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    t_sched = base + timedelta(hours=2)
    t_changed = base + timedelta(hours=4)
    url = "https://x.com/thsottiaux/status/999"

    _stage_payload(plugin, _tibo_payload("SCHEDULED", t_sched.isoformat(), url))
    _stage_payload(plugin, _tibo_payload("DUE", t_sched.isoformat(), url))
    _stage_payload(plugin, _tibo_payload("CONFIRMING", t_sched.isoformat(), url))
    _stage_payload(plugin, _tibo_payload("TIME_CHANGED", t_changed.isoformat(), url))
    confirmed = {
        "status": "CONFIRMED",
        "expectedResetAt": None,
        "expectedResetTimeText": None,
        "isApproximate": False,
        "resetSource": "thsottiaux",
        "resetSourceUrl": url,
        "verificationStatus": "DIRECT_VERIFIED",
        "confirmation": {"type": "DIRECT", "confidence": 0.95,
                         "confirmedAt": t_changed.isoformat(),
                         "sourceUrl": url},
    }
    _stage_payload(plugin, confirmed)
    assert plugin._ctx.send.sent_messages == []
    # 用户侧退役：不写任何 Tibo 通知 receipt（旧字段仅由死代码路径写入）。
    assert _gstate(plugin).get("last_notified_reset_at") is None
    assert _gstate(plugin).get("active_plan") is None


def test_due_confirming_future_time_still_silent(tmp_path):
    """即使 DUE/CONFIRMING 携带了未来 expectedResetAt（未观察到的形状），
    也绝不发送，且不清 active_plan。"""
    plugin = _make_plugin(tmp_path)
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    t_future = base + timedelta(hours=2)
    first = _conclusion()
    asyncio.run(plugin._maybe_notify(first, ["100000001"], BEIJING))  # noqa: SLF001
    assert _gstate(plugin)["active_plan"] is not None
    for status in ("DUE", "CONFIRMING"):
        _stage_payload(plugin, _tibo_payload(status, t_future.isoformat()))
    assert len(plugin._ctx.send.sent_messages) == 1
    assert _gstate(plugin)["active_plan"] is not None


def test_no_source_time_changed_still_never_sends(tmp_path):
    """v0.1.10：缺真实 source URL 的 TIME_CHANGED 同样不发任何 QQ 通知
    （用户侧整体退役，不再有"已确认"退化发送）。"""
    plugin = _make_plugin(tmp_path)
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    t1 = base + timedelta(hours=2)
    t2 = base + timedelta(hours=4)
    _stage_payload(plugin, _tibo_payload("SCHEDULED", t1.isoformat(), source_url=""))
    _stage_payload(plugin, _tibo_payload("TIME_CHANGED", t2.isoformat(), source_url=""))
    assert plugin._ctx.send.sent_messages == []


def test_feed_failure_degrades_reason_and_notifies(tmp_path):
    """feed 获取失败（None）→ reason/原文回退，不阻塞 Tibo 主结论。"""
    plugin = _make_plugin(tmp_path)
    current = _tibo_current()

    async def fake_get(url: str, timeout_seconds: int):
        if "api/reset/current" in url:
            return current
        return None  # feed 失败

    plugin._get_json = fake_get  # type: ignore[method-assign]

    async def scenario():
        return await plugin._fetch_conclusion()

    conclusion = asyncio.run(scenario())
    assert conclusion is not None
    assert conclusion.reason == "未说明"
    assert conclusion.raw_text is None


def test_current_payload_structured_log(tmp_path, caplog):
    """部署观察日志（v0.1.5）：真实 current 响应以人类可读中文摘要记录；
    INFO 不再包含原始 enum dump 与机器字段名（raw 字段仅 DEBUG）。"""
    import logging

    plugin = _make_plugin(tmp_path)
    current = _probe("probe2_tibo_current.json")
    with caplog.at_level(logging.INFO, logger="codex_reset_watcher.plugin"):
        _wired_conclusion(plugin, current, {"data": []}, None)
    assert (
        "Tibo 监控：当前无待执行的重置计划｜最近状态：已确认｜验证：直接确认"
        in caplog.text
    )
    assert "Tibo current raw" not in caplog.text  # raw 字段仅 DEBUG 可见
    assert "resetSourceUrl" not in caplog.text
    assert "isApproximate" not in caplog.text


def test_wiring_tibo_events_endpoint_no_longer_fetched(tmp_path):
    """feed 主源化后不再请求 Tibo /api/events；SCHEDULED 结论不受其门槛影响。"""
    plugin = _make_plugin(tmp_path)
    current = _tibo_current()
    requested: list[str] = []

    async def fake_get(url: str, timeout_seconds: int):
        requested.append(url)
        if "api/reset/current" in url:
            return current
        return None

    plugin._get_json = fake_get  # type: ignore[method-assign]
    conclusion = asyncio.run(plugin._fetch_conclusion())
    assert conclusion is not None
    assert conclusion.reset_at is not None
    assert all("/api/events" not in url for url in requested)


def test_wiring_tibo_conclusion_joins_feed_source_text(tmp_path):
    """resetSourceUrl 与 feed 推文同 ID 时，结论携带展示原文（仅展示）。"""
    plugin = _make_plugin(tmp_path)
    feed = _probe("probe_cr_feed.json")
    target = next(t for t in feed["tweets"] if t.get("text"))
    current = _tibo_current(resetSourceUrl=target["url"])
    conclusion = _wired_conclusion(plugin, current, None, feed)
    assert conclusion is not None
    assert conclusion.raw_text == target["text"]


def test_status_id_extracts_tweet_id():
    """结构化 join 用的推文 ID 提取（不做任何语义解析）。"""
    assert (
        _status_id("https://x.com/thsottiaux/status/2095651088502591861")
        == "2095651088502591861"
    )
    assert _status_id("https://x.com/thsottiaux/status/123?foo=bar") == "123"
    assert _status_id("no-source:2026-01-01T00:00:00+00:00") == ""
    assert _status_id(None) == ""


def test_display_source_collapses_and_truncates():
    """原文展示：折叠空白、超长截断（纯展示，不参与语义）。"""
    assert _display_source("a\n\n  b\tc") == "a b c"
    long_text = "x" * 400
    shown = _display_source(long_text)
    assert shown is not None and len(shown) == 300 and shown.endswith("…")
    assert _display_source(None) is None
    assert _display_source("   ") is None


def test_probe_real_confirmed_sample_stays_silent_and_clears_plan(tmp_path):
    """probe2_tibo_current.json 是真实 /api/reset/current 样本（CONFIRMED + null）：
    走完整封装必须静默，并清除 active plan。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["active_plan"] = {
        "reset_at": "2026-08-31T02:00:00+00:00",
        "source_url": "x",
        "notified_at": "2026-08-30T10:00:00+00:00",
    }
    current = _probe("probe2_tibo_current.json")
    events = _probe("probe_tibo_ev1.json")
    feed = _probe("probe_cr_feed.json")
    assert _wired_conclusion(plugin, current, events, feed) is None
    assert _gstate(plugin).get("active_plan") is None


def test_probe_real_feed_reason_via_wiring(tmp_path):
    """probe_cr_feed.json 真实 shape：feed["events"][].url + reason_tags；
    source_url 匹配时 reason 应从真实 feed 提取（milestone → 庆祝活动）。"""
    feed = _probe("probe_cr_feed.json")
    target_event = next(e for e in feed["events"] if e.get("reason_tags"))
    plugin = _make_plugin(tmp_path)
    current = _tibo_current(resetSourceUrl=target_event["url"])
    events = {"data": [{"confidence": 0.95, "source_url": target_event["url"]}]}
    conclusion = _wired_conclusion(plugin, current, events, feed)
    assert conclusion is not None
    assert conclusion.reason == "庆祝活动"


# ===== Codex 宽 window 反例：永不触发 =====


def test_codex_conclusion_extractor_removed():
    """Codex forecast 提取器已删除：import 即失败才是预期。"""
    import codex_reset_watcher.plugin as plugin

    assert not hasattr(plugin, "conclusion_from_forecast")


def test_wide_center_window_has_no_trigger_path():
    """around 2 PM PT（2 小时宽 center 窗口，中点 21:00）没有任何触发路径。

    插件唯一的触发函数是 conclusion_from_tibo_current，它不读取
    Codex window 字段；此处断言该反例数据无法构造出 Conclusion。
    """
    codex_window = {
        "label": "around 2 PM PT on Aug 23",
        "start_at": "2026-08-23T20:00:00.000Z",
        "end_at": "2026-08-23T22:00:00.000Z",
        "time_zone": "America/Los_Angeles",
        "target_kind": "center",
        "target_at": "2026-08-23T21:00:00.000Z",
    }
    assert conclusion_from_tibo_current(codex_window) is None
    assert conclusion_from_tibo_current({}) is None


# ===== 插件行为（fake ctx） =====


class _FakeChat:
    async def get_stream_by_group_id(self, group_id: str, platform: str = "qq"):
        return {"stream": {"stream_id": f"qq-group-{group_id}"}}

    async def open_session(self, **kwargs):
        raise AssertionError("should not need open_session")


class _FakeSend:
    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []

    async def text(self, text: str, stream_id: str, **kwargs):
        self.sent_messages.append((stream_id, text))
        return {"sent": True, "message_id": "mid-1"}


class _FakeLLM:
    """可替换的 ctx.llm 代理：默认禁止调用，测试自行覆写 generate。"""

    def __init__(self):
        self.generate_calls: list[dict] = []
        self.models: list[str] = ["utils", "replyer"]

    async def get_available_models(self):
        result = self.models
        if callable(result):
            return result()
        return result

    async def generate(self, prompt, model="", temperature=None, max_tokens=None, **kwargs):
        self.generate_calls.append(
            {"prompt": prompt, "model": model, "temperature": temperature,
             "max_tokens": max_tokens, **kwargs}
        )
        raise AssertionError("测试未设置 ctx.llm.generate 行为")


class _FakeCtx:
    def __init__(self, tmp_path: Path):
        from types import SimpleNamespace

        self.chat = _FakeChat()
        self.send = _FakeSend()
        self.llm = _FakeLLM()
        self.paths = SimpleNamespace(data_dir=tmp_path)


def _make_plugin(tmp_path: Path, **watcher: object) -> CodexResetWatcher:
    plugin = CodexResetWatcher()
    config = {
        "plugin": {"enabled": True, "config_version": "1.0.0"},
        "watcher": {
            "group_id": "100000001",
            "check_interval": 240,
            "timezone": "Asia/Shanghai",
            "tibo_base": "https://tibo.modelyard.dev",
            "codex_base": "https://codex-reset.com",
            **watcher,
        },
    }
    plugin.set_plugin_config(config)
    plugin._ctx = _FakeCtx(tmp_path)  # noqa: SLF001
    plugin._load_state()  # noqa: SLF001
    return plugin


def _gstate(plugin: CodexResetWatcher, gid: str = "100000001") -> dict:
    """v0.1.3 per-group receipt 访问助手（等价 plugin._group_state）。"""
    return plugin._group_state(gid)  # noqa: SLF001


async def _drain_inflight(plugin: CodexResetWatcher) -> None:
    """等待全部后台管线（l4/l1/banked）完成（轮询 0.01s）。"""
    while plugin._inflight:
        await asyncio.sleep(0.01)


def _conclusion(
    kind: str = "scheduled",
    hours: float = 3.0,
    stable_id: str = "https://x.com/thsottiaux/status/999",
    reset_at: datetime | None = None,
) -> Conclusion:
    return Conclusion(
        kind=kind,
        stable_id=stable_id,
        reset_at=reset_at or (datetime.now(timezone.utc) + timedelta(hours=hours)),
        reason="未说明",
        source_url=stable_id,
    )


def _run(plugin: CodexResetWatcher, conclusion: Conclusion | None):
    """v0.1.10 起生产路径不再调用 _maybe_notify（Tibo 用户侧退役）。
    本助手显式驱动保留的 _maybe_notify（死代码），仅用于覆盖其内部
    去重/升级/多群逻辑；生产用户行为由 Tibo 退役测试（零发送）代表。"""

    async def scenario():
        if conclusion is None:
            return

        async def none_fetch(url: str, timeout_seconds: int):
            return None  # feed 车道数据置空，保持既有用例聚焦 Tibo 车道

        plugin._get_json = none_fetch  # type: ignore[method-assign]
        await plugin._maybe_notify(  # noqa: SLF001
            conclusion, plugin._target_groups(), plugin._target_zone()  # noqa: SLF001
        )

    asyncio.run(scenario())


def test_scheduled_first_notify(tmp_path):
    plugin = _make_plugin(tmp_path)
    _run(plugin, _conclusion())
    assert len(plugin._ctx.send.sent_messages) == 1
    body = plugin._ctx.send.sent_messages[0][1]
    assert body.startswith("Codex 额度重置已确认｜北京时间")
    assert "状态：已确认" in body


def test_repeat_scheduled_no_duplicate(tmp_path):
    plugin = _make_plugin(tmp_path)
    first = _conclusion()
    _run(plugin, first)
    _run(
        plugin,
        Conclusion(
            kind="scheduled",
            stable_id=first.stable_id,
            reset_at=first.reset_at,
            reason="未说明",
            source_url=first.source_url,
        ),
    )
    assert len(plugin._ctx.send.sent_messages) == 1


def test_time_changed_with_active_plan_sends_update(tmp_path):
    plugin = _make_plugin(tmp_path)
    _run(plugin, _conclusion(kind="scheduled", hours=2.0))
    _run(plugin, _conclusion(kind="time_changed", hours=4.0))
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 2
    assert bodies[1].startswith("Codex 重置时间更新")
    assert "原计划：北京时间" in bodies[1]
    assert "最新计划：北京时间" in bodies[1]


def test_same_time_different_url_sends_once(tmp_path):
    """A：SCHEDULED 同一时间、不同 source URL → 只发送一次。"""
    plugin = _make_plugin(tmp_path)
    first = _conclusion(
        kind="scheduled", hours=3.0, stable_id="https://x.com/thsottiaux/status/AAA"
    )
    _run(plugin, first)
    _run(
        plugin,
        Conclusion(
            kind="scheduled",
            stable_id="https://x.com/thsottiaux/status/BBB",
            reset_at=first.reset_at,
            reason="未说明",
            source_url="https://x.com/thsottiaux/status/BBB",
        ),
    )
    assert len(plugin._ctx.send.sent_messages) == 1


def test_time_changed_same_time_different_url_is_silent(tmp_path):
    """B：TIME_CHANGED 同一时间、不同 source URL → 不再发送任何通知。"""
    plugin = _make_plugin(tmp_path)
    first = _conclusion(
        kind="scheduled", hours=3.0, stable_id="https://x.com/thsottiaux/status/AAA"
    )
    _run(plugin, first)
    _run(
        plugin,
        Conclusion(
            kind="time_changed",
            stable_id="https://x.com/thsottiaux/status/BBB",
            reset_at=first.reset_at,
            reason="未说明",
            source_url="https://x.com/thsottiaux/status/BBB",
        ),
    )
    assert len(plugin._ctx.send.sent_messages) == 1


def test_time_changed_across_cycle_without_active_plan_confirms(tmp_path):
    """上一轮已结束（无 active plan 只有历史）→ 新一轮首见 TIME_CHANGED
    必须发"已确认"，不能引用上一轮时间。"""
    plugin = _make_plugin(tmp_path)
    old = _conclusion(
        kind="scheduled", hours=1.0, stable_id="https://x.com/thsottiaux/status/OLD"
    )
    _run(plugin, old)
    assert len(plugin._ctx.send.sent_messages) == 1
    # 模拟上一轮结束：active plan 被清除，只剩历史通知时间。
    _gstate(plugin).pop("active_plan", None)
    _gstate(plugin)["last_notified_reset_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=5)
    ).isoformat()
    new = _conclusion(
        kind="time_changed", hours=3.0, stable_id="https://x.com/thsottiaux/status/NEW"
    )
    _run(plugin, new)
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 2
    assert bodies[1].startswith("Codex 额度重置已确认")
    assert "原计划" not in bodies[1]


def test_terminal_status_clears_active_plan(tmp_path):
    """CONFIRMED/NONE 等终止状态清除 active plan，但保留去重时间。"""
    plugin = _make_plugin(tmp_path)
    _run(plugin, _conclusion(kind="scheduled", hours=1.0))
    assert _gstate(plugin).get("active_plan") is not None
    plugin._clear_active_plan()  # noqa: SLF001
    assert _gstate(plugin).get("active_plan") is None
    assert _gstate(plugin).get("last_notified_reset_at") is not None


def test_time_changed_without_history_falls_back_to_confirmed(tmp_path):
    plugin = _make_plugin(tmp_path)
    _run(plugin, _conclusion(kind="time_changed", hours=4.0))
    assert len(plugin._ctx.send.sent_messages) == 1
    assert plugin._ctx.send.sent_messages[0][1].startswith("Codex 额度重置已确认")


def test_send_failure_does_not_mark_notified(tmp_path):
    plugin = _make_plugin(tmp_path)

    async def failing_send(group_id: str, message: str) -> bool:
        return False

    plugin._send_group_text = failing_send  # type: ignore[method-assign]
    asyncio.run(plugin._maybe_notify(_conclusion(), ["100000001"], BEIJING))  # noqa: SLF001
    # 发送失败 → 该群 receipt 无任何记录（落盘也不会发生）。
    assert _gstate(plugin) == {}


def test_network_failure_keeps_loop_alive(tmp_path, monkeypatch):
    plugin = _make_plugin(tmp_path)

    async def boom(feed=None):
        raise ConnectionError("dns down")

    async def none_fetch(url: str, timeout_seconds: int):
        return None

    monkeypatch.setattr(plugin, "_get_json", none_fetch)
    monkeypatch.setattr(plugin, "_fetch_conclusion", boom)
    sleeps = 0

    async def fake_sleep(_: float):
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(plugin._watch_loop())  # noqa: SLF001
    assert sleeps == 2


def test_restart_does_not_renotify(tmp_path):
    plugin = _make_plugin(tmp_path)
    conclusion = _conclusion()
    _run(plugin, conclusion)
    assert (tmp_path / "reset_state.json").exists()

    restarted = _make_plugin(tmp_path)
    assert _gstate(restarted).get("last_source_url") == conclusion.stable_id
    _run(
        restarted,
        Conclusion(
            kind="scheduled",
            stable_id=conclusion.stable_id,
            reset_at=conclusion.reset_at,
            reason="未说明",
            source_url=conclusion.source_url,
        ),
    )
    assert restarted._ctx.send.sent_messages == []


def test_old_state_not_migrated(tmp_path):
    (tmp_path / "reset_state.json").write_text(
        '{"version": 3, "state": {"last_alert_event_id": "codex-signal:1"}}',
        encoding="utf-8",
    )
    plugin = _make_plugin(tmp_path)
    _run(plugin, _conclusion())
    assert len(plugin._ctx.send.sent_messages) == 1


def test_unload_cancels_background_task(tmp_path):
    async def scenario():
        plugin = _make_plugin(tmp_path)

        async def fake_fetch(feed=None) -> None:
            await asyncio.sleep(3600)
            return None

        async def none_fetch(url: str, timeout_seconds: int):
            return None

        plugin._get_json = none_fetch  # type: ignore[method-assign]
        plugin._fetch_conclusion = fake_fetch  # type: ignore[method-assign]
        await plugin.on_load()
        assert plugin._task is not None and not plugin._task.done()  # noqa: SLF001
        await plugin.on_unload()
        assert plugin._task is None  # noqa: SLF001

    asyncio.run(scenario())


def test_beijing_format():
    beijing = datetime(2026, 8, 31, 9, 0, tzinfo=BEIJING)
    now = datetime(2026, 8, 31, 6, 30, tzinfo=BEIJING)
    body = build_confirmed_message(beijing, now, "未说明")
    assert body.startswith("Codex 额度重置已确认｜北京时间 8月31日 09:00")
    assert "北京时间 2026-08-31 09:00" in body
    assert "距离重置：2小时30分钟" in body


# ===== feed 车道：真实 fixture 提取 =====
# 真实样本：live/feed_0905.json = 2026-09-03~05 Astra banked 链 +
# 已知误报 reply 事件；probe_cr_feed.json = 2026-08-21~22 banked
# announced→arriving→available 全链 + 8-31 25M 宣告。


def test_signals_from_feed_banked_chain_real():
    """live 真实样本：9 月 Astra banked 链（announced + arriving×2）
    全部产出信号；连同 8 月链共 6 条 banked 信号。"""
    feed = _probe("live/feed_0905.json")
    banked = {s.key for s in signals_from_feed(feed) if s.lane == "banked"}
    assert {
        "banked:2095651088502591861:announced",
        "banked:2095979536043401428:arriving",
        "banked:2096035437299237298:arriving",
    } <= banked
    assert len(banked) == 6  # 8 月 20M 链 3 条 + 9 月 Astra 链 3 条


def test_signals_from_feed_banked_available_real():
    """probe 真实样本：8-21~22 生命周期三态齐备，含 available。"""
    feed = _probe("probe_cr_feed.json")
    banked = {s.key for s in signals_from_feed(feed) if s.lane == "banked"}
    assert banked == {
        "banked:2090766694897619318:announced",
        "banked:2090947196107764189:arriving",
        "banked:2090964822422949999:available",
    }


def test_signals_from_feed_declared_real():
    """probe 真实样本：announced 非 preview 且回复状态"明确为 False"的
    宣告事件产出信号。

    - 2092311059197808936（archive、tweet 已滚出窗口）回复状态 unknown
      → 不视为非 reply，排除（fail closed）；
    - 2087706104814023111 为 preview 变体，排除；
    - archive 事件 + tweet is_reply=false（2094252447271366730 等）
      仍正常产出。"""
    feed = _probe("probe_cr_feed.json")
    declared = {s.key for s in signals_from_feed(feed) if s.lane == "global_declared"}
    assert declared == {
        "global-declared:2094252447271366730",
        "global-declared:2093801758665715784",
        "global-declared:2093014447833116908",
        "global-declared:2091688655828246890",
    }


def test_signals_from_feed_excludes_known_false_positive_reply():
    """已知误报样本 2096035748130795560（回复推文被上游标为 announced，
    官方 TG 误发后删除更正）必须被排除。"""
    feed = _probe("live/feed_0905.json")
    declared = {s.key for s in signals_from_feed(feed) if s.lane == "global_declared"}
    assert "global-declared:2096035748130795560" not in declared


def test_archived_reply_event_cannot_become_declared_notify():
    """回归（真实误报事件）：live → archive 后 event 级 is_reply 消失
    （真实 archive 事件即缺该字段），tweets[] 仍保留 is_reply=true →
    仍不得产出 Global declared 信号。只动结构化字段，不碰文本。"""
    feed = _probe("live/feed_0905.json")
    fp_tweet = next(
        t for t in feed["tweets"] if t.get("id") == "2096035748130795560"
    )
    assert fp_tweet["is_reply"] is True  # 前提：tweet 元数据保留回复标记

    archived_events = []
    for event in feed["events"]:
        if event.get("id") == "2096035748130795560":
            archived = {k: v for k, v in event.items() if k != "is_reply"}
            archived["source"] = "archive"
            archived["source_label"] = "Verified archive"
            archived_events.append(archived)
        else:
            archived_events.append(event)

    signals = signals_from_feed({"events": archived_events, "tweets": feed["tweets"]})
    declared = {s.key for s in signals if s.lane == "global_declared"}
    assert "global-declared:2096035748130795560" not in declared


def test_signals_from_feed_excludes_future_promise_events():
    """真实预告推文（8-30 "will land at 6pm PST" 与 8-23 hinted）在 feed
    中 announcement_state=none/hinted，无结构化 watch 分数 → 不产出信号；
    Global 预告由 Tibo isApproximate 车道承担。"""
    feed = _probe("probe_cr_feed.json")
    declared = {s.key for s in signals_from_feed(feed) if s.lane == "global_declared"}
    assert "global-declared:2094144275957350900" not in declared
    assert "global-declared:2091412393368945027" not in declared


def test_signals_from_feed_baseline_includes_banked_unknown():
    """synthetic 边界（真实样本无 unknown 枚举）：baseline 记录 unknown，
    正常车道静默。"""
    payload = {
        "events": [
            {
                "id": "1",
                "type": "credits",
                "reset_kind": "banked",
                "banked_state": "unknown",
                "url": "https://x.com/thsottiaux/status/1",
                "announced_at": "2026-09-01T00:00:00.000Z",
                "reason_tags": [],
            }
        ]
    }
    assert [s.key for s in signals_from_feed(payload, baseline=True)] == [
        "banked:1:unknown"
    ]
    assert signals_from_feed(payload) == []


def test_signals_from_feed_joins_tweet_text():
    """feed tweets[] 按事件 ID join 逐字原文（banked announced 真实推文）。"""
    feed = _probe("live/feed_0905.json")
    announced = next(
        s
        for s in signals_from_feed(feed)
        if s.key == "banked:2095651088502591861:announced"
    )
    assert announced.raw_text.startswith(
        "We will give one banked reset for every day"
    )
    assert announced.url == "https://x.com/thsottiaux/status/2095651088502591861"


# ===== feed 车道：baseline / 去重 / 年龄护栏（生产路径） =====


def _fixed_now() -> datetime:
    # live/feed_0905.json fetched_at=2026-09-05T01:08Z 的同窗口：
    # 9 月事件 < 48h（会通知/重试），8 月事件 > 48h（年龄护栏跳过）。
    return datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc)


def _wire_feed(
    plugin: CodexResetWatcher,
    feed: dict | None,
    current: dict | None = None,
):
    async def fake_get(url: str, timeout_seconds: int):
        if "api/feed" in url:
            return feed
        if "api/reset/current" in url:
            return current
        return None

    plugin._get_json = fake_get  # type: ignore[method-assign]


def test_feed_baseline_first_run_silent_full_history(tmp_path):
    """首次 feed 生效：真实历史（8 月 banked 全链 + 5 条宣告）全量静默
    baseline，不补发一条。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = False
    _gstate(plugin)["notified_keys"] = []
    _wire_feed(plugin, _probe("probe_cr_feed.json"))
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin)["feed_baseline_done"] is True
    keys = set(_gstate(plugin)["notified_keys"])
    # baseline 记录全部当前 announced 宣告（含 preview 变体与无 tweet 的
    # archive 事件，不依赖通知元数据）+ 全部 banked 事件。
    assert len(keys) == 9  # 6 条宣告 + 3 条 banked
    assert "banked:2090964822422949999:available" in keys
    assert "global-declared:2094252447271366730" in keys
    assert "global-declared:2087706104814023111" in keys  # preview 变体也记录
    assert "global-declared:2092311059197808936" in keys  # 无 tweet 的 archive 也记录


def test_feed_baseline_not_repeated_and_new_events_notify(tmp_path, monkeypatch):
    """baseline 后：live 真实 payload 相对 probe 新增的 banked 事件
    （announced + arriving×2）各通知一次（v0.1.10 统一结构，经后台管线）；
    已 baseline 的旧事件静默。"""
    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path)
    _wire_feed(plugin, _probe("probe_cr_feed.json"))
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []

    _wire_feed(plugin, _probe("live/feed_0905.json"))

    async def round_two():
        await plugin._check_once()
        await _drain_inflight(plugin)

    asyncio.run(round_two())
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 3
    assert all(b.startswith("Codex Banked Reset 提醒") for b in bodies)
    joined = "\n".join(bodies)
    # 生命周期标签与固定免责声明行退役；正文来自 feed tweets[] 全文
    assert "已公告" not in joined and "不代表当前额度已自动刷新" not in joined
    assert "We will give one banked reset for every day" in joined
    assert "Tibo 原文摘录：" in joined  # feed 兜底来源 → 诚实措辞
    assert "banked:2095651088502591861:announced" in set(
        _gstate(plugin)["notified_keys"]
    )


def test_feed_age_guard_blocks_stale_signals(tmp_path, monkeypatch):
    """正常车道下超过 48h 的真实历史信号只记录不通知。

    注意：本测试针对 48h 年龄护栏（基于 announced_at），与上游
    feed["stale"] 标记无关——stale API 行为由 test_stale_feed_* 覆盖。"""
    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    _wire_feed(plugin, _probe("probe_cr_feed.json"))
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []
    assert len(_gstate(plugin)["notified_keys"]) == 7


def test_feed_send_failure_leaves_key_for_retry(tmp_path, monkeypatch):
    """synthetic 边界（发送失败）：新键不落盘留待重试；年龄护栏命中的
    旧事件键仍记录（它们永远不会走发送路径）。"""
    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []

    async def failing_send(group_id: str, message: str) -> bool:
        return False

    plugin._send_group_text = failing_send  # type: ignore[method-assign]
    _wire_feed(plugin, _probe("live/feed_0905.json"))

    async def run_and_drain():
        await plugin._check_once()
        await _drain_inflight(plugin)

    asyncio.run(run_and_drain())
    keys = set(_gstate(plugin)["notified_keys"])
    assert "banked:2095651088502591861:announced" not in keys
    assert keys == {
        "banked:2090766694897619318:announced",
        "banked:2090947196107764189:arriving",
        "banked:2090964822422949999:available",
        "global-declared:2094252447271366730",
        "global-declared:2093801758665715784",
        "global-declared:2093014447833116908",
        "global-declared:2091688655828246890",
    }


def test_repeat_poll_no_duplicate_feed_notify(tmp_path, monkeypatch):
    """相同 feed payload 反复轮询（240s 场景）：首轮通知后静默。"""
    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    _wire_feed(plugin, _probe("live/feed_0905.json"))

    async def check_and_drain():
        await plugin._check_once()
        await _drain_inflight(plugin)

    asyncio.run(check_and_drain())
    first_count = len(plugin._ctx.send.sent_messages)
    assert first_count == 3  # 仅 9 月 banked 新事件；8 月历史全部被 48h 护栏跳过

    asyncio.run(check_and_drain())
    assert len(plugin._ctx.send.sent_messages) == first_count


# ===== feed 车道：通知文案（产品红线） =====


def test_banked_notice_unified_structure_real_signal():
    """v0.1.10：banked 通知改用统一结构（真实 9 月 announced 信号）——
    标题「Codex Banked Reset 提醒」+ 中文翻译 + 完整原文 + 原帖；
    固定文案中的生命周期标签/免责声明行退役（语义安全移入 Contract）。"""
    feed = _probe("live/feed_0905.json")
    announced = next(
        s
        for s in signals_from_feed(feed)
        if s.lane == "banked" and s.semantic_state == "announced"
    )
    body = build_full_notice(
        BANKED_NOTICE_TITLE,
        announced.url,
        announced.raw_text,
        "full",
        "我们将为 Astra 不可用的每一天补偿一次 banked reset。",
    )
    assert body.startswith("Codex Banked Reset 提醒")
    assert "中文翻译：\n我们将为 Astra 不可用的每一天补偿一次 banked reset。" in body
    assert "Tibo 原文：\n" + announced.raw_text in body
    assert f"原帖：{announced.url}" in body
    # 旧展示内容不再出现
    assert "已公告" not in body and "到账中" not in body and "已存入" not in body
    assert "重置原因" not in body
    assert "Banked Reset 是官方存入账户" not in body
    assert "北京时间" not in body  # 标题时间戳退役


def test_contract_prompt_banked_safety_is_translation_constraint():
    """Banked 语义安全红线移入固定 Contract：不得译成"当前额度已自动刷新"，
    且只是防误译约束——不得要求模型添加原文没有的 Banked 解释；
    faithfulness（原文说什么翻什么）必须同时在场。"""
    assert "Banked Reset" in CONTRACT_PROMPT
    assert "不得把 Banked Reset 表达为当前额度已经自动刷新" in CONTRACT_PROMPT
    assert "不添加原文没有的 Banked 解释" in CONTRACT_PROMPT
    assert "不得总结、压缩、删减、省略或改写成摘要" in CONTRACT_PROMPT
    # translation-only 契约：唯一输出字段
    assert '{"translation_zh": string}' in CONTRACT_PROMPT
    for retired in ("summary_zh", "time_expressions", "key_points", "ambiguities", "context_notes"):
        assert retired not in CONTRACT_PROMPT


def test_contract_prompt_timezone_and_boundary_rules():
    """review-fix 契约文本：PST/PDT 固定偏移与 PT 判定规则、不得改写原文
    明写的时区缩写、实现细节词的忠实翻译边界、信息不足时的翻译行为。"""
    # 时区缩写：固定偏移明写；PT 按 published_at 日期判断
    assert "PST 即 UTC-8" in CONTRACT_PROMPT
    assert "PDT 即 UTC-7" in CONTRACT_PROMPT
    assert "单独的 PT 则根据 published_at 的日期按太平洋时间判断标准时/夏令时" in CONTRACT_PROMPT
    # 不得把原文明写的 PST 擅自重解释为 PDT
    assert "不得因为日期处于夏令时，就把原文明写的 PST 重新解释成 PDT" in CONTRACT_PROMPT
    # 实现细节：不得额外添加进译文；原文本身明确提到则必须忠实翻译
    assert "不得把插件元数据、Provider、API、数据来源等实现细节额外添加进译文" in CONTRACT_PROMPT
    assert "如果 <SOURCE_TEXT> 原文本身明确提到这些词或概念，必须忠实翻译" in CONTRACT_PROMPT
    # 信息不足：仍正常翻译原表达，但不擅自补北京时间/时区/具体日期
    assert "时间或时区信息不足时，仍正常翻译原有时间表达" in CONTRACT_PROMPT
    assert "不要擅自补北京时间、时区或具体日期" in CONTRACT_PROMPT
    # 旧措辞退役
    assert "保留原文时间表达原样" not in CONTRACT_PROMPT
    assert "译文中不得讨论或提及插件内部的数据来源" not in CONTRACT_PROMPT


def test_banked_available_never_claimed_auto_refresh_in_contract():
    """available 红线（v0.1.10 起在 Contract 层强制）：Contract 明确 Banked
    Reset 是存入账户、供之后使用/兑换的机会。真实 available 推文原文
    （"has landed ... redeemable on demand"）交由完整翻译忠实传达。"""
    assert "存入账户、供之后使用/兑换" in CONTRACT_PROMPT
    feed = _probe("probe_cr_feed.json")
    available = next(
        s
        for s in signals_from_feed(feed)
        if s.lane == "banked" and s.semantic_state == "available"
    )
    assert "landed" in available.raw_text


# ===== v0.1.10 统一完整通知 formatter（Global / Banked 共用）=====


def test_global_notice_full_structure_exact():
    """统一完整通知结构精确断言：标题 → 中文翻译 → Tibo 原文 → 原帖。"""
    url = "https://x.com/thsottiaux/status/1"
    body = build_full_notice(
        GLOBAL_NOTICE_TITLE, url, "Lands around 6pm PST today.", "full", "预计今天北京时间 10:00 左右落地。"
    )
    assert body == (
        "Codex 额度重置提醒\n"
        "\n"
        "中文翻译：\n"
        "预计今天北京时间 10:00 左右落地。\n"
        "\n"
        "Tibo 原文：\n"
        "Lands around 6pm PST today.\n"
        "\n"
        f"原帖：{url}"
    )


def test_global_notice_observed_semantic_title():
    """observed A-primary 保留语义标题「Codex 额度重置已确认生效」，
    正文同样是统一结构；不恢复旧的重置原因/确认时间等字段。"""
    body = build_full_notice(
        GLOBAL_CONFIRMED_TITLE,
        "https://x.com/thsottiaux/status/2",
        "All reset for everyone.",
        "full",
        "所有人的额度都已重置。",
    )
    assert body.startswith("Codex 额度重置已确认生效")
    assert "中文翻译：" in body and "Tibo 原文：" in body and "原帖：" in body
    assert "确认时间" not in body
    assert "重置原因" not in body


def test_global_notice_excerpt_label_honesty():
    """completeness=unknown → 诚实措辞「Tibo 原文摘录」，且不得同时出现
    「Tibo 原文：」；展示护栏裁剪同样降级为摘录。"""
    excerpt = build_full_notice(
        GLOBAL_NOTICE_TITLE, "https://x.com/thsottiaux/status/3", "只有部分文本", "unknown", "译文"
    )
    assert "Tibo 原文摘录：\n只有部分文本" in excerpt
    assert "Tibo 原文：" not in excerpt
    long_text = "x" * 3000
    truncated = build_full_notice(
        GLOBAL_NOTICE_TITLE, None, long_text, "full", None
    )
    assert "Tibo 原文摘录：" in truncated
    assert ("x" * 1999 + "…") in truncated


def test_global_notice_llm_failure_and_missing_fields():
    """LLM 失败（translation=None）→ 无中文翻译块，告警结构完整；
    正文缺失 → 原文块整块省略；URL 缺失 → 原帖行省略。"""
    body = build_full_notice(
        GLOBAL_NOTICE_TITLE,
        "https://x.com/thsottiaux/status/4",
        "original text",
        "full",
        None,
    )
    assert body == (
        "Codex 额度重置提醒\n"
        "\n"
        "Tibo 原文：\n"
        "original text\n"
        "\n"
        "原帖：https://x.com/thsottiaux/status/4"
    )
    bare = build_full_notice(GLOBAL_NOTICE_TITLE, None, None, None, None)
    assert bare == "Codex 额度重置提醒"
    # 空 URL / 非字符串 URL：原帖行省略，不否决通知
    assert build_full_notice(GLOBAL_NOTICE_TITLE, "", "t", "full", None) == (
        "Codex 额度重置提醒\n\nTibo 原文：\nt"
    )
    assert build_full_notice(GLOBAL_NOTICE_TITLE, 12345, None, None, None) == (
        "Codex 额度重置提醒"
    )


# ===== Tibo 车道：预估预告与原文展示 =====


def test_approximate_and_update_variants_never_send(tmp_path):
    """v0.1.10 退役回归：isApproximate 预告、预估→精确升级、时间变化等
    Tibo 变体全部零 QQ 通知（feed join 的原文/原帖展示随之退役）。"""
    plugin = _make_plugin(tmp_path)
    moment = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(microsecond=0)
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    t2 = base + timedelta(hours=4)
    feed = _probe("probe_cr_feed.json")
    target = next(t for t in feed["tweets"] if t.get("text"))

    _stage_payload(
        plugin, _tibo_current(isApproximate=True, expectedResetAt=moment.isoformat())
    )
    _stage_payload(plugin, _tibo_current(expectedResetAt=moment.isoformat()))
    _stage_payload(
        plugin, _tibo_current(status="TIME_CHANGED", expectedResetAt=t2.isoformat())
    )
    _wire_feed(plugin, feed, current=_tibo_current(resetSourceUrl=target["url"]))
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []


# ===== 状态迁移：baseline 标志独立于 schema 版本 =====


def test_state_v4_carries_over_with_feed_defaults(tmp_path):
    """v4 → v6：Tibo 去重/active_plan 整包继承（不重发、不丢原计划）；
    feed 车道字段 v4 不存在 → 继承为空，baseline 留给首次成功拉取。"""
    legacy = {
        "version": 4,
        "state": {
            "last_notified_reset_at": "2026-08-31T02:34:00+00:00",
            "last_kind": "scheduled",
            "active_plan": {
                "reset_at": "2026-08-31T02:34:00+00:00",
                "source_url": "u",
                "notified_at": "n",
            },
        },
    }
    (tmp_path / "reset_state.json").write_text(json.dumps(legacy), encoding="utf-8")
    plugin = _make_plugin(tmp_path)
    assert _gstate(plugin)["active_plan"]["source_url"] == "u"
    assert not _gstate(plugin).get("feed_baseline_done")
    assert not _gstate(plugin).get("notified_keys")
    # v4 历史去重时间仍然生效：同时间 Tibo 结论静默。
    moment = datetime(2026, 8, 31, 2, 34, tzinfo=timezone.utc)
    asyncio.run(
        plugin._maybe_notify(_conclusion(reset_at=moment), ["100000001"], BEIJING)  # noqa: SLF001
    )
    assert plugin._ctx.send.sent_messages == []


def test_baseline_flag_survives_future_version_bump(tmp_path):
    """未来 schema 升级不得让 feed 车道重新 baseline（否则会吞掉升级
    窗口内恰好出现的新事件）——baseline 标志与 notified_keys 成对保留，
    v6 中经严格校验后继承给 legacy 群的 per-group receipt。"""
    future_state = {
        "version": 99,
        "state": {
            "feed_baseline_done": True,
            "notified_keys": ["banked:1:announced"],
        },
    }
    (tmp_path / "reset_state.json").write_text(json.dumps(future_state), encoding="utf-8")
    plugin = _make_plugin(tmp_path)
    assert _gstate(plugin) == {
        "feed_baseline_done": True,
        "notified_keys": ["banked:1:announced"],
    }
    assert set(plugin._state["groups"]) == {"100000001"}


def test_state_rollback_missing_keys_rebaselines_silently(tmp_path, monkeypatch):
    """回滚/升级回归：baseline 标志在而 notified_keys 缺失 → 不得带着
    空去重表直接发送历史事件；下一轮对当前历史重新静默 baseline。"""
    rollback_state = {
        "version": 99,
        "state": {"feed_baseline_done": True},  # 去重表缺失
    }
    (tmp_path / "reset_state.json").write_text(json.dumps(rollback_state), encoding="utf-8")
    plugin = _make_plugin(tmp_path)
    # 成对校验失败 → baseline 视为未完成（缺失即 False 语义）。
    assert not _gstate(plugin).get("feed_baseline_done")

    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    _wire_feed(plugin, _probe("probe_cr_feed.json"))
    asyncio.run(plugin._check_once())
    # 重新 baseline：全量静默，历史一条不发。
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin)["feed_baseline_done"] is True
    assert len(_gstate(plugin)["notified_keys"]) == 9


def test_state_corrupt_keys_type_forces_rebaseline(tmp_path):
    """notified_keys 类型损坏（非字符串元素）→ 成对校验失败，feed 字段
    不继承（缺省即待 baseline），不进入空去重表发送状态。"""
    corrupt_state = {
        "version": 5,
        "state": {
            "feed_baseline_done": True,
            "notified_keys": ["ok", 42],  # 42 非 str
        },
    }
    (tmp_path / "reset_state.json").write_text(json.dumps(corrupt_state), encoding="utf-8")
    plugin = _make_plugin(tmp_path)
    assert not _gstate(plugin).get("feed_baseline_done")
    assert not _gstate(plugin).get("notified_keys")


# ===== feed 顶层 stale 护栏与不完整 payload =====


def _with_stale(feed: dict) -> dict:
    payload = dict(feed)
    payload["stale"] = True
    return payload


def test_stale_feed_skips_lane_entirely(tmp_path, monkeypatch):
    """stale=true：整轮跳过 feed 车道——不通知、不记录新键。"""
    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    _wire_feed(plugin, _with_stale(_probe("live/feed_0905.json")))
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin)["notified_keys"] == []
    assert _gstate(plugin)["feed_baseline_done"] is True


def test_stale_feed_blocks_first_baseline(tmp_path):
    """stale=true 时首次 baseline 不得完成。"""
    plugin = _make_plugin(tmp_path)
    _wire_feed(plugin, _with_stale(_probe("probe_cr_feed.json")))
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin).get("feed_baseline_done") is not True
    assert not _gstate(plugin).get("notified_keys")


def test_malformed_feed_defers_baseline_then_valid_completes(tmp_path):
    """HTTP 200 但缺核心数组的不完整 payload：baseline 暂缓（不完成、
    不记录、不通知）；下一个完整 payload 到来后正常静默完成。"""
    plugin = _make_plugin(tmp_path)
    malformed = {
        "version": 1,
        "fetched_at": "2026-09-05T01:08:00.000Z",
        "stale": False,
        # 缺 events / tweets
    }
    _wire_feed(plugin, malformed)
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin).get("feed_baseline_done") is not True

    _wire_feed(plugin, _probe("probe_cr_feed.json"))
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin)["feed_baseline_done"] is True
    assert len(_gstate(plugin)["notified_keys"]) == 9


def test_baseline_without_reply_metadata_records_declared_keys(tmp_path, monkeypatch):
    """回归（职责分离）：真实历史 feed 的 tweets 缺失 reply 元数据（剔除
    is_reply 字段模拟上游 metadata 缺失）时，baseline 仍须记录全部
    announced 宣告键；下一轮元数据恢复后不得补发部署前已存在的
    Global declared。"""
    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path)
    feed = _probe("probe_cr_feed.json")
    stripped = dict(feed)
    stripped["tweets"] = [
        {k: v for k, v in t.items() if k != "is_reply"} for t in feed["tweets"]
    ]
    _wire_feed(plugin, stripped)
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin)["feed_baseline_done"] is True
    keys = set(_gstate(plugin)["notified_keys"])
    for event_id in (
        "2094252447271366730",
        "2093801758665715784",
        "2093014447833116908",
        "2092311059197808936",
        "2091688655828246890",
        "2087706104814023111",
    ):
        assert f"global-declared:{event_id}" in keys

    # 元数据恢复（完整真实 payload）：历史宣告不得补发。
    _wire_feed(plugin, feed)
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []


def test_baseline_defers_when_tweets_empty(tmp_path):
    """tweets=[] 的明显不完整 payload（HTTP 200）不得完成 baseline。"""
    plugin = _make_plugin(tmp_path)
    payload = dict(_probe("probe_cr_feed.json"))
    payload["tweets"] = []
    _wire_feed(plugin, payload)
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []
    assert not _gstate(plugin).get("feed_baseline_done")
    assert not _gstate(plugin).get("notified_keys")


# ===== 未知版本（升级/回滚窗口）：Tibo 去重状态兼容 =====


def test_state_rollback_preserves_tibo_dedup_state(tmp_path):
    """回滚回归：未知版本状态中已通知过的 SCHEDULED，回滚到本版本后
    同一时间不得重发；TIME_CHANGED 仍能引用 active_plan 原计划。
    （moment 用相对未来时间，避免硬编码日期越过当前时刻后失效。）"""
    moment = datetime.now(timezone.utc) + timedelta(hours=2)
    url = "https://x.com/thsottiaux/status/999"
    future_state = {
        "version": 99,
        "state": {
            "feed_baseline_done": True,
            "notified_keys": ["banked:1:announced"],
            "last_notified_reset_at": moment.isoformat(),
            "last_notified_approximate": False,
            "last_source_url": url,
            "last_kind": "scheduled",
            "active_plan": {
                "reset_at": moment.isoformat(),
                "source_url": url,
                "notified_at": "2026-09-05T04:00:00+00:00",
            },
        },
    }
    (tmp_path / "reset_state.json").write_text(json.dumps(future_state), encoding="utf-8")
    plugin = _make_plugin(tmp_path)
    assert _gstate(plugin)["feed_baseline_done"] is True
    assert _parse_iso(_gstate(plugin)["last_notified_reset_at"]) == moment
    assert _gstate(plugin)["active_plan"]["source_url"] == url

    # 同一 SCHEDULED 已通知过 → 静默（去重状态经回滚保留，死代码路径验证）。
    asyncio.run(
        plugin._maybe_notify(_conclusion(reset_at=moment), ["100000001"], BEIJING)  # noqa: SLF001
    )
    assert plugin._ctx.send.sent_messages == []

    # v0.1.10：TIME_CHANGED 经生产路径不再发送任何 QQ 通知；
    # 去重/active_plan 字段仍经 _carry_validated_state 保留（死代码兼容）。
    _stage_payload(
        plugin,
        _tibo_payload("TIME_CHANGED", (moment + timedelta(hours=2)).isoformat(), url),
    )
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin)["active_plan"]["reset_at"] == moment.isoformat()


def test_state_rollback_drops_corrupt_tibo_fields(tmp_path):
    """回滚时损坏的 Tibo 字段（时间不可解析/类型错误）直接丢弃，不携带
    损坏状态运行；feed 基线成对状态不受影响。"""
    corrupt_state = {
        "version": 99,
        "state": {
            "feed_baseline_done": True,
            "notified_keys": ["banked:1:announced"],
            "last_notified_reset_at": "not-a-date",
            "last_notified_approximate": "yes",
            "last_kind": 42,
            "active_plan": {"reset_at": "garbage", "source_url": 7},
        },
    }
    (tmp_path / "reset_state.json").write_text(json.dumps(corrupt_state), encoding="utf-8")
    plugin = _make_plugin(tmp_path)
    assert _gstate(plugin)["feed_baseline_done"] is True
    assert _gstate(plugin)["notified_keys"] == ["banked:1:announced"]
    assert "last_notified_reset_at" not in _gstate(plugin)
    assert "last_notified_approximate" not in _gstate(plugin)
    assert "last_kind" not in _gstate(plugin)
    assert "active_plan" not in _gstate(plugin)


# ===== v0.1.3 多群通知：一次性迁移 / per-group receipt =====


def _config_for(**watcher: object) -> dict:
    base = {
        "group_id": "",
        "group_ids": [],
        "group_ids_migrated": False,
        "check_interval": 240,
        "timezone": "Asia/Shanghai",
        "tibo_base": "https://tibo.modelyard.dev",
        "codex_base": "https://codex-reset.com",
    }
    base.update(watcher)
    return {
        "plugin": {"enabled": True, "config_version": "1.1.0"},
        "watcher": base,
    }


def test_legacy_group_id_migrates_to_group_ids(tmp_path):
    """0.1.2 生产形态（仅 group_id）：首次归一化迁移进 group_ids 并置位
    标记——legacy 群 100000001（虚构示例，生产真实群号仅在私有配置）自动进入列表。"""
    plugin = _make_plugin(tmp_path)  # group_id="100000001"，无 group_ids
    assert plugin.config.watcher.group_ids == ["100000001"]
    assert plugin.config.watcher.group_ids_migrated is True


def test_migration_is_one_shot_cleared_list_stays_empty(tmp_path):
    """核心回归：迁移完成后用户主动清空 group_ids，不得被 legacy 字段
    回填（WebUI 保存同样会调用 normalize）。"""
    plugin = _make_plugin(tmp_path)  # 首次归一化：迁移发生，标记置位
    assert plugin.config.watcher.group_ids == ["100000001"]
    # 模拟用户清空列表后保存（磁盘上标记已为 True）
    plugin.set_plugin_config(
        _config_for(group_id="100000001", group_ids=[], group_ids_migrated=True)
    )
    assert plugin.config.watcher.group_ids == []
    # 再次保存仍保持为空
    plugin.set_plugin_config(
        _config_for(group_id="100000001", group_ids=[], group_ids_migrated=True)
    )
    assert plugin.config.watcher.group_ids == []


def test_migration_marker_false_with_existing_list_never_overwrites(tmp_path):
    """标记未置位但列表已有值：不得覆盖用户列表（仅置位标记）。"""
    plugin = _make_plugin(
        tmp_path,
        group_id="100000009",
        group_ids=["100000001", "100000002"],
        group_ids_migrated=False,
    )
    assert plugin.config.watcher.group_ids == ["100000001", "100000002"]
    assert plugin.config.watcher.group_ids_migrated is True


def test_group_ids_stripped_deduped_preserve_order():
    plugin = CodexResetWatcher()
    plugin.set_plugin_config(
        _config_for(group_ids=[" 100000001 ", "", "100000001", "100000002", " 100000003 "])
    )
    assert plugin.config.watcher.group_ids == ["100000001", "100000002", "100000003"]
    assert plugin._target_groups() == ["100000001", "100000002", "100000003"]  # noqa: SLF001


def test_empty_group_list_makes_no_requests(tmp_path):
    """列表为空（且无 legacy 值）：与 0.1.2 空 group_id 一致——不拉取、
    不 baseline、不发送。"""
    plugin = _make_plugin(tmp_path, group_id="", group_ids=[])
    requested: list[str] = []

    async def fake_get(url: str, timeout_seconds: int):
        requested.append(url)
        return None

    plugin._get_json = fake_get  # type: ignore[method-assign]
    asyncio.run(plugin._check_once())
    assert requested == []
    assert plugin._state["groups"] == {}


def test_webui_schema_group_ids_is_native_list():
    """SDK schema 回归：group_ids 必须是原生数组编辑器（type=array +
    ui_type=list + item_type=string），legacy 字段与迁移标记隐藏。"""
    from maibot_sdk.config import generate_plugin_config_schema

    schema = generate_plugin_config_schema(CodexResetWatcherConfig)
    fields = schema["sections"]["watcher"]["fields"]
    group_ids = fields["group_ids"]
    assert group_ids["type"] == "array"
    assert group_ids["ui_type"] == "list"
    assert group_ids["item_type"] == "string"
    assert group_ids["label"] == "通知群号"
    assert group_ids["hidden"] is False
    # 隐私红线：UI placeholder 必须是语义提示，不得出现真实群号示例
    assert group_ids["placeholder"] == "请输入 QQ 群号"
    assert fields["group_id"]["hidden"] is True
    assert fields["group_ids_migrated"]["hidden"] is True


def test_state_v5_receipt_inherits_to_legacy_group_only(tmp_path):
    """v5 → v6：全局 receipt 整包继承给 legacy 群；其余群不存在条目
    （首次成功拉取时做本群静默 baseline）。"""
    moment = datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)
    v5 = {
        "version": 5,
        "state": {
            "feed_baseline_done": True,
            "notified_keys": ["banked:1:announced"],
            "last_notified_reset_at": moment.isoformat(),
            "last_notified_approximate": False,
            "last_kind": "scheduled",
            "active_plan": {
                "reset_at": moment.isoformat(),
                "source_url": "https://x.com/thsottiaux/status/999",
                "notified_at": "n",
            },
        },
    }
    (tmp_path / "reset_state.json").write_text(json.dumps(v5), encoding="utf-8")
    plugin = _make_plugin(tmp_path, group_ids=["100000001", "100000003"])
    assert set(plugin._state["groups"]) == {"100000001"}
    receipt = _gstate(plugin)
    assert receipt["notified_keys"] == ["banked:1:announced"]
    assert receipt["active_plan"]["reset_at"] == moment.isoformat()
    assert _gstate(plugin, "100000003") == {}


def test_state_v5_new_group_baselines_silently_while_legacy_keeps_receipt(
    tmp_path, monkeypatch
):
    """新加入群：本群静默 baseline（不补发加入前历史）；legacy 群沿用
    继承的 receipt 独立判定，两群互不影响。"""
    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    v5 = {
        "version": 5,
        "state": {
            "feed_baseline_done": True,
            "notified_keys": ["banked:1:announced"],
        },
    }
    (tmp_path / "reset_state.json").write_text(json.dumps(v5), encoding="utf-8")
    plugin = _make_plugin(tmp_path, group_ids=["100000001", "100000003"])
    _wire_feed(plugin, _probe("probe_cr_feed.json"))
    asyncio.run(plugin._check_once())
    # 新群 baseline 全量静默（9 键）；legacy 群 receipt 仅 1 键 → 其余
    # 真实事件键对它是新键，但全部超过 48h 年龄护栏 → 只记录不发送。
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin, "100000003")["feed_baseline_done"] is True
    assert len(_gstate(plugin, "100000003")["notified_keys"]) == 9
    legacy_keys = set(_gstate(plugin)["notified_keys"])
    assert "banked:1:announced" in legacy_keys
    assert "banked:2090964822422949999:available" in legacy_keys


def test_multi_group_tibo_all_receive_and_record(tmp_path):
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    _run(plugin, _conclusion())
    streams = [s for s, _ in plugin._ctx.send.sent_messages]
    assert streams == ["qq-group-100000001", "qq-group-100000002"]
    assert (
        _gstate(plugin, "100000001")["last_notified_reset_at"]
        == _gstate(plugin, "100000002")["last_notified_reset_at"]
    )


def test_multi_group_failure_only_failed_group_retries(tmp_path):
    """核心需求：A 成功 / B 失败 → 下一轮只有 B 重试，A 不重复收到。"""
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    sent: list[str] = []
    calls: dict[str, int] = {}

    async def flaky_send(group_id: str, message: str) -> bool:
        calls[group_id] = calls.get(group_id, 0) + 1
        if group_id == "100000002" and calls["100000002"] == 1:
            return False  # 100000002 首次发送失败
        sent.append(group_id)
        return True

    plugin._send_group_text = flaky_send  # type: ignore[method-assign]
    conclusion = _conclusion()
    asyncio.run(
        plugin._maybe_notify(conclusion, ["100000001", "100000002"], BEIJING)  # noqa: SLF001
    )
    assert sent == ["100000001"]
    assert _gstate(plugin, "100000001").get("last_notified_reset_at") is not None
    assert _gstate(plugin, "100000002") == {}
    # 第二轮同一结论：100000001 命中去重键静默，仅 100000002 补发成功。
    asyncio.run(
        plugin._maybe_notify(conclusion, ["100000001", "100000002"], BEIJING)  # noqa: SLF001
    )
    assert sent == ["100000001", "100000002"]


def test_multi_group_feed_failure_only_failed_group_retries(tmp_path, monkeypatch):
    """feed 车道同样按群独立（v0.1.10 banked 后台管线）：100000002 首轮
    3 条全部失败 → 下一轮仅 100000002 收到 3 条 banked 通知，100000001
    零重复。"""
    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    # 两群均已 baseline 完成（本测试聚焦投递隔离，不测 baseline）。
    _gstate(plugin, "100000001")["feed_baseline_done"] = True
    _gstate(plugin, "100000002")["feed_baseline_done"] = True
    _wire_feed(plugin, _probe("live/feed_0905.json"))
    sent: list[str] = []
    calls_100000002 = {"n": 0}

    async def flaky_send(group_id: str, message: str) -> bool:
        if group_id == "100000002":
            calls_100000002["n"] += 1
            if calls_100000002["n"] <= 3:
                return False  # 100000002 首轮 3 条全部失败
        sent.append(group_id)
        return True

    plugin._send_group_text = flaky_send  # type: ignore[method-assign]

    async def check_and_drain():
        await plugin._check_once()
        await _drain_inflight(plugin)

    asyncio.run(check_and_drain())
    assert sent == ["100000001", "100000001", "100000001"]
    # 第二轮：100000001 静默，100000002 重试成功。
    asyncio.run(check_and_drain())
    assert sent == ["100000001", "100000001", "100000001", "100000002", "100000002", "100000002"]
    assert "banked:2095651088502591861:announced" in set(
        _gstate(plugin, "100000002")["notified_keys"]
    )


def test_multi_group_time_changed_per_group_plan_semantics(tmp_path):
    """逐群 receipt：首轮 scheduled 100000002 失败 → TIME_CHANGED 时 100000001 有
    本群原计划收"时间更新"，100000002 无原计划保守收"已确认"。"""
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    sent: list[tuple[str, str]] = []
    calls: dict[str, int] = {}

    async def flaky_send(group_id: str, message: str) -> bool:
        calls[group_id] = calls.get(group_id, 0) + 1
        if group_id == "100000002" and calls["100000002"] == 1:
            return False  # 100000002 首次发送失败
        sent.append((group_id, message))
        return True

    plugin._send_group_text = flaky_send  # type: ignore[method-assign]
    asyncio.run(
        plugin._maybe_notify(
            _conclusion(kind="scheduled", hours=2.0), ["100000001", "100000002"], BEIJING  # noqa: SLF001
        )
    )
    asyncio.run(
        plugin._maybe_notify(
            _conclusion(kind="time_changed", hours=4.0), ["100000001", "100000002"], BEIJING  # noqa: SLF001
        )
    )
    bodies = dict(sent)
    assert bodies["100000001"].startswith("Codex 重置时间更新")
    assert bodies["100000002"].startswith("Codex 额度重置已确认")
    assert "原计划" not in bodies["100000002"]


def test_new_group_receives_current_active_schedule(tmp_path):
    """产品决策：新加入群对仍有效的当前 Tibo 计划正常通知（当前有效
    计划不是历史补发）；已通知群同一时间保持静默。"""
    plugin = _make_plugin(tmp_path)
    conclusion = _conclusion(kind="scheduled", hours=2.0)
    _run(plugin, conclusion)
    assert [s for s, _ in plugin._ctx.send.sent_messages] == ["qq-group-100000001"]
    # 配置热更新加入新群 100000003，receipt 为空。
    plugin.set_plugin_config(
        _config_for(
            group_id="100000001",
            group_ids=["100000001", "100000003"],
            group_ids_migrated=True,
        )
    )
    plugin._reconcile_groups()  # noqa: SLF001
    _run(
        plugin,
        Conclusion(
            kind="scheduled",
            stable_id=conclusion.stable_id,
            reset_at=conclusion.reset_at,
            reason="未说明",
            source_url=conclusion.source_url,
        ),
    )
    streams = [s for s, _ in plugin._ctx.send.sent_messages]
    assert streams == ["qq-group-100000001", "qq-group-100000003"]


def test_removed_group_receipt_pruned_and_rejoin_is_fresh(tmp_path):
    """产品决策：删除群即删除其 receipt；重新加入按新群处理（receipt
    为空，下一轮重新静默 baseline）。"""
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    _run(plugin, _conclusion())
    assert set(plugin._state["groups"]) == {"100000001", "100000002"}
    plugin.set_plugin_config(
        _config_for(group_ids=["100000001"], group_ids_migrated=True)
    )
    plugin._reconcile_groups()  # noqa: SLF001
    assert set(plugin._state["groups"]) == {"100000001"}
    plugin.set_plugin_config(
        _config_for(group_ids=["100000001", "100000002"], group_ids_migrated=True)
    )
    plugin._reconcile_groups()  # noqa: SLF001
    assert _gstate(plugin, "100000002") == {}


def test_terminal_status_clears_all_groups_plans(tmp_path):
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    _run(plugin, _conclusion(kind="scheduled", hours=1.0))
    assert _gstate(plugin, "100000001").get("active_plan") is not None
    assert _gstate(plugin, "100000002").get("active_plan") is not None
    plugin._clear_active_plan()  # noqa: SLF001
    assert _gstate(plugin, "100000001").get("active_plan") is None
    assert _gstate(plugin, "100000002").get("active_plan") is None
    assert _gstate(plugin, "100000001").get("last_notified_reset_at") is not None
    assert _gstate(plugin, "100000002").get("last_notified_reset_at") is not None


def test_runner_style_upgrade_path_with_real_sdk(tmp_path):
    """端到端（真实 SDK 函数）：0.1.2 生产 TOML → host rebuild（版本升级
    路径）→ 插件 normalize 一次性迁移 → tomlkit 写盘 round-trip → 用户
    清空列表不回填 → 回滚丢标记后再升级可重新迁移。"""
    import tomlkit
    from maibot_sdk.config import rebuild_plugin_config_data, validate_plugin_config

    old_toml = (
        '[plugin]\nenabled = true\nconfig_version = "1.0.0"\n\n[watcher]\n'
        'group_id = "100000001"\ncheck_interval = 240\ntimezone = "Asia/Shanghai"\n'
        'tibo_base = "https://tibo.modelyard.dev"\ncodex_base = "https://codex-reset.com"\n'
    )
    raw_config = dict(tomlkit.parse(old_toml))
    default_config = CodexResetWatcherConfig().model_dump(mode="python")

    # runner 版本升级路径：compare_versions("1.0.0","1.1.0") → rebuild
    rebuilt = rebuild_plugin_config_data(default_config, raw_config)
    assert rebuilt["plugin"]["config_version"] == "1.2.0"  # v0.1.8 起
    assert rebuilt["watcher"]["group_id"] == "100000001"  # 旧值按名保留
    assert rebuilt["watcher"]["group_ids"] == []
    assert rebuilt["watcher"]["group_ids_migrated"] is False

    plugin = _make_plugin(tmp_path, group_id="", group_ids=[])
    normalized, changed = plugin.normalize_plugin_config(rebuilt)
    assert changed is True
    assert normalized["watcher"]["group_ids"] == ["100000001"]
    assert normalized["watcher"]["group_ids_migrated"] is True

    # 注入 + 写盘 round-trip（tomlkit 序列化 → 解析 → pydantic 再校验）
    plugin.set_plugin_config(normalized)
    assert plugin.config.watcher.group_ids == ["100000001"]
    doc = tomlkit.document()
    for section, values in normalized.items():
        table = tomlkit.table()
        for key, value in values.items():
            table[key] = value
        doc[section] = table
    reparsed = dict(tomlkit.parse(tomlkit.dumps(doc)))
    assert 'group_ids = ["100000001"]' in tomlkit.dumps(doc)
    assert validate_plugin_config(CodexResetWatcherConfig, reparsed).watcher.group_ids == [
        "100000001"
    ]

    # 用户清空列表（marker 已置位）→ 不回填、无变更
    cleared = dict(normalized)
    cleared["watcher"] = dict(normalized["watcher"], group_ids=[])
    final, changed = plugin.normalize_plugin_config(cleared)
    assert final["watcher"]["group_ids"] == []
    assert changed is False

    # 回滚 0.1.2 丢弃未知字段 → marker 复原 False → 再升级可重新迁移
    v012_view = dict(cleared)
    v012_view["watcher"] = {"group_id": "100000001", "check_interval": 240}
    re_upgraded = rebuild_plugin_config_data(default_config, v012_view)
    assert re_upgraded["watcher"]["group_ids_migrated"] is False
    norm2, _ = plugin.normalize_plugin_config(re_upgraded)
    assert norm2["watcher"]["group_ids"] == ["100000001"]


# ===== v0.1.4 维护：age-guard 日志只对首次遇到的过期信号打印一次 =====


def test_age_guard_already_recorded_key_next_round_fully_silent(
    tmp_path, monkeypatch, caplog
):
    """回归（v0.1.4 生产日志噪音）：已存在于 notified_keys 的过期信号，
    下一轮 0 发送、0 state 变化、0 age-guard 日志、0 落盘；再一轮仍如此。"""
    import copy
    import logging

    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path)
    feed = _probe("probe_cr_feed.json")  # _fixed_now 下全部信号 >48h
    pre_keys = sorted(s.key for s in signals_from_feed(feed))
    assert len(pre_keys) == 7
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = list(pre_keys)
    _wire_feed(plugin, feed)

    state_before = copy.deepcopy(plugin._state)
    with caplog.at_level(logging.INFO, logger="codex_reset_watcher.plugin"):
        asyncio.run(plugin._check_once())
        asyncio.run(plugin._check_once())

    assert plugin._ctx.send.sent_messages == []
    assert plugin._state == state_before
    assert caplog.text.count("Feed 信号超过") == 0
    assert not (tmp_path / "reset_state.json").exists()  # 0 落盘


def test_age_guard_first_encounter_logs_once_then_silent(
    tmp_path, monkeypatch, caplog
):
    """首次遇到且尚未记录的过期信号：age-guard 日志恰好一次并写入 key；
    下一轮同一 feed：不再打印、0 发送、0 state 变化。"""
    import copy
    import logging

    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    feed = _probe("probe_cr_feed.json")
    _wire_feed(plugin, feed)

    with caplog.at_level(logging.INFO, logger="codex_reset_watcher.plugin"):
        asyncio.run(plugin._check_once())
        assert plugin._ctx.send.sent_messages == []
        assert len(_gstate(plugin)["notified_keys"]) == 7
        assert caplog.text.count("Feed 信号超过") == 7  # 每个新过期信号恰好一次
        state_after_round1 = copy.deepcopy(plugin._state)
        state_file_after_round1 = (tmp_path / "reset_state.json").read_text(
            encoding="utf-8"
        )

        asyncio.run(plugin._check_once())
        assert plugin._ctx.send.sent_messages == []
        assert caplog.text.count("Feed 信号超过") == 7  # 不再增加
        assert plugin._state == state_after_round1
        assert (tmp_path / "reset_state.json").read_text(
            encoding="utf-8"
        ) == state_file_after_round1


# ===== v0.1.5：Tibo 观察日志 formatter（纯展示，不参与业务判断） =====


def _future_moment(hours: float = 3.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def test_tibo_observation_log_confirmed_no_plan():
    """真实生产形态：CONFIRMED + expectedResetAt=None + DIRECT_VERIFIED。"""
    line = _format_tibo_observation_log("CONFIRMED", None, False, "DIRECT_VERIFIED")
    assert line == "Tibo 监控：当前无待执行的重置计划｜最近状态：已确认｜验证：直接确认"


def test_tibo_observation_log_scheduled_approximate():
    moment = _future_moment()
    line = _format_tibo_observation_log(
        "SCHEDULED", moment, True, "DIRECT_VERIFIED", now=datetime.now(timezone.utc)
    )
    beijing = _format_title(moment.astimezone(BEIJING))
    assert line == (
        f"Tibo 监控：发现重置计划｜预计北京时间 {beijing} 左右"
        "｜状态：预估｜验证：直接确认"
    )


def test_tibo_observation_log_scheduled_exact():
    moment = _future_moment()
    line = _format_tibo_observation_log(
        "SCHEDULED", moment, False, "OFFICIAL_VERIFIED", now=datetime.now(timezone.utc)
    )
    beijing = _format_title(moment.astimezone(BEIJING))
    assert line == (
        f"Tibo 监控：发现重置计划｜北京时间 {beijing}｜状态：已确认｜验证：官方确认"
    )


def test_tibo_observation_log_time_changed():
    moment = _future_moment()
    line = _format_tibo_observation_log(
        "TIME_CHANGED", moment, True, "DIRECT_VERIFIED", now=datetime.now(timezone.utc)
    )
    beijing = _format_title(moment.astimezone(BEIJING))
    assert line == (
        f"Tibo 监控：重置时间已更新｜最新预计北京时间 {beijing} 左右｜验证：直接确认"
    )


def test_tibo_observation_log_unknown_values_fail_safe():
    """未知 status / verification 必须安全回退：展示原始值或"未知"。"""
    line = _format_tibo_observation_log("SOMETHING_NEW", None, False, "WEIRD_VERIFIED")
    assert line == (
        "Tibo 监控：当前无待执行的重置计划｜最近状态：SOMETHING_NEW｜验证：WEIRD_VERIFIED"
    )
    line = _format_tibo_observation_log("NONE", None, False, None)
    assert line.endswith("｜验证：未知")
    # 官方状态映射全覆盖（含未观察形态）
    assert _display_tibo_status("SCHEDULED") == "已计划"
    assert _display_tibo_status("DUE") == "已到预计时间"
    assert _display_tibo_status("CONFIRMING") == "正在确认"
    assert _display_tibo_status("EXPIRED_UNCONFIRMED") == "已过期但未确认"
    assert _display_verification_status("OFFICIAL_VERIFIED") == "官方确认"
    # 未知未来状态：fail-safe 原始值 + 未来计划模板
    moment = _future_moment()
    line = _format_tibo_observation_log(
        "FUTURE_KIND", moment, False, "DIRECT_VERIFIED", now=datetime.now(timezone.utc)
    )
    assert "发现重置计划" in line
    assert "状态：FUTURE_KIND" in line


# ===== v0.1.6 L4 upstream-alert mirror（上游告警镜像车道） =====
# 职责边界：上游判断什么值得告警，插件忠实镜像。最小触发契约仅三项
# （official_signal 是 object、delivery_destination=="alerts"、
# alert_event_id 非空字符串）；展示字段缺失/未知/schema 演化不否决。
# 真实 fixture：live/forecast_0908.json（本次事故的活跃 83% 告警）、
# live/forecast_0905.json（official_signal=null + latest_alert 误报回声）。


def _osig(**overrides: object) -> dict:
    """真实 2026-09-08 forecast.official_signal 形状（可逐字段覆写）。"""
    osig: dict = {
        "tweet_id": "2097043464538264003",
        "summary": "Never gonna give you up\nNever gonna let you down",
        "at": "2026-09-07T19:24:57.000Z",
        "url": "https://x.com/thsottiaux/status/2097043464538264003",
        "kind": "signal",
        "score": {"band": "promise", "base": 83, "modifiers": [], "value": 83},
        "signal_type": "promise",
        "signal_tier": "likely",
        "alert_event_id": "signal:2097043464538264003:likely",
        "delivery_destination": "alerts",
        "window": None,
    }
    osig.update(overrides)
    return osig


def _wire_forecast(
    plugin: CodexResetWatcher,
    forecast: dict | None,
    feed: dict | None = None,
    current: dict | None = None,
):
    async def fake_get(url: str, timeout_seconds: int):
        if "api/forecast" in url:
            return forecast
        if "api/feed" in url:
            return feed
        if "api/reset/current" in url:
            return current
        return None

    plugin._get_json = fake_get  # type: ignore[method-assign]

def _wire_providers(
    plugin: CodexResetWatcher,
    fx: object = None,
    vx: object = None,
    forecast: dict | None = None,
    feed: dict | None = None,
):
    """L4 provider 密闭桩：按 URL 路由 fx/vx/forecast/feed/current；
    fx/vx 传 Exception 则模拟抛错，传 dict 则返回该 payload；
    返回调用计数用于「单次 fetch / 零请求 / 预算」断言。"""
    calls: dict[str, list] = {"fx": [], "vx": []}

    async def fake_get(url: str, timeout_seconds: int):
        if "api.fxtwitter.com" in url:
            calls["fx"].append((url, timeout_seconds))
            if isinstance(fx, Exception):
                raise fx
            return fx
        if "api.vxtwitter.com" in url:
            calls["vx"].append((url, timeout_seconds))
            if isinstance(vx, Exception):
                raise vx
            return vx
        if "api/forecast" in url:
            return forecast
        if "api/feed" in url:
            return feed
        if "api/reset/current" in url:
            return current
        return None

    plugin._get_json = fake_get  # type: ignore[method-assign]
    return calls


# --- 最小触发契约（纯函数） ---


def test_upstream_alert_contract_accepts_real_signal():
    assert (
        upstream_alert_event_id({"official_signal": _osig()})
        == "signal:2097043464538264003:likely"
    )


def test_upstream_alert_contract_rejects_non_object_official_signal():
    assert upstream_alert_event_id({"official_signal": None}) is None
    assert upstream_alert_event_id({"official_signal": ["x"]}) is None
    assert upstream_alert_event_id({"official_signal": "alerts"}) is None
    assert upstream_alert_event_id({}) is None
    assert upstream_alert_event_id(None) is None
    assert upstream_alert_event_id("forecast") is None


def test_upstream_alert_contract_rejects_non_alerts_delivery():
    for delivery in (None, "web", "ALERTS", "", "telegram"):
        osig = _osig(delivery_destination=delivery)
        assert upstream_alert_event_id({"official_signal": osig}) is None


def test_upstream_alert_contract_rejects_bad_alert_event_id():
    for alert_id in (None, "", "   ", 123, ["signal:1:likely"]):
        osig = _osig(alert_event_id=alert_id)
        assert upstream_alert_event_id({"official_signal": osig}) is None


def test_upstream_alert_contract_ignores_latest_alert_entirely():
    """latest_alert 是内部回声（9-05 误报且未投递）：无论形态如何都不参与判定。"""
    forecast = {
        "official_signal": None,
        "latest_alert": _osig(),  # 形似 official_signal 的回声也不行
    }
    assert upstream_alert_event_id(forecast) is None


def test_0905_false_positive_fixture_never_triggers_mirror(tmp_path):
    """回归（2026-09-05 真实误报）：official_signal=null + latest_alert=
    reset/confirmed（reply 2096035748130795560，Telegram 从未投递）
    → L4 永不触发。"""
    forecast = _probe("live/forecast_0905.json")
    assert forecast["official_signal"] is None
    assert forecast["latest_alert"]["id"] == "2096035748130795560"
    assert forecast["latest_alert"]["state"] == "confirmed"

    plugin = _make_plugin(tmp_path)
    _wire_forecast(plugin, forecast)
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []
    assert not _gstate(plugin).get("upstream_alert_keys")

    # 即便 latest_alert 带上 delivery/alert_id 等告警形字段也无效：
    decoy = {
        "official_signal": None,
        "latest_alert": {
            "id": "2096035748130795560",
            "kind": "reset",
            "state": "confirmed",
            "delivery_destination": "alerts",
            "alert_event_id": "signal:2096035748130795560:likely",
        },
    }
    _wire_forecast(plugin, decoy)
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []


# --- 展示字段永不否决 + 文案 ---


def test_upstream_alert_minimal_contract_fires_without_display_fields(tmp_path):
    """三个契约字段之外全部缺失：仍必须触发（展示字段不否决）。
    v0.1.10：置信度/预计时间等展示字段退役 → 极简告警仅剩标题。"""
    plugin = _make_plugin(tmp_path)
    osig = {"delivery_destination": "alerts", "alert_event_id": "signal:X:likely"}
    asyncio.run(
        plugin._process_upstream_alert({"official_signal": osig}, ["100000001"])  # noqa: SLF001
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert bodies[0] == GLOBAL_NOTICE_TITLE  # 全部展示字段缺失：仅标题
    assert _gstate(plugin)["upstream_alert_keys"] == ["upstream-alert:signal:X:likely"]


def test_upstream_alert_unknown_display_values_shown_raw_not_vetoed(tmp_path):
    """展示字段未知 enum / 错误类型 / 极旧 at：绝不否决告警
    （official_signal 是当前告警指针，无 freshness 窗口）。
    v0.1.10：score/window 等不再展示，但字段存在仍不得阻断发送与 URL 展示。"""
    plugin = _make_plugin(tmp_path)
    osig = {
        "delivery_destination": "alerts",
        "alert_event_id": "signal:Y:unknown_tier",
        "tweet_id": None,  # 缺失
        "summary": "",  # 空
        "at": "2026-01-01T00:00:00Z",  # 半年多以前：不设 freshness 否决
        "url": "not-a-url",
        "score": "high",  # 非 dict 非数值
        "signal_tier": "super-strong",  # 未知 enum
        "signal_type": 123,  # 错误类型
        "window": "6pm PST",  # 非 dict
    }
    asyncio.run(
        plugin._process_upstream_alert({"official_signal": osig}, ["100000001"])  # noqa: SLF001
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    body = bodies[0]
    assert body.startswith(GLOBAL_NOTICE_TITLE)
    assert "原帖：not-a-url" in body
    # v0.1.10 退役字段：置信度/预计时间不得出现；taxonomy 仅内部使用
    assert "置信度" not in body
    assert "预计时间" not in body
    assert "2026-01-01" not in body  # at 只是指针元数据，不展示也不否决
    assert "super-strong" not in body
    assert "123" not in body
    assert "signal:Y:unknown_tier" not in body
    assert "告警 ID" not in body


def test_upstream_alert_message_hides_internal_implementation():
    """QQ 用户侧不得暴露内部实现：上游站名（Codex Reset）、告警身份
    （alert_event_id/告警 ID）、分类 taxonomy（signal_type/tier）、
    provider 名称（fxtwitter/vxtwitter）、镜像机制与上游来源一律不出现。
    v0.1.10：统一结构只有原文（或摘录）/中文翻译/原帖；置信度、预计时间、
    AI 解读块全部退役。"""
    body = build_full_notice(
        GLOBAL_NOTICE_TITLE,
        _osig()["url"],
        "完整正文内容",
        "full",
        "完整中文翻译内容",
    )
    assert body.startswith(GLOBAL_NOTICE_TITLE)
    assert "中文翻译：\n完整中文翻译内容" in body
    assert "Tibo 原文：\n完整正文内容" in body
    assert "原帖：https://x.com/thsottiaux/status/2097043464538264003" in body
    # v0.1.10 退役的旧展示字段负断言
    assert "置信度" not in body and "83%" not in body
    assert "预计时间" not in body
    assert "AI 解读" not in body
    assert "要点" not in body and "关键信息" not in body and "说明：" not in body
    # 内部实现负断言（taxonomy 值与机制词一并覆盖）：
    assert "Codex Reset" not in body
    assert "镜像" not in body and "上游" not in body
    assert "alert_event_id" not in body
    assert "告警 ID" not in body
    assert "signal_type" not in body and "信号类型" not in body
    assert "promise" not in body and "likely" not in body
    assert "official_signal" not in body and "forecast" not in body
    assert "fxtwitter" not in body and "vxtwitter" not in body
    assert "官方承诺" not in body  # 术语红线沿用：不放大上游语义判定
    # 非 full（unknown/truncated）→ 诚实措辞「摘录」：
    excerpt = build_full_notice(
        GLOBAL_NOTICE_TITLE, _osig()["url"], "只有前 200 字", "unknown", None
    )
    assert "Tibo 原文摘录：\n只有前 200 字" in excerpt
    assert "Tibo 原文：" not in excerpt  # 「原文：」不得出现在摘录文案中


# --- 生产路径：真实 fixture、去重、升级、receipt、失败隔离 ---


def test_real_0908_forecast_mirrors_once_then_dedups(tmp_path):
    """真实事故 fixture：部署即对现存有效 83% 告警补发一次（当前有效
    告警不是历史补发）；同 alert_event_id 第二轮静默。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    _wire_forecast(plugin, _probe("live/forensic_0908/forecast_0908.json"))

    async def once() -> None:
        await plugin._check_once()
        while plugin._inflight:  # 等待后台管线完成
            await asyncio.sleep(0.01)

    asyncio.run(asyncio.wait_for(once(), 10))
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    body = bodies[0]
    assert body.startswith(GLOBAL_NOTICE_TITLE)
    assert "原帖：https://x.com/thsottiaux/status/2097043464538264003" in body
    # v0.1.10：置信度等旧展示字段退役；QQ 正文不暴露内部实现；
    # alert_event_id 仅存 state / 内部日志
    assert "置信度" not in body and "83%" not in body
    assert "Codex Reset" not in body
    assert "2097043464538264003:likely" not in body
    assert "告警 ID" not in body and "镜像" not in body
    assert _gstate(plugin)["upstream_alert_keys"] == [
        "upstream-alert:signal:2097043464538264003:likely"
    ]
    assert json.loads(
        (tmp_path / "reset_state.json").read_text(encoding="utf-8")
    )["version"] == 6  # state schema 仍 v6，只是新增键
    # 第二轮同 id：静默。
    asyncio.run(asyncio.wait_for(once(), 10))
    assert len(plugin._ctx.send.sent_messages) == 1


def test_tier_upgrade_new_alert_event_id_mirrors_again(tmp_path):
    """likely→strong 等任何 alert_event_id 变化都是新 upstream alert：
    作为新告警镜像，不得被去重吞掉。"""
    plugin = _make_plugin(tmp_path)
    _wire_providers(plugin, fx=None, vx=None)  # providers 全挂 → forecast 摘录
    asyncio.run(plugin._process_upstream_alert(_probe("live/forensic_0908/forecast_0908.json"), ["100000001"]))  # noqa: SLF001
    upgraded = _osig(
        signal_tier="strong",
        alert_event_id="signal:2097043464538264003:strong",
        score={"band": "promise", "base": 93, "modifiers": [], "value": 93},
    )
    asyncio.run(
        plugin._process_upstream_alert({"official_signal": upgraded}, ["100000001"])  # noqa: SLF001
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 2
    # 升级告警以新告警身份再镜像一次（v0.1.10 无置信度展示）；
    # alert_event_id 仅落 state，不进文案：
    assert "置信度" not in bodies[1] and "93%" not in bodies[1]
    assert _gstate(plugin)["upstream_alert_keys"] == [
        "upstream-alert:signal:2097043464538264003:likely",
        "upstream-alert:signal:2097043464538264003:strong",
    ]


def test_upstream_alert_multi_group_failed_group_retries(tmp_path):
    """per-group receipt：A 成功 / B 失败 → 下一轮仅 B 重试，A 零重复。"""
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    sent: list[str] = []
    calls: dict[str, int] = {}

    async def flaky_send(group_id: str, message: str) -> bool:
        calls[group_id] = calls.get(group_id, 0) + 1
        if group_id == "100000002" and calls["100000002"] == 1:
            return False
        sent.append(group_id)
        return True

    plugin._send_group_text = flaky_send  # type: ignore[method-assign]
    calls = _wire_providers(plugin, fx=None, vx=None)
    forecast = {"official_signal": _osig()}

    async def round(n_expected: int) -> None:
        await plugin._process_upstream_alert(
            forecast, ["100000001", "100000002"]  # noqa: SLF001
        )
        while plugin._inflight:  # 等待后台管线完成
            await asyncio.sleep(0.01)
        assert len(sent) == n_expected, (n_expected, sent)

    asyncio.run(asyncio.wait_for(round(1), 10))
    assert len(calls["fx"]) == 1  # A 成/B 败 → 本轮仅取一次全文
    assert sent == ["100000001"]
    assert _gstate(plugin, "100000001")["upstream_alert_keys"] == [
        "upstream-alert:signal:2097043464538264003:likely"
    ]
    assert _gstate(plugin, "100000002") == {}
    asyncio.run(asyncio.wait_for(round(2), 10))
    assert sent == ["100000001", "100000002"]


def test_forecast_fetch_failure_keeps_other_lanes_working(tmp_path, monkeypatch):
    """forecast 挂 → L4 静默跳过；feed baseline 与 Tibo 观察不受影响。"""
    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path)

    async def fake_get(url: str, timeout_seconds: int):
        if "api/forecast" in url:
            return None  # forecast 挂
        if "api/feed" in url:
            return _probe("probe_cr_feed.json")
        return None

    plugin._get_json = fake_get  # type: ignore[method-assign]
    asyncio.run(plugin._check_once())
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin)["feed_baseline_done"] is True
    assert not _gstate(plugin).get("upstream_alert_keys")


def test_upstream_alert_send_failure_records_nothing(tmp_path):
    """发送失败 → 不落任何键（下一轮整体重试）。"""
    plugin = _make_plugin(tmp_path)

    async def failing_send(group_id: str, message: str) -> bool:
        return False

    plugin._send_group_text = failing_send  # type: ignore[method-assign]
    _wire_providers(plugin, fx=None, vx=None)
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"]  # noqa: SLF001
        )
    )
    assert plugin._ctx.send.sent_messages == []
    assert _gstate(plugin) == {}


def test_upstream_alert_keys_survive_state_reload(tmp_path):
    """v0.1.6 自身 reload / state round-trip 必须保留 upstream_alert_keys
    （去重继续生效）。

    回滚语义（已接受的保守行为，不在本测试范围）：回滚 0.1.5 时旧版本
    不认识该字段，会整体丢弃；之后重新升级、且同一 upstream alert 仍
    active 时，可能再次镜像一次。"""
    plugin = _make_plugin(tmp_path)
    _wire_providers(plugin, fx=None, vx=None)

    async def run_and_drain() -> None:
        await plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"]  # noqa: SLF001
        )
        while plugin._inflight:  # 等待后台管线完成
            await asyncio.sleep(0.01)

    asyncio.run(asyncio.wait_for(run_and_drain(), 10))
    assert (tmp_path / "reset_state.json").exists()
    reloaded = _make_plugin(tmp_path)
    assert _gstate(reloaded)["upstream_alert_keys"] == [
        "upstream-alert:signal:2097043464538264003:likely"
    ]

    async def rerun() -> None:
        await reloaded._process_upstream_alert(  # noqa: SLF001
            {"official_signal": _osig()}, ["100000001"]
        )
        while reloaded._inflight:
            await asyncio.sleep(0.01)

    asyncio.run(asyncio.wait_for(rerun(), 10))
    assert reloaded._ctx.send.sent_messages == []


def test_upstream_alert_keys_survive_without_feed_baseline(tmp_path):
    """核心回归（receipt 独立性）：未完成 feed baseline 的群（如新加入群）
    只收到过镜像告警时，插件重载后镜像去重键不得被 L1 配对校验丢弃。"""
    plugin = _make_plugin(tmp_path)
    _wire_providers(plugin, fx=None, vx=None)
    # 故意不做 feed baseline（receipt 无 feed_baseline_done/notified_keys）。

    async def run_and_drain() -> None:
        await plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"]  # noqa: SLF001
        )
        while plugin._inflight:  # 等待后台管线完成
            await asyncio.sleep(0.01)

    asyncio.run(asyncio.wait_for(run_and_drain(), 10))
    reloaded = _make_plugin(tmp_path)
    _wire_providers(reloaded, fx=None, vx=None)
    assert not _gstate(reloaded).get("feed_baseline_done")
    # v0.1.9：三个 receipt 字段全部独立存活（无 baseline 的群亦不丢失）。
    assert _gstate(reloaded)["upstream_alert_keys"] == [
        "upstream-alert:signal:2097043464538264003:likely"
    ]
    assert _gstate(reloaded)["upstream_alert_tweet_ids"] == [
        "2097043464538264003"
    ]
    # 重载后同一 upstream alert 静默（不得重复镜像）。
    asyncio.run(
        reloaded._process_upstream_alert(  # noqa: SLF001
            {"official_signal": _osig()}, ["100000001"]
        )
    )
    assert reloaded._ctx.send.sent_messages == []


def test_upstream_alert_keys_corrupt_type_dropped_conservatively(tmp_path):
    """upstream_alert_keys 类型损坏 → 整体丢弃（保守方向：最多对当前有效
    告警多镜像一次，绝不带着损坏状态静默吞掉后续告警）。"""
    state = {
        "version": 6,
        "state": {"groups": {"100000001": {"upstream_alert_keys": ["ok", 42]}}},
    }
    (tmp_path / "reset_state.json").write_text(json.dumps(state), encoding="utf-8")
    plugin = _make_plugin(tmp_path)
    assert _gstate(plugin) == {}


def test_no_cross_lane_dedup_between_feed_and_mirror(tmp_path, monkeypatch):
    """v0.1.6 决策回归：L1 declared 键不影响 L4 镜像（各自独立 receipt，
    短期真实事件重复提醒可接受；跨 lane suppression 曾造成漏报）。"""
    monkeypatch.setattr(plugin_module, "_utcnow", _fixed_now)
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = [
        # 同推文已被 L1 记录（假设上游后续把它翻成 announced）：
        "global-declared:2097043464538264003",
    ]
    _wire_forecast(plugin, _probe("live/forensic_0908/forecast_0908.json"))
    asyncio.run(plugin._check_once())
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert bodies[0].startswith(GLOBAL_NOTICE_TITLE)


# ===== v0.1.7 L4 Tweet Content Provider（展示层 enrichment，不参与触发/去重）=====
# 黄金 fixture：2026-09-08 真实 note tweet（2097043464538264003）的 FxTwitter /
# VxTwitter 实测响应 + 真实非-note 推文（2095651088502591861）FxTwitter 响应。

_FX_GOLDEN = _probe("live/forensic_0908/providers/fxtwitter_status.json")
_VX_GOLDEN = _probe("live/forensic_0908/providers/vxtwitter_status.json")
_FX_BANKED = _probe("live/forensic_0908/providers/fxtwitter_banked_nonnote.json")
N1 = chr(10)  # 运行时拼接用：标签行后的真实换行


def test_golden_fxtwitter_real_note_tweet_full_text():
    """黄金回归（真实 note tweet）：FxTwitter 实测响应 → full / 422 字 /
    含落地时间；QQ 用「Tibo 原文」而非「摘录」。"""
    assert _FX_GOLDEN["tweet"]["is_note_tweet"] is True
    content = _content_from_provider_payload(_FX_GOLDEN, "fxtwitter")
    assert content is not None
    assert content.source == "fxtwitter" and content.completeness == "full"
    assert len(content.text) == 422
    assert "Lands around 6pm PST today" in content.text


def test_golden_vxtwitter_real_payload_full_text():
    """真实 VxTwitter 响应（顶层 text、无 is_note_tweet 字段）：
    text>280 → full。"""
    content = _content_from_provider_payload(_VX_GOLDEN, "vxtwitter")
    assert content is not None
    assert content.source == "vxtwitter" and content.completeness == "full"
    assert len(content.text) == 422
    assert "Lands around 6pm PST today" in content.text


def test_golden_non_note_fxtwitter_tweet_is_full_not_failure():
    """真实非-note 推文（278 字，is_note_tweet=False）：普通短帖全文即完整，
    不得因为不是 note tweet 而误判失败或降级。"""
    assert _FX_BANKED["tweet"]["is_note_tweet"] is False
    content = _content_from_provider_payload(_FX_BANKED, "fxtwitter")
    assert content is not None and content.completeness == "full"
    assert len(content.text) == 278


def test_golden_fx_provider_through_production_path(tmp_path):
    """黄金回归（生产路径）：FxTwitter 成功 → QQ 消息含完整原文与落地时间，
    用「Tibo 原文」标签。"""
    plugin = _make_plugin(tmp_path)
    _wire_providers(plugin, fx=_FX_GOLDEN)
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"]  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert "Tibo 原文：" + N1 in bodies[0]
    assert "Lands around 6pm PST today" in bodies[0]
    assert "Tibo 原文摘录" not in bodies[0]
    assert bodies[0].startswith(GLOBAL_NOTICE_TITLE)  # v0.1.10：无置信度行


def test_fx_timeout_falls_to_vxtwitter(tmp_path):
    """FxTwitter 超时/抛错 → 立刻尝试 VxTwitter 并成功。"""
    plugin = _make_plugin(tmp_path)
    calls = _wire_providers(plugin, fx=TimeoutError(), vx=_VX_GOLDEN)
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"]  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert "Lands around 6pm PST today" in bodies[0]
    assert "Tibo 原文：" + N1 in bodies[0]
    assert len(calls["fx"]) == 1 and len(calls["vx"]) == 1


def test_fx_404_or_bad_payload_falls_to_vxtwitter(tmp_path):
    """FxTwitter 404/坏 JSON（解析后不可用）→ VxTwitter 成功。"""
    plugin = _make_plugin(tmp_path)
    calls = _wire_providers(plugin, fx={}, vx=_VX_GOLDEN)  # {} = 无 tweet 字段
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"]  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1 and "Lands around 6pm PST today" in bodies[0]
    assert len(calls["vx"]) == 1


def test_both_providers_fail_uses_feed_text(tmp_path):
    """两个 provider 都失败 → feed 匹配推文文本（completeness=unknown，
    QQ 措辞「Tibo 原文摘录」）。"""
    plugin = _make_plugin(tmp_path)
    calls = _wire_providers(plugin, fx=None, vx=None)
    feed = {
        "tweets": [
            {"id": "2097043464538264003", "text": "feed 280 字文本，比 39 字 summary 更长——" + "f" * 60},
        ]
    }
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], feed  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert "Tibo 原文摘录：" in bodies[0] and ("f" * 60) in bodies[0]  # feed 候选更长 → 获胜
    assert len(calls["fx"]) == 1 and len(calls["vx"]) == 1  # 顺序：先 fx 后 vx


def test_l4_summary_only_never_displayed_nor_translated(tmp_path):
    """review-fix 回归：providers 与 feed 全不可用（feed 无匹配推文）时，
    official_signal.summary 是上游摘要而非逐字 Tibo 原文——不得展示、
    不得交给 LLM；告警仍发标题+原帖，receipt 正常落盘。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    llm_calls = {"n": 0}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, **kwargs):
        llm_calls["n"] += 1
        return {"success": True, "response": json.dumps({"translation_zh": "不应出现的翻译"}, ensure_ascii=False), "model_name": "m1", "total_tokens": 1}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    calls = _wire_providers(plugin, fx=None, vx=None)
    feed = {"tweets": [{"id": "999", "text": "无关推文"}]}
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], feed  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert bodies[0] == (
        f"{GLOBAL_NOTICE_TITLE}\n\n原帖：https://x.com/thsottiaux/status/2097043464538264003"
    )
    assert "Never gonna give you up" not in bodies[0]  # summary 不冒充原文/摘录
    assert "Tibo 原文" not in bodies[0]
    assert len(calls["fx"]) == 1 and len(calls["vx"]) == 1  # 真实来源仍被尝试
    assert llm_calls["n"] == 0  # summary 不交给 LLM
    assert _gstate(plugin)["upstream_alert_keys"] == [
        "upstream-alert:signal:2097043464538264003:likely"
    ]  # receipt 正常落盘（alert decision 不变）


def test_all_text_sources_missing_still_sends(tmp_path):
    """providers/feed/summary 全缺失：alert 仍然发送（标题+置信度+原帖），
    绝不因正文为空而漏报。"""
    plugin = _make_plugin(tmp_path)
    calls = _wire_providers(plugin, fx=None, vx=None)
    osig = {
        "delivery_destination": "alerts",
        "alert_event_id": "signal:BARE:likely",
        "score": {"value": 83},
        "url": "https://x.com/thsottiaux/status/42",
    }
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": osig}, ["100000001"], None  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert bodies[0] == (
        f"{GLOBAL_NOTICE_TITLE}\n\n原帖：https://x.com/thsottiaux/status/42"
    )
    assert "Tibo 原文" not in bodies[0]  # 无正文则整块省略
    assert "置信度" not in bodies[0]  # v0.1.10：置信度展示退役


def test_provider_timeout_budget():
    """时间预算：单 provider 10.0s（quality-first）；双 provider 最坏 ≈≤20s。"""
    assert plugin_module.PROVIDER_TIMEOUT_SECONDS == 10.0
    assert plugin_module.PROVIDER_TIMEOUT_SECONDS * 2 <= 20.0


def test_provider_call_receives_timeout_budget(tmp_path):
    """每次 provider 请求都带 2.0s 预算（经生产路径桩捕获）。"""
    plugin = _make_plugin(tmp_path)
    calls = _wire_providers(plugin, fx=None, vx=None)
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"]  # noqa: SLF001
        )
    )
    assert calls["fx"][0][1] == plugin_module.PROVIDER_TIMEOUT_SECONDS
    assert calls["vx"][0][1] == plugin_module.PROVIDER_TIMEOUT_SECONDS


def test_multi_group_single_fetch_shared_content(tmp_path):
    """多群同一 alert：全文只获取一次，两群复用同一内容。"""
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    calls = _wire_providers(plugin, fx=_FX_GOLDEN)

    async def fetch_once_and_mirror_both() -> None:
        await plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001", "100000002"]  # noqa: SLF001
        )
        while plugin._inflight:  # 等待后台管线完成
            await asyncio.sleep(0.01)

    asyncio.run(asyncio.wait_for(fetch_once_and_mirror_both(), 10))
    assert len(calls["fx"]) == 1 and calls["vx"] == []
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 2 and bodies[0] == bodies[1]
    assert "Lands around 6pm PST today" in bodies[0]


def test_all_groups_deduped_zero_provider_requests(tmp_path):
    """所有群已命中 upstream_alert_keys → 零 provider 请求、零发送、零日志。"""
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    for gid in ("100000001", "100000002"):
        _gstate(plugin, gid)["upstream_alert_keys"] = [
            "upstream-alert:signal:2097043464538264003:likely"
        ]
    calls = _wire_providers(plugin, fx=_FX_GOLDEN)
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001", "100000002"]  # noqa: SLF001
        )
    )
    assert calls["fx"] == [] and calls["vx"] == []
    assert plugin._ctx.send.sent_messages == []


def test_retry_refetches_but_never_duplicated_for_success_group(tmp_path):
    """A 成/B 败 → 下一轮允许再次 fetch 且只给 B 补发；A 不重复。"""
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    sent: list[str] = []
    calls_holder = {"calls": None}

    async def flaky_send(group_id: str, message: str) -> bool:
        if group_id == "100000002" and len(
            calls_holder["calls"]["fx"]
        ) == 1:
            return False  # 第一轮 B 失败
        sent.append(group_id)
        return True

    plugin._send_group_text = flaky_send  # type: ignore[method-assign]
    calls_holder["calls"] = _wire_providers(plugin, fx=_FX_GOLDEN)
    forecast = {"official_signal": _osig()}
    asyncio.run(
        plugin._process_upstream_alert(forecast, ["100000001", "100000002"], None)  # noqa: SLF001
    )
    assert sent == ["100000001"]
    asyncio.run(
        plugin._process_upstream_alert(forecast, ["100000001", "100000002"], None)  # noqa: SLF001
    )
    assert len(calls_holder["calls"]["fx"]) == 2  # 允许为 B 重试再次 fetch
    assert sent == ["100000001", "100000002"]  # A 不重复，B 补发成功
    assert _gstate(plugin, "100000001")["upstream_alert_keys"] == [
        "upstream-alert:signal:2097043464538264003:likely"
    ]


def test_provider_malformed_payload_falls_through(tmp_path):
    """provider 字段未知/缺失（无 tweet / 空 text）→ 降级，不影响告警。"""
    plugin = _make_plugin(tmp_path)
    calls = _wire_providers(plugin, fx={"foo": 1}, vx={"text": "   "})
    feed = {"tweets": [{"id": "2097043464538264003", "text": "feed 兜底文本——" + "m" * 60}]}
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], feed  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1 and ("m" * 60) in bodies[0]  # feed 候选更长 → 获胜
    assert len(calls["fx"]) == 1 and len(calls["vx"]) == 1


def test_enrichment_cap_guards_pathological_note_tweet(tmp_path):
    """回归（部署 blocker 1）：3000 字完整 note tweet →
    Provider 层不裁剪：TweetContent.text 保留完整 3000 字且 completeness=full
    （未来 LLM 消费语义不被破坏）；QQ 展示层裁到 ~2000 字含省略号，
    且因展示被裁剪，标签必须降级为「原文摘录」而非「原文」。"""
    long_payload = {"tweet": {"text": "x" * 3000, "is_note_tweet": True}}
    content = _content_from_provider_payload(long_payload, "fxtwitter")
    assert content is not None and content.completeness == "full"
    assert len(content.text) == 3000  # Provider 层完整保留
    body = build_full_notice(
        GLOBAL_NOTICE_TITLE, _osig()["url"], content.text, content.completeness
    )
    assert "Tibo 原文摘录：" in body  # 展示被裁剪 → 不得仍称「原文」
    assert "Tibo 原文：" not in body
    assert ("x" * 1999 + "…") in body  # 展示层 ~2000 字含省略号
    assert ("x" * 2001) not in body


def test_fx_full_returns_immediately_vx_not_called(tmp_path):
    """FxTwitter 返回 full → 立即采用，不请求 VxTwitter。"""
    plugin = _make_plugin(tmp_path)
    calls = _wire_providers(plugin, fx=_FX_GOLDEN)
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"]  # noqa: SLF001
        )
    )
    assert len(calls["fx"]) == 1 and calls["vx"] == []
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert "Lands around 6pm PST today" in bodies[0]


def test_fx_unknown_vx_full_wins(tmp_path):
    """定向回归（部署 blocker 2）：FxTwitter 只拿到 280 字 note 截断
    （is_note=true → unknown）不得提前返回；VxTwitter 返回真实 422 字
    full → 最终必须选择 VxTwitter 全文（含落地时间）。"""
    truncated_note = {"tweet": {"text": "x" * 280, "is_note_tweet": True}}
    plugin = _make_plugin(tmp_path)
    calls = _wire_providers(plugin, fx=truncated_note, vx=_VX_GOLDEN)
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"]  # noqa: SLF001
        )
    )
    assert len(calls["fx"]) == 1 and len(calls["vx"]) == 1  # 都被尝试
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert "Lands around 6pm PST today" in bodies[0]  # VxTwitter 全文获胜
    assert "Tibo 原文：" in bodies[0]  # full → 「原文」措辞
    assert "Tibo 原文摘录" not in bodies[0]
    assert ("x" * 280) not in bodies[0]  # 不得误用 fx 截断候选


def test_both_unknown_keeps_longer_candidate(tmp_path):
    """双 provider 均 unknown → 取更长候选（fx 280 vs vx 100 → fx），
    且更短的 feed/forecast 兜底不得顶替。"""
    fx_unknown = {"tweet": {"text": "f" * 280, "is_note_tweet": True}}
    vx_unknown = {"text": "v" * 100}
    plugin = _make_plugin(tmp_path)
    calls = _wire_providers(plugin, fx=fx_unknown, vx=vx_unknown)
    feed = {"tweets": [{"id": "2097043464538264003", "text": "q" * 50}]}
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], feed  # noqa: SLF001
        )
    )
    assert len(calls["fx"]) == 1 and len(calls["vx"]) == 1
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert ("f" * 280) in bodies[0]  # 更长的 fx 候选获胜
    assert ("v" * 100) not in bodies[0] and ("q" * 50) not in bodies[0]
    assert "Tibo 原文摘录：" in bodies[0]  # unknown → 摘录措辞


def test_fx_unknown_shorter_vx_unknown_longer_takes_vx(tmp_path):
    """反向：先到者更短时，更长的后到候选获胜（不机械选第一个）。"""
    fx_unknown = {"tweet": {"text": "f" * 100, "is_note_tweet": True}}
    vx_unknown = {"text": "v" * 280}
    plugin = _make_plugin(tmp_path)
    _wire_providers(plugin, fx=fx_unknown, vx=vx_unknown)
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"]  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert ("v" * 280) in bodies[0] and ("f" * 100) not in bodies[0]


def test_fx_unknown_kept_when_no_better_source(tmp_path):
    """fx unknown 候选保留为最优可得真实文本（review-fix：summary 不再
    参与候选比较；无更优来源时 fx 文本以「摘录」措辞展示）。"""
    fx_unknown = {"tweet": {"text": "f" * 280, "is_note_tweet": True}}
    plugin = _make_plugin(tmp_path)
    _wire_providers(plugin, fx=fx_unknown, vx=None)
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], None  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert ("f" * 280) in bodies[0]  # fx 真实文本保留
    assert "Tibo 原文摘录：" in bodies[0]
    assert "Never gonna give you up" not in bodies[0]  # summary 不冒充原文


def test_feed_fallback_requires_matching_tweet_id(tmp_path):
    """feed 中无匹配 tweet_id → 不得误用无关推文；仅剩 summary 时不展示、
    不翻译，告警以标题+原帖发送（review-fix）。"""
    plugin = _make_plugin(tmp_path)
    _wire_providers(plugin, fx=None, vx=None)
    feed = {"tweets": [{"id": "111", "text": "无关推文"}]}
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], feed  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert "无关推文" not in bodies[0]
    assert "Never gonna give you up" not in bodies[0]  # summary 不冒充原文
    assert bodies[0] == (
        f"{GLOBAL_NOTICE_TITLE}\n\n原帖：https://x.com/thsottiaux/status/2097043464538264003"
    )


# ===== v0.1.8 LLM enrichment（总预算状态机 / inflight / state 锁 / JSON 管道）=====

def _llm_plugin(tmp_path: Path, llm_overrides: dict | None = None, **watcher: object):
    """带 [llm] 配置的插件；fx=golden、llm 可在测试中再覆写。"""
    plugin = _make_plugin(tmp_path, **watcher)
    _wire_providers(plugin, fx=_FX_GOLDEN)
    if llm_overrides:
        cfg = plugin.config.llm
        for key, value in llm_overrides.items():
            setattr(cfg, key, value)
    return plugin


def test_llm_success_adds_translation(tmp_path):
    """LLM 返回合法 translation-only JSON → QQ 消息含「中文翻译」块
    （原文照旧保留），且不出现任何退役的 AI 解读字段与内部实现词。"""
    plugin = _llm_plugin(tmp_path)
    good = {"translation_zh": "祝你说谎愉快……全球重置预计今天北京时间 10:00 左右落地"}
    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, **kwargs):
        return {"success": True, "response": json.dumps(good, ensure_ascii=False), "model_name": "m1", "total_tokens": 100}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], None  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    body = bodies[0]
    assert body.startswith(GLOBAL_NOTICE_TITLE)
    assert "中文翻译：\n祝你说谎愉快……全球重置预计今天北京时间 10:00 左右落地" in body
    assert "Lands around 6pm PST today" in body  # 原文仍在
    assert "Tibo 原文：" in body and "Tibo 原文摘录" not in body
    # v0.1.10：AI 解读字段全部退役
    assert "—— AI 解读 ——" not in body
    assert "要点" not in body and "类型：global" not in body
    assert "时间：around 6pm PST today" not in body
    assert "fxtwitter" not in body and "official_signal" not in body
    # receipt 正常写入
    assert _gstate(plugin)["upstream_alert_keys"] == [
        "upstream-alert:signal:2097043464538264003:likely"
    ]


def test_llm_host_failure_no_repair_alert_still_sent(tmp_path):
    """Host success=False → 不修复直接无 AI 块；告警照常发送。"""
    plugin = _llm_plugin(tmp_path)
    llm_calls = {"n": 0}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, **kwargs):
        llm_calls["n"] += 1
        return {"success": False, "error": "boom", "response": "", "model_name": "m1", "total_tokens": 0}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], None  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert llm_calls["n"] == 1  # 不修复
    assert len(bodies) == 1 and "中文翻译" not in bodies[0]
    assert "Tibo 原文：" in bodies[0]  # 通知不受影响


def test_llm_rpc_exception_no_repair(tmp_path):
    """RPC/调用异常 → 不修复直接无翻译块。"""
    plugin = _llm_plugin(tmp_path)
    llm_calls = {"n": 0}

    async def boom(*args, **kwargs):
        llm_calls["n"] += 1
        raise TimeoutError("rpc timeout")

    plugin.ctx.llm.generate = boom  # type: ignore[method-assign]
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], None  # noqa: SLF001
        )
    )
    assert llm_calls["n"] == 1
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1 and "中文翻译" not in bodies[0]


def test_llm_bad_json_one_repair_then_success(tmp_path):
    """首次输出坏 JSON（含围栏与杂讯）→ 用剩余预算修复一次 → 成功。"""
    plugin = _llm_plugin(tmp_path)
    good = {"translation_zh": "完整中文翻译"}
    responses = ["抱歉，我无法输出……```json{broken```", "好的：" + json.dumps(good, ensure_ascii=False)]
    rpc_timeouts: list[int] = []

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, rpc_timeout_ms=None, **kwargs):
        rpc_timeouts.append(rpc_timeout_ms)
        return {"success": True, "response": responses[len(rpc_timeouts) - 1], "model_name": "m1", "total_tokens": 1}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], None  # noqa: SLF001
        )
    )
    assert len(rpc_timeouts) == 2  # 恰好一次修复
    assert 590000 <= rpc_timeouts[0] <= 600000
    assert rpc_timeouts[1] <= rpc_timeouts[0]  # 修复使用剩余预算（共享总预算）
    assert rpc_timeouts[1] >= 5000  # 且不低于保护下限
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1 and "中文翻译：\n完整中文翻译" in bodies[0]


def test_llm_bad_json_repair_fails_alert_still_sent(tmp_path):
    """修复后仍坏 → 无翻译块，告警照发。"""
    plugin = _llm_plugin(tmp_path)
    llm_calls = {"n": 0}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, **kwargs):
        llm_calls["n"] += 1
        return {"success": True, "response": "not json at all", "model_name": "m1", "total_tokens": 0}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], None  # noqa: SLF001
        )
    )
    assert llm_calls["n"] == 2  # 首次+一次修复
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1 and "中文翻译" not in bodies[0]
    assert "Tibo 原文：" in bodies[0]


def test_llm_budget_exhausted_skips_repair(tmp_path, monkeypatch):
    """总预算状态机：修复前剩余预算不足 → 不再发起修复。"""
    plugin = _llm_plugin(tmp_path)
    clock = {"now": 1000.0}
    llm_calls = {"n": 0}
    monkeypatch.setattr(plugin_module.time, "monotonic", lambda: clock["now"])

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, rpc_timeout_ms=None, **kwargs):
        llm_calls["n"] += 1
        clock["now"] += plugin.config.llm.timeout_seconds - 3  # 消耗预算至剩 3 秒（<5）
        return {"success": True, "response": "broken", "model_name": "m1", "total_tokens": 0}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], None  # noqa: SLF001
        )
    )
    assert llm_calls["n"] == 1  # 预算耗尽，不再修复
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1 and "中文翻译" not in bodies[0]


def test_llm_disabled_no_llm_call(tmp_path):
    plugin = _llm_plugin(tmp_path, {"enabled": False})

    async def fail(*args, **kwargs):
        raise AssertionError("disabled 时不得调用 LLM")

    plugin.ctx.llm.generate = fail  # type: ignore[method-assign]
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], None  # noqa: SLF001
        )
    )
    assert len(plugin._ctx.send.sent_messages) == 1  # v0.1.7 通知照常


def test_llm_translation_validation_newlines_caps_and_defaults():
    """translation-only 校验：保留换行（歌词/分段忠实性）、CRLF 统一、
    去首尾空白；空/类型不符 → None；未知字段忽略；限幅只防病态输出。"""
    assert _validate_llm_translation({"translation_zh": "第一行\n第二行"}) == "第一行\n第二行"
    assert _validate_llm_translation({"translation_zh": "a\r\nb\rc"}) == "a\nb\nc"
    assert _validate_llm_translation({"translation_zh": "  x  "}) == "x"
    assert _validate_llm_translation({"translation_zh": 42}) is None
    assert _validate_llm_translation({"translation_zh": ""}) is None
    assert _validate_llm_translation({"translation_zh": "   \n  "}) is None
    assert _validate_llm_translation({"unknown_field": "忽略"}) is None
    assert _validate_llm_translation("nope") is None
    # 限幅：超过上限截断（防病态输出），上限内完整保留
    long = "x" * (TRANSLATION_MAX_CHARS + 100)
    v = _validate_llm_translation({"translation_zh": long})
    assert v is not None
    assert len(v) == TRANSLATION_MAX_CHARS and v.endswith("…")
    exact = _validate_llm_translation({"translation_zh": "y" * TRANSLATION_MAX_CHARS})
    assert exact == "y" * TRANSLATION_MAX_CHARS  # 恰好上限：不截断


def test_translation_cap_accommodates_real_corpus():
    """限幅红线：TRANSLATION_MAX_CHARS 必须远大于全部真实 Tibo 语料原文
    长度（含黄金 provider 全文），真实完整翻译不会被截断。"""
    texts: list[str] = []
    for name in ("probe_cr_feed.json", "live/feed_0905.json"):
        feed = _probe(name)
        texts.extend(str(t.get("text") or "") for t in feed.get("tweets", []))
    texts.append(str(_FX_GOLDEN["tweet"]["text"]))
    texts.append(str(_VX_GOLDEN["text"]))
    texts = [t for t in texts if t]
    longest = max(len(t) for t in texts)
    assert longest > 0
    # 中文译文一般不比原文更长；4 倍余量保证任何忠实完整翻译都不受限幅影响
    assert TRANSLATION_MAX_CHARS >= longest * 4
    t = _validate_llm_translation({"translation_zh": "译" * longest})
    assert t is not None and "…" not in t and len(t) == longest


def test_llm_extract_json_object_variants():
    obj = {"a": 1}
    assert _extract_json_object("```json" + chr(10) + chr(34) + chr(34) + "") is None
    assert _extract_json_object(json.dumps(obj)) == obj
    fence = chr(96) * 3 + "json" + chr(10) + json.dumps(obj) + chr(10) + chr(96) * 3
    assert _extract_json_object(fence) == obj
    assert _extract_json_object("前言" + json.dumps(obj) + "后记") == obj
    assert _extract_json_object("no braces") is None


def test_llm_never_overrides_alert_decision(tmp_path):
    """LLM 输出「不是 Reset」的翻译内容也绝不影响发送与 receipt
    （alert decision 不变；翻译只进文案）。"""
    plugin = _llm_plugin(tmp_path)

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, **kwargs):
        return {"success": True, "response": json.dumps({"translation_zh": "我认为这不是 Reset，不该发送"}, ensure_ascii=False), "model_name": "m1", "total_tokens": 0}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], None  # noqa: SLF001
        )
    )
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    # 照常发送：翻译内容原样进入文案（也证明它无否决权）
    assert "中文翻译：\n我认为这不是 Reset，不该发送" in bodies[0]
    assert _gstate(plugin)["upstream_alert_keys"] != []


def test_inflight_dedup_single_pipeline(tmp_path):
    """同一 alert 未处理完时，下一轮调度不得重复启动管线。"""
    plugin = _llm_plugin(tmp_path)
    release = asyncio.Event()
    fx_calls = {"n": 0}

    async def counting_get(url, timeout_seconds):
        if "api.fxtwitter.com" in url:
            fx_calls["n"] += 1
            await release.wait()  # 模拟 provider 慢
            return _FX_GOLDEN
        return None

    plugin._get_json = counting_get  # type: ignore[method-assign]
    forecast = {"official_signal": _osig()}

    async def scenario():
        await plugin._process_upstream_alert(forecast, ["100000001"], None)
        assert list(plugin._inflight.keys()) == [
            "upstream-alert:signal:2097043464538264003:likely"
        ]
        await plugin._process_upstream_alert(forecast, ["100000001"], None)  # inflight 命中
        assert fx_calls["n"] == 0  # inflight 命中：未重复启动管线
        release.set()
        while plugin._inflight:
            await asyncio.sleep(0.01)

    asyncio.run(asyncio.wait_for(scenario(), 5))
    assert fx_calls["n"] == 1 and len(plugin._ctx.send.sent_messages) == 1


def test_on_unload_gathers_inflight(tmp_path):
    """unload：cancel 后必须 gather 回收，inflight 清空，无悬空任务。"""
    plugin = _llm_plugin(tmp_path)
    release = asyncio.Event()

    async def hanging_get(url, timeout_seconds):
        if "api.fxtwitter.com" in url:
            await release.wait()
            return _FX_GOLDEN
        return None

    plugin._get_json = hanging_get  # type: ignore[method-assign]

    async def scenario():
        await plugin._process_upstream_alert({"official_signal": _osig()}, ["100000001"], None)
        assert plugin._inflight
        release.set()
        while plugin._inflight:
            await asyncio.sleep(0.01)
        # 重新制造 in-flight 再 unload
        release.clear()
        osig_b = _osig(alert_event_id="signal:2097043464538264003:strong")
        await plugin._process_upstream_alert({"official_signal": osig_b}, ["100000001"], None)
        assert plugin._inflight
        await plugin.on_unload()  # cancel + gather
        assert plugin._inflight == {}

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_state_lock_concurrent_receipts_no_loss(tmp_path):
    """并发回归（实现 blocker 审计）：两个不同 alert task 并发完成，
    两边 receipt 都不能丢；state lock 串行化落盘，且发送等待期间
    不持有 state lock（否则 slow_send 内拿锁会死锁）。"""
    plugin = _llm_plugin(tmp_path, group_id="", group_ids=["100000001"])
    gate = asyncio.Event()
    lock_reentrant_detected = {"flag": False}

    async def slow_send(group_id: str, message: str) -> bool:
        locked = plugin._state_lock.locked()
        if locked:
            lock_reentrant_detected["flag"] = True
        await gate.wait()
        return True

    plugin._send_group_text = slow_send  # type: ignore[method-assign]
    _wire_providers(plugin, fx=_FX_GOLDEN)
    osig_a = _osig()
    osig_b = _osig(alert_event_id="signal:2097043464538264003:strong")

    async def scenario():
        task_a = asyncio.create_task(
            plugin._process_upstream_alert({"official_signal": osig_a}, ["100000001"], None)
        )
        task_b = asyncio.create_task(
            plugin._process_upstream_alert({"official_signal": osig_b}, ["100000001"], None)
        )
        await asyncio.sleep(0.05)  # 两个 task 都进入 slow_send 等待窗口
        gate.set()
        await asyncio.gather(task_a, task_b)

    asyncio.run(asyncio.wait_for(scenario(), 5))
    assert lock_reentrant_detected["flag"] is False  # 发送期间未持锁
    keys = set(_gstate(plugin)["upstream_alert_keys"])
    assert keys == {
        "upstream-alert:signal:2097043464538264003:likely",
        "upstream-alert:signal:2097043464538264003:strong",
    }


def test_llm_config_sanitize_clamps_invalid(tmp_path):
    """[llm] 非法配置 normalize 到安全值；空 prompt 恢复默认。"""
    plugin = _make_plugin(tmp_path)
    normalized, changed = plugin.normalize_plugin_config({
        "plugin": {"enabled": True, "config_version": "1.2.0"},
        "watcher": {"group_ids": ["100000001"], "group_ids_migrated": True},
        "llm": {
            "enabled": "yes",
            "model_task": "  ",
            "temperature": 5.0,
            "max_tokens": 0,
            "timeout_seconds": 1,
            "prompt": "   ",
        },
    })
    assert changed is True
    llm = normalized["llm"]
    assert llm["enabled"] is True  # 非布尔 → bool() 归一
    assert llm["model_task"] == "replyer"
    assert llm["temperature"] == 2.0  # 越界就近钳到上界
    assert llm["max_tokens"] == 64  # 越界就近钳到下界
    assert llm["timeout_seconds"] == 30  # 越界就近钳到下界
    assert llm["prompt"] == plugin_module.DEFAULT_TRANSLATION_PROMPT


def test_llm_config_schema_widgets(tmp_path):
    """[llm] schema：boolean/number/多行 textarea/默认 prompt 非空。"""
    from maibot_sdk.config import generate_plugin_config_schema

    schema = generate_plugin_config_schema(CodexResetWatcherConfig)
    fields = schema["sections"]["llm"]["fields"]
    assert fields["enabled"]["type"] == "boolean"
    assert fields["model_task"]["type"] == "string"
    assert fields["temperature"]["type"] == "number"
    assert fields["temperature"]["min"] == 0 and fields["temperature"]["max"] == 2
    assert fields["max_tokens"]["type"] == "integer"
    assert fields["timeout_seconds"]["min"] == 30 and fields["timeout_seconds"]["max"] == 1800
    assert fields["prompt"]["ui_type"] == "textarea" and fields["prompt"]["rows"] == 12
    assert fields["prompt"]["default"].strip() != ""  # 默认 prompt 非空、可直接工作


# ===== v0.1.8 部署 blocker 修正：manifest capabilities / on_load 校验 /
# stale-group 竞态 / config-update cancel =====

def test_manifest_declares_llm_capabilities():
    """manifest 必须声明 llm.generate 与 llm.get_available_models（Host 按
    manifest capabilities 做授权注册，未声明即被拒）。"""
    manifest = json.load(open(
        Path(__file__).resolve().parent / "codex_reset_watcher" / "_manifest.json",
        encoding="utf-8",
    ))
    caps = manifest["capabilities"]
    assert "llm.generate" in caps
    assert "llm.get_available_models" in caps


def test_on_load_validates_llm_task_info(tmp_path, caplog):
    """enabled=true 且任务存在 → INFO 校验行；watcher 正常启动。"""
    import logging

    plugin = _llm_plugin(tmp_path)
    async def fake_models():
        return ["replyer", "utils"]
    plugin.ctx.llm.get_available_models = fake_models  # type: ignore[method-assign]
    with caplog.at_level(logging.INFO, logger="codex_reset_watcher.plugin"):
        asyncio.run(plugin.on_load())
    assert "model_task=replyer 可用" in caplog.text
    asyncio.run(plugin.on_unload())


def test_on_load_missing_task_warns_but_still_loads(tmp_path, caplog):
    """任务缺失/查询失败 → WARNING 但插件正常加载（首个 alert 时自动降级）。"""
    import logging

    plugin = _llm_plugin(tmp_path)
    async def fake_models():
        return ["utils"]
    plugin.ctx.llm.get_available_models = fake_models  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="codex_reset_watcher.plugin"):
        asyncio.run(plugin.on_load())  # 不抛异常
    assert "不在 Host 任务列表" in caplog.text
    asyncio.run(plugin.on_unload())


def test_on_load_query_failure_warns_but_still_loads(tmp_path, caplog):
    import logging

    plugin = _llm_plugin(tmp_path)

    def boom():
        raise RuntimeError("rpc down")

    plugin.ctx.llm.get_available_models = boom  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="codex_reset_watcher.plugin"):
        asyncio.run(plugin.on_load())
    assert "任务列表查询失败" in caplog.text
    asyncio.run(plugin.on_unload())


def test_llm_disabled_skips_on_load_validation(tmp_path, caplog):
    import logging

    plugin = _llm_plugin(tmp_path, {"enabled": False})
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return ["utils"]

    plugin.ctx.llm.get_available_models = counting  # type: ignore[method-assign]
    with caplog.at_level(logging.INFO, logger="codex_reset_watcher.plugin"):
        asyncio.run(plugin.on_load())
    assert calls["n"] == 0  # disabled → 不检查
    asyncio.run(plugin.on_unload())


def test_stale_group_removed_mid_pipeline_skipped_and_no_receipt(tmp_path):
    """定向回归（blocker 2）：A/B 启动 inflight；LLM 未完成时删除 B；
    完成后只允许 A 收消息；state 中不得重新出现 B。"""
    plugin = _llm_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    b_removed = asyncio.Event()

    streams: list[str] = []

    async def racing_send(group_id: str, message: str) -> bool:
        if group_id == "100000001":
            # A 发送成功后、管线继续处理 B 之前，配置删除 B（模拟热更新竞态）
            plugin.set_plugin_config(_config_for(
                group_ids=["100000001"], group_ids_migrated=True)
            )
            plugin._reconcile_groups()
            b_removed.set()
            streams.append(group_id)
            return True
        await b_removed.wait()
        streams.append(group_id)
        return True

    plugin._send_group_text = racing_send  # type: ignore[method-assign]
    _wire_providers(plugin, fx=_FX_GOLDEN)
    async def scenario():
        await plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001", "100000002"], None  # noqa: SLF001
        )
        while plugin._inflight:
            await asyncio.sleep(0.01)
    asyncio.run(asyncio.wait_for(scenario(), 5))
    streams = [s for s, _ in plugin._ctx.send.sent_messages]

    assert "100000002" not in plugin._state["groups"]  # B 未被写回 state
    assert _gstate(plugin)["upstream_alert_keys"] == [
        "upstream-alert:signal:2097043464538264003:likely"
    ]


def test_on_config_update_cancels_inflight_and_recovers(tmp_path):
    """定向回归（blocker 2）：config update 取消旧 inflight（gather 清空、
    无悬空 task）；未写 receipt 的 alert 下一轮按新配置重新处理。"""
    plugin = _llm_plugin(tmp_path)
    release = asyncio.Event()

    async def hanging_get(url, timeout_seconds):
        if "api.fxtwitter.com" in url:
            await release.wait()
            return _FX_GOLDEN
        return None

    plugin._get_json = hanging_get  # type: ignore[method-assign]
    new_config = {
        "plugin": {"enabled": True, "config_version": "1.2.0"},
        "watcher": {"group_ids": ["100000001"], "group_ids_migrated": True},
        "llm": {"enabled": False, "model_task": "utils", "temperature": 0.2,
                 "max_tokens": 4096, "timeout_seconds": 600, "prompt": "p"},
    }

    async def scenario():
        await plugin._process_upstream_alert({"official_signal": _osig()}, ["100000001"], None)
        assert plugin._inflight  # 管线在途
        await plugin.on_config_update("self", new_config, "1")
        assert plugin._inflight == {}  # 旧管线已被取消并回收
        release.set()
        # 下一轮按新配置重新处理未 receipt 的 alert（llm disabled → 无 AI 块）
        await plugin._process_upstream_alert({"official_signal": _osig()}, ["100000001"], None)
        while plugin._inflight:
            await asyncio.sleep(0.01)
        # 悬空 task 检查：除 watcher 主循环外无遗留 pipeline task
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()
                   and t.get_name() != "codex-reset-watcher"
                   and "codex-alert" in (t.get_name() or "")]
        assert pending == []

    asyncio.run(asyncio.wait_for(scenario(), 5))
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1 and "中文翻译" not in bodies[0]
    asyncio.run(plugin.on_unload())


# ===== v0.1.8 二次修正：config-update orphan 竞态 + LLM 信息边界 =====

def test_llm_prompt_contains_no_internal_provider_terms(tmp_path):
    """信息边界：发给 ctx.llm.generate 的实际 prompt 中不得出现内部
    Provider/数据源 taxonomy（fxtwitter/vxtwitter/content_source/forecast）；
    v0.1.10：元数据必须携带 published_at / current_time / 目标时区，
    fixed contract 携带时间本地化与完整翻译规则。"""
    plugin = _llm_plugin(tmp_path)
    captured: dict = {}
    good = {"translation_zh": "翻译内容"}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, rpc_timeout_ms=None, **kwargs):
        captured["prompt"] = prompt
        return {"success": True, "response": json.dumps(good, ensure_ascii=False), "model_name": "m1", "total_tokens": 1}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], None  # noqa: SLF001
        )
    )
    assert "prompt" in captured
    prompt = captured["prompt"]
    assert isinstance(prompt, list)  # system + user 消息列表
    joined = "".join(str(m.get("content", "")) for m in prompt)
    assert "content_completeness: full" in joined  # 完整性标记必须携带
    assert "<SOURCE_TEXT>" in joined and "</SOURCE_TEXT>" in joined
    # v0.1.10 时间上下文：published_at 来自 official_signal.at；current_time
    # 由插件注入；目标时区显式给出（Contract 中有本地化规则）
    assert "published_at: 2026-09-07T19:24:57.000Z" in joined
    assert "current_time: " in joined
    assert "target_timezone: Asia/Shanghai" in joined
    assert "Asia/Shanghai" in CONTRACT_PROMPT
    assert "published_at" in CONTRACT_PROMPT and "current_time" in CONTRACT_PROMPT
    # 相对时间基准 / 不擅自补全 / 保留模糊词 / 不输出独立时间字段
    for keyword in (
        "相对时间",
        "不要单独输出时间字段",
        "不要擅自补北京时间、时区或具体日期",
        "around / approximately / ~",
    ):
        assert keyword in CONTRACT_PROMPT, keyword
    for term in ("fxtwitter", "vxtwitter", "content_source", "forecast", "api.fxtwitter.com"):
        assert term not in joined, term


def test_l4_published_at_falls_back_to_feed_tweet_at(tmp_path):
    """review-fix：official_signal.at 缺失但 feed.tweets[] 有同 id 推文
    的 at → LLM 元数据 published_at 回退 tweet.at。仅影响 enrichment
    元数据，不影响 L4 触发/去重/receipt。"""
    plugin = _llm_plugin(tmp_path)
    captured: dict = {}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, rpc_timeout_ms=None, **kwargs):
        captured["messages"] = prompt
        return {"success": True, "response": json.dumps({"translation_zh": "译"}, ensure_ascii=False), "model_name": "m1", "total_tokens": 1}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    feed = {"tweets": [{"id": "2097043464538264003", "at": "2026-09-07T19:00:00.000Z"}]}
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig(at="")}, ["100000001"], feed  # noqa: SLF001
        )
    )
    joined = "".join(str(m.get("content", "")) for m in captured["messages"])
    assert "published_at: 2026-09-07T19:00:00.000Z" in joined  # feed tweet.at 兜底
    # receipt 照常落盘（去重/receipt 语义不变）
    assert _gstate(plugin)["upstream_alert_keys"] == [
        "upstream-alert:signal:2097043464538264003:likely"
    ]


def test_l4_published_at_unknown_when_no_at_anywhere(tmp_path):
    """official_signal.at 与 feed 同 id 推文 at 均缺失 → published_at=unknown
    （不得编造）。"""
    plugin = _llm_plugin(tmp_path)
    captured: dict = {}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, rpc_timeout_ms=None, **kwargs):
        captured["messages"] = prompt
        return {"success": True, "response": json.dumps({"translation_zh": "译"}, ensure_ascii=False), "model_name": "m1", "total_tokens": 1}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    feed = {"tweets": [{"id": "2097043464538264003", "text": "x"}]}  # 无 at
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig(at="")}, ["100000001"], feed  # noqa: SLF001
        )
    )
    joined = "".join(str(m.get("content", "")) for m in captured["messages"])
    assert "published_at: unknown" in joined


def test_l4_published_at_prefers_official_signal_at(tmp_path):
    """official_signal.at 存在时不读 feed（osig.at 优先，行为与 v0.1.9 一致）。"""
    plugin = _llm_plugin(tmp_path)
    captured: dict = {}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, rpc_timeout_ms=None, **kwargs):
        captured["messages"] = prompt
        return {"success": True, "response": json.dumps({"translation_zh": "译"}, ensure_ascii=False), "model_name": "m1", "total_tokens": 1}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    feed = {"tweets": [{"id": "2097043464538264003", "at": "2026-09-07T19:00:00.000Z"}]}
    asyncio.run(
        plugin._process_upstream_alert(
            {"official_signal": _osig()}, ["100000001"], feed  # noqa: SLF001
        )
    )
    joined = "".join(str(m.get("content", "")) for m in captured["messages"])
    assert "published_at: 2026-09-07T19:24:57.000Z" in joined  # osig.at 优先


def test_config_update_race_no_orphan_alert_task(tmp_path):
    """竞态回归（二次 blocker）：config update 先停 watcher 再 cancel/
    gather；gather 窗口内被创建的新 alert B 不得被抹成 orphan。
    终态：无旧 A receipt、B 正常完成、inflight 清空、无未追踪
    codex-alert-* task、watcher 按新配置重启。"""
    plugin = _llm_plugin(tmp_path, group_id="", group_ids=["100000001"], llm_overrides={"enabled": False})
    release = asyncio.Event()
    provider_calls = {"n": 0}

    async def provider(url, timeout_seconds):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            try:
                await release.wait()
            except asyncio.CancelledError:
                # A 被取消的瞬间：竞态窗口内另一生产者创建 alert B
                osig_b = _osig(alert_event_id="signal:2097043464538264003:strong")
                asyncio.create_task(
                    plugin._process_upstream_alert({"official_signal": osig_b}, ["100000001"], None)
                )
                raise
        return _FX_GOLDEN

    plugin._get_json = provider  # type: ignore[method-assign]
    new_config = {
        "plugin": {"enabled": True, "config_version": "1.2.0"},
        "watcher": {"group_ids": ["100000001"], "group_ids_migrated": True},
        "llm": {"enabled": False, "model_task": "utils", "temperature": 0.2,
                 "max_tokens": 4096, "timeout_seconds": 600, "prompt": "p"},
    }

    async def scenario():
        await plugin._process_upstream_alert({"official_signal": _osig()}, ["100000001"], None)
        assert plugin._inflight  # A 在途
        await plugin.on_config_update("self", new_config, "1")  # 先停 watcher，再 cancel+gather
        # gather 窗口内创建的 B 已注册并在跑；等待其自然完成
        while plugin._inflight:
            await asyncio.sleep(0.01)
        # 无未追踪 codex-alert-* task（inflight 与真实 all_tasks 一致）
        orphans = [
            t for t in asyncio.all_tasks()
            if (t.get_name() or "").startswith("codex-alert-") and not t.done()
        ]
        assert orphans == []
        assert plugin._task is not None and not plugin._task.done()  # watcher 已按新配置重启

    asyncio.run(asyncio.wait_for(scenario(), 5))
    receipt = set(_gstate(plugin)["upstream_alert_keys"])
    assert "upstream-alert:signal:2097043464538264003:likely" not in receipt  # A 被取消：无 receipt
    assert "upstream-alert:signal:2097043464538264003:strong" in receipt  # B 正常完成
    assert plugin._inflight == {}
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1 and "中文翻译" not in bodies[0]  # 新配置 llm disabled


# ===== v0.1.9 L1 Confirmation Lane（per-group 四格决策）=====
# 黄金数据：confirmation_cycle_landed.json（09-08 两条真实 L1 的完整
# event/tweet JSON）。判别字段纯结构化：observation_result / source /
# observed_at + per-group upstream_alert_keys。

def _confirmation_feed() -> dict:
    """09-08 两条真实 L1 的 event+tweet（真实落地后归档形态）。"""
    data = json.loads(
        (Path(__file__).resolve().parent / "live/forensic_0908/replay/corpus/confirmation_cycle_landed.json")
        .read_text(encoding="utf-8")
    )
    events = [v["event"] for v in data.values()]
    tweets = [v["tweet"] for v in data.values() if v.get("tweet")]
    return {"events": events, "tweets": tweets}

L4_RECEIPTS = [
    "upstream-alert:signal:2097043464538264003:likely",
    "upstream-alert:signal:2097174560412246215:likely",
]


def test_golden_0908_l1_1_observed_becomes_short_confirm(tmp_path):
    """黄金回归（09-08 L1#1）：promise tweet 被 operator 改写为 observed/
    announced，同群已有对应 L4 receipt → 只发 B-confirm 短确认，
    含北京时间 9月8日 09:34；零 Provider、零 LLM。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = list(L4_RECEIPTS)  # 同群已有 L4
    _gstate(plugin)["upstream_alert_keys"] = list(L4_RECEIPTS)
    _gstate(plugin)["upstream_alert_tweet_ids"] = [
        "2097043464538264003",
        "2097174560412246215",
    ]
    calls = _wire_providers(plugin, fx=_FX_GOLDEN)
    feed = _confirmation_feed()
    asyncio.run(plugin._process_feed_signals(feed, ["100000001"], BEIJING))  # noqa: SLF001
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert bodies == ["✅ Codex 额度重置已确认生效" + N1 + "确认时间：北京时间 9月8日 09:34"]
    assert calls["fx"] == [] and calls["vx"] == []  # 零 Provider
    assert plugin._ctx.llm.generate_calls == []  # 零 LLM
    assert "global-declared:2097043464538264003" in _gstate(plugin)["notified_keys"]


def test_golden_0908_l1_2_confirmation_late_archive_is_silenced(tmp_path):
    """黄金回归（09-08 L1#2）：All reset for everyone 确认推文已被 L4+LLM
    通知（同群 receipt 在），feed 晚一步归档 → C-silence：0 QQ、0 Provider、
    0 LLM，并落 handled key 防重复判定。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = list(L4_RECEIPTS)
    _gstate(plugin)["upstream_alert_keys"] = list(L4_RECEIPTS)
    _gstate(plugin)["upstream_alert_tweet_ids"] = [
        "2097043464538264003",
        "2097174560412246215",
    ]
    calls = _wire_providers(plugin, fx=_FX_GOLDEN)
    feed = _confirmation_feed()
    feed["events"] = [
        e for e in feed["events"] if str(e["id"]) == "2097174560412246215"
    ]
    asyncio.run(plugin._process_feed_signals(feed, ["100000001"], BEIJING))  # noqa: SLF001
    assert plugin._ctx.send.sent_messages == []  # 0 QQ
    assert calls["fx"] == [] and calls["vx"] == []  # 0 Provider
    assert plugin._ctx.llm.generate_calls == []  # 0 LLM
    assert "global-declared:2097174560412246215" in _gstate(plugin)["notified_keys"]  # handled key


def test_mixed_groups_same_event_four_quadrant(tmp_path):
    """混合群（spec 6）：同一 observed event 与同一 confirmation event，
    群 A（已有 L4 receipt）→ B-confirm / C-silence；群 B（无 receipt）→
    A-primary（Content+LLM）。一个群的 receipt 不影响另一个群。"""
    plugin = _make_plugin(
        tmp_path, group_id="", group_ids=["100000001", "100000002"],
        llm_overrides={"enabled": True},
    )
    # 群 A 已有两条 L4 receipt（keys + 结构化 tweet receipt）；群 B 没有
    _gstate(plugin, "100000001")["upstream_alert_keys"] = list(L4_RECEIPTS)
    _gstate(plugin, "100000001")["upstream_alert_tweet_ids"] = [
        "2097043464538264003",
        "2097174560412246215",
    ]
    _gstate(plugin, "100000001")["feed_baseline_done"] = True
    _gstate(plugin, "100000002")["feed_baseline_done"] = True
    _gstate(plugin, "100000002")["notified_keys"] = []
    good = {"translation_zh": "完整中文翻译"}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, rpc_timeout_ms=None, **kwargs):
        return {"success": True, "response": json.dumps(good, ensure_ascii=False), "model_name": "m1", "total_tokens": 1}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    _wire_providers(plugin, fx=_FX_GOLDEN)
    asyncio.run(plugin._process_feed_signals(_confirmation_feed(), ["100000001", "100000002"], BEIJING))  # noqa: SLF001
    by_group: dict[str, list[str]] = {}
    for stream, body in plugin._ctx.send.sent_messages:
        by_group.setdefault(stream, []).append(body)
    # 群 A：observed → B-confirm（1 条）；confirmation → C-silence（0 条）
    assert len(by_group.get("qq-group-100000001", [])) == 1
    assert by_group["qq-group-100000001"][0].startswith("✅ Codex 额度重置已确认生效")
    # 群 B：两个事件都是 A-primary → 全量通知（含 enrichment 与中文翻译）；
    # observed → 语义标题，非 observed → 普通提醒标题
    assert len(by_group.get("qq-group-100000002", [])) == 2
    assert all("Tibo 原文" in b for b in by_group["qq-group-100000002"])
    assert all("中文翻译：\n完整中文翻译" in b for b in by_group["qq-group-100000002"])
    titles = sorted(b.split("\n", 1)[0] for b in by_group["qq-group-100000002"])
    assert titles == sorted([GLOBAL_CONFIRMED_TITLE, GLOBAL_NOTICE_TITLE])
    # receipt 独立：A 两个 key（1 发送 + 1 handled）；B 两个 key（2 发送）
    assert sorted(_gstate(plugin, "100000001")["notified_keys"]) == [
        "global-declared:2097043464538264003",
        "global-declared:2097174560412246215",
    ]
    assert sorted(_gstate(plugin, "100000002")["notified_keys"]) == [
        "global-declared:2097043464538264003",
        "global-declared:2097174560412246215",
    ]


def test_confirmation_primary_uses_provider_full_text(tmp_path):
    """A-primary 复用 Content Provider：B 群（无 L4 receipt）的 declared
    通知带 FxTwitter 完整原文（原文措辞）与原帖。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    _wire_providers(plugin, fx=_FX_GOLDEN)
    conf = _confirmation_feed()
    feed = {
        "events": [conf["events"][0]],
        "tweets": [conf["tweets"][0]],  # 显式 is_reply=False → reply 判定通过
    }
    asyncio.run(plugin._process_feed_signals(feed, ["100000001"], BEIJING))  # noqa: SLF001
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    # observed A-primary：语义标题（v0.1.10 无 emoji/确认时间行），该群从未
    # 收到此事件的通知；正文为统一结构 + provider 完整原文
    assert bodies[0].startswith(GLOBAL_CONFIRMED_TITLE)
    assert "确认时间" not in bodies[0]
    assert "Tibo 原文：" in bodies[0] and "Lands around 6pm PST today" in bodies[0]
    assert "Tibo 原文摘录" not in bodies[0]
    assert "已宣告" not in bodies[0]
    assert f"原帖：https://x.com/thsottiaux/status/2097043464538264003" in bodies[0]


def test_confirmation_short_message_omits_unparseable_observed_at(tmp_path):
    """observed_at 不可解析 → 省略时间行（只用可靠 observed_at）。"""
    from codex_reset_watcher.plugin import build_confirm_effective_message
    assert build_confirm_effective_message("not-a-date") == "✅ Codex 额度重置已确认生效"
    assert build_confirm_effective_message(None) == "✅ Codex 额度重置已确认生效"


# ===== v0.1.9 修正轮：同轮 defer / 接管 / duplicate 指纹负例 =====

def _event1_only_feed() -> dict:
    feed = _confirmation_feed()
    feed["events"] = [e for e in feed["events"] if str(e["id"]) == "2097043464538264003"]
    return feed


def test_same_round_l4_l1_defers_then_receipt_confirms(tmp_path):
    """同轮 same-tweet Feed+Forecast：L4 先行调度，L1 defer 不发送；
    receipt 落地后下一轮 → B-confirm 短确认。"""
    plugin = _llm_plugin(tmp_path, group_ids=["100000001"], llm_overrides={"enabled": False})
    _gstate(plugin)["feed_baseline_done"] = True  # 模拟已在运行的系统
    calls = _wire_providers(plugin, fx=_FX_GOLDEN)
    forecast = {"official_signal": _osig()}  # same tweet
    feed = _event1_only_feed()
    asyncio.run(plugin._process_feed_signals(feed, ["100000001"], BEIJING, forecast))  # noqa: SLF001
    assert plugin._ctx.send.sent_messages == []  # defer：0 发送
    assert calls["fx"] == []  # defer：0 Provider
    assert _gstate(plugin).get("notified_keys", []) == []  # defer 不写任何 key
    # 下一轮：L4 receipt 已落地（模拟 L4 发送成功）→ B-confirm 短确认
    _gstate(plugin)["upstream_alert_keys"] = [
        "upstream-alert:signal:2097043464538264003:likely"
    ]
    _gstate(plugin)["upstream_alert_tweet_ids"] = ["2097043464538264003"]
    asyncio.run(plugin._process_feed_signals(feed, ["100000001"], BEIJING, forecast))  # noqa: SLF001
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    assert bodies[0].startswith("✅ Codex 额度重置已确认生效")
    assert "确认时间：北京时间 9月8日 09:34" in bodies[0]


def test_defer_released_when_forecast_clears(tmp_path):
    """defer 后上游清除 official_signal 且仍无 receipt → L1 保守接管
    A-primary（完整通知），不会永久 defer。"""
    plugin = _llm_plugin(tmp_path, group_ids=["100000001"], llm_overrides={"enabled": False})
    _gstate(plugin)["feed_baseline_done"] = True  # 模拟已在运行的系统
    calls = _wire_providers(plugin, fx=None, vx=None)  # providers 全挂 → forecast 摘录兜底
    feed = _event1_only_feed()
    forecast = {"official_signal": _osig()}
    asyncio.run(plugin._process_feed_signals(feed, ["100000001"], BEIJING, forecast))  # noqa: SLF001
    assert plugin._ctx.send.sent_messages == []  # 第一轮 defer
    # 下一轮：official_signal 清除（forecast 不再暴露）→ A-primary 接管
    asyncio.run(plugin._process_feed_signals(feed, ["100000001"], BEIJING, None))  # noqa: SLF001
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    # observed A-primary → 语义标题接管；providers 全挂 → feed 摘录兜底
    assert bodies[0].startswith(GLOBAL_CONFIRMED_TITLE)
    assert "确认时间" not in bodies[0]
    assert "Tibo 原文摘录：" in bodies[0]
    assert "global-declared:2097043464538264003" in _gstate(plugin)["notified_keys"]


def test_per_group_defer_with_existing_receipt(tmp_path):
    """per-group defer：群 A 已有 L4 receipt（confirmation → C-silence），
    群 B 无 receipt → defer；osig 清除后 B 接管 A-primary；A 不重复。"""
    plugin = _llm_plugin(
        tmp_path, group_id="", group_ids=["100000001", "100000002"],
        llm_overrides={"enabled": False},
    )
    _gstate(plugin, "100000001")["upstream_alert_keys"] = [
        "upstream-alert:signal:2097174560412246215:likely"
    ]
    _gstate(plugin, "100000001")["upstream_alert_tweet_ids"] = ["2097174560412246215"]
    _gstate(plugin, "100000001")["feed_baseline_done"] = True
    _gstate(plugin, "100000002")["feed_baseline_done"] = True
    _wire_providers(plugin, fx=_FX_GOLDEN)
    feed = {"events": [e for e in _confirmation_feed()["events"] if str(e["id"]) == "2097174560412246215"]}
    feed["tweets"] = [t for t in _confirmation_feed()["tweets"] if str(t["id"]) == "2097174560412246215"]
    forecast = {"official_signal": _osig( tweet_id="2097174560412246215", alert_event_id="signal:2097174560412246215:likely")}
    asyncio.run(plugin._process_feed_signals(feed, ["100000001", "100000002"], BEIJING, forecast))  # noqa: SLF001
    by_group: dict[str, list[str]] = {}
    for stream, body in plugin._ctx.send.sent_messages:
        by_group.setdefault(stream, []).append(body)
    assert "qq-group-100000001" not in by_group  # A：C-silence
    assert "qq-group-100000002" not in by_group  # B：defer（osig 同 tweet 在）
    assert "global-declared:2097174560412246215" in _gstate(plugin, "100000001")["notified_keys"]
    assert _gstate(plugin, "100000002").get("notified_keys", []) == []  # defer 不写 key
    # osig 清除 → B 接管 A-primary；A 已 handled 不重复
    asyncio.run(plugin._process_feed_signals(feed, ["100000001", "100000002"], BEIJING, None))  # noqa: SLF001
    by_group2: dict[str, list[str]] = {}
    for stream, body in plugin._ctx.send.sent_messages:
        by_group2.setdefault(stream, []).append(body)
    assert len(by_group2.get("qq-group-100000002", [])) == 1  # B 接管
    assert len(by_group2.get("qq-group-100000001", [])) == 0  # A 不重复


def test_duplicate_fingerprint_missing_field_takes_over(tmp_path):
    """spec 7：duplicate 指纹任一字段缺失/不一致 → 不得 C-silence，
    A-primary 照常发送（防 schema 漂移造成静默漏报）。"""
    base_event = next(
        e for e in _confirmation_feed()["events"]
        if str(e["id"]) == "2097174560412246215"
    )
    base_tweet = next(
        t for t in _confirmation_feed()["tweets"]
        if str(t["id"]) == "2097174560412246215"
    )
    # (变体, 期望 observed)：source=operator-observed → observed=True →
    # 确认型 primary 文案；其余变体 observed=False → declaration 形态。
    variants = [
        ({"events": [{**base_event, "source": ""}], "tweets": [base_tweet]}, False),
        ({"events": [{**base_event, "source": "operator-observed"}], "tweets": [base_tweet]}, True),
        ({"events": [base_event], "tweets": [{**base_tweet, "explicit_reset_claim": False}]}, False),
        ({"events": [base_event], "tweets": [{**base_tweet, "explicit_reset_claim": None}]}, False),
        ({"events": [base_event], "tweets": [{**base_tweet, "at": "2026-09-08T04:05:54.000Z"}]}, False),
        ({"events": [base_event], "tweets": [{k: v for k, v in base_tweet.items() if k != "at"}]}, False),
    ]
    for i, (variant, expected_observed) in enumerate(variants):
        plugin = _make_plugin(tmp_path / f"case{i}")
        _gstate(plugin)["feed_baseline_done"] = True
        _gstate(plugin)["notified_keys"] = [
            "upstream-alert:signal:2097174560412246215:likely"
        ]
        _gstate(plugin)["upstream_alert_keys"] = [
            "upstream-alert:signal:2097174560412246215:likely"
        ]
        _wire_providers(plugin, fx=_FX_GOLDEN)

        async def run_variant():
            await plugin._process_feed_signals(variant, ["100000001"], BEIJING)  # noqa: SLF001
            while plugin._inflight:  # 等待 background 管线完成
                await asyncio.sleep(0.01)
            return [b for _, b in plugin._ctx.send.sent_messages]

        bodies = asyncio.run(asyncio.wait_for(run_variant(), 10))
        assert len(bodies) == 1, f"variant {i} 应 A-primary 发送"
        if expected_observed:
            # source=operator-observed → observed=True → 语义标题 primary
            assert bodies[0].startswith(GLOBAL_CONFIRMED_TITLE), f"variant {i}"

        else:
            # 非 observed declaration → 普通提醒标题（v0.1.10：无"已宣告"文案）
            assert bodies[0].startswith(GLOBAL_NOTICE_TITLE), f"variant {i}"
            assert "已宣告" not in bodies[0], f"variant {i}"


# ===== v0.1.9 修正轮：defer 契约门控 + 并发 receipt 注入回归 =====


def test_defer_contract_gating_invalid_delivery(tmp_path):
    """delivery != alerts → contract 不成立 → 不 defer，保守 A-primary。
    event1 是 observed → 确认型 primary（非 declaration）。"""
    plugin = _llm_plugin(tmp_path, group_ids=["100000001"], llm_overrides={"enabled": False})
    _wire_providers(plugin, fx=None, vx=None)
    _gstate(plugin)["feed_baseline_done"] = True  # 跳过 baseline，直达 dispatch
    forecast = {"official_signal": {**_osig(), "delivery_destination": "web"}}
    feed = _event1_only_feed()
    asyncio.run(plugin._process_feed_signals(feed, ["100000001"], BEIJING, forecast))  # noqa: SLF001
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1  # A-primary：未 defer
    assert bodies[0].startswith(GLOBAL_CONFIRMED_TITLE)


def test_defer_contract_gating_missing_alert_event_id(tmp_path):
    """alert_event_id 非空字符串要求不满足 → contract 不成立 → 不 defer。"""
    plugin = _llm_plugin(tmp_path, group_ids=["100000001"], llm_overrides={"enabled": False})
    _wire_providers(plugin, fx=None, vx=None)
    _gstate(plugin)["feed_baseline_done"] = True  # 跳过 baseline，直达 dispatch
    forecast = {"official_signal": {**_osig(), "alert_event_id": ""}}
    feed = _event1_only_feed()
    asyncio.run(plugin._process_feed_signals(feed, ["100000001"], BEIJING, forecast))  # noqa: SLF001
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1  # A-primary：未 defer
    assert bodies[0].startswith(GLOBAL_CONFIRMED_TITLE)


def test_defer_contract_gating_valid_contract_defers(tmp_path):
    """正向对照：三项契约成立 → defer（不发不写）。"""
    plugin = _llm_plugin(tmp_path, group_ids=["100000001"], llm_overrides={"enabled": False})
    _wire_providers(plugin, fx=None, vx=None)
    _gstate(plugin)["feed_baseline_done"] = True  # 跳过 baseline，直达 dispatch
    forecast = {"official_signal": _osig()}  # promise/likely/alerts 全齐
    feed = _event1_only_feed()
    asyncio.run(plugin._process_feed_signals(feed, ["100000001"], BEIJING, forecast))  # noqa: SLF001
    assert plugin._ctx.send.sent_messages == []  # defer
    assert _gstate(plugin).get("notified_keys", []) == []  # 不写 key


@pytest.mark.parametrize("event_id", ["2097043464538264003", "2097174560412246215"])
@pytest.mark.parametrize("mixed_groups", [False, True], ids=["single-group", "mixed-groups"])
def test_l1_midflight_receipt_reclassifies_before_send(tmp_path, event_id, mixed_groups):
    """Provider 确认挂起后注入 receipt：observed → B，duplicate → C；未注入群保持 A。"""
    groups = ["100000001", "100000002"] if mixed_groups else ["100000001"]
    plugin = _llm_plugin(
        tmp_path, group_id="", group_ids=groups, llm_overrides={"enabled": False},
    )
    signal = next(s for s in signals_from_feed(_confirmation_feed()) if s.event_id == event_id)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hanging_get(url, timeout_seconds):
            assert "api.fxtwitter.com" in url
            entered.set()
            await release.wait()
            return _FX_GOLDEN

        plugin._get_json = hanging_get
        task = asyncio.create_task(plugin._l1_primary_pipeline(signal, None, groups, BEIJING))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert not task.done()
            assert plugin._ctx.send.sent_messages == []
            assert all(not _gstate(plugin, g).get("upstream_alert_tweet_ids") for g in groups)
            async with plugin._state_lock:
                _gstate(plugin, groups[0])["upstream_alert_tweet_ids"] = [event_id]
                plugin._save_state()
            release.set()
            await asyncio.wait_for(task, 2)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    by_group = {g: [body for stream, body in plugin._ctx.send.sent_messages
                    if stream == f"qq-group-{g}"] for g in groups}
    if event_id == "2097043464538264003":
        assert by_group[groups[0]] == [plugin_module.build_confirm_effective_message(signal.observed_at)]
    else:
        assert by_group[groups[0]] == []
    if mixed_groups:
        assert len(by_group[groups[1]]) == 1
        assert "Tibo 原文：" in by_group[groups[1]][0]
        assert not _gstate(plugin, groups[1]).get("upstream_alert_tweet_ids")
    assert plugin._ctx.llm.generate_calls == []
    plugin._load_state()
    for group in groups:
        assert signal.key in _gstate(plugin, group)["notified_keys"]
    assert _gstate(plugin, groups[0])["upstream_alert_tweet_ids"] == [event_id]


@pytest.mark.parametrize("late_tweet_id", [False, True], ids=["upgrade-existing-key", "late-tweet-id"])
def test_l4_backfill_persists_silently_and_is_idempotent(tmp_path, late_tweet_id):
    """已有 alert key 的升级与同 alert 后补 tweet_id，均静默持久化且不重复写盘。"""
    plugin = _llm_plugin(tmp_path, llm_overrides={"enabled": True})
    calls = _wire_providers(plugin, fx=_FX_GOLDEN)
    osig = _osig()
    key = f"upstream-alert:{osig['alert_event_id']}"
    _gstate(plugin)["upstream_alert_keys"] = [key]
    _gstate(plugin)["upstream_alert_tweet_ids"] = ["older-receipt"]
    plugin._save_state()
    before = plugin._state_path().read_bytes()

    async def scenario():
        if late_tweet_id:
            await plugin._process_upstream_alert({"official_signal": {**osig, "tweet_id": None}}, ["100000001"])
            assert plugin._state_path().read_bytes() == before
            assert _gstate(plugin)["upstream_alert_tweet_ids"] == ["older-receipt"]
        await plugin._process_upstream_alert({"official_signal": osig}, ["100000001"])
        assert _gstate(plugin)["upstream_alert_tweet_ids"] == sorted(["older-receipt", osig["tweet_id"]])
        plugin._load_state()
        assert _gstate(plugin)["upstream_alert_tweet_ids"] == sorted(["older-receipt", osig["tweet_id"]])
        saved = plugin._state_path().read_bytes()
        def unexpected_save():
            pytest.fail("idempotent backfill must not write state again")
        plugin._save_state = unexpected_save
        await plugin._process_upstream_alert({"official_signal": osig}, ["100000001"])
        assert plugin._state_path().read_bytes() == saved

    asyncio.run(scenario())
    assert plugin._ctx.send.sent_messages == []
    assert calls == {"fx": [], "vx": []}
    assert plugin._ctx.llm.generate_calls == []
    assert plugin._inflight == {}
    assert _gstate(plugin)["upstream_alert_keys"] == [key]


@pytest.mark.parametrize("override", [
    {"tweet_id": None}, {"tweet_id": ""},
    {"delivery_destination": "web"}, {"alert_event_id": ""},
], ids=["no-tweet-id", "empty-tweet-id", "invalid-delivery", "no-alert-id"])
def test_l4_backfill_requires_explicit_tweet_id_and_valid_contract(tmp_path, override):
    """不从 alert ID 猜 tweet ID；无效契约不补写 receipt。"""
    plugin = _llm_plugin(tmp_path)
    calls = _wire_providers(plugin, fx=_FX_GOLDEN)
    _gstate(plugin)["upstream_alert_keys"] = [f"upstream-alert:{_osig()['alert_event_id']}"]
    plugin._save_state()
    before = plugin._state_path().read_bytes()
    asyncio.run(plugin._process_upstream_alert({"official_signal": _osig(**override)}, ["100000001"]))
    assert not _gstate(plugin).get("upstream_alert_tweet_ids")
    assert plugin._state_path().read_bytes() == before
    assert plugin._ctx.send.sent_messages == []
    assert calls == {"fx": [], "vx": []}
    assert plugin._ctx.llm.generate_calls == []
    assert plugin._inflight == {}


# ===== v0.1.10 Banked 接入全文 + translation-only LLM 后台管线 =====


def _banked_fresh_feed(
    *,
    tweet_text: str = "The banked reset has landed. Redeem on demand.",
    with_tweet: bool = True,
    with_summary: bool = True,
) -> dict:
    """合成新鲜 banked 事件（announced_at=现在，48h 护栏内）。"""
    tweet_id = "999909111111111111"
    event: dict = {
        "id": tweet_id,
        "type": "credits",
        "reset_kind": "banked",
        "banked_state": "announced",
        "url": f"https://x.com/thsottiaux/status/{tweet_id}",
        "announced_at": datetime.now(timezone.utc).isoformat(),
        "reason_tags": ["milestone"],
    }
    if with_summary:
        event["summary"] = "Banked reset summary fallback text."
    feed: dict = {"events": [event], "tweets": []}
    if with_tweet:
        feed["tweets"] = [
            {
                "id": tweet_id,
                "text": tweet_text,
                "at": datetime.now(timezone.utc).isoformat(),
                "is_reply": False,
            }
        ]
    return feed


def test_banked_signal_joins_tweet_at_for_published_at():
    """v0.1.10：banked 信号结构化 join 发帖时刻（tweet.at）作为 LLM 翻译的
    published_at 基准；declared 车道语义不变。"""
    feed = _banked_fresh_feed()
    signal = next(s for s in signals_from_feed(feed) if s.lane == "banked")
    assert signal.tweet_at == feed["tweets"][0]["at"]
    declared = next(
        s for s in signals_from_feed(_confirmation_feed()) if s.lane == "global_declared"
    )
    assert declared.tweet_at  # declared 车道原有行为不变


def test_banked_pipeline_full_text_and_translation(tmp_path):
    """banked 新信号经后台管线：Provider 全文 + LLM 翻译 → 统一结构通知
    （标题/中文翻译/原文/原帖），单次 provider 请求，receipt 正常落盘。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    good = {"translation_zh": "Banked reset 已落地，可按需兑换。"}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, **kwargs):
        return {"success": True, "response": json.dumps(good, ensure_ascii=False), "model_name": "m1", "total_tokens": 10}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    calls = _wire_providers(plugin, fx=_FX_GOLDEN)
    feed = _banked_fresh_feed()

    async def run_and_drain():
        await plugin._process_feed_signals(feed, ["100000001"], BEIJING)
        await _drain_inflight(plugin)

    asyncio.run(run_and_drain())
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    body = bodies[0]
    assert body.startswith(BANKED_NOTICE_TITLE)
    assert "中文翻译：\nBanked reset 已落地，可按需兑换。" in body
    # Provider 全文获胜（feed 推文只是 unknown 候选，fx full 立即采用）
    assert "Tibo 原文：\n" in body and "Lands around 6pm PST today" in body
    assert "Tibo 原文摘录" not in body
    assert "原帖：https://x.com/thsottiaux/status/999909111111111111" in body
    assert len(calls["fx"]) == 1 and calls["vx"] == []
    assert "banked:999909111111111111:announced" in _gstate(plugin)["notified_keys"]


def test_banked_pipeline_passes_published_at_to_llm(tmp_path):
    """banked 管线把 tweet.at 作为 published_at 传入 LLM 元数据。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    captured: dict = {}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, **kwargs):
        captured["messages"] = prompt
        return {"success": True, "response": json.dumps({"translation_zh": "译"}, ensure_ascii=False), "model_name": "m1", "total_tokens": 1}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    _wire_providers(plugin, fx=_FX_GOLDEN)
    feed = _banked_fresh_feed()

    async def run_and_drain():
        await plugin._process_feed_signals(feed, ["100000001"], BEIJING)
        await _drain_inflight(plugin)

    asyncio.run(run_and_drain())
    joined = "".join(str(m.get("content", "")) for m in captured["messages"])
    assert f"published_at: {feed['tweets'][0]['at']}" in joined
    assert "current_time: " in joined and "target_timezone: Asia/Shanghai" in joined


def test_banked_summary_only_sends_without_body_or_translation(tmp_path):
    """review-fix 回归：providers 全挂 + feed 无匹配推文 → 仅剩 event
    summary 可用时，summary 不展示、不交给 LLM；告警仍发标题+原帖，
    receipt 正常落盘（绝不漏报，alert decision 不变）。"""
    plugin = _llm_plugin(tmp_path, llm_overrides={"enabled": True})
    _wire_providers(plugin, fx=None, vx=None)  # providers 全挂（覆盖 _llm_plugin 默认）
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    feed = _banked_fresh_feed(with_tweet=False)  # feed 无匹配推文 → 仅剩 summary

    async def run_and_drain():
        await plugin._process_feed_signals(feed, ["100000001"], BEIJING)
        await _drain_inflight(plugin)

    asyncio.run(run_and_drain())
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 1
    body = bodies[0]
    assert body == (
        f"{BANKED_NOTICE_TITLE}\n\n原帖：https://x.com/thsottiaux/status/999909111111111111"
    )
    assert "Banked reset summary fallback text." not in body  # summary 不冒充原文
    assert "Tibo 原文" not in body and "中文翻译" not in body
    assert plugin._ctx.llm.generate_calls == []  # summary 不交给 LLM
    assert "banked:999909111111111111:announced" in _gstate(plugin)["notified_keys"]


def test_banked_all_text_sources_missing_still_sends(tmp_path):
    """providers/summary 全缺失 → 仅标题 + 原帖的极简 banked 通知照发。"""
    plugin = _llm_plugin(tmp_path, llm_overrides={"enabled": False})
    _wire_providers(plugin, fx=None, vx=None)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    feed = _banked_fresh_feed(with_tweet=False, with_summary=False)

    async def run_and_drain():
        await plugin._process_feed_signals(feed, ["100000001"], BEIJING)
        await _drain_inflight(plugin)

    asyncio.run(run_and_drain())
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert bodies == [
        BANKED_NOTICE_TITLE + "\n\n原帖：https://x.com/thsottiaux/status/999909111111111111"
    ]
    assert "banked:999909111111111111:announced" in _gstate(plugin)["notified_keys"]


def test_banked_multi_group_shared_enrichment(tmp_path):
    """同一 banked 事件多群：全文与 LLM 各只一次，两群复用同一内容。"""
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    for gid in ("100000001", "100000002"):
        _gstate(plugin, gid)["feed_baseline_done"] = True
    good = {"translation_zh": "共享翻译"}

    async def fake_llm(prompt, model="", temperature=None, max_tokens=None, **kwargs):
        return {"success": True, "response": json.dumps(good, ensure_ascii=False), "model_name": "m1", "total_tokens": 1}

    plugin.ctx.llm.generate = fake_llm  # type: ignore[method-assign]
    calls = _wire_providers(plugin, fx=_FX_GOLDEN)
    feed = _banked_fresh_feed()

    async def run_and_drain():
        await plugin._process_feed_signals(feed, ["100000001", "100000002"], BEIJING)
        await _drain_inflight(plugin)

    asyncio.run(run_and_drain())
    bodies = [b for _, b in plugin._ctx.send.sent_messages]
    assert len(bodies) == 2 and bodies[0] == bodies[1]
    assert "中文翻译：\n共享翻译" in bodies[0]
    assert len(calls["fx"]) == 1 and calls["vx"] == []
    for gid in ("100000001", "100000002"):
        assert "banked:999909111111111111:announced" in _gstate(plugin, gid)["notified_keys"]


def test_banked_send_failure_retries_only_failed_group(tmp_path):
    """A 成 / B 败 → 下一轮仅 B 补发；A 零重复；重试重新 enrich 属预期。"""
    plugin = _make_plugin(tmp_path, group_id="", group_ids=["100000001", "100000002"])
    for gid in ("100000001", "100000002"):
        _gstate(plugin, gid)["feed_baseline_done"] = True
    sent: list[str] = []
    fx_rounds = {"n": 0}

    async def flaky_send(group_id: str, message: str) -> bool:
        if group_id == "100000002" and fx_rounds["n"] == 1:
            return False  # 首轮 B 失败
        sent.append(group_id)
        return True

    plugin._send_group_text = flaky_send  # type: ignore[method-assign]

    async def counting_get(url: str, timeout_seconds: int):
        if "api.fxtwitter.com" in url:
            fx_rounds["n"] += 1
            return _FX_GOLDEN
        return None

    plugin._get_json = counting_get  # type: ignore[method-assign]
    feed = _banked_fresh_feed()

    async def round_and_drain():
        await plugin._process_feed_signals(feed, ["100000001", "100000002"], BEIJING)
        await _drain_inflight(plugin)

    asyncio.run(round_and_drain())
    assert sent == ["100000001"]
    asyncio.run(round_and_drain())
    assert sent == ["100000001", "100000002"]
    assert fx_rounds["n"] == 2  # 重试允许再次 fetch（与 L4 一致）
    assert "banked:999909111111111111:announced" in _gstate(plugin, "100000001")["notified_keys"]
    assert "banked:999909111111111111:announced" in _gstate(plugin, "100000002")["notified_keys"]


def test_banked_inflight_dedup_single_pipeline(tmp_path):
    """同一 banked 事件管线在途时，下一轮调度不得重复启动（单群单请求）。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    release = asyncio.Event()
    fx_calls = {"n": 0}

    async def hanging_get(url: str, timeout_seconds: int):
        if "api.fxtwitter.com" in url:
            fx_calls["n"] += 1
            await release.wait()
            return _FX_GOLDEN
        return None

    plugin._get_json = hanging_get  # type: ignore[method-assign]
    feed = _banked_fresh_feed()

    async def scenario():
        await plugin._process_feed_signals(feed, ["100000001"], BEIJING)
        assert list(plugin._inflight.keys()) == [
            "banked-primary:banked:999909111111111111:announced"
        ]
        await plugin._process_feed_signals(feed, ["100000001"], BEIJING)  # inflight 命中
        assert fx_calls["n"] == 0  # 未重复启动管线
        release.set()
        await _drain_inflight(plugin)

    asyncio.run(asyncio.wait_for(scenario(), 5))
    assert fx_calls["n"] == 1 and len(plugin._ctx.send.sent_messages) == 1


def test_banked_midflight_receipt_prevents_double_send(tmp_path):
    """send-time receipt 重查：管线在途时该群 receipt 已被记录 → 跳过发送
    且不重复落盘（防并发重复投递）。"""
    plugin = _make_plugin(tmp_path)
    _gstate(plugin)["feed_baseline_done"] = True
    _gstate(plugin)["notified_keys"] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hanging_get(url: str, timeout_seconds: int):
        if "api.fxtwitter.com" in url:
            entered.set()
            await release.wait()
            return _FX_GOLDEN
        return None

    plugin._get_json = hanging_get  # type: ignore[method-assign]
    feed = _banked_fresh_feed()
    key = "banked:999909111111111111:announced"

    async def scenario():
        await plugin._process_feed_signals(feed, ["100000001"], BEIJING)
        await asyncio.wait_for(entered.wait(), 2)
        # 管线等待期间 receipt 被并发写入（模拟另一路径先落盘）
        async with plugin._state_lock:
            _gstate(plugin)["notified_keys"] = sorted(
                set(_gstate(plugin)["notified_keys"]) | {key}
            )
            plugin._save_state()
        release.set()
        await _drain_inflight(plugin)

    asyncio.run(asyncio.wait_for(scenario(), 5))
    assert plugin._ctx.send.sent_messages == []  # send-time 重查命中：0 发送
    assert _gstate(plugin)["notified_keys"] == [key]
