# Live Validation

- 生成时间: 2026-03-16T23:44:07
- JSON 报告: `state/reports/live_validation_20260316_233054.json`
- Obsidian 备份:
  - `inbox_backup` -> `/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/obsidian_cleanup_backup_20260316_233054/Inbox_OpenClaw`
  - `keywords_backup` -> `/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/obsidian_cleanup_backup_20260316_233054/Topics__Keywords`

## Summary

- 成功样本: `7/10`
- 失败样本:
  - `xhs_note_1` -> `https://www.xiaohongshu.com/explore/68e10f380000000007008c4b`
  - `xhs_note_2` -> `https://www.xiaohongshu.com/explore/69a3032400000000150305bb`
  - `xhs_note_3` -> `https://www.xiaohongshu.com/explore/69b41a4c000000002103b520`

## Cases

### B站视频 1
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.bilibili.com/video/BV1WAcQzKEW8/`
- 尝试: `https://www.bilibili.com/video/BV1WAcQzKEW8/`
  - job_status: `done` / `completed_with_warnings`
  - model: `gpt-4.1` / elapsed: `24.364`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-16 2331 简中互联网十大糟糕交互设计盘点.md`
  - telegram_ok: `True`
  - warnings:
    - `summary_model_upgrade_low_quality: primary=gpt-4o-mini -> upgrade=gpt-4.1`

```text
这个视频在吐槽“简中互联网里最反人类的 10 种交互设计”。核心观点是：很多设计不是为了用户体验，而是产品经理为了导流、KPI 或自我感动硬塞进去的，结果把本来顺手的操作越做越恶心。

他大致盘点的是这 10 类，倒序是：

第10名：双击图片点赞
特别点名小红书。正常人双击图片会以为是放大，结果却变成点赞，想放大还得先误触再取消。

第9名：登录验证码死循环
比如验证码收不到，重发又提示“操作频繁”，退出重进后旧码又过期，来回卡死。

第8名：强制扫码登录
明明知道账号密码，也被逼着掏手机扫码；网页版登录状态还特别短，隔几天又要重新扫。

第7名：下拉刷新却把你拉进二级页面/活动页
本来只想刷新，结果被拽进广告、会场、小程序之类的“抽屉页”。

第6名：硬塞 AI 聊天入口
像运营商、银行之类 App 把 AI 头像放在首页最显眼的位置，但真正有用的功能反而被藏起来。

第5名：截图后强行弹出“分享/反馈”
用户只是想保存证据或留图，结果被弹窗打断，尤其连续截图时更烦。

第4名：什么 App 都硬塞短视频
外卖、网购、浏览器都加短视频入口，还配红包、现金奖励、营销号内容，完全是低配抖音化。

第3名：复制分享链接时夹带一大段废话
不是只给纯链接，而是附带“XX 邀请你一起看”之类引流文案，真正链接反而埋在后面。

第2名：打开 App 就自动刷新首页
刚看到一个感兴趣的帖子，还没点进去就被系统刷新掉，只能靠记忆回找。

第1名：shadowban / 幽灵屏蔽
尤其在小红书、抖音这类平台，你以为消息、评论、联系方式发出去了，实际上只有自己看得见，别人根本收不到，但平台又不明说。

整体风格就是高强度吐槽，结论很明确：这些设计本质上都在拿用户习惯、注意力和时间换平台利益，而不是在认真做体验。

我这次是结合视频页信息、公开音轨转写交叉整理的；评论区这轮抓取有平台限制，但光靠音轨已经足够把主线和 10 个条目核实清楚。

使用gpt-4.1模型经过24.4秒总结完成。
```

### B站视频 2
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.bilibili.com/video/BV1bFPMzFEnd/`
- 尝试: `https://www.bilibili.com/video/BV1bFPMzFEnd/`
  - job_status: `done` / `completed_with_warnings`
  - model: `gpt-4o-mini` / elapsed: `21.486`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-16 2332 OpenClaw股票量化交易推荐.md`
  - telegram_ok: `True`
  - warnings:
    - `summary_model_upgrade_skipped: upgrade_score=0.1 < primary_score=0.15`
    - `summary_quality_flags: missing_signals:links:https://www.bilibili.com/video/BV1bFPMzFEnd`

