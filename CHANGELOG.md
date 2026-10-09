# Changelog

## 0.2.0 — candidate

### 通知功能

- 可选 Tibo 动态全推送，普通帖、长帖、带评论引用帖及明确回复自己的续帖均可通知；
  回复他人、归属不明回复和纯转发保守排除。默认关闭。
- 默认文字通知；可选 HTML 图片通知覆盖全部车道，截图/发图失败按群回退文字。
- Tweet 与系统通知模板分别表达真实来源，整块中文在英文上方，正文 URL 正常保留。
- 主帖、引用正文与结构化投票独立 enrichment；支持投票选项、百分比、比例条、
  总票数、截止状态和可选选项翻译，保留原文且不推测 viewer 选择。
- Tibo 并发准备、逐群按真实时间投递；同轮与跨轮在途尝试保持 oldest-first。
  失败/异常/取消释放屏障；本群尝试完成后不会被其它群在途状态错误 defer。

### 发布工程

- 收口 Host/SDK 正式版本兼容范围，说明 Host-managed Playwright/Chromium 与降级合同。
- 离线 Noto SC 字体按核心/补充块传输，保留既有全部字形覆盖，显著降低典型 HTML RPC。
- 将必需历史公开 API 快照保真归入测试 fixtures，删除安装树中的 raw forensic、probe
  与过时开发截图；保留完整回归测试和用户效果图。
- 扩展 release verifier 的卫生、tree/font/runtime 大小与 HTML payload 门禁。

`config_version=1.3.0`、state schema 和所有成功后 receipt/逐群 dedup/retry 规则不变。
此候选未 merge main，未建立 tag、GitHub Release 或 Marketplace 发布。

## 0.1.13

基于成功投递 identity 协调 feed/告警镜像的重复通知。没有可靠关联时保守发送；
系统观测使用真实上游来源，不把事件 ID 当成 Tweet ID。

## 0.1.12

新增 latest-only Push Banked 镜像，不因上游生命周期暂为 unknown 而否决已决定推送的告警。
不承诺恢复停机期间已被最新指针覆盖的历史消息。
