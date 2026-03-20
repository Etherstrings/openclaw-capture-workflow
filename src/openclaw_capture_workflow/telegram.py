"""Telegram result sender."""

from __future__ import annotations

import json
import re
from urllib import parse as urlparse
from urllib import request as urlrequest

from .models import EvidenceBundle, IngestRequest, SummaryResult
from .video_experiment_summarizer import _dedupe_finance_row_fields, _normalize_executive_line, _normalize_finance_fact


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
        return "用途: 可以快速判断是否需要安装，以及先做哪一步。"
    if any(token in corpus for token in ["视频链接:", "关键链接:"]) and summary.coverage == "partial":
        return "用途: 可先用于粗筛，再决定是否回看原视频。"
    if any(token in corpus for token in ["视频链接:", "关键链接:"]):
        return "用途: 可快速判断是否放入待看清单。"
    if any(token in corpus for token in ["项目名称:", "github地址:"]):
        return "用途: 可快速判断项目是否值得继续跟进。"
    return "用途: 可快速获取主线结论和后续核验方向。"


def _recommendation_line(summary: SummaryResult) -> str:
    mapping = {
        "must_read": "优先级：高",
        "recommended": "优先级：中高",
        "optional": "优先级：中",
        "skip": "优先级：低",
    }
    return mapping.get(summary.recommendation_level, "优先级：中")


def _is_videoish(summary: SummaryResult, ingest: IngestRequest) -> bool:
    url = (ingest.source_url or "").lower()
    if ingest.source_kind == "video_url":
        return True
    return any(domain in url for domain in ["bilibili.com", "youtu.be", "youtube.com", "xiaohongshu.com/explore"])


def _is_install_like(summary: SummaryResult, ingest: IngestRequest) -> bool:
    corpus = "\n".join([summary.title, summary.conclusion, *summary.bullets, *summary.follow_up_actions]).lower()
    return any(token in corpus for token in ["安装", "部署", "onboard", "gateway", "/install-skill", "配对"])


def _summary_key_points_block(summary: SummaryResult, limit: int = 3) -> list[str]:
    bullets, _ = _display_bullets_for_telegram(summary, limit=limit)
    return bullets


def _non_video_judgment_lines(summary: SummaryResult) -> list[str]:
    lines: list[str] = []
    judgment = _simplify_reader_judgment(summary)
    if judgment:
        lines.append(judgment.rstrip("。") + "。")
    else:
        lines.append("当前已提炼主线信息，可按需回看原文细节。")
    if summary.coverage == "partial":
        lines.append("当前只覆盖到部分证据，细节最好回原文复核。")
    elif summary.follow_up_actions:
        action = re.sub(r"\s+", " ", str(summary.follow_up_actions[0]).strip()).strip("。；;")
        if action:
            if action.startswith(("如果", "继续", "进入", "打开", "先去")):
                lines.append(action if action.endswith("。") else action + "。")
            else:
                lines.append(f"如果后续要继续用这条内容，建议先{action}。")
    deduped: list[str] = []
    for item in lines:
        clean = re.sub(r"\s+", " ", item.strip())
        if not clean or clean in deduped:
            continue
        deduped.append(clean)
    return deduped[:2]


def _non_video_resource_lines(summary: SummaryResult) -> list[str]:
    _, link_lines = _display_bullets_for_telegram(summary, limit=5)
    project_lines = _extract_priority_project_lines(summary)
    lines: list[str] = []
    for item in project_lines[:2]:
        clean = re.sub(r"\s+", " ", str(item).strip())
        if clean and clean not in lines:
            lines.append(clean)
    for link in link_lines[:2]:
        clean = re.sub(r"\s+", " ", str(link).strip())
        if clean and clean not in lines:
            lines.append(f"链接: {clean}")
    return lines[:3]


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
        "这条视频在盘点简中互联网里最常见的 10 类糟糕交互设计，核心判断是很多设计服务的是导流、KPI 或平台利益，不是用户体验。",
        "",
        "关键信息：",
        "",
    ]
    for rank, chunk in chunks:
        title, detail = _ranked_chunk_to_title_and_detail(rank, chunk)
        lines.append(f"- 第{rank}名：{title}。{detail.rstrip('。')}")
    lines.extend(
        [
            "",
            "边界：",
            "- 这类内容偏观点表达，条目主线可以确认，但具体语气和例子仍建议按需回看原视频。",
        ]
    )
    evidence_note = _video_evidence_note(evidence)
    if evidence_note:
        lines.append(f"- {evidence_note}")
    return "\n".join(lines).strip()


def _video_has_any_track(evidence: EvidenceBundle | None) -> bool:
    metadata = evidence.metadata if evidence and isinstance(evidence.metadata, dict) else {}
    tracks = metadata.get("tracks", {}) if isinstance(metadata.get("tracks"), dict) else {}
    if tracks:
        return any(bool(tracks.get(key)) for key in ["has_subtitle", "has_transcript", "has_keyframes", "has_keyframe_ocr"])
    manifest = evidence.capture_manifest.to_dict() if evidence else {}
    return any(manifest.get(key, {}).get("status") == "ok" for key in ["subtitle", "asr", "keyframes", "keyframe_ocr"])


