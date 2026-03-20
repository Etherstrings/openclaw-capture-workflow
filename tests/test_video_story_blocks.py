import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.models import EvidenceBundle
from openclaw_capture_workflow.video_story_blocks import build_video_story_blocks


class VideoStoryBlocksTest(unittest.TestCase):
    def test_benchmark_video_story_blocks_do_not_use_generic_trading_templates(self) -> None:
        evidence = EvidenceBundle(
            source_kind="video_url",
            source_url="https://www.bilibili.com/video/BV1o9cSzpExL",
            platform_hint="bilibili",
            title="本地AI哪家强？统一内存大横评！",
            text=(
                "标题: 本地AI哪家强？统一内存大横评！\n"
                "简介: 我们集齐了来自苹果、AMD、英伟达的三款128GB超大统一内存电脑，比比本地AI大模型运行速度！\n"
                "[03:25] 输入长度固定为 2048token。\n"
                "[05:03] 接下来测试 8K 和 32K 上下文。\n"
                "[07:10] 分别测试 1、2、4、8 并发场景。\n"
                "[02:51] 使用 llama bench 和 flash attention 跑本地模型基准测试。"
            ),
            evidence_type="multimodal_video",
            coverage="full",
            transcript=(
                "我们集齐了来自苹果、AMD、英伟达的三款128GB超大统一内存电脑，比比本地AI大模型运行速度。"
                "测试会统一模型、统一量化和统一上下文长度，再比较预填充、解码、长上下文和并发。"
            ),
            metadata={
                "bilibili_title": "本地AI哪家强？统一内存大横评！",
                "bilibili_description": "我们集齐了来自苹果、AMD、英伟达的三款128GB超大统一内存电脑，比比本地AI大模型运行速度！",
                "timeline_highlights": [
                    "[03:25] 输入长度固定为 2048token。",
                    "[05:03] 接下来测试 8K 和 32K 上下文。",
                    "[07:10] 分别测试 1、2、4、8 并发场景。",
                ],
                "transcript_timeline_lines": [
                    "[02:51] 使用 llama bench 和 flash attention 跑本地模型基准测试。",
                ],
                "viewer_feedback": [
                    "我就直说了，普通人不需要本地运行ai",
                    "这个测试没有图片和视频生成测试，英伟达在这些场景还是更强。",
                    "在乎性价比就 AMD，在乎生态就英伟达。",
                ],
            },
        )

        blocks = build_video_story_blocks(evidence)
        summaries = [str(item.get("summary", "")) for item in blocks]

        self.assertIn(
            "视频在横评苹果、英伟达、AMD 三套统一内存平台的本地 AI 推理性能，重点比较吞吐、解码、长上下文和并发表现。",
            summaries,
        )
        self.assertIn(
            "测试流程是统一模型、统一量化和统一上下文长度，再比较预填充、解码、长上下文衰减和多并发表现。",
            summaries,
        )
        self.assertIn(
            "评测实现使用 llama.cpp / llama bench 与统一配置，并分别测试 2K、8K、32K 上下文以及多并发场景。",
            summaries,
        )
        self.assertIn(
            "评论区主要在争论普通人是否需要本地 AI，以及苹果、英伟达、AMD 在生态、图片/视频生成和性价比上的取舍。",
            summaries,
        )
        self.assertFalse(any("玩法思路和关键套路" in item for item in summaries))
        self.assertFalse(any("行情、业绩和多种数据源" in item for item in summaries))
        self.assertFalse(any("实盘体验" in item for item in summaries))


if __name__ == "__main__":
    unittest.main()
