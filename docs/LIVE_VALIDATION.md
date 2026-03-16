# Live Validation

- 生成时间: 2026-03-17T00:37:04
- JSON 报告: `state/reports/live_validation_20260317_002230.json`
- Obsidian 备份:
  - `inbox_backup` -> `/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/obsidian_cleanup_backup_20260317_002230/Inbox_OpenClaw`
  - `keywords_backup` -> `/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/obsidian_cleanup_backup_20260317_002230/Topics__Keywords`

## Summary

- 成功样本: `10/10`

## Cases

### B站视频 1
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.bilibili.com/video/BV1WAcQzKEW8/`
- 尝试: `https://www.bilibili.com/video/BV1WAcQzKEW8/`
  - job_status: `done` / `completed_with_warnings`
  - model: `gpt-4.1` / elapsed: `17.517`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-17 0023 简中互联网十大反人类交互设计盘点.md`
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

使用gpt-4.1模型经过17.5秒总结完成。
```

### B站视频 2
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.bilibili.com/video/BV1bFPMzFEnd/`
- 尝试: `https://www.bilibili.com/video/BV1bFPMzFEnd/`
  - job_status: `done` / `completed_with_warnings`
  - model: `gpt-4o-mini` / elapsed: `13.31`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-17 0023 OpenClaw股票量化交易推荐.md`
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

使用gpt-4o-mini模型经过13.3秒总结完成。
```

### 小红书视频 1
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7`
- 尝试: `https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7`
  - job_status: `done` / `completed_with_warnings`
  - model: `gpt-4.1` / elapsed: `8.772`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-17 0024 小红书页面无法访问.md`
  - telegram_ok: `True`
  - warnings:
    - `video_recovery_not_improved: reasons 2 -> 2`
    - `video_evidence_incomplete: missing speech track (subtitle/transcript); evidence text too short (<180 chars)`
    - `summary_model_upgrade_low_quality: primary=gpt-4o-mini -> upgrade=gpt-4.1`

```text
该小红书页面已失效，无法访问任何视频或内容，当前证据不完整。

主要讲了这几件事：

验证动作: 小红书 - 你访问的页面不见了。
关键链接: https://www.xiaohongshu.com/explore/699bf9a1000000001b01d4b7。
主题: 小红书 - 你访问的页面不见了。

一句话总结：
该小红书页面已失效，无法访问任何视频或内容，当前证据不完整。

使用gpt-4.1模型经过8.8秒总结完成。
```

### 小红书视频 2
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.xiaohongshu.com/explore/6895cd780000000025026d99`
- 尝试: `https://www.xiaohongshu.com/explore/6895cd780000000025026d99`
  - job_status: `done` / `completed_with_warnings`
  - model: `gpt-4.1` / elapsed: `10.274`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-17 0025 小红书页面无法访问.md`
  - telegram_ok: `True`
  - warnings:
    - `video_recovery_not_improved: reasons 2 -> 2`
    - `video_evidence_incomplete: missing speech track (subtitle/transcript); evidence text too short (<180 chars)`
    - `summary_model_upgrade_low_quality: primary=gpt-4o-mini -> upgrade=gpt-4.1`

```text
该小红书页面已失效，无法访问或获取任何视频内容，当前证据不完整。

主要讲了这几件事：

验证动作: 小红书 - 你访问的页面不见了。
关键链接: https://www.xiaohongshu.com/explore/6895cd780000000025026d99。
主题: 小红书 - 你访问的页面不见了。

一句话总结：
该小红书页面已失效，无法访问或获取任何视频内容，当前证据不完整。

使用gpt-4.1模型经过10.3秒总结完成。
```

### YouTube 视频 1
- 类型: `video_url`
- 成功: `True`
- 最终 URL: `https://www.youtube.com/watch?v=c7qJzG_swUE`
- 尝试: `https://www.youtube.com/watch?v=c7qJzG_swUE`
  - job_status: `done` / `completed`
  - model: `gpt-4o-mini` / elapsed: `6.081`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-17 0026 偉迪的美國夢.md`
  - telegram_ok: `True`

```text
偉迪的故事展示了從困境中崛起的美國夢。

主要讲了这几件事：

视频链接: https://www.youtube.com/watch?v=c7qJzG_swUE。
偉迪15年前隨父母移民美國，起初生活困難。
在拉斯維加斯無家可歸，後轉至舊金山找到工作機會。
他以變魔術為生，幫助家庭支付租金。
後來加入美國海軍，服役5年，獲得教育資助。
退役後利用GI Bill上學，獲得每月房屋津貼。

一句话总结：
偉迪的故事展示了從困境中崛起的美國夢。