def _is_blocked_video_capture(summary: SummaryResult, evidence: EvidenceBundle | None) -> bool:
    if not evidence or evidence.source_kind != "video_url":
        return False
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    title = re.sub(r"\s+", " ", str(evidence.title or summary.title or "").strip())
    warning_text = " ".join(str(item) for item in metadata.get("fetch_warnings", [])[:4]) if isinstance(metadata.get("fetch_warnings"), list) else ""
    if any(token in title for token in ["页面不见了", "暂时无法浏览", "无法浏览"]):
        return True
    if "web_blocked_notice" in (metadata.get("evidence_sources", []) if isinstance(metadata.get("evidence_sources"), list) else []):
        return True
    return (not _video_has_any_track(evidence)) and bool(warning_text.strip())


def _is_degraded_video_summary(summary: SummaryResult) -> bool:
    tags = {re.sub(r"\s+", " ", str(item).strip()) for item in summary.note_tags}
    return bool(tags & {"video_theme_only", "video_model_unavailable", "video_refused"})


def _video_brief_key_lines(summary: SummaryResult, evidence: EvidenceBundle | None, *, limit: int = 5) -> list[str]:
    corpus = _video_corpus(evidence, summary).lower()
    special_patterns = ["openclaw", "world monitor", "wordmonitor", "全球实时监控", "世界地图", "自然灾害", "情报中心"]
    detail_first_lines: list[str] = []
    for raw in _video_detail_lines(summary, evidence):
        clean = _clean_video_note_fact(raw, max_len=72)
        if clean and clean not in detail_first_lines:
            detail_first_lines.append(clean)
        if len(detail_first_lines) >= limit:
            break
    if detail_first_lines and any(token in corpus for token in special_patterns):
        return detail_first_lines[:limit]
    lines = _core_judgment_lines(summary, evidence, limit=limit)
    if lines:
        return lines[:limit]
    if detail_first_lines:
        return detail_first_lines[:limit]
    for section in summary.timeline_sections[:3]:
        if not isinstance(section, dict):
            continue
        heading = re.sub(r"\s+", " ", str(section.get("heading", "")).strip())
        section_summary = _clean_video_note_fact(str(section.get("summary", "")), max_len=72)
        if heading and section_summary:
            candidate = f"{heading}：{section_summary}"
        else:
            candidate = section_summary or heading
        if candidate and candidate not in lines:
            lines.append(candidate)
        if len(lines) >= limit:
            break
    return lines[:limit]


def _video_boundary_lines(summary: SummaryResult, evidence: EvidenceBundle | None) -> list[str]:
    lines: list[str] = []
    if _is_blocked_video_capture(summary, evidence):
        lines.extend(
            [
                "当前拿不到正文或稳定证据，现有结果不能当内容总结使用。",
                "原因更像页面或平台限制，不是内容已经被成功提取。",
            ]
        )
    elif summary.outcome == "refused":
        reason = re.sub(r"\s+", " ", str(summary.refusal_reason or summary.conclusion or "").strip())
        if reason:
            lines.append(reason)
    elif "video_model_unavailable" in summary.note_tags:
        lines.append("当前拿到了部分证据，但总结模型不可用，这版结果不能给出可靠细节。")
    elif "video_theme_only" in summary.note_tags:
        lines.append("当前只能确认主题和大致范围，细节级结论不可靠。")
    elif summary.coverage == "partial":
        lines.append("当前只覆盖到部分证据，适合先看主线，不适合当完整逐段总结。")
    if summary.uncertainties:
        for item in summary.uncertainties[:3]:
            clean = _clean_video_note_fact(str(item), max_len=72)
            if clean and clean not in lines:
                lines.append(clean)
    evidence_note = _video_evidence_note(evidence)
    if evidence_note and evidence_note not in lines:
        lines.append(evidence_note)
    deduped: list[str] = []
    for item in lines:
        clean = re.sub(r"\s+", " ", str(item).strip())
        if not clean or clean in deduped:
            continue
        deduped.append(clean)
    if not deduped:
        deduped.append("主线已经能确认，但细节仍建议按需回看原视频复核。")
    return deduped[:4]


def _render_story_paragraph(lines: list[str], *, max_items: int = 3) -> str:
    cleaned: list[str] = []
    for raw in lines:
        text = _clean_video_note_fact(raw, max_len=84)
        if not text:
            continue
        text = text.rstrip("。")
        if text in cleaned:
            continue
        cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0] + "。"
    return "；".join(cleaned) + "。"


def _render_story_section(title: str, lines: list[str], *, max_items: int = 3) -> str:
    paragraph = _render_story_paragraph(lines, max_items=max_items)
    if not paragraph:
        return ""
    return f"{title}\n{paragraph}"


