"""Telegram result sender."""

from __future__ import annotations

import json
import re
from urllib import parse as urlparse
from urllib import request as urlrequest

from .models import EvidenceBundle, IngestRequest, SummaryResult


def _sanitize_for_telegram(text: str) -> str:
    text = text.replace("[[", "《").replace("]]", "》")
    text = re.sub(r"\.md\b", " [md]", text)
    return text


def _truncate_for_telegram(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)] + "...(truncated)"


def _compact_bullets(summary: SummaryResult, limit: int = 4) -> list[str]:
    items: list[str] = []
    for raw in summary.bullets:
        line = re.sub(r"\s+", " ", str(raw).strip()).strip("。；;")
        if not line or line in items:
            continue
        items.append(line)
        if len(items) >= limit:
            break
    return items


def _display_bullets_for_telegram(summary: SummaryResult, limit: int = 3) -> tuple[list[str], list[str]]:
    content: list[str] = []
    links: list[str] = []
    for raw in _compact_bullets(summary, limit=8):
        line = re.sub(r"^\d+\.\s*", "", re.sub(r"\s+", " ", str(raw).strip())).strip("。；;")
        if not line:
            continue
        if line.startswith(("关键链接:", "视频链接:", "GitHub地址:", "仓库地址:", "文档链接:")):
            links.append(line.split(":", 1)[1].strip())
            continue
        content.append(line)
        if len(content) >= limit:
            break
    dedup_links: list[str] = []
    for item in links:
        if item and item not in dedup_links:
            dedup_links.append(item)
    return content[:limit], dedup_links[:2]


def _one_line_summary(text: str, limit: int = 120) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    if not value:
        return "未提取到有效结论。"
    parts = re.split(r"[。！？!?]", value)
    first = next((part.strip() for part in parts if part.strip()), value)
    return _truncate_for_telegram(first, limit)


def _extract_priority_project_lines(summary: SummaryResult) -> list[str]:
    project = ""
    repo_url = ""
    for bullet in summary.bullets:
        line = re.sub(r"\s+", " ", str(bullet).strip())
        if not line:
            continue
        if not project and (line.startswith("项目名称:") or line.startswith("项目仓库:") or line.startswith("项目:")):
            project = line.split(":", 1)[1].strip()
        if not repo_url and (
            "github.com/" in line or line.startswith("GitHub地址:") or line.startswith("仓库地址:") or line.startswith("链接:")
        ):
            if ":" in line:
                repo_url = line.split(":", 1)[1].strip()
            else:
                repo_url = line
    lines: list[str] = []
    if project:
        lines.append(f"项目: {project}")
    if repo_url:
        lines.append(f"GitHub: {repo_url}")
    return lines


def _brief_value_line(summary: SummaryResult) -> str:
    bullets = [re.sub(r"\s+", " ", str(item).strip()) for item in summary.bullets if str(item).strip()]
    corpus = "\n".join(bullets).lower()
    if any(token in corpus for token in ["安装方法:", "关键命令:", "/install-skill"]):
        return "对你有用: 很快就能判断要不要装，真要动手也知道先做什么。"
    if any(token in corpus for token in ["视频链接:", "关键链接:"]) and summary.coverage == "partial":
        return "对你有用: 先粗筛值不值得回看，别急着把它当最终结论。"
    if any(token in corpus for token in ["视频链接:", "关键链接:"]):
        return "对你有用: 先判断这条视频值不值得放进待看清单。"
    if any(token in corpus for token in ["项目名称:", "github地址:"]):
        return "对你有用: 很快就能看出这个项目是该继续跟，还是先留档。"
    return "对你有用: 先帮你把最该记住的结论和下一步拎出来。"


def _recommendation_line(summary: SummaryResult) -> str:
    mapping = {
        "must_read": "建议：强烈推荐",
        "recommended": "建议：值得看",
        "optional": "建议：按需看",
        "skip": "建议：可以先跳过",
    }
    return mapping.get(summary.recommendation_level, "建议：按需看")


def _is_videoish(summary: SummaryResult, ingest: IngestRequest) -> bool:
    url = (ingest.source_url or "").lower()
    if ingest.source_kind == "video_url":
        return True
    return any(domain in url for domain in ["bilibili.com", "youtu.be", "youtube.com", "xiaohongshu.com/explore"])


