# 文字与图片通知

`display_mode="text"` 是默认值；`image` 是可选的展示模式，适用于全部通知车道。
它不改变告警资格、delivery identity、receipt、state 或逐群重试。

## Host 管理浏览器

插件调用 `NoticeCard → ctx.render.html2png → ctx.send.image`。Playwright Python
package 属于 MaiBot Host 的运行依赖；插件不安装 Python 包、浏览器或系统库。
页面只使用本地打包资产，以 data URL 传给 Host，`allow_network=false`。

受支持组合为 MaiBot Host **1.2.4–1.3.5**、SDK **2.8.0–2.10.0**。
`render.html2png` 的首个稳定 Host 是 1.0.0，SDK 代理从 2.3.0 提供；本插件
使用的 `send.image(return_details=True)` 需要 SDK 2.8.0、Host 1.2.0。
1.2.4 是官方 lockfile 首次供应 SDK 2.8.0 的稳定 Host。
这些下限不能仅用“渲染 API 已存在”替代。具体正式 tag/commit 证据见
[PRE_MAIN_AUDIT.md](../PRE_MAIN_AUDIT.md)。

SDK **2.8.1** 存在已知的默认模型任务解析回归，可能让翻译失败；应使用
2.8.0 或 **2.8.2 及以上**，推荐后者。manifest 的闭区间无法表达这个版本空洞。
不宣称范围内每一种 Host/SDK 组合都经过完整运行测试，也不预先承诺未来版本。

官方 1.2.5–1.3.5 Docker 使用 `uv sync --locked --no-dev --no-install-project`
安装 Playwright；1.2.4 使用 `uv sync --no-dev --no-install-project`。均在构建时执行
`python -m playwright install-deps chromium` 安装系统库；没有预装浏览器 binary。
Host 默认先检查 CDP/配置路径/系统浏览器，再尝试 managed Chromium。
managed browser 缺失且开启 `auto_download_chromium` 时，由 Host 在
`plugin_runtime.render.browser_install_root` 下载。默认目录是
`data/playwright-browsers`。这次下载属于 Host 环境准备，页面禁止联网并不会阻止它。

非 Docker 环境也走同一 Host 逻辑，但系统库需要管理员安装。先完成 MaiBot
官方运行依赖安装；在 Host 的 Python 环境中安装 Chromium 所需系统库，例如
Linux 的 `python -m playwright install-deps chromium`。Windows/macOS 使用各自的
Playwright 或已有浏览器环境。不要把开发机器 `.venv` 中的浏览器复制成插件依赖。

还需核对 Host 的 Playwright 与操作系统发行版是否匹配。审计中 Playwright 1.58
在 Ubuntu 26.04 自动安装 Chromium 明确失败（不支持该平台）；Playwright 1.63
配合对应系统库则完成了同一 Host 合同的冷启动。Host 版本范围并不保证任意
操作系统/Playwright 组合；环境准备失败不会阻止已加载插件回退文字。

首次下载可能超过插件的发送准备预算，首条通知可能因此回退文字。建议在启用
图片模式前准备 Host 浏览器。若有下载进程仍在运行，先确认它的完成状态，
不要并行启动第二个安装器，也不要修改插件 timeout 掩盖下载或缺库问题。
Host 的 `download_connection_timeout_sec` 是下载连接预算，不是整个安装的完成
时间保证。慢速下载可能远超这个数值；应保留后台日志，确认安装成功后再启用图片。

## 失败与回退

`plugin_runtime.render.enabled=false`、缺 Playwright、缺可启动 browser、下载失败、
RPC 失败或截图结果无效，都进入现有文字回退。某个群发图失败，也只向该群
回退文字。发图成功不追加文字；文字回退成功后记录正常 receipt，不再补图。
两种发送都失败则不记成功 receipt，由该群后续重试。

manifest/版本不兼容会在加载时拒绝插件；这时插件尚未运行，不能执行 fallback。
capability 未授权或 Host 未注册实现则在调用时失败，可以回退文字。
图片模式的来源 URL 不独立显示；正文原本含有的网址保留。文字与文字回退
继续包含来源链接。

## 模板和字体

- 有明确 Tibo 原帖来源：Tweet 模板，中文整块在英文之上。
- 无 Tibo 原帖来源：系统/上游模板，不冒充 Tibo。
- 有引用正文或结构化 poll：显示引用块。无引用正文时隐藏残缺区块。
- 短 poll 标题显示 `Vote（投票）`；选项显示 `👌 good day（美好的一天）`，
  下一行展示比例条和一位小数百分比。快照不随 QQ 消息后续更新；不猜 viewer 选择。

Noto Sans SC 与 Noto Sans 保持同一 `Watcher Sans SC` CSS 家族。标题/姓名为
600，正文/账号/时间为 400；布局、颜色、间距均保持既有设计。
SC 被划分为 GB2312 核心和通用 Unicode 补充块。每次只内嵌核心与实际显示文字
所需的补充块，覆盖并集仍为原有 **22,342 codepoints / 20,976 基本汉字**。
字形不会根据演示 fixture 增删；原有 emoji/扩展文字仍由系统 fallback 负责。
详见 [字体许可与构建](../templates/assets/ATTRIBUTION.md)。

拒绝用 `file://` 代替 data URL：Host 的 `page.set_content` 页面可能处于
`about:blank`，浏览器会拒绝读取本地字体，即使 Host 路由允许 file scheme。
不通过关掉浏览器安全检查或固定安装路径绕过。

## 离线开发验证

开发工具需要开发环境里的 pytest、Ruff、Playwright/Chromium、FontTools/Brotli；
这些都不列为插件依赖。

```bash
python -m pytest -q
python -m pytest -q test_tibo_ordering.py test_card_browser.py
python tools/preview_cards.py --output /tmp/watcher-previews
ruff check .
ruff format --check .
git diff --check
python tools/verify_release.py <immutable-commit>
```

验证器对真正的 tracked clone tree 检查文件字节、卫生与大小，并检查常见/全字形
HTML 请求。`git clone --depth 1 --branch <candidate>` 会安装全部 tracked 文件；
`.gitignore` 和 `export-ignore` 不是 Marketplace 安装过滤器。

独立 Host 合同 smoke 使用官方 tag checkout 和 Host 的 Python 依赖，不启动
Core/Watcher 轮询。它复制候选运行资产到指定的新目录，QQ 边界只记录到本地，
通过真实 SDK、Host 授权/能力实现和 MsgPack 编解码验证 loader/render/fallback：

```bash
python tools/check_host_render.py --host-root /path/to/official-MaiBot-tag \
  --host-version 1.2.4 --plugin-root /path/to/candidate-clone \
  --output /tmp/new-watcher-host-audit
```

默认空缓存会由 Host 下载 Chromium；下载较慢时应后台执行，保留进程及日志。
已有浏览器的 warm smoke 可另加 `--browser-executable /absolute/path/to/chromium`，
必须与冷启动结果区分。工具只写审计输出目录，不连接生产配置、state 或 QQ。
