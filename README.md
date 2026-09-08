# Codex 额度重置提醒插件（v0.1.8 · 多群通知）

长期监控 OpenAI Codex / ChatGPT Work 的**全局额度重置**与 **Banked Reset
生命周期**，向 QQ 群（北京时间）发送通知。只消费上游结构化字段做语义判定，
Tibo 原文仅作通知展示；上游未形成面向用户的告警时保持静默，插件不自行
创造告警。

**当前状态**：0.1.8 为当前稳定版本（多群通知 / age-guard 去噪 / upstream
alert 镜像 / Tweet 全文补全 / LLM 解读均稳定运行）。

## 数据源与车道（一页速览）

| 源 | 职责 |
|---|---|
| `codex-reset.com/api/feed`（主源） | Banked 生命周期（announced/arriving/available 各通知一次）+ Global declared 生命周期通知 |
| Tibo `/api/reset/current`（辅助源） | Global 精确时间：`SCHEDULED`+精确 → 已确认/时间更新；`SCHEDULED`+近似 → 预估 |
| `/api/forecast`（告警镜像源） | 仅消费 `official_signal`（上游已决定面向用户告警的信号）：`delivery_destination=="alerts"` + `alert_event_id` 即镜像，dedup = `upstream-alert:{alert_event_id}`；`latest_alert`/`probabilities` 不消费 |
| `/feed.xml`、`/api/timeline` | 不消费 |

Banked Reset 通知必须声明：存入账户供之后**手动兑换**，不代表当前额度已
自动刷新——它不是普通 Global Reset。

## 文件地图

| 文件 | 说明 |
|---|---|
| `codex_reset_watcher/plugin.py` | 插件主体（唯一业务代码） |
| `codex_reset_watcher/_manifest.json` | 插件清单（id `codex-reset.watcher`，v0.1.8） |
| `codex_reset_watcher/config.toml` | 默认配置样例 |
| `test_watcher.py` | pytest（159 项，真实 fixture 优先） |
| `probe_*.json` / `live/*.json` | 真实 API 样本（公开接口响应） |

## 安装与配置

把 `codex_reset_watcher/` 拷入 MaiBot `plugins/`，Runner 自动生成配置后在
WebUI"通知群号"添加群号（原生列表编辑器，可多个；旧
`group_id` 自动一次性迁入列表）：

```toml
[watcher]
group_ids = ["100000001"]     # 通知群列表（示例群号，非真实），每群独立发送与独立重试
group_ids_migrated = false    # 一次性迁移标记（内部，WebUI 隐藏）
group_id = ""                 # legacy 入口（隐藏，仅供迁移与回滚）
check_interval = 240          # 轮询间隔秒，钳制 60~3600
timezone = "Asia/Shanghai"
tibo_base = "https://tibo.modelyard.dev"
codex_base = "https://codex-reset.com"

[llm]
enabled = true                # AI 解读（失败自动降级为普通通知）
model_task = "replyer"        # MaiBot 模型任务名
temperature = 0.2
max_tokens = 4096
timeout_seconds = 600         # 单条告警 LLM 分析总预算（秒）
prompt = """（内置默认分析提示词，可在 WebUI 多行编辑）"""
```

## 状态文件

`<plugin-data>/codex-reset.watcher/reset_state.json`（`version: 6`）：
`groups.{群号}` 下按群独立保存 `feed_baseline_done` / `notified_keys`
（feed 去重键）与 `upstream_alert_keys`（告警镜像去重）与 Tibo 去重状态
（per-group receipt）。由插件自动迁移与写入，**禁止手工编辑**。

## 验证

```bash
python -m pytest test_watcher.py -q
# 159 passed（测试全部离线：fixture 驱动，不发起任何网络请求）
```

## 数据来源说明

- 重置信号与事件数据来自 [codex-reset.com](https://codex-reset.com)（公开 API）；
- 精确时间辅助来自 [tibo.modelyard.dev](https://tibo.modelyard.dev)（公开 API）；
- 推文全文补全使用 [FxTwitter](https://github.com/FxEmbed/FxEmbed) /
  [VxTwitter](https://github.com/dangeredwolf/ModernDeck) 系公开 API（只读、
  无凭据），失败时自动降级为 feed/forecast 摘要；
- 本插件与 OpenAI 无隶属关系。
