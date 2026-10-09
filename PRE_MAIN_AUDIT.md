# PRE_MAIN_AUDIT — 0.2.0 candidate

审计日期：2026-10-09（UTC+8）。起点为 `fa09454985ee4dde04e01f2acc7b6340166c2abf`。
工程实现与安装树测量快照为 **`ece7ef3bb818a6d94de614f7522d56e99f412940`**；本报告作为随后单独的文档提交，
其自身字节不包含在该快照的 clone 数字内。最终 review SHA 及最终克隆的 verifier
结果另随交付报告给出。不存在 release-only 源码、过滤安装器或新 tag。

## 结论与范围

候选完成 Host contract 收口、保留全字形的字体按需传输、安装树清理及 0.2.0 文档。
工程快照 `ece7ef3` 的 `plugin.py` 除版本 docstring 外 AST 与 fa0945 相同。
后续兼容修复仅将 LLM wrapper 改为直接 capability 调用，保留共用总预算；
不改变 Watcher 判定、Tibo ordering、
quote/poll enrichment、delivery identity、receipt、retry、state 或配置 schema。
`config_version=1.3.0`；默认 text、image 可选；全推送默认关闭。

生产 live 没有部署或 reload，生产 plugin/config/state 前后只读 SHA256 一致，
Core/NapCat 容器身份与启动时间不变。没有真实 QQ 发送或人工告警。
本候选未 merge main、未建 tag、未发 Release、未操作 Marketplace。

冷启动已在等价的独立非 Docker Host 环境完成；隔离 Docker 的 warm loader/render、
fallback 和 file-font 实验也通过。服务器独立 Docker 的无缓存下载仍在后台进行，
**不把尚未结束的 Docker 冷下载写成通过**。其任务/证据位置见下文。

## A1. 正式版本与 API 合同

### 所需下限与已核实上限

| API/行为 | Host | SDK | 依据 |
| --- | --- | --- | --- |
| `render.html2png` | 首个公开 prerelease 1.0.0-pre.1；首个稳定 1.0.0 | 2.3.0 | Host render 引入 commit、正式 tag 与 SDK 历史 |
| `render_timeout_ms` 参数 | 稳定 1.0.0 提供相应能力 | 2.5.3 | SDK 变更与 Host render 实现 |
| `send.image` 基础代理 | 新 IPC Host 的早期发行已有 | 2.0.0 | SDK v2.0.0 源码；不能据此推导本插件下限 |
| 本插件 `send.image(return_details=True)` | 首个稳定 1.2.0 | 2.8.0 | Host/SDK 分别引入返回详细结果 |
| 官方 Host 与 SDK 成套安装下限 | **1.2.4** | lockfile **2.8.0** | 1.2.0/1.2.3 尚锁旧 SDK；1.2.4 首次锁 2.8.0 |
| 当前核实正式上限 | **1.3.5** | **2.10.0** | 正式 GitHub Release 与 API 源码、端点合同 smoke |

manifest 调整为 **Host 1.2.4–1.3.5 / SDK 2.8.0–2.10.0**，替换不准确的
Host 1.0.0–1.2.99 与没有验证依据的 SDK 2.99.99 上限。
这不是依据 main 中显示的版本号推断。1.3.5 Release 于 2026-10-07T06:21:48Z 发布，
为 stable，tag 对应 `ef223606208cb24bce68751692c94e8e591e92e9`。
1.2.4 tag 对应 `21cd74d81d47b6f77ba5ab7116a88916e957d15b`。
这两者的 `src/services/html_render_service.py` 与
`src/plugin_runtime/capabilities/render.py` **原始 Git blob 字节相同**。
manifest validator 虽有显示/验证增强，版本范围的比较合同继续保留。
validator 对同 minor 的未来 patch 可能仅警告；这不等于我们承诺未来版本已验证。

**LLM 兼容修复：** Watcher 直接调用 `ctx.call_capability("llm.generate", ...)`，
仅发送 `prompt`、`model=cfg.model_task`、`temperature`、`max_tokens`，不发送
`task_name`。RPC 使用 `timeout_ms`，与首次翻译、可选一次修复共用原有 monotonic
deadline；并发等待也消耗该预算。无需排除 SDK 2.8.1。

实际 SDK wheel 与正式 Host Git blob 的矩阵回归结果：