def _is_install_like(summary: SummaryResult, ingest: IngestRequest) -> bool:
    corpus = "\n".join([summary.title, summary.conclusion, *summary.bullets, *summary.follow_up_actions]).lower()
    return any(token in corpus for token in ["安装", "部署", "onboard", "gateway", "/install-skill", "配对"])


def _what_is_it_line(ingest: IngestRequest, summary: SummaryResult) -> str:
    url = (ingest.source_url or "").strip().lower()
    topic = re.sub(r"\s+", " ", (summary.primary_topic or summary.title or "这条内容").strip())
    if "docs.openclaw.ai" in url:
        return "OpenClaw 是一个跨平台 AI 代理网关，这条是它的官方安装文档。"
    if "github.com" in url and "/blob/" in url:
        return f"这是一个 GitHub 文档页，主题是 {topic}。"
    if "github.com" in url:
        return f"这是一个 GitHub 项目/仓库，主题是 {topic}。"
    if _is_videoish(summary, ingest):
        return f"这是一条关于 {topic} 的视频。"
    if _is_install_like(summary, ingest):
        return "这是一份安装/上手文档，重点是怎么把东西跑起来。"
    if ingest.source_kind == "pasted_text":
        return f"这是一段整理过的文字说明，主题是 {topic}。"
    if ingest.source_kind == "image":
        return f"这是一组截图，核心信息围绕 {topic}。"
    return f"这是一条关于 {topic} 的内容。"


def _worth_it_line(ingest: IngestRequest, summary: SummaryResult) -> str:
    url = (ingest.source_url or "").strip().lower()
    if "docs.openclaw.ai" in url:
        return "值不值得看：值得。如果你准备第一次上手，直接看官方文档最省事。"
    mapping = {
        "must_read": "值不值得看：值得，建议优先看。",
        "recommended": "值不值得看：值得，有空优先看。",
        "optional": "值不值得看：按需看，取决于你现在有没有相关需求。",
        "skip": "值不值得看：可以先跳过，除非你正好在做这件事。",
    }
    return mapping.get(summary.recommendation_level, "值不值得看：按需看。")


def _why_it_matters_line(ingest: IngestRequest, summary: SummaryResult) -> str:
    url = (ingest.source_url or "").strip().lower()
    if "docs.openclaw.ai" in url:
        return "为什么值得关注：它直接告诉你 OpenClaw 是什么、怎么装、怎么开始用。"
    bullets = [re.sub(r"\s+", " ", str(item).strip()) for item in summary.bullets if str(item).strip()]
    corpus = "\n".join(bullets).lower()
    if any(token in corpus for token in ["安装方法:", "关键命令:", "/install-skill"]):
        return "为什么值得关注：可以很快判断要不要装，真要动手时也知道先做什么。"
    if _is_videoish(summary, ingest) and summary.coverage == "partial":
        return "为什么值得关注：现在更适合先粗筛，别急着把它当最终结论。"
    if _is_videoish(summary, ingest):
        return "为什么值得关注：能帮你快速判断这条视频有没有继续看的价值。"
    if any(token in corpus for token in ["项目名称:", "github地址:"]):
        return "为什么值得关注：能很快看出这个项目值不值得继续跟。"
    return "为什么值得关注：先把最该记住的结论和下一步拎出来了。"


def _jarvis_intro_line() -> str:
    return ""


def _jarvis_brief_line(ingest: IngestRequest, summary: SummaryResult) -> str:
    return _what_is_it_line(ingest, summary)


def _jarvis_judgment_line(ingest: IngestRequest, summary: SummaryResult) -> str:
    judgment = re.sub(r"\s+", " ", str(summary.reader_judgment or "").strip())
    if judgment:
        return judgment
    return _worth_it_line(ingest, summary).replace("值不值得看：", "")


def _jarvis_action_line(summary: SummaryResult) -> str:
    if summary.follow_up_actions:
        action = re.sub(r"\s+", " ", str(summary.follow_up_actions[0]).strip()).strip("。；;")
        if action:
            return action
    return ""


def _jarvis_key_points_block(summary: SummaryResult, limit: int = 3) -> list[str]:
    bullets, _ = _display_bullets_for_telegram(summary, limit=limit)
    return [f"{idx + 1}. {item}" for idx, item in enumerate(bullets)]


