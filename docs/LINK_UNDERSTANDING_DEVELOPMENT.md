# 链接理解引擎开发文档

## 1. 背景

当前 `openclaw_capture_workflow` 的主能力是：

- 接收外部入口传来的链接、文字、图片、视频
- 做本地抽取与摘要
- 回写 Obsidian，并可回传 Telegram

现有实现已经在 `extractor.py`、`processor.py`、`summarizer.py`、`telegram.py` 中积累了不少可复用能力，尤其是：

- 网页与 GitHub 文本抽取
- 视频下载、关键帧采样、OCR、ASR
- 结构化摘要与质量门禁

但从产品方向上看，真正要建设的核心能力不应该是“某个入口工作流”，而应该是：

> 拿到一个网址，系统能真正理解这个链接内容，并稳定产出结构化文档。

入口可以是 Telegram、CLI、HTTP API，甚至未来的桌面按钮，但这些都只是包装层，不应再主导核心设计。

## 2. 目标

建设一个统一的链接理解内核：

- 输入：`url`
- 输出：结构化 `JSON document`

目标输出格式：

```json
{
  "title": "",
  "summary": "",
  "sections": [],
  "images": [],
  "videos": [],
  "tables": []
}
```

核心要求：

- 能渲染真实网页，而不是只抓静态 HTML
- 能抽取正文、图片、嵌入视频、表格
- 遇到视频时能下载并采样关键帧
- 能把文本与视觉证据一起送入多模态模型
- 能返回稳定、可校验的结构化 JSON
- 临时图片、视频、截图、关键帧在分析完成后删除

非目标：

- 本轮不以 Telegram / Obsidian 输出层为中心
- 本轮不把 API 作为唯一交付物
- 本轮不追求“模型直接理解原始视频文件”

## 3. 产品参考与设计借鉴

以下判断基于公开产品体验做推断，不代表其内部真实架构。

### 3.1 Perplexity

值得借鉴的点：

- 用户提问后，结果组织清楚
- 不是简单摘抄网页，而是“先理解，再回答”
- 对多来源信息会做重组，而不是只返回抓取结果

对本项目的启发：

- 我们的核心不应只是“抓页面”，而应是“链接理解 + 结构化表达”

### 3.2 Arc Browser AI

值得借鉴的点：

- 更强调“当前网页上下文理解”
- 像是在读用户眼前这页，而不是泛泛检索

对本项目的启发：

- 页面渲染、可见内容、截图、视觉证据必须纳入主流程
- “像真的读懂页面”比“抓到更多文本”更重要

### 3.3 Notion AI

值得借鉴的点：

- 长内容重组能力强
- 更擅长把杂乱内容整理成可读结构

对本项目的启发：

- `sections` 设计要稳定
- 最终结果要便于再次消费，而不是一次性聊天回复

### 3.4 Glean

值得借鉴的点：

- 强调“对内容做定位、归类、可追溯组织”

对本项目的启发：

- 输出文档不能只有 `summary`
- 必须保留分节、图片、视频、表格等结构化字段，方便二次索引和后续接入搜索

## 4. 总体架构

建议将项目重构为“链接理解内核 + 外围适配层”两层：

### 4.1 链接理解内核

建议新增 `analyzer/` 子模块：

- `analyzer/models.py`
- `analyzer/service.py`
- `analyzer/render.py`
- `analyzer/dom_extract.py`
- `analyzer/video.py`
- `analyzer/llm.py`
- `analyzer/cleanup.py`

核心函数：

```python
def analyze_url(url: str, requested_output_lang: str = "zh-CN") -> StructuredDocument:
    ...
```

### 4.2 外围适配层

以下都应变成“调用内核”的薄封装：

- CLI
- 现有 `extractor.py` 中的 URL / video 路径
- 未来如需恢复或新增 API / Telegram / Obsidian 接入

也就是说，未来系统不再允许同时维护两套网页理解逻辑。

## 5. 渲染层设计：Playwright 与 PinchTab

## 5.1 结论

推荐采用：

- `Playwright` 作为默认渲染后端
- `PinchTab` 作为可选增强后端

不要把两者理解成二选一；更合理的做法是抽象一个浏览器运行时接口，在不同页面条件下选择最合适的后端。

## 5.2 为什么默认仍然是 Playwright

对当前项目来说，Playwright 仍然最适合做默认后端，原因是：

- Python 集成自然，和现有技术栈一致
- 获取渲染后 HTML、页面截图、DOM 状态比较直接
- 更适合我们这种“先拿结构化 HTML，再用 BeautifulSoup 做细抽取”的流程
- 更容易与现有测试体系和本地脚本结合

我们需要的不是“浏览器控制”本身，而是：

- 渲染后的 HTML
- 可见正文
- 页面截图
- 媒体节点
- 稳定的工程集成

这一点上，Playwright 是更稳的默认项。

## 5.3 PinchTab 适合放在哪

根据 PinchTab 官方站点与文档，它更像一个面向 AI Agent 的浏览器控制服务，突出能力包括：

