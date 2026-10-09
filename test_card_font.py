"""Optional deterministic offline-font audit; needs development-only FontTools."""

import json
from pathlib import Path

import pytest

from notice_card import NoticeCard, build_card_html
from datetime import datetime, timezone


ttlib = pytest.importorskip("fontTools.ttLib")
ASSETS = Path(__file__).with_name("templates") / "assets"


def test_offline_fonts_preserve_baseline_coverage_and_weights():
    index = json.loads((ASSETS / "font-index.json").read_text(encoding="utf-8"))
    coverage = set()
    for entry in index:
        path = ASSETS / entry["file"]
        assert path.stat().st_size == entry["bytes"]
        with ttlib.TTFont(path) as font:
            cmap = set(font.getBestCmap())
            assert cmap == {ord(c) for c in entry["codepoints"]}
            assert not cmap & coverage
            coverage |= cmap
            assert font.flavor == "woff2"
            axes = font["fvar"].axes
            assert len(axes) == 1 and axes[0].axisTag == "wght"
            assert (axes[0].minValue, axes[0].maxValue) == (400, 600)
            assert font["name"].getDebugName(1) == "Watcher Sans SC"
            assert "Open Font License" in font["name"].getDebugName(13)
    assert len(coverage) == 22342
    assert len(coverage & set(range(0x4E00, 0xA000))) == 20976
    sample = "镕喆囧龘銀行額度“”‘’—…。，！？：；（）【】《》￥¥€$%+−=→←✓"
    assert {ord(c) for c in sample} <= coverage
    assert set(range(0x20, 0x7F)) <= coverage
    common = set()
    for row in range(0xB0, 0xF8):
        for column in range(0xA1, 0xFF):
            try:
                common.add(ord(bytes([row, column]).decode("gb2312")))
            except UnicodeDecodeError:
                pass
    assert len(common) == 6763
    assert common <= {ord(c) for c in index[0]["codepoints"]}
    with ttlib.TTFont(ASSETS / "watcher-sans-latin.woff2") as latin:
        assert set(range(0x20, 0x7F)) <= latin.getBestCmap().keys()
        assert {ord(c) for c in "‘’“”—…€"} <= latin.getBestCmap().keys()
        assert latin["name"].getDebugName(1) == "Watcher Sans SC"
        assert latin["name"].getDebugName(6) == "WatcherSansSC-Latin"
        axes = latin["fvar"].axes
        assert len(axes) == 1 and axes[0].axisTag == "wght"
        assert (axes[0].minValue, axes[0].maxValue) == (400, 600)
        assert "Open Font License" in latin["name"].getDebugName(13)


def test_supplements_are_selected_for_every_displayed_content_field():
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    plain = NoticeCard("Tibo 动态", "Hello", "中文")
    html = build_card_html(plain, now)
    assert html.count("data:font/woff2;base64,") == 2  # Core + unchanged Latin.
    assert len(html.encode()) < 3 * 1024 * 1024
    for card in [
        NoticeCard("龘", "Hello"),
        NoticeCard("Tibo 动态", "Hello", "龘"),
        NoticeCard("Tibo 动态", "Hello", tweet=True, quote={"text": "龘"}),
        NoticeCard(
            "Tibo 动态",
            "Hello",
            tweet=True,
            quote={"text": "Hi", "author": {"name": "龘"}},
        ),
        NoticeCard(
            "Tibo 动态",
            "Hello",
            tweet=True,
            quote={"text": "Hi"},
            quote_translation="龘",
        ),
        NoticeCard(
            "Tibo 动态",
            "Hello",
            tweet=True,
            quote={
                "poll": {
                    "options": [
                        {"label": "龘", "percentage": 50},
                        {"label": "Hi", "translation": "龘", "percentage": 50},
                    ]
                }
            },
        ),
    ]:
        value = build_card_html(card, now)
        assert value.count("data:font/woff2;base64,") == 3
        assert "file://" not in value
        assert "unicode-range: U+9E00-" in value