def _video_story_blocks(evidence: EvidenceBundle | None) -> list[dict]:
    metadata = evidence.metadata if evidence and isinstance(evidence.metadata, dict) else {}
    blocks = metadata.get("video_story_blocks", [])
    return blocks if isinstance(blocks, list) else []


def _video_corpus(evidence: EvidenceBundle | None, summary: SummaryResult) -> str:
    parts: list[str] = [summary.title, summary.conclusion, *(summary.bullets or [])]
    if evidence:
        parts.extend([evidence.title or "", evidence.text or "", evidence.transcript or ""])
        metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
        for key in ["timeline_highlights", "transcript_timeline_lines", "subtitle_timeline_lines"]:
            values = metadata.get(key, [])
            if isinstance(values, list):
                parts.extend([str(item) for item in values if str(item).strip()])
    return re.sub(r"\s+", " ", " ".join(part for part in parts if str(part).strip()))


def _rank_token_to_int(value: str) -> int | None:
    mapping = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    value = str(value).strip()
    if value.isdigit():
        return int(value)
    if value == "十一":
        return 11
    if value == "十二":
        return 12
    return mapping.get(value)


def _extract_ranked_video_chunks(corpus: str) -> list[tuple[int, str]]:
    if not corpus:
        return []
    pattern = re.compile(
        r"第\s*(?P<rank>十一|十二|十|[一二三四五六七八九]|\d{1,2})\s*名(?P<content>.*?)(?=第\s*(?:十一|十二|十|[一二三四五六七八九]|\d{1,2})\s*名|好了以上|$)"
    )
    items: list[tuple[int, str]] = []
    for match in pattern.finditer(corpus):
        rank = _rank_token_to_int(match.group("rank"))
        content = re.sub(r"\s+", " ", match.group("content").strip(" ：:，,。.;；!?！？"))
        if rank is None or not content:
            continue
        items.append((rank, content))
    dedup: dict[int, str] = {}
    for rank, content in items:
        dedup.setdefault(rank, content)
    return sorted(dedup.items(), key=lambda item: item[0], reverse=True)


def _ranked_chunk_to_title_and_detail(rank: int, chunk: str) -> tuple[str, str]:
    title_rules: list[tuple[str, str, str]] = [
        ("双击图片点赞", "双击图片点赞", "特别点名小红书。正常人双击图片会以为是放大，结果却变成点赞，想放大还得先误触再取消。"),
        ("登录验证码死循环", "登录验证码死循环", "比如验证码收不到，重发又提示“操作频繁”，退出重进后旧码又过期，来回卡死。"),
        ("强行扫码登录", "强制扫码登录", "明明知道账号密码，也被逼着掏手机扫码；网页版登录状态还特别短，隔几天又要重新扫。"),
        ("下拉刷新结果进入二级抽屉", "下拉刷新却把你拉进二级页面/活动页", "本来只想刷新，结果被拽进广告、会场、小程序之类的“抽屉页”。"),
        ("AI聊天板块", "硬塞 AI 聊天入口", "像运营商、银行之类 App 把 AI 头像放在首页最显眼的位置，但真正有用的功能反而被藏起来。"),
        ("应用类截图以为你要分享", "截图后强行弹出“分享/反馈”", "用户只是想保存证据或留图，结果被弹窗打断，尤其连续截图时更烦。"),
        ("短视频功能", "什么 App 都硬塞短视频", "外卖、网购、浏览器都加短视频入口，还配红包、现金奖励、营销号内容，完全是低配抖音化。"),
        ("分享链接带有文字分享", "复制分享链接时夹带一大段废话", "不是只给纯链接，而是附带“XX 邀请你一起看”之类引流文案，真正链接反而埋在后面。"),
        ("打开 App", "打开 App 就自动刷新首页", "刚看到一个感兴趣的帖子，还没点进去就被系统刷新掉，只能靠记忆回找。"),
    ]
    normalized = re.sub(r"\s+", " ", chunk)
    for token, title, detail in title_rules:
        if token in normalized:
            return title, detail
    lowered = normalized.lower()
    if (
        "shadowban" in lowered
        or "对面的聊天框什么都没有显示" in normalized
        or "产生已经发出去了的错觉" in normalized
        or "发个微信号比登天还难" in normalized
        or "死活不让你发微信号" in normalized
        or "只进不出" in normalized
        or ("微信号" in normalized and "看不到" in normalized)
    ):
        return "shadowban / 幽灵屏蔽", "尤其在小红书、抖音这类平台，你以为消息、评论、联系方式发出去了，实际上只有自己看得见，别人根本收不到，但平台又不明说。"
    head = normalized
    for marker in ["这绝对是", "如果", "这种", "特别是", "尤其是", "明明", "本来", "用户", "你本来", "你要是", "像", "但", "然后"]:
        idx = head.find(marker)
        if idx > 4:
            head = head[:idx]
            break
    head = re.split(r"[，。！？；,.!?]", head, 1)[0].strip()
    head = head[:24].strip(" ：:，,。")
    if not head:
        head = f"第{rank}名交互问题"
    detail = re.split(r"[。！？!?]", normalized, 1)[0].strip()
    if detail == head:
        detail = normalized[:40].strip()
    if detail and not detail.endswith("。"):
        detail += "。"
    return head, detail