| Host | SDK | 原生 Host 请求 `task_name` | 原生 Host 请求 `model_name` |
| --- | --- | --- | --- |
| 1.2.4 | 2.8.0 | `replyer` | `None` |
| 1.2.5 | 2.8.1 | `replyer` | `None` |
| 1.2.5 | 2.8.2 | `replyer` | `None` |
| 1.3.5 | 2.10.0 | `replyer` | `None` |

测试执行实际 SDK 的 `PluginContext.call_capability`、正式 Host 的
`RuntimeCoreCapabilityMixin._cap_llm_generate`、任务 resolver 和 `LLMServiceRequest`，
仅隔离配置/日志与最终外部模型调用，不启动 Bot 或访问模型服务。具体模型名设为
`actual-reply-model`，与任务名 `replyer` 区分，禁止按具体模型 `replyer` 假通过。
SDK 2.8.1 的旧 wrapper 反向对照也复现了 `task_name="utils"`、
`model_name="replyer"` 的错误路由。此矩阵证明 SDK/Host 参数合同，不等同于外部模型实发。

可重复运行的入口为 `test_llm_contract.py`。真实 SDK wire/预算回归默认执行；
正式 Host 矩阵需要单独的官方 MaiBot clone，未设置环境时明确 skip，不把 skip 当通过：

```bash
CRW_HOST_GIT_ROOT=/path/to/official-MaiBot-clone \
CRW_EXPECT_SDK=2.8.1 \
python -m pytest -v test_llm_contract.py
```

该 clone 需包含 1.2.4、1.2.5、1.3.5 的正式提交，Python 环境安装对应实际 SDK。
来源及 SHA256 记录于 `tests/fixtures/llm-host-origins.json`，测试读取并校验 Git blobs；
上游 Core 源码、SDK wheels 和原始日志仅保存在仓库外的验收目录，未混入发布树。

可抽查的主来源：

