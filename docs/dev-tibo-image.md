# dev：Tibo 动态全推送 / HTML 图片通知

初始功能迭代起点：本地与远端 main / dev 均为
`90174ae7d59ab75d7a6fe6e6c5f826063ac7ccd6`，工作区干净，无需同步。
只在 dev 实现并推送；未 merge main、未部署、未修改 Marketplace、tag 或 Release。

上一轮 review fix 基于 dev 的 `f2e86c487e277d96f3e54880f6c0bb9f28902e2a`，
仅修复 self-reply 过滤、quote 独立 enrichment、图片隐藏 URL。字体文件和
整体视觉样式保持不变；CSS 只删除不再使用的 `.source`。

此前最小 review fix 基于 `c8d96bdf07127405de53a92b9e8dcd02ff07879e`，
取消正文和译文的 URL 正则清洗，保留原文中的网址及其后紧跟的中文标点和正文。
独立来源 URL 仍不展示；字体、布局、颜色及文字回退保持不变。

本轮 typography 调整基于 `bedba78af09e87fa8196aa1a6936b81f919cc281`。
只更新本地字体资产、字体 data URL 和 CSS 字族/字重；不改 HTML 模板结构、
卡片宽度/左右栏、字号、行高、间距、颜色、通知逻辑或配置。

## 路由与状态

- 默认 `watcher.tibo_full_push=false`、`watcher.display_mode="text"`。
- 使用现有 `/api/feed` 的 `tweets[]`，要求 `profile.handle=thsottiaux`、
  `source_scope=timeline`、非 stale。不把 reset classifier 的筛选当作动态资格。
- 主要动态包括普通帖、长帖、有评论的 quote 和明确回复自己的续帖。结合
  `is_reply`、`replying_to`、回复 ID 与 `referenced_tweets[type=replied_to]`
  判断回复关系；回复对象规范化大小写和可选前导 `@`。排除回复他人和归属
  不明确的回复；保留明确回复 `thsottiaux` 的续帖，即使存在 `replied_to` 引用。
  retweet/repost 元数据、`RT @` 原文和仅含链接的分享仍排除。
- 每群 `tibo_baseline_done + tibo_seen_ids` 记录历史基线/年龄排除，
  **不作为成功投递证据**。首次开启/新群不补发当前历史。缺时间等待元数据修复。
- `tibo_delivered_ids` 和 `delivered_notice_keys` 是逐群成功 Tweet coverage。
  当旧车道在全推送模式中先成功发送，保存 `tweet:{id}` coverage；timeline
  后到也可认领成功 coverage，不重发。基线、未知 linkage、其它群 receipt 不参与。
- 有明确 source Tweet ID 时，同群动态成功 coverage 才吸收既有通知及对应 revision /
  confirmation；在途只 defer、不记 receipt。取消/失败后原车道可重试。
  没有 Tweet 身份的系统事件独立发送；原车道的判定和默认模式 revision 规则保留。
- Tibo 全推送并发准备正文/翻译/截图，但逐群按真实 `at` 等待更早在途尝试后投递；
  失败/取消释放等待，慢群不拖住其它群，跨 polling cycle 也保留屏障。
  只使用内存事件，不新增 state 字段；[真实反序取证与修复](tibo-ordering.md)。
- `_delivery_locks` 在每群覆盖发送前重查、发送和 receipt；`_state_lock` 只覆盖
  状态修改/保存，不跨 Provider / LLM / QQ 网络等待。状态仍为 v6，新增字段严格校验，
  旧 v6 无新字段可加载。配置结构升为 1.3.0 以补齐默认项；插件版本未发布变更。

## 图片展示

`notice_card.py` 负责结构化内容、HTML escaping 和截图；`templates/tweet.html` /
`templates/system.html` 共用 `card.css`。截图为本地 HTML，经 SDK
`ctx.render.html2png`，1240px 视口、2 倍像素密度、`#capture` 元素全高截图。
依赖 Host 提供本地浏览器；常用中英文字体随插件打包，不再依赖 Host 中文 fallback。
缺少渲染能力/依赖时自动文字回退。
插件运行本身没有新增 Python 第三方依赖。