```text
这个视频大意是在演示：作者怎么把 OpenClaw 改造成一个“每天自动帮他看自选股、给出量化分析和提醒”的工具。

主要讲了这几件事：

他把自己的一批自选股丢给 OpenClaw，让它在每天开盘前自动分析。
系统会输出每只股票的建议，比如更偏向“买入”“持有观望”，并附上针对单只股票的分析理由。
不只是看个股，它还会顺带做大盘/当日市场的整体复盘，给出更宏观的方向判断。
他把这套东西部署在自己的设备和工作流里，提到是挂在一台 Mac 上跑，并结合 GitHub / 自动化流程来触发。
展示里还演示了消息触发和推送过程，意思是他发一个指令，后台工作流就会开始跑，最后把结果推送回来。
因为股票多、要拉取多种信源，整套分析不是秒出，视频里说大概要跑二十多分钟到半小时左右。
整体重点不是“保证赚钱”，而是“用自动化把盯盘、收集信息、初步分析这件事外包给 AI/工作流”。

一句话总结：
这视频是在秀一个 OpenClaw + 自动化工作流 的炒股辅助玩法，核心卖点是“每天自动分析自选股并推送建议”，更像技术展示，不是严肃投资建议。

使用gpt-4o-mini模型经过21.5秒总结完成。
```

### 小红书视频 1
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7`
- 尝试: `https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7`
  - job_status: `done` / `completed_with_warnings`
  - model: `gpt-4.1` / elapsed: `18.477`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-16 2333 小红书页面已失效.md`
  - telegram_ok: `True`
  - warnings:
    - `video_recovery_not_improved: reasons 2 -> 2`
    - `video_evidence_incomplete: missing speech track (subtitle/transcript); evidence text too short (<180 chars)`
    - `summary_model_upgrade_low_quality: primary=gpt-4o-mini -> upgrade=gpt-4.1`

```text
该小红书页面已失效，无法访问原视频内容，当前证据不完整。

主要讲了这几件事：

验证动作: 小红书 - 你访问的页面不见了。
关键链接: https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7。
主题: 小红书 - 你访问的页面不见了。

一句话总结：
该小红书页面已失效，无法访问原视频内容，当前证据不完整。

使用gpt-4.1模型经过18.5秒总结完成。
```

### 小红书视频 2
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.xiaohongshu.com/explore/6895cd780000000025026d99`
- 尝试: `https://www.xiaohongshu.com/explore/6895cd780000000025026d99`
  - job_status: `done` / `completed_with_warnings`
  - model: `gpt-4.1` / elapsed: `16.956`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-16 2333 小红书页面已失效.md`
  - telegram_ok: `True`
  - warnings:
    - `video_recovery_not_improved: reasons 2 -> 2`
    - `video_evidence_incomplete: missing speech track (subtitle/transcript); evidence text too short (<180 chars)`
    - `summary_model_upgrade_low_quality: primary=gpt-4o-mini -> upgrade=gpt-4.1`

```text
该小红书页面已失效，无法访问原视频内容，当前证据不完整。

主要讲了这几件事：

验证动作: 小红书 - 你访问的页面不见了。
关键链接: https://www.xiaohongshu.com/explore/6895cd780000000025026d99。
主题: 小红书 - 你访问的页面不见了。

一句话总结：
该小红书页面已失效，无法访问原视频内容，当前证据不完整。

使用gpt-4.1模型经过17.0秒总结完成。
```

### YouTube 视频 1
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.youtube.com/watch?v=c7qJzG_swUE`
- 尝试: `https://www.youtube.com/watch?v=c7qJzG_swUE`
  - job_status: `done` / `completed`
  - model: `gpt-4o-mini` / elapsed: `10.991`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-16 2335 偉迪的美國夢故事.md`
  - telegram_ok: `True`

```text
偉迪分享了他從無家可歸到成功的美國夢故事。

主要讲了这几件事：