def _render_video_summary_tech_line(
    evidence: EvidenceBundle | None,
    *,
    summary_model: str | None = None,
) -> str:
    if evidence is None:
        return ""
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    sources = metadata.get("evidence_sources", []) if isinstance(metadata.get("evidence_sources"), list) else []
    labels: list[str] = []
    mapping = {
        "video_audio_asr": "语音转写",
        "video_subtitles": "字幕",
        "video_keyframes": "关键帧",
        "video_keyframe_ocr": "关键帧OCR",
        "video_platform_metadata": "平台元数据",
    }
    for raw in sources:
        label = mapping.get(str(raw), "")
        if label and label not in labels:
            labels.append(label)
    model_label = re.sub(r"\s+", " ", str(summary_model or "").strip())
    if model_label and labels:
        return f"本次总结基于{ '、'.join(labels)}与{model_label}模型生成。"
    if labels:
        return f"本次总结基于{ '、'.join(labels)}生成。"
    if model_label:
        return f"本次总结由{model_label}模型生成。"
    return ""


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
    return f"证据主要来自{joined}，转写可能仍有少量口语或术语误差。"


def _render_video_direct_reply(
    ingest: IngestRequest,
    summary: SummaryResult,
    evidence: EvidenceBundle | None,
    *,
    assistant_name: str = "Milky",
    summary_elapsed_seconds: float | None = None,
    summary_model: str | None = None,
) -> str:
    opening = _sanitize_for_telegram(_video_note_opening(summary, evidence) or _video_opening_line(summary, evidence))
    detail_lines = [_sanitize_for_telegram(item) for item in _video_brief_key_lines(summary, evidence)]
    boundary_lines = [_sanitize_for_telegram(item) for item in _video_boundary_lines(summary, evidence)]
    workflow = _video_block_summary(evidence, "workflow")
    implementation = _video_block_summary(evidence, "implementation")
    risk = _video_block_summary(evidence, "risk")
    feedback = _video_block_summary(evidence, "viewer_feedback")

    elapsed_label = ""
    try:
        elapsed = float(summary_elapsed_seconds or 0.0)
    except (TypeError, ValueError):
        elapsed = 0.0
    if elapsed > 0:
        elapsed_label = f"{elapsed:.2f}秒"
    intro = (
        f"{assistant_name} 花了 {elapsed_label} 看完了视频，为你总结如下：".replace("  ", " ").replace("  ", " ").strip()
        if elapsed_label
        else f"{assistant_name} 已完成视频总结，为你总结如下："
    )

    # Try to render in the requested narrative style first.
    section_blocks: list[str] = []
    block_top = _render_story_section("🧊 关键做法", [workflow] if workflow else detail_lines[:2], max_items=3)
    block_mid = _render_story_section("💡 关键思路", [implementation] if implementation else detail_lines[2:5], max_items=3)
    block_risk = _render_story_section("🔥 边界与风险", [risk] if risk else boundary_lines, max_items=3)
    if block_top:
        section_blocks.append(block_top)
    if block_mid:
        section_blocks.append(block_mid)
    if block_risk:
        section_blocks.append(block_risk)
    if feedback:
        feedback_block = _render_story_section("🗣️ 观众反馈", [feedback], max_items=2)
        if feedback_block:
            section_blocks.append(feedback_block)

    tech_line = _render_video_summary_tech_line(evidence, summary_model=summary_model)
    lines: list[str] = [intro, opening]
    if section_blocks:
        lines.extend(section_blocks)
    else:
        if detail_lines:
            lines.append(_render_story_section("🧊 关键做法", detail_lines, max_items=4))
        if boundary_lines:
            lines.append(_render_story_section("🔥 边界与风险", boundary_lines, max_items=3))
    if tech_line:
        lines.append(tech_line)
    lines.append(f"记得随时呼叫{assistant_name}哦！")
    return "\n\n".join([item for item in lines if str(item).strip()])


def _render_refusal_text(summary: SummaryResult, evidence: EvidenceBundle | None) -> str:
    lines = [summary.title or "视频内容不可总结", "", summary.refusal_reason or summary.conclusion or "当前没有足够证据支撑内容总结。"]
    if summary.uncertainties:
        lines.extend(["", "证据缺口："])
        for item in summary.uncertainties[:4]:
            text = re.sub(r"\s+", " ", str(item).strip())
            if text:
                lines.append(f"- {text}")
    if summary.evidence_basis:
        lines.extend(["", "当前证据："])
        for item in summary.evidence_basis[:4]:
            lines.append(f"- {item}")
    source_url = (evidence.source_url if evidence else "") or ""
    if source_url:
        lines.extend(["", source_url])
    return "\n".join(lines)


