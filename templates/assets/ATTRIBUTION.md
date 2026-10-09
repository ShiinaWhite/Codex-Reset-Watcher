`tibo.jpg` is Tibo's public profile portrait, bundled for an offline notification
card so rendering does not depend on remote image access. It identifies the
source author; the generated card is a local notification, not a captured X page.

Author profile: https://x.com/thsottiaux

The portrait was retrieved on 2026-10-05 via
https://unavatar.io/twitter/thsottiaux and visually checked against the supplied
reference. Profile metadata was checked through FxTwitter's public status API.

## Offline card typography

The CSS family `Watcher Sans SC` combines locally bundled variable WOFF2 assets:

| Asset | Upstream | Bytes | Weights |
| --- | --- | ---: | --- |
| `watcher-sans-sc.woff2` (GB2312 core) | Noto Sans SC 2.004 | 1,793,360 | 400–600 |
| 21 `watcher-sans-sc-*.woff2` supplemental blocks | Same pinned SC font | 4,279,124 total | 400–600 |
| `watcher-sans-latin.woff2` | Noto Sans 2.015 | 95,056 | 400–600 |

Bundled fonts total **6,167,540 bytes**. This is 667,644 bytes larger than the
old monolithic 5,499,896-byte bundle because separate subsets retain shared
components. Each typical render embeds only the 1,793,360-byte core and the
95,056-byte Latin font, plus any supplemental blocks required by displayed text.
The tradeoff preserves deterministic glyphs while reducing typical RPC payload.
Full upstream TTFs (17,772,300 + 2,049,096 bytes) are not shipped.

Both fonts use **SIL Open Font License 1.1**. The original copyright and license
are bundled as `OFL.txt` (SC, Adobe) and `OFL-Latin.txt` (Latin, Noto Project
Authors); only trailing whitespace is normalized. The derived primary family is
renamed `Watcher Sans SC`, without the SC font's reserved name `Source`.
Copyright and OFL metadata remain embedded in the font files.

Pinned SC source:
[Google Fonts](https://github.com/google/fonts/blob/a85815a42757630ce188fdad368c2dfc444d4773/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf),
[license](https://github.com/google/fonts/blob/a85815a42757630ce188fdad368c2dfc444d4773/ofl/notosanssc/OFL.txt).
Source SHA256: `a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`.

Pinned Latin source:
[Google Fonts](https://github.com/google/fonts/blob/8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5/ofl/notosans/NotoSans%5Bwdth,wght%5D.ttf),
[license](https://github.com/google/fonts/blob/8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5/ofl/notosans/OFL.txt).
Source SHA256: `bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d`.

The union of SC partitions preserves the baseline general Unicode ranges: U+0020–024F, U+0370–052F,
U+2000–206F, U+20A0–20CF, U+2100–27BF, U+3000–303F, U+4E00–9FFF,
U+FE10–FE6F and U+FF00–FFEF. It preserves every upstream-supported character
in those ranges: 22,342 codepoints, including **20,976 basic Han characters and
all 6,763 GB2312 Han characters**. It is not based on the preview fixtures.
Unusual CJK extension characters, emoji and other scripts may still use system
fallbacks.

The Latin subset retains 1,352 supported codepoints in U+0020–024F,
U+0370–052F, U+1E00–1EFF, U+2000–206F and U+20A0–20CF, with width fixed at
100. A second `@font-face` with these `unicode-range` values selects the Latin
font within the same CSS family. This avoids SC's wide English curly quotes
while keeping Chinese, English, numerals and footer typography consistent.
Selected fonts are embedded as data URLs; rendering needs no CDN or network. The core contains every GB2312 Han character and the non-Han baseline repertoire; remaining Han are partitioned by generic 1,024-codepoint Unicode blocks. `font-index.json` records the exact disjoint coverage. No characters are chosen from a notification fixture. The union is unchanged, including traditional/rare basic Han. File URIs are intentionally not used.

Rebuild with development-only FontTools 4.66.1 / Brotli 1.2.0:

```bash
python -m pip install 'fonttools[woff]==4.66.1' 'brotli==1.2.0'
# Rebuild the pinned full baseline into a temporary file, then partition it.
python tools/build_card_font.py '/path/to/NotoSansSC[wght].ttf' --output /tmp/watcher-baseline.woff2
python tools/build_card_font.py /tmp/watcher-baseline.woff2 --partition
python tools/build_card_font.py '/path/to/NotoSans[wdth,wght].ttf' --latin
```

The build tool verifies the pinned input SHA256 and preserves the source font
timestamp. FontTools/Brotli are not plugin runtime dependencies.

The full baseline SHA256 is `598f5ad49eb1b5246840df70a46c6a1c83346b2b8fdd9ef797785e3130cbe471`; the partitioner checks it before generating any assets.