所有文本 HTML 转义，头像打包为 data URL，`allow_network=false`，渲染不加载远程资源。
字体同样以 data URL 嵌入。CSS 字族 `Watcher Sans SC` 使用 Noto Sans SC 的广覆盖
基本汉字子集，配合 Noto Sans 拉丁子集，保持英文弯引号为自然的比例宽度。
两者都是 400–600 可变 WOFF2：正文/账号/时间 400，标题/姓名 600。
没有调整字号、行高或布局；字体度量改变会使自适应截图高度和换行略有变化。
覆盖范围、许可、资产体积、固定上游 SHA 及重建命令见
[字体归属说明](../templates/assets/ATTRIBUTION.md)。
同一管线只生成一次图供各群使用。Host 渲染预算 15 秒，调用外层预算 20 秒。
异常、超时、空/非法 PNG 结果、无法解析群会话、发图返回失败/抛异常均进入该群文字回退。
图片成功不发文字；回退文字成功即落正常 receipt，不再补图；两者都失败不落 receipt。

- Tweet 模板由明确 Tibo Tweet 来源选择，保留原标题，不独立展示来源链接。
- 系统模板明确“系统 / 上游通知”，不冒充 Tibo。
- 主帖和 quote 各自整块中文在上、英文在下，固定 UTC+8 日期格式。
  发布时间未知时用生成时间。quote 仅在确有正文时显示；无正文不显示占位提示。
  不论是否取得 quote URL，均只展示正文、不展示链接；未知引用作者不标成 Tibo；
  引用图片/视频忽略。
- 主帖仍用已有 Provider → feed 全文补全和 Host LLM 翻译。quote 使用同样的忠实
  整体翻译；翻译失败不阻断通知。主帖正文与 quote 采用独立的 enrichment 结果，
  主帖候选按既有质量规则择优，任何已取得的有效 quote 不随正文切换丢失，feed
  quote 可兜底。图片模式 full 主帖无 quote 时可继续下一级 Provider 补 quote，
  文字模式 full 主帖仍直接返回，不为 quote 增加额外 Provider 延迟。
- 图片不独立展示主帖、系统来源或引用帖的来源 URL；正文和译文中的网址正常保留。
  清除模板 `$source`、`.source`，但保留内部 `NoticeCard.url` 和 provenance。
  文字模式与图片失败后的文字回退保持原有来源链接及原文展示护栏。

## 引用投票补全

基于 `ca679b80674fc8d69603659bc65fcfb2adf10c1e` 增加 quote poll 的结构化补全和展示。
FxTwitter 的 `quote.poll.choices`、VxTwitter 的 `qrt.pollData.options` 显式归一化，
feed 同 ID 的 quote 可兜底。主帖候选、引用正文和投票分别补全；投票的数字、状态、
截止时间整份保留，不混搭不同 Provider 的快照。图片模式可补充下一级 Provider 的 poll，
文字模式不因此额外等待。

quote 卡内部展示原始问题；选项首行是原文及括号内中文译文（emoji 只保留一次），
次行是比例条与一位小数百分比。仅展示层去掉整个英文选项的外层括号。
有 poll 的短引用标题合并为 `Vote（投票）` 一行；长正文与非 poll 引用仍整块翻译。
底部继续展示总票数及可靠截止状态。
选项翻译只传 label，原文与 emoji 保留；翻译异常或预算耗尽仍正常展示原始投票。
已知 poll 而选项不足时显示“投票内容暂不可用”，不伪造零票或百分比；无法确认 poll 时
维持普通 quote 行为。未取得可靠 viewer-selection 不显示选中标记。

只新增 poll 区域样式；原有字体资产、字体规则、主卡 HTML 与左右布局保持不变。
模型数据仅用于 enrichment / presentation，不进入 alert、dedup、receipt、retry 或 state。
真实取证及 fixtures 来源见 [quoted-poll.md](quoted-poll.md)。

## 验证与复现

基线套件：243 passed。验证命令（真实 SDK + fake QQ，未真实发消息）：

```bash
python -m pytest -q
python -m pytest -q test_tibo_render.py
python -m pytest -q test_card_browser.py
ruff check .
ruff format --check .
git diff --check
```