def _render_timeline_sections(summary: SummaryResult) -> str:
    lines: list[str] = [summary.title]
    if summary.conclusion:
        lines.extend(["", summary.conclusion])
    if summary.timeline_sections:
        lines.extend(["", "时间线："])
        for section in summary.timeline_sections[:6]:
            if not isinstance(section, dict):
                continue
            start = section.get("start")
            end = section.get("end")
            heading = re.sub(r"\s+", " ", str(section.get("heading", "")).strip())
            section_summary = re.sub(r"\s+", " ", str(section.get("summary", "")).strip())
            label = heading or "时间段"
            time_label = ""
            if isinstance(start, (int, float)):
                time_label = f"[{int(start // 60):02d}:{int(start % 60):02d}] "
            if isinstance(end, (int, float)) and isinstance(start, (int, float)):
                time_label = f"[{int(start // 60):02d}:{int(start % 60):02d}-{int(end // 60):02d}:{int(end % 60):02d}] "
            if section_summary:
                lines.append(f"{time_label}{label}: {section_summary}")
            elif label:
                lines.append(f"{time_label}{label}")
    elif summary.bullets:
        lines.extend(["", "主要内容："])
        for item in summary.bullets[:5]:
            text = re.sub(r"\s+", " ", str(item).strip())
            if text:
                lines.append(f"- {text}")
    if summary.uncertainties:
        lines.extend(["", "局限："])
        for item in summary.uncertainties[:3]:
            text = re.sub(r"\s+", " ", str(item).strip())
            if text:
                lines.append(f"- {text}")
    return "\n".join(lines)