视频链接: https://www.youtube.com/watch?v=c7qJzG_swUE。
偉迪15年前隨父母移民美國，起初生活困難。
在拉斯維加斯無家可歸，後來移居舊金山。
為了補貼家用，偉迪在漁人碼頭表演魔術。
他在美國海軍服役5年，利用GI Bill上學。
退役後，偉迪獲得學費和生活津貼，繼續學業。

一句话总结：
偉迪分享了他從無家可歸到成功的美國夢故事。

使用gpt-4o-mini模型经过11.0秒总结完成。
```

### YouTube 视频 2
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.youtube.com/watch?v=jNQXAC9IVRw`
- 尝试: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`
  - job_status: `timeout` / `timed_out_after_420s`
  - model: `` / elapsed: `None`
  - note_path: ``
  - telegram_ok: `None`
- 尝试: `https://www.youtube.com/watch?v=jNQXAC9IVRw`
  - job_status: `done` / `completed`
  - model: `gpt-4o-mini` / elapsed: `9.103`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-16 2343 大象的长鼻子.md`
  - telegram_ok: `True`

```text
视频展示了大象的长鼻子，内容简单直接。

主要讲了这几件事：

视频链接: https://www.youtube.com/watch?v=jNQXAC9IVRw。
大象的鼻子非常长。
视频内容简单，几乎没有其他信息。

一句话总结：
视频展示了大象的长鼻子，内容简单直接。

使用gpt-4o-mini模型经过9.1秒总结完成。
```

### 小红书图文 1
- 类型: `url`
- 成功: `False`
- 最终 URL: `https://www.xiaohongshu.com/explore/68e10f380000000007008c4b`
- 尝试: `https://www.xiaohongshu.com/explore/68e10f380000000007008c4b`
  - job_status: `failed` / `failed`
  - model: `` / elapsed: `None`
  - note_path: ``
  - telegram_ok: `None`

### 小红书图文 2
- 类型: `url`
- 成功: `False`
- 最终 URL: `https://www.xiaohongshu.com/explore/69a3032400000000150305bb`
- 尝试: `https://www.xiaohongshu.com/explore/69a3032400000000150305bb`
  - job_status: `failed` / `failed`
  - model: `` / elapsed: `None`
  - note_path: ``
  - telegram_ok: `None`

### 小红书图文 3
- 类型: `url`
- 成功: `False`
- 最终 URL: `https://www.xiaohongshu.com/explore/69b41a4c000000002103b520`
- 尝试: `https://www.xiaohongshu.com/explore/69aea021000000001a028a59`
  - job_status: `failed` / `failed`
  - model: `` / elapsed: `None`
  - note_path: ``
  - telegram_ok: `None`
- 尝试: `https://www.xiaohongshu.com/explore/69b41a4c000000002103b520`
  - job_status: `failed` / `failed`
  - model: `` / elapsed: `None`
  - note_path: ``
  - telegram_ok: `None`

### 普通图文网页
- 类型: `url`
- 成功: `True`
- 最终 URL: `https://docs.openclaw.ai/`
- 尝试: `https://docs.openclaw.ai/`
  - job_status: `done` / `completed`
  - model: `gpt-4o-mini` / elapsed: `9.502`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-16 2343 OpenClaw 安装指南.md`
  - telegram_ok: `True`

```text
OpenClaw 安装指南

OpenClaw 提供跨多个消息平台的 AI 代理服务，安装过程简单。

主要内容：
1. 支持 WhatsApp、Telegram、Discord、iMessage 等多个平台的 AI 代理
2. 安装服务后，可以通过手机发送消息并获取代理响应
3. 支持插件扩展，如 Mattermost

下一步：
1. 访问 OpenClaw 文档以获取详细安装步骤
2. 确保已准备好所需的消息平台账户
3. 按照文档中的步骤进行服务配对和安装

https://docs.openclaw.ai/

归档：Inbox/OpenClaw/2026/03/2026-03-16 2343 OpenClaw 安装指南 [md]
打开：http://127.0.0.1:8765/open?path=Inbox%2FOpenClaw%2F2026%2F03%2F2026-03-16%202343%20OpenClaw%20%E5%AE%89%E8%A3%85%E6%8C%87%E5%8D%97.md

使用gpt-4o-mini模型经过9.5秒总结完成。
```