`test_card_browser.py` 为可选开发验证，需在开发环境安装 Playwright 和 Chromium。
测试用真实 Chromium 验证普通/引用/系统/长文模板截图、完整高度、无水平溢出、头像加载、
日期、整块译文顺序以及无外部请求。它不连接 QQ，也不读生产配置/状态。
本轮还通过 Chromium 的实际 glyph 字体报告确认中英文和 footer 使用嵌入字体，
检查 400/600 字重及英文标点使用 Latin face。`test_card_font.py` 使用开发环境
FontTools 审计全部 6,763 个 GB2312 汉字、基本汉字区、ASCII、标点和变量字重范围。

上一轮结果：原有 243 项与新增 56 项离线测试通过，4 项真实 Chromium 截图测试通过，
合计 **303 passed**。Ruff 与 diff 空白检查通过。默认路径只更新配置升级测试中的
结构版本断言，其它原有测试保持原样。

上一轮 review fix 新增 27 个 targeted regression cases：self-reply 身份规范化/
不明确回复/纯转发排除、两种主帖质量下的跨 Provider quote 保留、feed 候选切换、
quote-only 或失败 Provider、文字模式请求数、三类图片无独立来源 URL、文字与图片回退保留链接。
完整 pytest **330 passed**（326 项离线测试 + 4 项真实 Chromium 截图），
targeted 子集 **27 passed**；仓库级 Ruff、format 和 diff 检查通过。
预览使用原有工具重生成，未向任何 QQ 群发送。

此前 URL 修复新增 3 个正文 URL 保留回归用例，覆盖 Tweet、quote、系统卡片及译文，
同时保留 3 类独立来源 URL 不展示的断言。完整 pytest **333 passed**，
正文/来源 URL 与文字回退 targeted 子集 **10 passed**，独立真实 Chromium
截图测试 **4 passed**；Ruff、format 和 diff 检查通过。未发送真实 QQ 消息。

本轮 typography 验证：完整 pytest **335 passed**，包含 4 项真实 Chromium
截图测试及 1 项广覆盖字体资产审计；单独运行 Chromium + 字体审计 **5 passed**。
Ruff、format、diff 检查通过。额外核对受保护文件与基线字节一致、非 typography
CSS 属性完全不变，`render_card`、正文 escaping 和卡片数据模型未改。
只生成本地预览，没有生产部署或 QQ 实发。

预览复现：

```bash
python tools/preview_cards.py --output docs/preview
```

演示译文用于视觉 review；生产翻译仍由 Host LLM 完成。
预览：[引用 Tweet](preview/tweet-quote.png) / [普通 Tweet](preview/tweet.png) /
[系统通知](preview/system.png)。未向测试群或其它群真实发送。
字体 before / after：[同环境对比](preview/typography.md)。

## 已知边界

1. 全推送覆盖公开 timeline 实际可见的主要动态。核对时 feed 只包含近期 30 条；
   未暴露/已删除/窗口覆盖前没采到的帖子无法恢复。不是 X 全历史抓取，也不是实时订阅。
2. 使用现有轮询间隔、48h 年龄护栏、首次静默基线；关闭期间再开启若已有基线，
   最近 48h 内尚未见的动态可以补发。上游缺少足以确认动态归属的回复元数据或时间时，
   等待后续数据修复。
3. 翻译/正文补全失败会保留可得原文；文字模式与文字回退保留来源链接，图片不独立显示来源链接。
   不猜测引用正文；普通引用缺正文仍隐藏，明确知道是投票但缺选项时只展示克制的投票占位。
   常用中英文使用打包字体；罕见扩展汉字、emoji 和其它文字仍可能使用 Host fallback。
   浏览器版本和光栅化差异仍可能影响实际显示；本轮只做本地 Chromium 验证。
4. 发送成功后落盘仍沿用项目 at-least-once 边界。平台已接受但 RPC 超时、进程在成功
   后落盘前退出等情况无法严格保证 exactly-once；发图异常按要求回退文字也受这一平台边界影响。
5. 本轮验证是离线 fake QQ + 本地真实浏览器截图，没有生产 Host/NapCat/QQ 实发证据。
   未改生产配置，也未安装任何生产依赖；如后续实发，仅限用户指定测试群 611817038。