- 状态持久化
- 可附着已有 Chrome
- 可通过 `snapshot` 返回无障碍树结构
- 可通过 `text` 返回 readability 或 raw 文本
- 提供 `screenshot`、`pdf`、交互动作等接口
- 具备 stealth / 持久会话 / 多标签能力

这非常适合以下场景：

- 需要登录态的网站
- Playwright 容易被识别的网站
- 需要长期持久化会话的网站
- 需要人为先登录一次、之后由程序持续复用的网页流

因此，PinchTab 更适合做：

- `Playwright` 的补位后端
- 难站点或登录站点的专用渲染器
- 后续 Agent 型产品能力的浏览器控制基础设施

不建议把 PinchTab 当成第一阶段唯一基础设施，原因是：

- 当前项目核心是“链接理解”，不是“复杂浏览器自动化”
- PinchTab 更像外部运行时服务，需要单独运维与安全边界
- 它的 tab-oriented、stateful 设计很强，但对我们当前的 Python 分析内核来说是增强项，不是唯一前提

## 5.4 浏览器后端抽象

建议定义统一接口：

```python
class BrowserBackend(Protocol):
    def render(self, url: str) -> RenderResult: ...
```

`RenderResult` 至少包含：

- `final_url`
- `title`
- `html`
- `text_hint`
- `screenshot_path`
- `metadata`

实现两套：

- `PlaywrightBackend`
- `PinchTabBackend`

后端选择策略：

- 默认：`PlaywrightBackend`
- 满足以下任一条件时切 `PinchTabBackend`
  - 域名在 challenge / login allowlist 中
  - Playwright 渲染失败
  - Playwright 获取文本明显过短
  - 需要用户已有浏览器登录态

## 6. 内容抽取层

在浏览器后端返回渲染结果后，统一进入 DOM 抽取阶段。

### 6.1 正文抽取

使用 BeautifulSoup 解析渲染后 HTML：

- 优先抽取 `article`、`main`、`[role=main]`
- 失败时退到 `body`
- 去掉：
  - `script`
  - `style`
  - `nav`
  - `footer`
  - `form`
  - cookie 弹窗
  - 广告 / 推荐 / 页脚噪声

输出应为：

- `title`
- `summary_candidate`
- `sections`

`sections` 不是机械按段切，而是尽量保留标题层级：

- `heading`
- `level`
- `content`

### 6.2 图片抽取

抽取：

- `img`
- `picture`
- OpenGraph image

每张图保留：

- `src`
- `alt`
- `caption`
- `context`

排序时优先：

- 正文附近
- 尺寸较大
- 非 logo / icon / tracking

### 6.3 视频抽取

抽取：

- `video`
- `source`
- 常见 `iframe` 视频来源

每个视频保留：

- `src`
- `poster`
- `provider`

如果是可下载视频，则进入视频处理阶段。

### 6.4 表格抽取

抽取所有 `table`：

- `caption`
- `headers`
- `rows`

表格必须作为一类一等输出对象存在，不能只被拼回正文。

## 7. 视频处理策略

根据 OpenAI 当前模型文档，`gpt-5.4`、`gpt-5-mini`、`gpt-4.1` 都支持文本与图片输入，但不支持把“视频”作为直接输入模态。

因此当前正确的视频策略仍然是：

- 下载视频
- 用 FFmpeg 抽样关键帧
- 如有需要，再额外做音频转写
- 把视频理解任务拆成：
  - 页面正文上下文
  - 视频帧图像
  - 可选 transcript

而不是把原始视频文件直接送入主模型。

建议复用现有：

- [scripts/video_keyframes_extract.py](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/video_keyframes_extract.py)
- [scripts/video_audio_asr.py](/Users/boyuewu/Documents/Projects/AIProjects/openclaw_capture_workflow/scripts/video_audio_asr.py)

但把它们从“工作流脚本”升级为“链接理解内核的媒体处理组件”。

## 8. 模型策略

## 8.1 当前推荐

根据 OpenAI 当前模型文档，建议如下：

- 默认主模型：`gpt-5-mini`
- 高质量升级模型：`gpt-5.4`
- 长文档稳定备选：`gpt-4.1`

原因：

- OpenAI 模型总览页明确建议：复杂任务优先从 `gpt-5.4` 开始；若更关注延迟和成本，选择 `gpt-5-mini`
- `gpt-5-mini` 支持：
  - image input
  - Responses API
  - Structured outputs
- `gpt-5.4` 支持：
  - image input
  - Structured outputs
  - 约 1M context
  - 更适合复杂专业工作流
- `gpt-4.1` 是当前很稳的非推理模型，仍适合长文档理解与工具调用

## 8.2 推荐分工

### 默认路径

使用 `gpt-5-mini`：

- 大多数普通网页
- 图文混合页
- 需要结构化 JSON 输出
- 成本敏感的批量分析

### 升级路径

升级到 `gpt-5.4`：

- 长篇官方文档
- README 特别长的项目页
- 页面结构复杂且图片较多
- `gpt-5-mini` 输出质量不达标

### 稳定备选

`gpt-4.1` 作为 A/B 与兼容路径：

- 当我们更需要稳定长文本梳理
- 当推理型输出不是重点，而是“严格照指令整理”