def _video_duration_seconds(evidence: EvidenceBundle | None) -> float:
    metadata = evidence.metadata if evidence and isinstance(evidence.metadata, dict) else {}
    for key in ["video_duration_seconds", "bilibili_duration_seconds"]:
        try:
            value = float(metadata.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _format_time_range_markdown(start: object, end: object) -> str:
    if not isinstance(start, (int, float)):
        return ""
    start_label = f"{int(start // 60):02d}:{int(start % 60):02d}"
    if isinstance(end, (int, float)):
        end_label = f"{int(end // 60):02d}:{int(end % 60):02d}"
        return f"{start_label}-{end_label}"
    return start_label


def _clean_video_note_fact(value: str, *, max_len: int = 88) -> str:
    text = re.sub(r"^\d+\.\s*", "", re.sub(r"\s+", " ", str(value or "").strip())).strip("。；;")
    if not text:
        return ""
    if text.startswith(("视频链接:", "关键链接:", "GitHub地址:", "仓库地址:", "文档链接:")):
        return ""
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        return ""
    if any(token in lowered for token in ["spm_id_from", "search-card", "vd_source"]):
        return ""
    if re.fullmatch(r"[0-9a-z._?&=:/+-]{12,}", lowered):
        return ""
    sentences = [item.strip("，,：:；;。 ") for item in re.split(r"[。；;\n]+", text) if item.strip("，,：:；;。 ")]
    if sentences:
        text = sentences[0]
    if len(text) > max_len:
        clauses = [item.strip("，,：:；;。 ") for item in re.split(r"[，,]", text) if item.strip("，,：:；;。 ")]
        kept: list[str] = []
        for item in clauses:
            if len(item) < 4:
                continue
            kept.append(item)
            if len("，".join(kept)) >= max_len - 8 or len(kept) >= 2:
                break
        if kept:
            text = "，".join(kept)
    if len(text) > max_len:
        text = text[:max_len].rstrip("，,：:；; ") + "..."
    return text


def _normalize_finance_rows_for_display(summary: SummaryResult) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in summary.finance_matrix[:10]:
        if not isinstance(raw, dict):
            continue
        name = re.sub(r"\s+", " ", str(raw.get("name", "")).strip())
        if not name:
            continue
        row = _dedupe_finance_row_fields(
            {
                "name": name,
                "sector": _normalize_executive_line(raw.get("sector", ""), max_len=24),
                "thesis": _normalize_finance_fact(raw.get("thesis", ""), kind="thesis", entity_name=name, max_len=56),
                "position_change": _normalize_finance_fact(raw.get("position_change", ""), kind="position_change", entity_name=name, max_len=48),
                "risk": _normalize_finance_fact(raw.get("risk", ""), kind="risk", entity_name=name, max_len=48),
            }
        )
        if any(row[field] for field in ["thesis", "position_change", "risk"]):
            rows.append(row)
    return rows


def _normalize_finance_snapshot_for_display(summary: SummaryResult) -> dict[str, list[str]]:
    snapshot = summary.finance_snapshot if isinstance(summary.finance_snapshot, dict) else {}
    normalized: dict[str, list[str]] = {}
    for key in ["market_view", "performance_review", "action_plan"]:
        values = snapshot.get(key, [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        cleaned: list[str] = []
        for item in values:
            line = _normalize_executive_line(item, max_len=72)
            if not line or line in cleaned:
                continue
            cleaned.append(line)
            if len(cleaned) >= 4:
                break
        if cleaned:
            normalized[key] = cleaned
    return normalized


def _strip_video_note_label_prefix(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip()).strip("。；;")
    if not text:
        return ""
    return re.sub(r"^[^：:]{1,10}[：:]\s*", "", text).strip()


def _executive_bullet_lines(summary: SummaryResult, *, limit: int = 5) -> list[str]:
    lines: list[str] = []
    for raw in summary.bullets:
        clean = _clean_video_note_fact(str(raw))
        if not clean or clean in lines:
            continue
        lines.append(clean)
        if len(lines) >= limit:
            break
    return lines


def _simplify_reader_judgment(summary: SummaryResult) -> str:
    judgment = re.sub(r"\s+", " ", str(summary.reader_judgment or "").strip())
    if not judgment:
        return ""
    replacements = {
        "从大厂程序员视角看，这条内容更适合用来快速筛选是否值得后续回看。": "更适合先粗筛，再决定是否值得回看。",
        "从大厂程序员视角看，这条内容有信息价值，但是否深入跟进取决于当前任务相关性。": "适合作为背景资料留档，是否继续深挖取决于当前任务。",
        "从大厂程序员视角看，这条内容偏实用，适合直接留作后续操作参考。": "偏实用，适合直接留作后续参考。",
        "更适合先看提炼结果，再决定是否需要回看原视频。": "更适合先看提炼结果，再决定是否需要回看原视频。",
        "偏实用，适合作为后续操作时的参考。": "偏实用，适合作为后续操作时的参考。",
        "有信息价值，是否继续深入取决于当前任务相关性。": "有信息价值，是否继续深入取决于当前任务相关性。",
    }
    return replacements.get(judgment, judgment)


def _finance_derived_core_lines(summary: SummaryResult) -> list[str]:
    lines: list[str] = []
    snapshot = _normalize_finance_snapshot_for_display(summary)
    rows = _normalize_finance_rows_for_display(summary)
    market_view = snapshot.get("market_view", []) if isinstance(snapshot.get("market_view", []), list) else []
    performance_review = snapshot.get("performance_review", []) if isinstance(snapshot.get("performance_review", []), list) else []
    action_plan = snapshot.get("action_plan", []) if isinstance(snapshot.get("action_plan", []), list) else []
    if market_view:
        lines.append(f"市场判断：{_clean_video_note_fact(str(market_view[0]), max_len=72)}")
    if performance_review:
        lines.append(f"组合表现：{_clean_video_note_fact(str(performance_review[0]), max_len=72)}")
    row_phrases: list[str] = []
    for row in rows[:2]:
        name = re.sub(r"\s+", " ", str(row.get("name", "")).strip())
        thesis = _clean_video_note_fact(str(row.get("thesis", "")), max_len=52)
        position_change = _clean_video_note_fact(str(row.get("position_change", "")), max_len=52)
        pieces = [item for item in [thesis, position_change] if item]
        if name and pieces:
            row_phrases.append(f"{name}={ '，'.join(pieces[:2]) }")
    if row_phrases:
        lines.append(f"标的取舍：{'；'.join(row_phrases)}")
    if action_plan:
        plan_lines = [_clean_video_note_fact(str(item), max_len=52) for item in action_plan[:2]]
        plan_lines = [item for item in plan_lines if item]
        if plan_lines:
            lines.append(f"后续计划：{'；'.join(plan_lines)}")
    return [item for item in lines if item][:5]


def _core_judgment_lines(summary: SummaryResult, evidence: EvidenceBundle | None, *, limit: int = 5) -> list[str]:
    if _is_finance_video(summary, evidence):
        finance_lines = _finance_derived_core_lines(summary)
        if finance_lines:
            return finance_lines[:limit]
    lines = _executive_bullet_lines(summary, limit=limit)
    if lines:
        return lines[:limit]
    return []


def has_direct_video_note_body(summary: SummaryResult, evidence: EvidenceBundle | None) -> bool:
    if not evidence or evidence.source_kind != "video_url":
        return False
    if summary.outcome == "refused" or _is_degraded_video_summary(summary):
        return True
    core_lines = _core_judgment_lines(summary, evidence, limit=3)
    if _is_finance_video(summary, evidence):
        return bool(summary.finance_matrix or summary.finance_snapshot or core_lines)
    return len(core_lines) >= 2


def _is_finance_video(summary: SummaryResult, evidence: EvidenceBundle | None) -> bool:
    if summary.finance_matrix:
        return True
    metadata = evidence.metadata if evidence and isinstance(evidence.metadata, dict) else {}
    estimate = metadata.get("video_direction_estimate", {})
    if isinstance(estimate, dict) and str(estimate.get("kind", "")).strip() == "finance_market":
        return True
    corpus = _video_corpus(evidence, summary)
    return any(token in corpus for token in ["股票", "持仓", "估值", "港股", "指数", "买入", "卖出"])


def _is_long_video_note_candidate(summary: SummaryResult, evidence: EvidenceBundle | None) -> bool:
    if summary.finance_matrix:
        return True
    duration_seconds = _video_duration_seconds(evidence)
    if duration_seconds >= 600 and summary.timeline_sections:
        return True
    return len(summary.timeline_sections) >= 3


def _escape_markdown_table_cell(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).replace("|", "｜")


def _video_note_opening(summary: SummaryResult, evidence: EvidenceBundle | None) -> str:
    opening = re.sub(r"\s+", " ", _video_opening_line(summary, evidence).strip())
    judgment = re.sub(r"\s+", " ", str(summary.reader_judgment or "").strip())
    conclusion = re.sub(r"\s+", " ", str(summary.conclusion or "").strip())
    if any(token in conclusion for token in ["已按时间段整理出视频主线", "视频按时间顺序讲了", "视频按时间顺序解释了", "可先用于快速筛选"]):
        conclusion = ""
    if any(token in opening for token in ["已按时间段整理出视频主线", "视频按时间顺序讲了", "视频按时间顺序解释了"]):
        opening = ""
    if not opening:
        core_lines = _core_judgment_lines(summary, evidence, limit=2)
        opening_bits = [_strip_video_note_label_prefix(item) for item in core_lines if _strip_video_note_label_prefix(item)]
        if opening_bits:
            opening = "核心在于：" + "；".join(opening_bits[:2])
    pieces: list[str] = []
    for item in [opening, judgment, conclusion]:
        text = item.strip()
        if not text:
            continue
        if text in pieces:
            continue
        pieces.append(text if text.endswith("。") else text + "。")
        if len(pieces) >= 2:
            break
    return " ".join(pieces).strip()


def _render_core_judgments_markdown(summary: SummaryResult, evidence: EvidenceBundle | None) -> list[str]:
    lines = _core_judgment_lines(summary, evidence, limit=5)
    if not lines:
        return []
    rendered: list[str] = ["## 核心判断", ""]
    for item in lines:
        rendered.append(f"- {item}")
    rendered.append("")
    return rendered


def _render_non_finance_value_markdown(summary: SummaryResult) -> list[str]:
    lines: list[str] = ["## 内容主线与用途", ""]
    judgment = _simplify_reader_judgment(summary)
    if judgment:
        lines.append(f"- {judgment}")
    lines.append(f"- {_recommendation_line(summary)}")
    if summary.follow_up_actions:
        action = _clean_video_note_fact(str(summary.follow_up_actions[0]), max_len=68)
        if action:
            lines.append(f"- 后续核验：{action}")
    if len(lines) <= 2:
        lines.append("- 适合作为后续回看或留档时的快速摘要入口。")
    lines.append("")
    return lines


def _render_non_finance_boundary_markdown(summary: SummaryResult) -> list[str]:
    lines: list[str] = ["## 适用边界与风险", ""]
    if summary.uncertainties:
        for item in summary.uncertainties[:3]:
            clean = _clean_video_note_fact(str(item), max_len=68)
            if clean:
                lines.append(f"- {clean}")
    elif summary.coverage == "partial":
        lines.append("- 当前只能确认主线和部分细节，不适合当成完整逐句版。")
    else:
        lines.append("- 主线已经够清楚，但细节仍建议按需回看原视频复核。")
    if summary.follow_up_actions:
        action = _clean_video_note_fact(str(summary.follow_up_actions[0]), max_len=68)
        if action:
            lines.append(f"- 继续确认：{action}")
    lines.append("")
    return lines


def _render_timeline_sections_markdown(summary: SummaryResult) -> list[str]:
    lines: list[str] = ["## 时间线附录", ""]
    for section in summary.timeline_sections[:5]:
        if not isinstance(section, dict):
            continue
        heading = re.sub(r"\s+", " ", str(section.get("heading", "")).strip()) or "时间段"
        label = _format_time_range_markdown(section.get("start"), section.get("end"))
        section_title = f"{label} {heading}".strip() if label else heading
        lines.append(f"### {section_title}")
        summary_line = re.sub(r"\s+", " ", str(section.get("summary", "")).strip())
        bullets = section.get("bullets", [])
        if not isinstance(bullets, list):
            bullets = []
        detail_lines: list[str] = []
        if summary_line:
            clean_summary = _clean_video_note_fact(summary_line, max_len=68)
            if clean_summary:
                detail_lines.append(clean_summary.rstrip("。"))
        for raw in bullets[:3]:
            text = _clean_video_note_fact(str(raw), max_len=56)
            if text and text not in detail_lines:
                detail_lines.append(text)
            if len(detail_lines) >= 3:
                break
        if not detail_lines:
            detail_lines = ["当前时间段有内容，但未提炼出稳定要点"]
        for item in detail_lines:
            lines.append(f"- {item}")
        lines.append("")
    return lines


def _render_finance_matrix_markdown(summary: SummaryResult) -> list[str]:
    rows = _normalize_finance_rows_for_display(summary)
    if not rows:
        return []
    lines = [
        "## 标的矩阵",
        "",
        "| 股票名称 | 板块 | 投资逻辑/估值情况 | 涨跌/持仓变动 | 预期/风险点 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_markdown_table_cell(str(row.get("name", ""))),
                    _escape_markdown_table_cell(str(row.get("sector", ""))),
                    _escape_markdown_table_cell(str(row.get("thesis", ""))),
                    _escape_markdown_table_cell(str(row.get("position_change", ""))),
                    _escape_markdown_table_cell(str(row.get("risk", ""))),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _render_finance_cards_markdown(summary: SummaryResult) -> list[str]:
    rows = _normalize_finance_rows_for_display(summary)
    if not rows:
        return []
    lines = ["## 标的卡片", ""]
    for row in rows:
        name = re.sub(r"\s+", " ", str(row.get("name", "")).strip())
        if not name:
            continue
        lines.append(f"### {name}")
        mapping = [
            ("板块", "sector"),
            ("投资逻辑/估值情况", "thesis"),
            ("涨跌/持仓变动", "position_change"),
            ("预期/风险点", "risk"),
        ]
        for label, key in mapping:
            value = re.sub(r"\s+", " ", str(row.get(key, "")).strip())
            if value:
                lines.append(f"- {label}：{value}")
        lines.append("")
    return lines


def _render_finance_snapshot_markdown(summary: SummaryResult) -> list[str]:
    snapshot = _normalize_finance_snapshot_for_display(summary)
    if not snapshot:
        return []
    lines = ["## 市场判断与后续计划", ""]
    for label, key in [("市场判断", "market_view"), ("业绩回顾", "performance_review"), ("后续计划", "action_plan")]:
        values = snapshot.get(key, [])
        cleaned = [re.sub(r"\s+", " ", str(item).strip()).strip("。；;") for item in values if str(item).strip()]
        if not cleaned:
            continue
        lines.append(f"### {label}")
        for item in cleaned[:4]:
            lines.append(f"- {item}")
        lines.append("")
    return lines


def _render_reliability_markdown(summary: SummaryResult, evidence: EvidenceBundle | None) -> list[str]:
    metadata = evidence.metadata if evidence and isinstance(evidence.metadata, dict) else {}
    tracks = metadata.get("tracks", {}) if isinstance(metadata.get("tracks"), dict) else {}
    source_labels = {
        "user_raw_text": "用户提示",
        "video_platform_metadata": "平台元数据",
        "video_audio_asr": "音频转写",
        "video_subtitles": "字幕轨",
        "video_keyframes": "关键帧",
        "video_keyframe_ocr": "关键帧 OCR",
        "bilibili_mcp_transcript": "Bilibili MCP 转写",
        "xiaohongshu_mcp_transcript": "小红书 MCP 转写",
    }
    raw_sources = metadata.get("evidence_sources", []) if isinstance(metadata.get("evidence_sources"), list) else []
    sources: list[str] = []
    for item in raw_sources:
        text = source_labels.get(str(item), str(item))
        if text and text not in sources:
            sources.append(text)
    lines = ["## 可信度与证据", ""]
    lines.append(f"- 覆盖度：{summary.coverage or 'unknown'}；置信度：{summary.confidence or 'unknown'}")
    if sources:
        lines.append(f"- 证据来源：{' / '.join(sources[:6])}")
    if tracks:
        lines.append(
            "- 证据轨道：字幕={subtitle}，转写={transcript}，关键帧={keyframes}，OCR={ocr}".format(
                subtitle="有" if tracks.get("has_subtitle") else "无",
                transcript="有" if tracks.get("has_transcript") else "无",
                keyframes="有" if tracks.get("has_keyframes") else "无",
                ocr="有" if tracks.get("has_keyframe_ocr") else "无",
            )
        )
    if evidence and evidence.source_url:
        lines.append(f"- 来源链接：{evidence.source_url}")
    if summary.uncertainties:
        for item in summary.uncertainties[:4]:
            text = re.sub(r"\s+", " ", str(item).strip())
            if text:
                lines.append(f"- 局限：{text}")
    elif summary.coverage == "full":
        lines.append("- 当前没有明显证据缺口，但细节仍建议在回看原视频时复核。")
    return lines


def _render_video_brief_markdown(summary: SummaryResult, evidence: EvidenceBundle | None) -> str:
    title = re.sub(r"\s+", " ", str(summary.title or evidence.title if evidence else "").strip()) or "视频简报"
    lines: list[str] = [f"# {title}", ""]
    opening = _video_note_opening(summary, evidence) or _video_opening_line(summary, evidence)
    if opening:
        lines.extend([opening, ""])
    key_lines = _video_brief_key_lines(summary, evidence)
    if key_lines:
        lines.extend(["## 关键信息", ""])
        for item in key_lines:
            lines.append(f"- {item}")
        lines.append("")
    boundary_lines = _video_boundary_lines(summary, evidence)
    if boundary_lines:
        lines.extend(["## 边界与证据", ""])
        for item in boundary_lines:
            lines.append(f"- {item}")
    return "\n".join(lines).strip()


def render_video_note_markdown(summary: SummaryResult, evidence: EvidenceBundle | None) -> str:
    if summary.outcome == "refused":
        return _render_refusal_text(summary, evidence)
    if _is_blocked_video_capture(summary, evidence) or _is_degraded_video_summary(summary) or not _is_long_video_note_candidate(summary, evidence):
        return _render_video_brief_markdown(summary, evidence)
    lines: list[str] = [f"# {summary.title}", ""]
    opening = _video_note_opening(summary, evidence)
    if opening:
        lines.extend([opening, ""])
    core_lines = _render_core_judgments_markdown(summary, evidence)
    if core_lines:
        lines.extend(core_lines)
    if _is_finance_video(summary, evidence):
        snapshot_lines = _render_finance_snapshot_markdown(summary)
        if snapshot_lines:
            lines.extend(snapshot_lines)
        matrix_lines = _render_finance_matrix_markdown(summary)
        if matrix_lines:
            lines.extend(matrix_lines)
        card_lines = _render_finance_cards_markdown(summary)
        if card_lines:
            lines.extend(card_lines)
    else:
        lines.extend(_render_non_finance_value_markdown(summary))
        lines.extend(_render_non_finance_boundary_markdown(summary))
    lines.extend(_render_timeline_sections_markdown(summary))
    lines.extend(_render_reliability_markdown(summary, evidence))
    return "\n".join(lines).strip()


def render_video_user_facing_text(
    summary: SummaryResult,
    evidence: EvidenceBundle | None,
    *,
    assistant_name: str = "Milky",
    summary_elapsed_seconds: float | None = None,
    summary_model: str | None = None,
) -> str:
    if summary.outcome == "refused":
        return _render_refusal_text(summary, evidence)
    return _render_ranked_rant_video_reply(summary, evidence) or _render_video_direct_reply(
        IngestRequest(chat_id="", reply_to_message_id=None, request_id="", source_kind="video_url"),
        summary,
        evidence,
        assistant_name=assistant_name,
        summary_elapsed_seconds=summary_elapsed_seconds,
        summary_model=summary_model,
    )


class TelegramNotifier:
    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token

    def build_refusal_message(self, summary: SummaryResult, evidence: EvidenceBundle | None = None) -> str:
        return _sanitize_for_telegram(_render_refusal_text(summary, evidence))

    def _completion_line(self, summary_model: str | None, summary_elapsed_seconds: float | None) -> str:
        label = re.sub(r"\s+", " ", str(summary_model or "").strip())
        if not label:
            return ""
        if label == "fallback":
            label = "fallback 规则总结器"
        elif label == "cache":
            label = "缓存结果"
        try:
            seconds = float(summary_elapsed_seconds if summary_elapsed_seconds is not None else 0.0)
        except (TypeError, ValueError):
            seconds = 0.0
        return f"使用{label}模型经过{seconds:.1f}秒总结完成。"

    def build_result_message_payload(
        self,
        ingest: IngestRequest,
        summary: SummaryResult,
        note_path: str,
        structure_map: str,
        open_url: str,
        evidence: EvidenceBundle | None = None,
        summary_model: str | None = None,
        summary_elapsed_seconds: float | None = None,
    ) -> dict[str, str]:
        if summary.outcome == "refused":
            text = self.build_refusal_message(summary, evidence)
        elif _is_videoish(summary, ingest):
            text = render_video_user_facing_text(
                summary,
                evidence,
                assistant_name="Milky",
                summary_elapsed_seconds=summary_elapsed_seconds,
                summary_model=summary_model,
            )
        else:
            safe_note_path = _sanitize_for_telegram(note_path)
            safe_title = _sanitize_for_telegram(summary.title)
            conclusion_line = _sanitize_for_telegram(re.sub(r"\s+", " ", str(summary.conclusion or "").strip()))
            key_points = [_sanitize_for_telegram(item) for item in _summary_key_points_block(summary, limit=5)]
            judgment_lines = [_sanitize_for_telegram(item) for item in _non_video_judgment_lines(summary)]
            resource_lines = [_sanitize_for_telegram(item) for item in _non_video_resource_lines(summary)]
            lines: list[str] = [safe_title]
            if conclusion_line:
                lines.extend(["", conclusion_line])
            if key_points:
                lines.extend(["", "关键信息："])
                lines.extend([f"- {item}" for item in key_points])
            if judgment_lines:
                lines.extend(["", "判断："])
                lines.extend([f"- {item}" for item in judgment_lines])
            if resource_lines:
                lines.extend(["", "资料："])
                lines.extend([f"- {item}" for item in resource_lines])
            lines.extend(["", f"归档：{safe_note_path}", f"打开：{open_url}"])
            text = "\n".join(lines)
            if len(text) > 3500:
                compact_lines: list[str] = [safe_title]
                if conclusion_line:
                    compact_lines.extend(["", _sanitize_for_telegram(_one_line_summary(summary.conclusion, limit=160))])
                if key_points:
                    compact_lines.extend(["", "关键信息："])
                    compact_lines.extend([f"- {item}" for item in key_points[:3]])
                if judgment_lines:
                    compact_lines.extend(["", "判断："])
                    compact_lines.extend([f"- {item}" for item in judgment_lines[:1]])
                if resource_lines:
                    compact_lines.extend(["", "资料："])
                    compact_lines.extend([f"- {item}" for item in resource_lines[:2]])
                compact_lines.extend(["", f"归档：{safe_note_path}", f"打开：{open_url}"])
                text = "\n".join(compact_lines)
        completion_line = _sanitize_for_telegram(self._completion_line(summary_model, summary_elapsed_seconds))
        if completion_line:
            text = text.rstrip() + "\n\n" + completion_line
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
        summary_model: str | None = None,
        summary_elapsed_seconds: float | None = None,
    ) -> None:
        payload = self.build_result_message_payload(
            ingest,
            summary,
            note_path,
            structure_map,
            open_url,
            evidence,
            summary_model,
            summary_elapsed_seconds,
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
