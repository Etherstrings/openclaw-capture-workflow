# Session Restore — 2026-03-13

Last updated: 2026-03-13

## 1. 当前分支与工作树状态

- 当前分支：`codex/backup-20260313-current-snapshot`
- 已推送远端备份分支：`origin/codex/backup-20260313-current-snapshot`
- 当前工作树不是干净状态；有大量未提交改动，覆盖：
  - `analyzer/` 新内核
  - `api.py`
  - `iterative_cases.py`
  - `iterative_runner.py`
  - `search_fallback.py`
  - `video_experiment_summarizer.py`
  - `video_truth_eval.py`
  - `note_renderer.py`
  - `run_video_truth_validation.py`
  - `report_video_capabilities.py`
  - 多个测试文件

## 2. 当前已经完成的能力

### 2.1 链接理解主链

- `analyzer/` 已存在并能真实跑通：
  - `Playwright` 默认后端
  - `PinchTab` 后备后端
  - 图片/视频临时下载
  - 视频抽帧
  - 结构化 `StructuredDocument` 输出

### 2.2 旧工作流回接

- 现有 `extractor.py` 中 `url` / `video_url` 路径已优先尝试新 analyzer
- 失败时回退到旧逻辑
- 外部 Telegram / Obsidian 协议未被整体改坏

### 2.3 识别效果迭代模块

- 已新增：
  - `iterative_cases.py`
  - `iterative_runner.py`
  - `search_fallback.py`
  - `scripts/run_iterative_recognition.py`
  - `scripts/merge_auto_cases.py`
- 已支持：
  - 手工 case + auto inbox 混合
  - 每个 case 都搜索补救
  - baseline / searched / final preview
  - Markdown 总报告

### 2.4 笔记生成链路（当前最新状态）

- 已改成“物料准备 + 模型直写正文”
- 当前原则：
  - 代码只准备 materials JSON
  - frontmatter 仍保留
  - frontmatter 之后的正文直接使用模型返回
- 当前最新实现还保留：
  - `prompts/learning_note_system.md`
  - `note_renderer.py`
  - `materials.json` 落盘
- 若 note render 失败：
  - 不再本地拼伪正文
  - 返回 `note_render_error`
  - 落 `materials_file`

## 3. 当前测试状态

最后一次全量测试结果：

```text
PYTHONPATH=src python3 -m unittest discover -s tests
Ran 152 tests in 32.302s
OK (skipped=2)
```

视频相关定向测试也通过：

```text
PYTHONPATH=src python3 -m unittest tests.test_summarizer tests.test_video_truth_eval tests.test_video_experiment_summarizer tests.test_processor tests.test_processor_validation
Ran 36 tests in 12.123s
OK
```

## 4. 当前视频能力现状

视频能力盘点报告已生成：