## 8.3 不建议的主路径

- 不建议继续把 `gpt-4o-mini` 作为未来默认主模型
  - 它仍然可用，也支持图像输入和结构化输出
  - 但如果要做新的主链路，`gpt-5-mini` 更符合当前 OpenAI 的推荐方向

## 9. 输出契约

推荐最终输出：

```json
{
  "title": "string",
  "summary": "string",
  "sections": [
    {
      "heading": "string|null",
      "level": 1,
      "content": "string"
    }
  ],
  "images": [
    {
      "src": "string",
      "alt": "string|null",
      "caption": "string|null",
      "context": "string|null"
    }
  ],
  "videos": [
    {
      "src": "string",
      "poster": "string|null",
      "provider": "string|null",
      "duration_seconds": 0,
      "frame_summaries": []
    }
  ],
  "tables": [
    {
      "caption": "string|null",
      "headers": [],
      "rows": []
    }
  ]
}
```

设计原则：

- `sections` 是主输出，不是附属信息
- `images`、`videos`、`tables` 必须始终存在，即使为空数组
- 不返回本地临时路径
- 允许后续再向旧 `EvidenceBundle` 做兼容压缩

## 10. 临时工件与删除策略

你已经明确要求：视频 / 图片在分析之后删除。

因此内核必须采用严格的临时目录策略：

- 每次任务只允许写入 `state/tmp/<job_id>/`
- 临时工件包括：
  - 页面截图
  - 下载图片
  - 下载视频
  - 关键帧
  - 转写中间文件
- 在 `finally` 中统一清理
- 最终结果、缓存、日志都不保留本地媒体路径

如果后续需要调试，策略不是“留存工件”，而是：

- 重新跑一次分析
- 或显式开启 debug 模式单独保留

默认正式路径不保留媒体工件。

## 11. 建议的交付顺序

### Phase 1：链接理解内核成型

- 新增 `analyzer/` 模块
- 先接 `PlaywrightBackend`
- 做正文 / 图片 / 表格 / 视频元数据抽取
- 跑通 `gpt-5-mini` 结构化 JSON 输出

### Phase 2：视频增强

- 视频下载
- FFmpeg 关键帧采样
- 帧摘要写入 `videos.frame_summaries`
- 临时工件清理稳定化

### Phase 3：PinchTab 接入

- 新增 `PinchTabBackend`
- 支持登录态站点和难渲染站点
- 做后端选择策略

### Phase 4：旧系统迁移

- 让现有 `extractor.py` 的 URL / video 路径调用 `analyze_url()`
- 再决定是否接回 Telegram / Obsidian / CLI / API

## 12. 当前推荐决策

针对当前项目，我建议直接定这几个决策，不再来回摇摆：

- 核心输入：`url`
- 核心内核：`analyze_url()`
- 默认浏览器后端：`Playwright`
- 增强后端：`PinchTab`
- 默认模型：`gpt-5-mini`
- 升级模型：`gpt-5.4`
- 备选模型：`gpt-4.1`
- 视频策略：下载 + 抽帧 + 可选 transcript
- 临时媒体：分析完成即删除

## 13. 参考资料

以下链接用于后续实现时核对能力边界：

- PinchTab 首页：<https://pinchtab.com/>
- PinchTab 文档：<https://pinchtab.com/docs/>
- PinchTab Tabs / Text / Snapshot 示例：<https://pinchtab.com/docs/tabs/>
- OpenAI Models 总览：<https://developers.openai.com/api/docs/models>
- OpenAI GPT-5.4：<https://developers.openai.com/api/docs/models/gpt-5.4>
- OpenAI GPT-5 mini：<https://developers.openai.com/api/docs/models/gpt-5-mini>
- OpenAI GPT-4.1：<https://developers.openai.com/api/docs/models/gpt-4.1>
- OpenAI Pricing：<https://developers.openai.com/api/docs/pricing>

## 14. 关键判断

最重要的不是入口形式，而是让系统形成一个稳定的“链接理解内核”。

只要这一层做对了：

- Telegram 可以接
- CLI 可以接
- API 可以接
- Obsidian 可以接

如果这一层没做对，入口越多，系统越乱。

## 15. 当前已实现

截至当前版本，第一阶段已落地：

- 新增 `analyzer/` 内核包
- 新增 `analyze_url()` 总编排
- 新增 `PlaywrightBackend`
- 新增可运行的 `PinchTabBackend`
- 新增 DOM 抽取、视频处理、LLM 结构化、临时工件清理模块
- 新增 CLI 命令：
  - `openclaw-capture-workflow analyze-url --config <path> --url <url>`
- 新增 `serve-api` 作为 analyzer 的薄 HTTP 包装
- 新增 analyzer 与 CLI 的自动化测试
- 分析结束后删除临时截图、图片、视频和关键帧
- 默认走 Playwright，失败或正文过短时可切到 PinchTab
- 现有 URL / video 工作流已优先调用新 analyzer，并在失败时回退旧逻辑

当前仍未实现：

- 视频音频转写并入新内核主流程
- 基于页面类型的更细粒度抽取策略
- 更强的多模态后校验与质量评分
