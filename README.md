# Codex 额度重置提醒插件（v0.1.5 · 多群通知）

长期监控 OpenAI Codex / ChatGPT Work 的**全局额度重置**与 **Banked Reset
生命周期**，向 QQ 群（北京时间）发送通知。只消费上游结构化字段做语义判定，
Tibo 原文仅作通知展示。宁可漏报，不猜。

**当前状态**：0.1.5 已部署生产（2026-09-05，maintenance：Tibo 观察日志
中文化，config/state 零变化、零发送；0.1.3 多群通知 / 0.1.4 age-guard
去噪均稳定，NapCat 全程未动），稳定运行等待下一次真实 Reset。

## 数据源与车道（一页速览）

| 源 | 职责 |
|---|---|
| `codex-reset.com/api/feed`（主源） | Banked 生命周期（announced/arriving/available 各通知一次）+ Global 已宣告 |
| Tibo `/api/reset/current`（辅助源） | Global 精确时间：`SCHEDULED`+精确 → 已确认/时间更新；`SCHEDULED`+近似 → 预估 |
| `/api/forecast`（告警镜像源，v0.1.6 起） | 仅消费 `official_signal`（上游已决定面向用户告警的信号）：`delivery_destination=="alerts"` + `alert_event_id` 即镜像，dedup = `upstream-alert:{alert_event_id}`；`latest_alert`/`probabilities` 不消费 |
| `/feed.xml`、`/api/timeline` | 不消费（理由见 architecture.md） |

Banked Reset 通知必须声明：存入账户供之后**手动兑换**，不代表当前额度已
自动刷新——它不是普通 Global Reset。

## 文件地图

| 文件 | 说明 |
|---|---|
| `codex_reset_watcher/plugin.py` | 插件主体（唯一业务代码） |
| `codex_reset_watcher/_manifest.json` | 插件清单（id `codex-reset.watcher`，v0.1.5） |
| `codex_reset_watcher/config.toml` | 默认配置样例 |
| `test_watcher.py` | pytest（93 项，真实 fixture 优先） |
| `probe_*.json` / `live/*.json` | 真实 API 样本（见 `docs/fixtures.md`） |
| `docs/architecture.md` | 数据源职责、双车道、设计理由与已知限制 |
| `docs/event-model.md` | 通知类型、事件状态、去重与静默规则 |
| `docs/operations.md` | 生产部署、升级、日志检查、状态文件、回滚、禁止事项 |
| `docs/fixtures.md` | 真实样本来源与用途 |

## 安装与配置

把 `codex_reset_watcher/` 拷入 MaiBot `plugins/`，Runner 自动生成配置后在
WebUI"通知群号"添加群号（原生列表编辑器，可多个；0.1.2 升级时旧
`group_id` 自动一次性迁入列表）：

```toml
[watcher]
group_ids = ["100000001"]     # 通知群列表（示例群号，非真实），每群独立发送与独立重试
group_ids_migrated = false    # 一次性迁移标记（内部，WebUI 隐藏）
group_id = ""                 # 0.1.2 legacy 入口（隐藏，仅供迁移与回滚）
check_interval = 240          # 轮询间隔秒，钳制 60~3600
timezone = "Asia/Shanghai"
tibo_base = "https://tibo.modelyard.dev"
codex_base = "https://codex-reset.com"
```

## 状态文件

`<plugin-data>/codex-reset.watcher/reset_state.json`（0.1.3 起 `version: 6`）：
`groups.{群号}` 下按群独立保存 `feed_baseline_done` / `notified_keys`
（feed 去重键）与 Tibo 去重状态（per-group receipt）。由插件自动迁移与
写入，**禁止手工编辑**。语义见 `docs/event-model.md`，生产路径与回滚见
`docs/operations.md`。

## 验证

```bash
/e/AI\ Bot/MaiM-with-u/MaiBot/.venv/Scripts/python.exe -m pytest test_watcher.py -q
# 93 passed
```