- [render 引入 commit 4ec06ece](https://github.com/Mai-with-u/MaiBot/commit/4ec06ece560df33afd8c6dad6d66618aec446354)
- [1.0.0-pre.1](https://github.com/Mai-with-u/MaiBot/releases/tag/1.0.0-pre.1)、[首个稳定 1.0.0](https://github.com/Mai-with-u/MaiBot/releases/tag/1.0.0)
- [Host send details 引入 7a2bdc26](https://github.com/Mai-with-u/MaiBot/commit/7a2bdc26a34984d67c48df6f39f0bd290eae1ed3)
- [SDK send details 引入 4d8e577d](https://github.com/Mai-with-u/maibot-plugin-sdk/commit/4d8e577d409fc7886e4a9f19dd94bea829081e4f)
- [Host 1.2.4 lockfile](https://github.com/Mai-with-u/MaiBot/blob/1.2.4/uv.lock)、[正式 1.3.5](https://github.com/Mai-with-u/MaiBot/releases/tag/1.3.5)、[1.3.5 lockfile](https://github.com/Mai-with-u/MaiBot/blob/1.3.5/uv.lock)
- [SDK 2.8.2 changelog](https://github.com/Mai-with-u/maibot-plugin-sdk/blob/v2.8.2/CHANGELOG.md)
- [1.2.4 manifest validator](https://github.com/Mai-with-u/MaiBot/blob/1.2.4/src/plugin_runtime/runner/manifest_validator.py)、[1.3.5 validator](https://github.com/Mai-with-u/MaiBot/blob/1.3.5/src/plugin_runtime/runner/manifest_validator.py)

### Playwright/Chromium 的归属与安装

Playwright 是 **Host runtime dependency**（`pyproject.toml`、requirements 与 uv.lock），
插件不新增 Python/browser runtime dependency。核实的稳定 Host 1.2.4 和 1.3.5
均锁 Playwright **1.58.0**，声明要求 `>=1.54.0`。
官方 Docker 用 Python 3.13-slim，安装运行依赖并运行
`python -m playwright install-deps chromium`，安装 Chromium system libs。
1.2.4 的 `uv sync --no-dev --no-install-project` 尚无 `--locked`；1.2.5 及之后使用
`uv sync --locked --no-dev --no-install-project`。不能假定更旧构建必定按锁版本。
Docker **没有预烘焙 Chromium binary**，不会因 Python package 存在就自动有 browser。

Host 浏览器来源依次考虑 CDP/显式 executable/已探测本机浏览器/managed Chromium。
当 managed launch 报 executable 缺失、没有采用系统 executable 且
`auto_download_chromium=true` 时，Host 调用自己 Python 的
`-m playwright install chromium`。缓存使用 `browser_install_root`，默认为
`data/playwright-browsers`。`allow_network=false` 限制页面资源，不禁止 Host 安装下载。
`download_connection_timeout_sec` 设置下载连接预算，**不是整个安装的总期限**；
Host 当前 await installer communicate，没有给整个下载再套一个总完成期限。

非 Docker 使用同一服务逻辑，管理员需提供 Python 依赖、匹配操作系统的 Playwright
及 system libs。实际 Ubuntu 26.04 + Playwright 1.58 的自动下载返回
`Playwright does not support chromium on ubuntu26.04-x64`；Playwright 1.63 + 独立
NSPR/NSS 库环境完成冷启动。没有修改 MaiBot Core 或替插件新增依赖来规避。

源代码：
[1.3.5 Dockerfile](https://github.com/Mai-with-u/MaiBot/blob/1.3.5/Dockerfile)、
[1.2.4 Dockerfile](https://github.com/Mai-with-u/MaiBot/blob/1.2.4/Dockerfile)、
[Host renderer](https://github.com/Mai-with-u/MaiBot/blob/1.2.4/src/services/html_render_service.py)、
[Host runtime dependency](https://github.com/Mai-with-u/MaiBot/blob/1.3.5/pyproject.toml)。

### 加载、权限与降级边界

| 条件 | Host 行为 | 已加载 Watcher image 行为 |
| --- | --- | --- |
| manifest 无效 / 版本拒绝 | PluginLoader 拒绝加载 | 插件尚未运行，无法 fallback |
| capability 名已声明但 Host 无实现 | 调用返回 `E_METHOD_NOT_ALLOWED` | 原文字通知 |
| capability 未授予 | 调用返回 `E_CAPABILITY_DENIED` | 原文字通知 |
| `plugin_runtime.render.enabled=false` | render invocation 失败 | 原文字通知 |
| 缺 Playwright | 服务导入/启动失败 | 原文字通知 |
| browser 缺失且禁止下载 | launch 失败 | 原文字通知 |
| 下载失败 / 截图失败 / RPC / 超时 | 调用失败或无有效 PNG | 原文字通知 |
| 某群发图失败 | 本群发送失败 | 仅本群文字回退 |

manifest validator 规范化 capability 名称，**没有检查 Host 实现注册表**；不是
“缺 render 必定拒绝加载”。授权和实现存在性在 cap.call 时判断。成功截图不双发；
文字 fallback 保留来源链接；receipt/dedup 仍在原投递成功边界处理。

[CapabilityService](https://github.com/Mai-with-u/MaiBot/blob/1.2.4/src/plugin_runtime/host/capability_service.py)、
[AuthorizationManager](https://github.com/Mai-with-u/MaiBot/blob/1.2.4/src/plugin_runtime/host/authorization.py)、
[render capability](https://github.com/Mai-with-u/MaiBot/blob/1.2.4/src/plugin_runtime/capabilities/render.py)。

## A2. 干净 Host 与合同实测

`tools/check_host_render.py` 是开发 smoke。只加载候选，不调用 on_load，不启动
Watcher 轮询，不读写正式 state，不持有 QQ 发送能力；文本/图片边界用 `offline-only`
本地 recorder。使用 **实际 SDK PluginContext → Host AuthorizationManager / CapabilityService
→ 原样 renderer / 原样 PluginLoader / manifest validator**。请求/响应经实际
MsgPackCodec encode/decode，并测量 request bytes；未声称跑完整 Core/Supervisor IPC。
日志/config manager 的边界隔离到审计目录，所有 Host 源码保持正式 tag 字节。

| 环境 / 来源 | Loader | render_ms | PNG | 降级 / file experiment |
| --- | --- | ---: | --- | --- |
| 官方 1.2.4 源码 + SDK 2.8.0 + Playwright 1.63；**空 managed cache** | 成功 | **41,052**（含自动下载） | 2480×892，83,185 bytes | 8 项 smoke 全通过 |
| 同组合，warm executable | 成功 | 989 | 2480×892，83,185 bytes | 8 项全通过 |
| 官方 1.3.5 源码 + SDK 2.10.0 + Playwright 1.63，warm | 成功 | 868 | 2480×892，83,185 bytes | 8 项全通过 |
| 隔离 Debian Docker + SDK 2.8.0 / Playwright 1.58；官方 1.2.4 源码、只读 browser cache | 成功 | 2,186 | 2480×892，81,187 bytes | 8 项全通过；file font 失败 |

8 项分别为 image、text、render-disabled、capability-denied、capability-unregistered、
browser-missing、playwright-missing、download-failed。后两项用 isolated import hook /
安装函数故障注入，不破坏真实 Host；关闭 render 与缺 browser 是实际配置/环境路径。
每例只出现一次本地发送，文字内容与原 message（含链接）完全相同。

冷启动 cache 初始不存在，确实由 Host 拉取 Chromium、headless-shell、FFmpeg；
没有复制开发机或生产 browser 充当冷下载。41 秒首次调用超过 Watcher 现有 20 秒
图片准备预算，因此常规通知首次可能 fallback；该 cold call 专门直接测试 Host，
不改插件 timeout。用户应提前准备浏览器。

Docker 使用现有 Python3.13-slim/Host 依赖 image 层，在**独立容器**中导入官方源码；
不是重新构建并宣称是官方发布镜像。没有挂载正式插件/config/state/QQ，唯一共享
browser cache 是只读 warm 验证，Host 使用记录写到审计根目录。

服务器另一个无缓存 Docker 测试已经由 Host 启动它的 installer，但网络缓慢；报告
收口时尚未完成。容器 `crw-pre-main-clean-host`，后台记录在
`/srv/maibot/deployment-evidence/codex-reset.watcher/pre-main-fa0945-clean-host/process.json`
及 `clean-host.log`，完成后应先读取 `out/clean-host-results.json` 的 `success`、实际
PNG 与 browser 安装记录。不要同时在同一缓存启动第二个 installer，不靠估计时间
宣称成功。**Docker cold download 尚无成功结果是独立待补证据；等价 clean environment 已通过。**

## A3. 精确 inventory 与 IPC

下表字节均来自 raw Git blobs；clone 为真正 `--depth 1 --branch dev file://...`。
候选 clone HEAD 核对 `ece7ef3bb818a6d94de614f7522d56e99f412940`、无 dirty files、所有 blob 原始字节匹配。
Marketplace 当前浅 clone **安装全部 tracked tree**，不使用 export-ignore/`.gitignore`
当包过滤器。[官方 release installer](https://github.com/Mai-with-u/MaiBot/blob/1.3.5/src/webui/routers/plugin/release_install.py)。

| 项目 | fa0945 before | 工程测量快照 after |
| --- | ---: | ---: |
| tracked blob bytes | 8,947,261 | 7,853,361 |
| tracked files | 115 | 84 |
| runtime tree（plugin / notice / manifest / LICENSE / templates） | 5,721,262 | 6,459,562 |
| templates runtime assets | 5,534,893 | 6,271,439 |
| WOFF2 合计 | 5,499,896 | 6,167,540 |
| shallow clone allocated disk | 17,022,976 | 15,511,552 |
| 其中 `.git` allocated | 7,704,576 | 7,356,416 |
| shallow clone apparent bytes | 16,514,214 | 15,095,713 |
| 其中 `.git` apparent | 7,566,953 | 7,223,165 |

一级目录分布（root 包含 root-level tests）：

| 目录 | before bytes | after bytes |
| --- | ---: | ---: |
| (root) | 579,475 | 524,488 |
| templates | 5,534,893 | 6,271,439 |
| docs | 1,822,575 | 811,584 |
| tests | 9,852 | 220,469 |
| tools | 9,814 | 25,381 |
| live | 421,692 | 0 |
| evidence | 568,960 | 0 |

按维护职责重新分类：root-level tests 为 316,178 → 319,981 bytes，tests/fixtures
为 9,852 → 220,469 bytes，完整测试树合计 **326,030 → 540,450 bytes**。
root `probe*.json` 为 **61,859 → 0 bytes**（被引用内容已计入新的 fixtures）。
这些分类与一级目录表有重叠，不能相加后再声称是总大小。

raw artifacts 清理后约少 1.09 MB tracked bytes；`.git` 并不会完全消失。保留完整测试，
没有为了几十/几百 KB 去掉 delivery/order 回归。runtime / font bytes **增加**，下面
明确交代这是换取全覆盖与显著较小每次 payload 的成本，不掩饰成字体总包瘦身。

### 典型请求、压力请求及 PNG

四项同一 Chromium、同一机器的 3 次 warm set_content/fonts.ready/screenshot，中位数
为本地 render_ms，**不是实际 Host RPC 耗时或 QQ 发送耗时**。before 与 after 每项
PNG SHA256 完全相同，四张图片逐像素一致。

| fixture | before HTML bytes | after HTML bytes | local median ms | PNG dimensions | PNG bytes |
| --- | ---: | ---: | ---: | --- | ---: |
| tweet-quote | 7,387,439 | 2,572,132 | 869.94 → 405.64 | 2480×1748 | 364,697 |
| tweet | 7,361,669 | 2,546,362 | 897.18 → 352.08 | 2480×892 | 83,299 |
| system | 7,337,095 | 2,521,788 | 781.46 → 292.81 | 2480×896 | 89,911 |
| tweet-quote-poll | 7,387,499 | 2,572,192 | 833.81 → 337.80 | 2480×1312 | 181,744 |

baseline 字体 5,499,896 bytes → base64 **7,333,200 bytes**；常见卡选字体
1,888,416 bytes → base64 **2,517,892 bytes**。HTML 减少约 65%，data URL 膨胀仍为
约 4/3，未以字符数代替 UTF-8 byte count。

| case | HTML bytes | 真实 Host MsgPack request bytes | 距 16 MiB frame 上限 |
| --- | ---: | ---: | ---: |
| 常见 Tweet | 2,546,362 | 2,546,756 | 14,230,460 |
| 160 段 long Chromium fixture | 2,556,519（before 7,371,826） | 2,556,913 | 14,220,303 |
| 故意显示全部 22,342 SC codepoints | 8,322,365 | 8,322,759 | 8,454,457 |

全字形压力卡加载所有 23 font faces，字体 base64 **8,223,412 bytes**；这是 font
selection 的最坏资产量，**不是任意无限正文的大小上限**。MsgPack envelope 实测增加
394 bytes。frame limit 是 **16,777,216 bytes**（不是十进制 16 MB），来自
[Host transport](https://github.com/Mai-with-u/MaiBot/blob/1.3.5/src/plugin_runtime/transport/base.py)。
六个 Chromium fixture 中最大 long PNG 为 **3,811,883 bytes**（base64 5,082,512），
常规预览最大 PNG 为 364,697 bytes。响应也要占 IPC，不能只算请求；当前这些实测
样例均有足够余量，但不声称无限长文本/任意大图永远不会超限。超限/失败沿用文字回退。

## A4. 字体候选与选型

保留 Noto Sans SC 2.004 + Noto Sans 2.015，SIL OFL1.1 允许随插件再分发；
本地 WOFF2、固定源 SHA256 与生成工具，不引入 CDN 或系统中文字体依赖。
来源/许可证/rename/构建方法见 [ATTRIBUTION.md](templates/assets/ATTRIBUTION.md)。

| SC 候选 | bytes | codepoints | Han | glyphs | 六个 Chromium fixture |
| --- | ---: | ---: | ---: | ---: | --- |
| 原 20,976 Basic Han baseline | 5,404,840 | 22,342 | 20,976 | 22,357 | 6/6 |
| 完整 GB2312 + 所有 baseline 非 Han | 1,793,360 | 8,129 | 6,763 | 8,145 | 3/6；实际 custom-font 字形断言失败 |
| GB2312 + U+4E00–65FF 中 baseline 支持 Han | 2,714,092 | 11,940 | 10,574 | 11,955 | 3/6；实际 custom-font 字形断言失败 |
| **采用：GB2312 core + 通用 supplement blocks** | **6,072,484** | **并集 22,342** | **并集 20,976** | 各分片共享组件，不能加总成并集 | **6/6** |

GB2312 独立包缺 `镕喆囧龘`；扩展候选仍缺 `镕龘`。在本机这些字符可能 fallback
到 YaHei，不一定显示方框，**不能把“本机没方框”当 deterministic Chinese coverage**。
候选否决基于实际 glyph source/cmap，而不是截图眼测。

历史公开 Provider/feed/accepted bilingual poll snapshot 的中文样本仅 6 个 text fields、
26 个 unique Han，三候选均覆盖；常用/传统/罕见基本汉字探针及 Chromium fixture
指出缩小 coverage 的风险。成功生产 LLM 日志只记译文长度，**没有完整历史 LLM 输出
corpus 可用**；fixture 中中文为手工演示译文，不冒充生产 LLM 语料。因此不凭小样本
给 GB2312 单包作无损保证，也没有基于 fixture 加几个字符过拟合。

采用的 core 包含完整 6,763 GB2312 汉字与所有原有非 Han。其余 14,213 Han 按
通用 1,024 Unicode codepoint 块生成 **21 份**补充 WOFF2（合计 4,279,124 bytes）。
`font-index.json` 保存互不相交的实际覆盖；按标题、主帖原文/译文、quote 正文/作者/
译文、poll option 原文/译文选择需要的块。所有原有基本汉字被保留，包括传统/罕见字。
Latin 95,056 bytes / 1,352 codepoints 不变；English/digits/punctuation 路径不变。
emoji 与非 baseline 的 CJK extension 仍走原 system fallback，没有额外保真承诺。

正文 400 与标题 600 的实际字体、字重、离线 glyph source 在 Chromium 测试中验证；
现有四预览在两套 assets 间像素相同。FontTools/Brotli 仅开发构建用，运行插件无需它们。
使用 FontTools4.66.1 / Brotli1.2.0 重建 22 SC 分片与 index，**全部字节匹配**。

代价：WOFF2 bundle 比 baseline **增加 667,644 bytes（12.1%）**，因为组件在分片内
重复。选择这个代价以保住全部 baseline 中文并让常见请求减少约 65%；安装树总量仍
因清理而减少。后续若要改 coverage/使用更大包，不应绕过门禁或重新依赖系统字体。

## A5. 本地 file font 实验 — 否决

在 actual Host renderer 的 page.set_content 与 allow_network=false 路由下，使用
含空格、中文和 `#` 的 Marketplace 风格安装路径，并用 `Path.as_uri()` 正确转义。
独立 non-Docker 与 Debian Docker 都得到 `document.fonts` status=`error`，console
为 `Not allowed to load local resource`。route layer 允许 file scheme，但 about:blank
页面 origin 阻止访问本地字体；无需猜测网络白名单是否放行。

因此 **不采用 file://**，无需进一步依赖 reload 后路径、OS 安装位置或浏览器安全 flag。
测试复制的安装目录同时证明 sibling import 及 data URL 与这类路径无耦合。
不修改 Host 上限、不关闭 Chromium 安全、不通过 permanent sys.path 解决。

## B. 清理、保留与未来门禁

删除前扫描 tests/README/docs/tools 的引用。15 份仍被回归使用的 JSON 迁入
`tests/fixtures/historical/`，**原始 bytes 不变**，ORIGINS.json 标注 fa0945 来源及逐个
SHA256；测试检查 SHA。测试只改 fixture 根路径，没有去掉语义断言。
未继续依赖历史源码 clone 的旧目录；fresh install 的 tests 可以完整执行。

原 tracked audit/artifacts 在清理前归档到仓库外，不进当前 clone：
`/home/dev/acceptance-evidence/codex-reset.watcher/pre-main-fa0945/baseline-artifacts.tar.gz`。
原有 ignored private 内容没有删除或写入 release tree；Git 历史仍保留此前 tracked 内容。

删除且未原样迁移的文件（58 份）：

- `docs/dev-tibo-image.md`
- `docs/forensics/2026-09-30/REPORT.md`
- `docs/images/qq-notification-full-translation.png`
- `docs/preview/before-typography/system.png`
- `docs/preview/before-typography/tweet-quote.png`
- `docs/preview/before-typography/tweet.png`
- `docs/preview/typography.md`
- `evidence/v0.1.12/REPORT.md`
- `evidence/v0.1.12/app.js`
- `evidence/v0.1.12/banked-evidence.js`
- `evidence/v0.1.12/banked-state.js`
- `evidence/v0.1.12/banked.html`
- `evidence/v0.1.12/feed.json`
- `evidence/v0.1.12/forecast.json`
- `evidence/v0.1.12/health.json`
- `evidence/v0.1.12/historical_inventory.json`
- `evidence/v0.1.12/home-radar.js`
- `evidence/v0.1.12/home.html`
- `evidence/v0.1.12/home.js`
- `evidence/v0.1.12/initial_fetch_failures.json`
- `evidence/v0.1.12/push.js`
- `evidence/v0.1.12/push_latest.json`
- `evidence/v0.1.12/release-notes.html`
- `evidence/v0.1.12/replay.json`
- `evidence/v0.1.12/replay.py`
- `evidence/v0.1.12/reset_current.json`
- `evidence/v0.1.12/source_manifest.json`
- `evidence/v0.1.12/status-page.js`
- `evidence/v0.1.12/sw.js`
- `evidence/v0.1.12/telegram_recovery_blocker/.gitattributes`
- `evidence/v0.1.12/telegram_recovery_blocker/REPORT.md`
- `evidence/v0.1.12/telegram_recovery_blocker/REPORT.original.md`
- `evidence/v0.1.12/telegram_recovery_blocker/process_environment.json`
- `evidence/v0.1.12/telegram_recovery_blocker/process_environment_probe.reconstructed.py`
- `evidence/v0.1.12/telegram_recovery_blocker/round1.jsonl`
- `evidence/v0.1.12/telegram_recovery_blocker/round2.jsonl`
- `evidence/v0.1.12/telegram_recovery_blocker/runtime_probe.py`
- `evidence/v0.1.12/telegram_recovery_blocker/runtime_probe_round2.py`
- `evidence/v0.1.12/telegram_recovery_blocker/source_manifest.json`
- `evidence/v0.1.12/telegram_recovery_blocker/verify_evidence.py`
- `evidence/v0.1.12/tibo_reset_current.json`
- `evidence/v0.1.12/timeline.json`
- `evidence/v0.1.12/validation.json`
- `live/forensic_0908/replay/corpus/feed_0905.json`
- `live/forensic_0908/replay/corpus/feed_0908.json`
- `live/forensic_0908/replay/corpus/forecast_0908.json`
- `live/forensic_0908/replay/corpus/forecast_landed.json`
- `live/forensic_0908/replay/corpus/forecast_snapshot_series.json`
- `live/forensic_0908/replay/corpus/telegram_channel_log.md`
- `live/forensic_0908/replay/corpus/tibo_current_0908.json`
- `live/forensic_0908/replay/corpus/tibo_landed.json`
- `live/forensic_0908/replay/corpus/timeline_landed.json`
- `live/forensic_0908/replay/replay_offline.py`
- `live/forensic_0908/replay/replay_output.txt`
- `live/forensic_0908/replay/simulate_confirmation_policy.py`
- `live/forensic_0908/replay/simulate_policy_output.txt`
- `live/forensic_0908/replay/simulate_v019_output.txt`
- `live/forensic_0908/tibo_current_0908.json`

迁移/保留的历史测试快照（15 份，206,629 bytes）：

- `tests/fixtures/historical/docs/forensics/2026-09-30/events.json`
- `tests/fixtures/historical/docs/forensics/2026-09-30/push_notification.json`
- `tests/fixtures/historical/evidence/v0.1.12/golden_2097752790177370535.json`
- `tests/fixtures/historical/evidence/v0.1.12/push_notification.json`
- `tests/fixtures/historical/live/feed_0905.json`
- `tests/fixtures/historical/live/forecast_0905.json`
- `tests/fixtures/historical/live/forensic_0908/forecast_0908.json`
- `tests/fixtures/historical/live/forensic_0908/providers/fxtwitter_banked_nonnote.json`
- `tests/fixtures/historical/live/forensic_0908/providers/fxtwitter_status.json`
- `tests/fixtures/historical/live/forensic_0908/providers/vxtwitter_status.json`
- `tests/fixtures/historical/live/forensic_0908/replay/corpus/confirmation_cycle_landed.json`
- `tests/fixtures/historical/live/forensic_0908/replay/corpus/feed_landed.json`
- `tests/fixtures/historical/probe2_tibo_current.json`
- `tests/fixtures/historical/probe_cr_feed.json`
- `tests/fixtures/historical/probe_tibo_ev1.json`

继续保留：全部 Python 回归测试、真实 quoted-poll/ordering schema fixture；
README 的 77,041-byte 文字效果图及四张 current preview（Tweet/quote/poll/system）；
字体 OFL/ATTRIBUTION/构建工具；quoted-poll 与 ordering 合同文档。
删除 before-typography、重复的 full-translation screenshot、过时 dev-only writeup。

`tools/verify_release.py` 仍验证 clean HEAD、每个 tracked file 与 Git blob 原始字节、
测试顶层重复定义，并新增：

- root `live/evidence/private/test_env`、root `probe*`、docs/forensics、before-typography 禁入。
- config.toml / reset_state.json / .env / .pyc / .log / .jsonl / .ttf 禁入。
- tracked **8 MiB**、runtime **7 MiB**、fonts **6 MiB**、单个 WOFF2 **2 MiB** 门禁。
- 常见 HTML **<3 MiB**、全字形压力 HTML **<9 MiB**；输出精确体积与 16 MiB headroom。
- 新增门禁回归、fixture 字节保真、完整字体覆盖与按字段分片选择回归。

门禁针对实际浅 clone 的完整 tree，不靠另一个 release-only 分支或安装过滤规则。
仍是同一份 main-ready 源码；tests 约数百 KB 有真实维护价值，予以保留。

## C. 0.2.0 收口

manifest 和 README 当前版本为 **0.2.0**；Tibo、image、quote、poll、ordering 都以
正式功能描述，不以“dev 新功能”说明。文档区分 text default / image optional，
明确 Host 浏览器管理、预备环境、调用失败 fallback 和加载失败不能 fallback。
[CHANGELOG.md](CHANGELOG.md) 为 0.2.0 候选 notes；config_version **1.3.0** 不变。
README 仍链接精简效果图、许可和普通用户配置方法。

## Verification 与复现

LLM 兼容修复后的验证：SDK 2.8.0 全量 **432 passed / 1 skipped**（11.85s），
SDK 2.8.1 全量 **433 passed**（11.88s），SDK 2.10.0 全量 **432 passed / 1 skipped**
（12.08s）。skip 仅为要求实际 SDK 2.8.1 的旧 wrapper 反向对照；正式 Host 矩阵
均设置外部源码路径执行，没有跳过。四组独立矩阵分别为 **4 / 5 / 4 / 4 passed**。
Ordering/LLM/预算 targeted 为 **56 passed / 1 skipped**，真实 Chromium 六种卡片
为 **6 passed**；Ruff、format、diff check 均通过。AST 归一化核对确认：除 docstring
外，`plugin.py` 相对 `24b9b6c` 仅改变这一处 LLM 入口及 RPC timeout 参数名。
模拟首次翻译用去 120 秒后，修复 RPC 只使用约 480 秒，而非重新获得 600 秒。

下表为前述 `ece7ef3` 工程快照的历史验证记录：

| 验证 | 结果 |
| --- | --- |
| full pytest，SDK2.8.0 | **426 passed**（11.69s；包括全部六个 Chromium fixture） |
| full pytest，实际 PyPI SDK2.10.0 | **426 passed**（12.57s） |
| ordering targeted + isolated package loader | **30 passed**（29 ordering + 1 loader，1.16s） |
| 原样 official Host1.2.4 + SDK2.8 / Host1.3.5 + SDK2.10 contract smoke | 全通过；各 8 项 |
| 等价 clean Host managed-download / screenshot | 通过，41,052ms，空缓存实证 |
| 隔离 Docker warm Host loader/render/fallback/file-font | 通过；非完整重建官方镜像 |
| 字体源覆盖、分片按字段选择及构建重现 | 全通过，重建 assets 字节相同 |
| Ruff check / format | 全通过，16 files formatted |
| git diff --check | 通过 |
| enhanced verify_release / clean shallow install tree | 已在工程快照通过，全部 blob 相同 |
| fresh shallow install 的 text/image/render-unavailable smoke | Host 合同工具在该 clone 上通过 |
| server Docker cold installer | **后台未完成；不计为 passed** |

raw evidence、JSON、日志和实验截图均在仓库外：
`/home/dev/acceptance-evidence/codex-reset.watcher/pre-main-fa0945/`。
重要文件：`before-inventory.json`、`candidate-inventory.json`、`font-benchmark.json`、
`corpus-coverage.json`、`before-after-render.json`、`rpc-size-measurement.json`、
`cold-sdk-host124/results.json`、`sdk-host-contract-135/results.json`、
`docker-contract-results.json`、`candidate-shallow-verify.json`、`marketplace-contract/results.json`。
Host Docker 证据另在 `/srv/maibot/deployment-evidence/codex-reset.watcher/pre-main-fa0945-docker-contract/`。

隔离合同重现（Host checkout 必须取正式 tag，Host Python deps/system libs 已准备）：

```bash
python tools/check_host_render.py --host-root /path/to/official-host-1.2.4 \
  --host-version 1.2.4 --plugin-root /path/to/fresh-candidate-clone \
  --output /tmp/new-empty-watcher-audit
# warm 复现可加 --browser-executable /absolute/path/to/chromium
python tools/verify_release.py <immutable-sha> --root /path/to/fresh-candidate-clone
```

cold call 可能数十秒或更久，应后台运行并记录 log/PID。检查 results.json 的 success
和 PNG，再执行依赖步骤；不以预计下载时间推断成功。源码、config/state、群列表
与 send decision 均不得为了这些实验修改。Tibo 头像授权与外部服务列表按本轮范围
不作为 blocker；本报告没有替它们作授权结论。