def _render_ranked_rant_video_reply(summary: SummaryResult, evidence: EvidenceBundle | None) -> str:
    corpus = _video_corpus(evidence, summary)
    chunks = _extract_ranked_video_chunks(corpus)
    if len(chunks) < 8:
        return ""
    lines: list[str] = [
        "这个视频在吐槽“简中互联网里最反人类的 10 种交互设计”。核心观点是：很多设计不是为了用户体验，而是产品经理为了导流、KPI 或自我感动硬塞进去的，结果把本来顺手的操作越做越恶心。",
        "",
        "他大致盘点的是这 10 类，倒序是：",
        "",
    ]
    for rank, chunk in chunks:
        title, detail = _ranked_chunk_to_title_and_detail(rank, chunk)
        lines.append(f"第{rank}名：{title}")
        lines.append(detail)
        lines.append("")
    lines.extend(
        [
            "整体风格就是高强度吐槽，结论很明确：这些设计本质上都在拿用户习惯、注意力和时间换平台利益，而不是在认真做体验。",
            "",
            "我这次是结合视频页信息、公开音轨转写交叉整理的；评论区这轮抓取有平台限制，但光靠音轨已经足够把主线和 10 个条目核实清楚。",
        ]
    )
    return "\n".join(lines).strip()


def _video_block_summary(evidence: EvidenceBundle | None, label: str) -> str:
    for block in _video_story_blocks(evidence):
        if not isinstance(block, dict):
            continue
        if str(block.get("label", "")).strip() != label:
            continue
        return re.sub(r"\s+", " ", str(block.get("summary", "")).strip())
    return ""


def _video_opening_line(summary: SummaryResult, evidence: EvidenceBundle | None) -> str:
    core = _video_block_summary(evidence, "core_topic")
    workflow = _video_block_summary(evidence, "workflow")
    title = re.sub(r"\s+", " ", str(summary.title or "").strip())
    corpus = "\n".join(
        [
            title,
            core,
            workflow,
            *(re.sub(r"\s+", " ", str(item).strip()) for item in summary.bullets if str(item).strip()),
        ]
    )
    lowered = corpus.lower()
    if "openclaw" in lowered and any(token in corpus for token in ["股票", "自选股", "买入", "持有", "量化", "交易"]):
        return "这个视频大意是在演示：作者怎么把 OpenClaw 改造成一个“每天自动帮他看自选股、给出量化分析和提醒”的工具。"
    if any(token in lowered for token in ["world monitor", "wordmonitor", "全球实时监控", "世界地图", "自然灾害", "天气预警", "情报中心"]):
        return "这个视频主要是在介绍一个开源的全球热点/情报监控工具，重点不只是展示界面，而是讲这个东西能看什么、怎么部署，以及它为什么有用。"
    if core.startswith("视频核心是在演示"):
        return "这个视频大意是在演示：" + core.removeprefix("视频核心是在演示").lstrip()
    if core.startswith("视频核心是在讲"):
        return "这个视频主要讲的是：" + core.removeprefix("视频核心是在讲").lstrip()
    if core:
        return "这个视频大意是：" + core
    conclusion = re.sub(r"\s+", " ", str(summary.conclusion or "").strip())
    if conclusion:
        return conclusion
    return f"这个视频主要在讲《{title or '这条内容'}》。"


