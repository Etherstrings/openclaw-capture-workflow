#!/usr/bin/env python3
"""Generate a capability report for the current video pipeline."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    root = _project_root()
    report_dir = root / "state" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"video_capabilities_{timestamp}.md"
    content = """# 视频能力现状报告

## 页面元数据抓取

- 当前输入来源：Bilibili 视频页元数据接口 + 页面标题
- 触发时机：视频 URL 命中 bilibili 平台路径时
- 成功标志：`video_platform_metadata` 出现在 `evidence_sources`
- 已知失败模式：公开视频接口不可用、平台限制、页面标题噪声
- 样例覆盖：`sample-bili-long-1773325820.md`、`report-bili-project-1773329130.md`

## 字幕提取

- 当前输入来源：`scripts/video_subtitle_extract.py`
- 触发时机：存在 `video_subtitle_command`
- 成功标志：字幕文本进入 evidence，`video_subtitles` 出现在 `evidence_sources`
- 已知失败模式：平台无字幕、反爬、下载失败
- 样例覆盖：`tests/test_extractor.py` 的字幕分支用例

## 音频 ASR

- 当前输入来源：`scripts/video_audio_asr.py`
- 触发时机：字幕不足或 `always_run_audio=true`
- 成功标志：转写文本进入 evidence，`video_audio_asr` 出现在 `evidence_sources`
- 已知失败模式：下载失败、容器识别失败、STT 接口失败
- 样例覆盖：`sample-bili-long-1773325820.md`、`sample-xhs-long-1773325820.md`

## Gemini 视频总结实验路径

- 当前输入来源：AiHubMix 代理上的 Gemini 系列模型
- 触发时机：`run_video_truth_validation.py` 中的 Gemini 对照实验
- 成功标志：同一个视频 case 产生 `current_model / gemini_2_5_pro / gemini_2_5_flash` 三组结果
- 已知失败模式：模型返回结构仍可能过于概括；若只喂字幕而非结构化 outline，则不一定优于现有模型
- 当前状态：已接通并完成真实对照回测

## SiliconFlow STT 对照入口

- 当前输入来源：配置级 STT provider 预留
- 触发时机：后续视频链路实验时切换音频转写 provider
- 成功标志：同一视频在不同 STT provider 下能稳定对比转写质量
- 当前状态：主链仍以现有 OpenAI-compatible STT 为准；SiliconFlow 作为下一轮对照入口

## 关键帧抽取

- 当前输入来源：`scripts/video_keyframes_extract.py`
- 触发时机：存在 `video_keyframes_command`
- 成功标志：关键帧路径写入 metadata/keyframes
- 已知失败模式：视频下载失败、ffmpeg 失败、平台视频流异常
- 样例覆盖：`tests/test_extractor.py`、analyzer `video.py`

## 关键帧 OCR

- 当前输入来源：现有 OCR 命令 / 本地 OCR
- 触发时机：关键帧存在且启用 OCR
- 成功标志：`video_keyframe_ocr` 出现在 `evidence_sources`
- 已知失败模式：OCR 引擎不可用、帧文字稀疏、图片质量差
- 样例覆盖：`sample-xhs-long-1773325820.md`

## 证据合并

- 当前输入来源：页面文本 + 元数据 + 字幕 + ASR + 关键帧 + OCR
- 触发时机：视频抽取完成后
- 成功标志：`evidence.text` 足够长，`coverage=full` 或带明确 partial 原因
- 已知失败模式：长视频被压缩过度、视频点位结构丢失
- 样例覆盖：`sample-bili-long-1773325820.md`

## 视频质量门禁

- 当前输入来源：`processor._video_gate_reasons()`、`_video_assessment()`
- 触发时机：summarize 前
- 成功标志：`video_assessment` 与 `video_gate_reasons` 可解释
- 已知失败模式：证据足够但总结压缩过度；页内枚举结构未被保留
- 样例覆盖：`tests/test_processor_validation.py`

## 成本与预算控制

- 当前输入来源：`processor._estimate_video_cost_rmb()`
- 触发时机：视频 job summarize 前
- 成功标志：`video_cost_estimate` 存在
- 已知失败模式：预算预警存在，但不会自动改善总结质量
- 样例覆盖：`tests/test_processor.py::test_video_cost_estimate_warns_when_over_budget`

## 当前已知弱点

- 长视频的“结构点”现在能识别，但点位表达仍偏贴近原始转写，不够像人话
- 当前还没有一个真实“9点型”公开视频样本完成闭环验证
- STT 对照（现有路径 vs SiliconFlow）尚未纳入真实回测矩阵
"""
    report_path.write_text(content, encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
