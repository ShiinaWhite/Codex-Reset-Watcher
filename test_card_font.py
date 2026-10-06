"""Optional asset audit; requires development-only fonttools[woff]."""

from pathlib import Path

import pytest


ttlib = pytest.importorskip("fontTools.ttLib")


def test_offline_font_covers_general_notification_text():
    path = Path(__file__).with_name("templates") / "assets/watcher-sans-sc.woff2"
    with ttlib.TTFont(path) as font:
        cmap = font.getBestCmap()
        # All 6,763 GB2312 Han characters, not a list extracted from previews.
        common_han = set()
        for row in range(0xB0, 0xF8):
            for column in range(0xA1, 0xFF):
                try:
                    common_han.add(ord(bytes([row, column]).decode("gb2312")))
                except UnicodeDecodeError:
                    pass
        assert len(common_han) == 6763
        assert common_han <= cmap.keys()
        # Also retain the broader basic Han repertoire and common UI symbols.
        assert len(set(cmap) & set(range(0x4E00, 0xA000))) >= 20900
        sample = "镕喆囧龘銀行額度“”‘’—…。，！？：；（）【】《》￥¥€$%+−=→←✓"
        assert {ord(c) for c in sample} <= cmap.keys()
        assert set(range(0x20, 0x7F)) <= cmap.keys()
        assert font.flavor == "woff2"
        axis = font["fvar"].axes
        assert len(axis) == 1 and axis[0].axisTag == "wght"
        assert (axis[0].minValue, axis[0].maxValue) == (400, 600)
        assert font["name"].getDebugName(1) == "Watcher Sans SC"
        assert "Open Font License" in font["name"].getDebugName(13)
    with ttlib.TTFont(path.with_name("watcher-sans-latin.woff2")) as latin:
        assert set(range(0x20, 0x7F)) <= latin.getBestCmap().keys()
        assert {ord(c) for c in "‘’“”—…€"} <= latin.getBestCmap().keys()
        assert latin["name"].getDebugName(1) == "Watcher Sans SC"
        assert latin["name"].getDebugName(6) == "WatcherSansSC-Latin"
        axis = latin["fvar"].axes
        assert len(axis) == 1 and axis[0].axisTag == "wght"
        assert (axis[0].minValue, axis[0].maxValue) == (400, 600)
        assert "Open Font License" in latin["name"].getDebugName(13)