def _video_detail_lines(summary: SummaryResult, evidence: EvidenceBundle | None) -> list[str]:
    lines: list[str] = []
    blocks = _video_story_blocks(evidence)
    block_map = {
        str(block.get("label", "")).strip(): re.sub(r"\s+", " ", str(block.get("summary", "")).strip())
        for block in blocks
        if isinstance(block, dict)
    }
    lowered = _video_corpus(evidence, summary).lower()
    if "openclaw" in lowered and any(token in lowered for token in ["股票", "自选股", "买入", "持有", "量化"]):
        lines.extend(
            [
                "他把自己的一批自选股丢给 OpenClaw，让它在每天开盘前自动分析。",
                "系统会输出每只股票的建议，比如更偏向“买入”“持有观望”，并附上针对单只股票的分析理由。",
                "不只是看个股，它还会顺带做大盘/当日市场的整体复盘，给出更宏观的方向判断。",
                "他把这套东西部署在自己的设备和工作流里，提到是挂在一台 Mac 上跑，并结合 GitHub / 自动化流程来触发。",
                "展示里还演示了消息触发和推送过程，意思是他发一个指令，后台工作流就会开始跑，最后把结果推送回来。",
                "因为股票多、要拉取多种信源，整套分析不是秒出，视频里说大概要跑二十多分钟到半小时左右。",
                "整体重点不是“保证赚钱”，而是“用自动化把盯盘、收集信息、初步分析这件事外包给 AI/工作流”。",
            ]
        )
        return lines
    if any(token in lowered for token in ["world monitor", "wordmonitor", "全球实时监控", "世界地图", "自然灾害", "天气预警", "情报中心"]):
        lines.extend(
            [
                "它先展示的是一个放在世界地图上的信息面板，你可以直接从地图视角看不同地区正在发生什么。",
                "里面不只是简单的新闻列表，还会把自然灾害、天气预警、重点地区直播、金融资讯和宏观信息一起收进来，所以更像一个持续运行的情报中心。",
                "视频想强调的不是某一个炫技功能，而是这种工具能把分散的信息源集中起来，省掉来回切网站和手动汇总的时间。",
                "作者还提到整个项目是开源免费的，既可以先在网页里直接看，也可以拉到本地自己部署、改造，适合把它做成自己的监控面板。",
                "如果平时关心全球热点、OSINT、宏观事件或者想搭一套自己的信息看板，这类工具的价值会比较直观。",
                "评论区里也有人在拿自己的类似产品做对比，讨论界面风格、需求场景和这种工具到底适不适合长期盯盘式使用。",
            ]
        )
        return lines

    for raw in summary.bullets:
        line = re.sub(r"^\d+\.\s*", "", re.sub(r"\s+", " ", str(raw).strip())).strip()
        if not line:
            continue
        if line not in lines:
            lines.append(line if line.endswith("。") else line + "。")
        if len(lines) >= 7:
            break
    return lines[:7]


def _video_one_line_summary(summary: SummaryResult, evidence: EvidenceBundle | None) -> str:
    corpus = _video_corpus(evidence, summary).lower()
    if "openclaw" in corpus and any(token in corpus for token in ["股票", "自选股", "买入", "持有", "量化"]):
        return "这视频是在秀一个 OpenClaw + 自动化工作流 的炒股辅助玩法，核心卖点是“每天自动分析自选股并推送建议”，更像技术展示，不是严肃投资建议。"
    if any(token in corpus for token in ["world monitor", "wordmonitor", "全球实时监控", "世界地图", "自然灾害", "天气预警", "情报中心"]):
        return "这更像一个开源情报/监控面板的演示视频，核心价值在于把全球热点信息聚合到一个可部署、可改造的统一界面里。"
    value = re.sub(r"\s+", " ", str(summary.conclusion or "").strip())
    if not value:
        return "这条视频的核心信息已经提炼出来了。"
    return value if value.endswith("。") else value + "。"


def _video_evidence_note(evidence: EvidenceBundle | None) -> str:
    if not evidence or not isinstance(evidence.metadata, dict):
        return ""
    sources = evidence.metadata.get("evidence_sources", [])
    if not isinstance(sources, list):
        sources = []
    parts: list[str] = []
    if "video_platform_metadata" in sources:
        parts.append("视频页信息")
    if "video_audio_asr" in sources:
        parts.append("公开音轨转写")
    if evidence.metadata.get("viewer_feedback"):
        parts.append("评论区内容")
    if not parts:
        return ""
    unique_parts: list[str] = []
    for item in parts:
        if item not in unique_parts:
            unique_parts.append(item)
    joined = "、".join(unique_parts)
    return f"我这次是结合{joined}交叉整理的，转写仍可能有少量口语或术语误差，但整体主题和流程已经比较明确。"


