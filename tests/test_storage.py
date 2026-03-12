import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openclaw_capture_workflow.models import IngestRequest, JobRecord
from openclaw_capture_workflow.storage import JobStore


class JobStoreTest(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(Path(tmp) / "jobs")
            ingest = IngestRequest(
                chat_id="123",
                reply_to_message_id=None,
                request_id="job-storage-1",
                source_kind="url",
                source_url="https://example.com",
            )
            job = JobRecord.queued(ingest)
            job.mark("processing", message="extracting")
            store.save(job)

            loaded = store.load("job-storage-1")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.job_id, "job-storage-1")
            self.assertEqual(loaded.status, "processing")
            self.assertEqual(loaded.message, "extracting")

    def test_save_uses_atomic_replace_without_temp_leftovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp) / "jobs"
            store = JobStore(jobs_dir)
            ingest = IngestRequest(
                chat_id="123",
                reply_to_message_id=None,
                request_id="job-storage-atomic",
                source_kind="text",
                raw_text="hello",
            )
            job = JobRecord.queued(ingest)
            store.save(job)
            store.save(job)

            self.assertTrue((jobs_dir / "job-storage-atomic.json").exists())
            self.assertEqual(sorted(path.name for path in jobs_dir.iterdir()), ["job-storage-atomic.json"])


if __name__ == "__main__":
    unittest.main()