- [`state/reports/video_capabilities_20260313_175828.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/reports/video_capabilities_20260313_175828.md)

当前确认成立的链路：

- 页面元数据抓取：有
- 字幕提取：有
- 音频 ASR：有
- 关键帧抽取：有
- 关键帧 OCR：有
- 证据合并：有
- `video_assessment` / `coverage` / warnings：有

当前最大短板：

- 总结层虽然已经开始保留 outline / point，但表达仍然偏“贴着原始转写”
- 还没有接近用户给出的 Milky 那种“主题块 + 人话总结 + 风险提示 + 观众反馈”的效果
- 评论/弹幕/观众反馈目前仍基本被当噪声过滤

## 5. Gemini / AiHubMix 实验现状

### 5.1 已确认

- AiHubMix 当前模型列表里确实有：
  - `gemini-2.5-pro`
  - `gemini-2.5-flash`
- 代码中已接入：
  - `video_experiment_summarizer.py`
- 默认视频实验路径：
  - current model
  - `gemini_2_5_pro`
  - `gemini_2_5_flash`

### 5.2 真实回测结果

报告：

- [`state/reports/video_truth_validation_20260313_175929_143975.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/reports/video_truth_validation_20260313_175929_143975.md)
- [`state/reports/video_truth_validation_20260313_175929_144120.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/reports/video_truth_validation_20260313_175929_144120.md)

真实 preview：

- [`state/previews/video-truth-new_bilibili_video-current_model.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/video-truth-new_bilibili_video-current_model.md)
- [`state/previews/video-truth-new_bilibili_video-gemini_2_5_pro.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/video-truth-new_bilibili_video-gemini_2_5_pro.md)
- [`state/previews/video-truth-new_xiaohongshu_video-current_model.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/video-truth-new_xiaohongshu_video-current_model.md)
- [`state/previews/video-truth-new_xiaohongshu_video-gemini_2_5_pro.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/video-truth-new_xiaohongshu_video-gemini_2_5_pro.md)
- [`state/previews/video-truth-youtube_structured_chapters-current_model.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/video-truth-youtube_structured_chapters-current_model.md)
- [`state/previews/video-truth-youtube_structured_chapters-gemini_2_5_pro.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/previews/video-truth-youtube_structured_chapters-gemini_2_5_pro.md)

当前结论：

- `Gemini` 已经真实接通，不是空接
- 章节型 YouTube 样本可做到 `2 -> 2`
- 一般公开视频样本当前可做到 `3 -> 3`
- 但 current model 与 Gemini 的结果**没有拉开本质差距**
- 主要问题不再是“接不通模型”，而是“输入给模型的结构仍然不够像主题块”

## 6. 为什么现在效果还不够好

当前实现已经能做到：

- 识别视频 outline / point
- 保留 outline point 数量
- 用 Gemini 做真实对照

但离用户要的 Milky 效果还有明显差距，原因主要有三条：

1. `point` 还是太贴近原始转写  
   现在很多点仍然是转写截断句，而不是更高层的“主题块”。

2. 缺少“视频观点块聚类”  
   还没有把：
   - 核心方法
   - 系统流程
   - 技术实现
   - 风险提示
   - 观众反馈
   这种层次先提纯，再交给模型。

3. 评论/反馈没有进入主证据  
   Milky 的效果明显把“观众反馈 / 网友争议点”也纳入了总结，但当前仓库仍在大面积过滤评论噪声。

## 7. 用户最新明确要求

用户当前最明确要求有三条：

1. 继续迭代，不管怎么做，都要达成视频理解效果提升
2. 总结不要像“弱智”
3. 给了一个 Milky 的视频总结示例，要求朝那个效果靠拢

### 7.1 用户给的 Milky 示例（原文保留，不删除）

> Milky召唤了GPT-4o模型，用时28.38秒，为以老师总结如下：  
> 1. 视频核心内容是介绍一种通过自动化工具进行股票量化交易的方法。在这个过程中，UP主利用了一种名为OpenClaw的系统，将个人的自选股票列表交给系统分析，从而获得每日的买卖建议。这种方法强调的是利用自动化技术来简化和优化投资决策过程。  
>  
> 2. OpenClaw系统的工作流程是将用户的股票列表通过算法分析，并在开盘前提供具体的买入或持有建议。UP主展示了如何使用这套系统进行股票分析，强调了其数据分析的专业性。系统不仅能分析个股，还能提供市场大盘的整体分析，帮助用户更全面地理解市场动态。  
>  
> 3. UP主利用了GitHub作为技术支持平台，将OpenClaw部署在自己的服务器上，结合自动化工作流，使得整个分析过程无需手动操作。通过这种方式，UP主实现了一种高效的股票交易决策支持系统。观众对于这种技术的自动化能力表现出极大的兴趣和惊叹。  
>  
> 4. 视频中UP主强调，这一系统虽然强大，但建议观众不要盲目跟随其建议进行实际投资，更多是作为一种技术展示和娱乐。UP主本人虽然尝试跟随，但主要目的还是为观众提供一种技术上的思路和参考。  
>  
> 5. 观众对于这种利用技术进行股票分析的方式表现出浓厚的兴趣，尤其是对于系统的自动化能力和数据分析的准确性给予了积极的反馈。然而，也有观众提醒大家在实际投资中需要结合自身判断，避免过于依赖技术工具。  
>  
> 通过这段视频，UP主不仅展示了如何通过技术手段来进行股票交易分析，还为观众提供了一种新颖的投资思路，即利用自动化技术辅助投资决策，减轻个人在复杂市场中的分析负担。  
> 本次总结利用了SiliconFlow提供的语音技术与GPT-4o。  
> 记得随时呼叫Milky哦！💚  
> 网址：https://www.bilibili.com/video/BV1bFPMzFEnd/?spm_id_from=333.337.search-card.all.click&vd_source=74b26c8022b2f0d66e2448dfc0bf6ea1

### 7.2 这份示例反映出的目标效果

它强在：

- 不是字幕片段拼接，而是主题块总结
- 能把“OpenClaw 工作流程”讲清楚
- 会把 `GitHub / 自动化工作流 / 实际用途 / 风险提醒 / 观众反馈` 串起来
- 像一个看懂视频的人，而不是转写器

## 8. 下一次 session 最应该做什么

优先级建议如下：

1. 以用户给的 `BV1bFPMzFEnd` 为新真值样本  
   目标：围绕这个视频做专门对照实验

2. 新增“视频主题块聚类”层  
   在 summary 前先做：
   - 主题块抽取
   - 方法/流程/风险/反馈分类

3. 评论/反馈作为副证据进入链路  
   不再把所有评论都当噪声，至少要能提取高赞/高信号反馈

4. 再做 current / Gemini / 其他模型对照  
   不是先换模型，而是先把输入喂对

## 9. 推荐恢复顺序

新 session 开始时，建议按这个顺序读：

1. [`docs/SESSION_RESTORE_20260313.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/docs/SESSION_RESTORE_20260313.md)
2. [`state/reports/video_capabilities_20260313_175828.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/reports/video_capabilities_20260313_175828.md)
3. [`state/reports/video_truth_validation_20260313_175929_143975.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/reports/video_truth_validation_20260313_175929_143975.md)
4. [`state/reports/video_truth_validation_20260313_175929_144120.md`](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/state/reports/video_truth_validation_20260313_175929_144120.md)
5. 用户给的 Milky 示例（上面原文）

