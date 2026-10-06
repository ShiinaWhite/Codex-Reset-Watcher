"""Build the offline card font; development-only fonttools[woff] is required.

Inputs: Google Fonts Noto Sans SC / Noto Sans (see assets/ATTRIBUTION.md).
Coverage uses general Unicode ranges, never notification fixtures.
"""

import argparse
import hashlib
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


SOURCE_SHA256 = "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"
LATIN_SHA256 = "bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d"
LATIN_RANGES = (
    (0x0020, 0x024F),
    (0x0370, 0x052F),
    (0x1E00, 0x1EFF),
    (0x2000, 0x206F),
    (0x20A0, 0x20CF),
)
RANGES = (
    (0x0020, 0x024F),  # Latin, digits and punctuation.
    (0x0370, 0x052F),  # Greek/Cyrillic supported by the upstream font.
    (0x2000, 0x206F),  # General punctuation.
    (0x20A0, 0x20CF),  # Currency symbols.
    (0x2100, 0x27BF),  # Numbers, arrows, mathematics and symbols.
    (0x3000, 0x303F),  # CJK punctuation.
    (0x4E00, 0x9FFF),  # Entire basic CJK unified ideographs block.
    (0xFE10, 0xFE6F),  # Vertical/small punctuation variants.
    (0xFF00, 0xFFEF),  # Fullwidth/halfwidth characters.
)


def build(source, output, *, latin=False):
    expected = LATIN_SHA256 if latin else SOURCE_SHA256
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise ValueError("Unexpected upstream font; see assets/ATTRIBUTION.md")
    font = TTFont(source, recalcTimestamp=False)
    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.name_legacy = True
    selected = subset.Subsetter(options=options)
    ranges = LATIN_RANGES if latin else RANGES
    selected.populate(
        unicodes={cp for start, end in ranges for cp in range(start, end + 1)}
    )
    selected.subset(font)
    axes = {"wght": (400, 400, 600)}
    if latin:
        axes["wdth"] = 100
    font = instantiateVariableFont(font, axes, inplace=True)
    # A distinct derivative family; preserve upstream copyright/OFL metadata.
    names = {
        1: "Watcher Sans SC",
        2: "Regular",
        3: "Watcher Sans SC; "
        + ("Noto Sans Latin subset" if latin else "Noto Sans SC 2.004 subset"),
        4: "Watcher Sans SC",
        6: "WatcherSansSC-Latin" if latin else "WatcherSansSC-Regular",
        16: "Watcher Sans SC",
        17: "Regular",
    }
    for record in font["name"].names:
        if record.nameID in names:
            record.string = names[record.nameID].encode(record.getEncoding())
    font.flavor = "woff2"
    output.parent.mkdir(parents=True, exist_ok=True)
    font.save(output)
    print(
        f"{output}: {output.stat().st_size} bytes; {len(font.getBestCmap())} codepoints"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--latin", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
    )
    args = parser.parse_args()
    output = args.output or Path(
        "templates/assets/watcher-sans-latin.woff2"
        if args.latin
        else "templates/assets/watcher-sans-sc.woff2"
    )
    build(args.source, output, latin=args.latin)
