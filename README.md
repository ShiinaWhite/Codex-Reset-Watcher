# Codex Reset Watcher

面向 MaiBot 的 QQ 群通知插件，监控 Codex 全局额度重置与 Banked Reset 生命周期。
当前版本 **0.1.9 release candidate**，审核分支为 `review/v0.1.9`。
此仓库描述候选版本，不代表该版本已经部署或通过线上验收。

## 通知行为

| 数据入口 | 用途 |
| --- | --- |
| `codex-reset.com/api/feed` | Banked 生命周期，以及 Global Reset 宣告与确认事件 |
| `codex-reset.com/api/forecast` 的 `official_signal` | 镜像上游面向用户的告警，按 `alert_event_id` 去重 |
| Tibo `/api/reset/current` | 已安排重置的精确时间、时间更新或近似时间预告 |

语义判定使用结构化字段；不根据原文猜测重置、不新增概率判定。
Tweet 内容补全与可选 LLM 解读用于通知展示，不决定是否触发告警。
Banked Reset 通知指额度重置机会存入账户、供之后手动兑换，不表示当前额度自动刷新。

L1 确认事件按群判断：已有同 Tweet 的 L4 receipt 时，observed 事件发送短确认；
符合重复确认指纹的事件静默并记录已处理。没有对应 receipt 时走完整首条通知。
同 Tweet 的有效 L4 告警或该群在途任务会暂缓 L1；完整通知在发送前再次按群检查 receipt。
已有 alert 去重键的群可以从有效上游信号的显式 `tweet_id` 静默补齐结构化 receipt。

## 配置与运行环境

插件清单声明 MaiBot Host `1.0.0–1.2.99`、SDK `2.8.0–2.99.99`；
这些是清单声明范围，不是所有版本的实测兼容承诺。
将 `codex_reset_watcher/` 交给 MaiBot 插件加载器后，在 WebUI 配置通知群。
完整默认值见 [config.toml](codex_reset_watcher/config.toml)。

```toml
[watcher]
group_ids = ["100000001"]
check_interval = 240
timezone = "Asia/Shanghai"
tibo_base = "https://tibo.modelyard.dev"
codex_base = "https://codex-reset.com"

[llm]
enabled = true
model_task = "replyer"
```

每群独立去重与重试；旧 `group_id` 通过内部迁移字段转为 `group_ids`。
状态保存在 SDK 提供的数据目录下的 `reset_state.json`（状态格式 version 6），
由插件管理，包含逐群通知记录、alert 键和结构化 Tweet receipt。

## 离线测试

测试使用 fake Host context 和仓库内 fixture；不启动插件轮询服务、不发送真实 QQ 消息。
候选版本验证环境为 Python 3.13.3、MaiBot SDK 2.8.0、pytest 9.0.3、
aiohttp 3.13.5、pydantic 2.13.3、tzdata 2026.1。
SDK 来自已有 MaiBot 环境；仓库不携带 SDK，缺少 `maibot_sdk` 时无法收集测试。
应选择已经安装这些依赖的 Python 解释器，或先按 MaiBot 的方式准备 SDK 环境。

在仓库根目录执行完整测试，不使用 `-k` 过滤：

```bash
python -m pytest -q
```

当前套件 **188 项**：`test_watcher.py` 覆盖通知、状态、Provider、LLM、并发和补写；
`test_replay_corpus.py` 使用历史公开 API corpus 验证确认策略。
midflight 用例通过事件屏障确认 Provider 已挂起，随后注入并持久化 receipt，
验证 B-confirm / C-silence，以及混合群中的未注入群仍走完整通知。
backfill 用例覆盖升级、后补 Tweet ID、持久化、幂等、缺字段和无效契约。
`live/` 中的历史输出文件是归档材料，不是当前测试结果。

## 发布候选的唯一事实源

以 GitHub `review/v0.1.9` 的 **exact commit SHA** 为准。
本地工作区、导出目录和历史补丁脚本均不能覆盖远端基线；变更必须形成新提交并重新验证。
记录提交 SHA、Git tree OID、完整 pytest 结果和跟踪文件的 SHA-256 清单。

```bash
git -c core.autocrlf=false clone --single-branch --branch review/v0.1.9 https://github.com/ShiinaWhite/Codex-Reset-Watcher.git rc-check
cd rc-check
git checkout --detach <EXACT_SHA>
python -m pytest -q
python tools/verify_release.py <EXACT_SHA>
```

验证脚本要求干净 checkout，并逐文件比较磁盘原始字节和提交中的 blob，检查重复测试定义。
聚合 SHA-256 的输入是按路径排序的 `[path, git_mode, file_sha256]` 数组，
以 UTF-8 编码、无额外空格的 JSON 序列化。Git tree OID 单独报告，不能与 SHA-256 字符串混为一谈。
本地 canonical checkout、clean clone 与远端 exact commit 的 blob 清单必须得到相同聚合 SHA-256。

业务代码在 [plugin.py](codex_reset_watcher/plugin.py)，清单在 [_manifest.json](codex_reset_watcher/_manifest.json)，
项目许可证见 [LICENSE](LICENSE)。
