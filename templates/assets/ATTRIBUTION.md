`tibo.jpg` is Tibo's public profile portrait, bundled for an offline notification
card so rendering does not depend on remote image access. It identifies the
source author; the generated card is a local notification, not a captured X page.

Author profile: https://x.com/thsottiaux

The portrait was retrieved on 2026-10-05 via
https://unavatar.io/twitter/thsottiaux and visually checked against the supplied
reference. Profile metadata was checked through FxTwitter's public status API.

## Offline card typography

The CSS family `Watcher Sans SC` combines two local variable WOFF2 subsets:

| Asset | Upstream | Bytes | Weights |
| --- | --- | ---: | --- |
| `watcher-sans-sc.woff2` | Noto Sans SC 2.004 | 5,404,840 | 400–600 |
| `watcher-sans-latin.woff2` | Noto Sans 2.015 | 95,056 | 400–600 |

Total: **5,499,896 bytes (5.245 MiB)**. The previous Latin-only TTF was 556,328
bytes; the net font asset increase is 4,943,568 bytes. Full upstream TTFs are
17,772,300 + 2,049,096 bytes and are not distributed with the plugin. For the same
Chinese subset, separate static 400/600 WOFF2 files measured 3,080,620 + 3,142,884
bytes, larger than the selected variable file.

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

SC coverage uses general Unicode ranges: U+0020–024F, U+0370–052F,
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
Both files are embedded as data URLs; rendering needs no CDN or network.

Rebuild with development-only FontTools 4.66.1 / Brotli 1.2.0:

```bash
python -m pip install 'fonttools[woff]==4.66.1' 'brotli==1.2.0'
python tools/build_card_font.py '/path/to/NotoSansSC[wght].ttf'
python tools/build_card_font.py '/path/to/NotoSans[wdth,wght].ttf' --latin
```

The build tool verifies the pinned input SHA256 and preserves the source font
timestamp. FontTools/Brotli are not plugin runtime dependencies.