使用gpt-4o-mini模型经过6.1秒总结完成。
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
  - model: `gpt-4o-mini` / elapsed: `4.082`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-17 0034 大象的长鼻子.md`
  - telegram_ok: `True`

```text
视频展示了大象及其长鼻子的特点。

主要讲了这几件事：

视频链接: https://www.youtube.com/watch?v=jNQXAC9IVRw。
大象以其非常长的鼻子而闻名。
视频内容简单，主要介绍了大象的外观特征。

一句话总结：
视频展示了大象及其长鼻子的特点。

使用gpt-4o-mini模型经过4.1秒总结完成。
```

### 小红书图文 1
- 类型: `url`
- 成功: `True`
- 最终 URL: `https://www.xiaohongshu.com/explore/68e10f380000000007008c4b`
- 尝试: `https://www.xiaohongshu.com/explore/68e10f380000000007008c4b`
  - job_status: `done` / `completed`
  - model: `gpt-4o-mini` / elapsed: `3.679`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-17 0035 页面不可见.md`
  - telegram_ok: `True`

```text
该小红书页面当前不可访问，可能是链接失效或访问限制。

主要讲了这几件事：

关键链接: https://www.xiaohongshu.com/explore/68e10f380000000007008c4b。
页面返回“不可见/暂时无法浏览”的状态。
链接本身存在，但无法抓取正文内容。
可能是平台对该笔记实施了访问限制。

一句话总结：
该小红书页面当前不可访问，可能是链接失效或访问限制。

使用gpt-4o-mini模型经过3.7秒总结完成。
```

### 小红书图文 2
- 类型: `url`
- 成功: `True`
- 最终 URL: `https://www.xiaohongshu.com/explore/69a3032400000000150305bb`
- 尝试: `https://www.xiaohongshu.com/explore/69a3032400000000150305bb`
  - job_status: `done` / `completed`
  - model: `gpt-4o-mini` / elapsed: `4.549`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-17 0036 页面不可见.md`
  - telegram_ok: `True`

```text
当前小红书页面无法访问，内容不可见。

主要讲了这几件事：

关键链接: https://www.xiaohongshu.com/explore/69a3032400000000150305bb。
页面返回'不可见/暂时无法浏览'提示。
可能是原链接失效或访问限制。
确认链接存在，但无法抓取正文内容。

一句话总结：
当前小红书页面无法访问，内容不可见。

使用gpt-4o-mini模型经过4.5秒总结完成。
```

### 小红书图文 3
- 类型: `url`
- 成功: `True`
- 最终 URL: `https://www.xiaohongshu.com/explore/69aea021000000001a028a59`
- 尝试: `https://www.xiaohongshu.com/explore/69aea021000000001a028a59`
  - job_status: `done` / `completed`
  - model: `gpt-4o-mini` / elapsed: `3.406`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-17 0036 页面不可见.md`
  - telegram_ok: `True`

```text
当前小红书页面无法访问，内容不可见。

主要讲了这几件事：

关键链接: https://www.xiaohongshu.com/explore/69aea021000000001a028a59。
页面返回'不可见/暂时无法浏览'提示。
可能是原链接失效或访问限制。
确认链接存在，但无法抓取正文内容。

一句话总结：
当前小红书页面无法访问，内容不可见。

使用gpt-4o-mini模型经过3.4秒总结完成。
```

### 普通图文网页
- 类型: `url`
- 成功: `True`
- 最终 URL: `https://docs.openclaw.ai/`
- 尝试: `https://docs.openclaw.ai/`
  - job_status: `done` / `completed`
  - model: `gpt-4o-mini` / elapsed: `4.989`
  - note_path: `Inbox/OpenClaw/2026/03/2026-03-17 0036 OpenClaw 安装指南.md`
  - telegram_ok: `True`

```text
OpenClaw 安装指南

OpenClaw 提供了一个跨多个消息平台的 AI 代理网关，安装过程简单。

主要内容：
1. 支持 WhatsApp、Telegram、Discord、iMessage 等多个平台的 AI 代理
2. 安装服务并配对 WhatsApp 开始使用网关
3. 插件支持 Mattermost 等其他平台

下一步：
1. 访问 OpenClaw 文档以获取详细安装步骤
2. 确保已准备好所需的消息平台账户
3. 按照文档中的步骤进行服务安装和配对

https://docs.openclaw.ai/

归档：Inbox/OpenClaw/2026/03/2026-03-17 0036 OpenClaw 安装指南 [md]
打开：http://127.0.0.1:8765/open?path=Inbox%2FOpenClaw%2F2026%2F03%2F2026-03-17%200036%20OpenClaw%20%E5%AE%89%E8%A3%85%E6%8C%87%E5%8D%97.md

使用gpt-4o-mini模型经过5.0秒总结完成。
```