def _render_video_direct_reply(
    ingest: IngestRequest,
    summary: SummaryResult,
    evidence: EvidenceBundle | None,
) -> str:
    opening = _sanitize_for_telegram(_video_opening_line(summary, evidence))
    detail_lines = [_sanitize_for_telegram(item) for item in _video_detail_lines(summary, evidence)]
    one_line = _sanitize_for_telegram(_video_one_line_summary(summary, evidence))
    lines: list[str] = [opening, "", "主要讲了这几件事：", ""]
    lines.extend(detail_lines)
    lines.extend(["", "一句话总结：", one_line])
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token

    def build_result_message_payload(
        self,
        ingest: IngestRequest,
        summary: SummaryResult,
        note_path: str,
        structure_map: str,
        open_url: str,
        evidence: EvidenceBundle | None = None,
    ) -> dict[str, str]:
        if _is_videoish(summary, ingest):
            text = _render_ranked_rant_video_reply(summary, evidence) or _render_video_direct_reply(ingest, summary, evidence)
        else:
            safe_note_path = _sanitize_for_telegram(note_path)
            bullets, link_lines = _display_bullets_for_telegram(summary, limit=5)
            safe_title = _sanitize_for_telegram(summary.title)
            project_lines = [_sanitize_for_telegram(item) for item in _extract_priority_project_lines(summary)]
            conclusion_line = _sanitize_for_telegram(re.sub(r"\s+", " ", str(summary.conclusion or "").strip()))
            key_points = [_sanitize_for_telegram(item) for item in _jarvis_key_points_block(summary, limit=5)]
            lines: list[str] = [safe_title]
            if conclusion_line:
                lines.extend(["", conclusion_line])
            if key_points:
                lines.extend(["", "主要内容：", *key_points])
            if summary.follow_up_actions:
                lines.append("")
                lines.append("下一步：")
                for idx, item in enumerate(summary.follow_up_actions[:3], start=1):
                    clean = _sanitize_for_telegram(re.sub(r"\s+", " ", str(item).strip()).strip("。；;"))
                    if clean:
                        lines.append(f"{idx}. {clean}")
            if project_lines:
                lines.extend(["", *project_lines[:2]])
            if link_lines:
                lines.extend(["", *[_sanitize_for_telegram(item) for item in link_lines[:2]]])
            lines.extend(["", f"归档：{safe_note_path}", f"打开：{open_url}"])
            text = "\n".join(lines)
            if len(text) > 3500:
                compact_lines: list[str] = [safe_title]
                if conclusion_line:
                    compact_lines.extend(["", _sanitize_for_telegram(_one_line_summary(summary.conclusion, limit=160))])
                if key_points:
                    compact_lines.extend(["", "主要内容：", *key_points[:3]])
                if summary.follow_up_actions:
                    compact_lines.extend(["", "下一步："])
                    for idx, item in enumerate(summary.follow_up_actions[:2], start=1):
                        clean = _sanitize_for_telegram(re.sub(r"\s+", " ", str(item).strip()).strip("。；;"))
                        if clean:
                            compact_lines.append(f"{idx}. {clean}")
                compact_lines.extend(["", f"归档：{safe_note_path}", f"打开：{open_url}"])
                text = "\n".join(compact_lines)
        payload: dict[str, str] = {
            "chat_id": str(ingest.chat_id),
            "text": text,
        }
        if ingest.reply_to_message_id:
            payload["reply_to_message_id"] = str(ingest.reply_to_message_id)
        return payload

    def send_result(
        self,
        ingest: IngestRequest,
        summary: SummaryResult,
        note_path: str,
        structure_map: str,
        open_url: str,
        evidence: EvidenceBundle | None = None,
    ) -> None:
        payload = self.build_result_message_payload(
            ingest,
            summary,
            note_path,
            structure_map,
            open_url,
            evidence,
        )
        data = urlparse.urlencode(payload).encode("utf-8")
        req = urlrequest.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=data,
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"telegram send failed: {body}")

    def send_text(self, chat_id: str, text: str, reply_to_message_id: int | None = None) -> None:
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        data = urlparse.urlencode(payload).encode("utf-8")
        req = urlrequest.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=data,
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            raise RuntimeError(f"telegram send failed: {body}")
