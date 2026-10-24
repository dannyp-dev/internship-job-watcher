import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_watcher import (
    Job, canonical_url, check_jobs, job_matches, notification_text,
    notify_telegram, seen_key_for_job,
)


class DeliveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.filters = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text())["filters"]
        self.amazon = Job("Amazon", "Software Development Engineer Intern, AWS Database - 2027 (US)",
                          "Austin, Texas, USA", "https://www.amazon.jobs/en/jobs/10565667", "Amazon Jobs")
        self.account_url = "https://account.amazon.jobs/en-US/applicant/jobs/10565667/apply?fbclid=tracking"

    def fixture(self, root, notification=None, seen=None):
        config, state = root / "config.json", root / "state.json"
        config.write_text(json.dumps({
            "sources": [{"type": "fixture", "name": "Jobs"}],
            "filters": self.filters,
            "notification": notification or {"provider": "telegram", "max_per_run": 20},
        }))
        state.write_text(json.dumps({
            "initialized_at": "2026-09-10T20:01:13+00:00",
            "last_checked_at": "2026-09-10T20:01:13+00:00",
            "last_notification_errors": ["obsolete error"],
            "seen": seen or {},
        }))
        return config, state

    def test_exact_reported_amazon_role_and_application_alias(self):
        self.assertTrue(job_matches(self.amazon, self.filters))
        self.assertEqual(self.amazon.url, canonical_url(self.account_url))
        history = {"original": {"job": {"url": self.amazon.url}}}
        alternate = Job("Amazon", "Software Engineer Intern - AWS Database", "United States",
                        self.account_url, "Aggregate")
        self.assertEqual("original", seen_key_for_job(alternate, history))
        self.assertIn(self.amazon.url, notification_text(self.amazon)[1])

    @patch("job_watcher.send_notification")
    @patch("job_watcher.provider_is_configured", return_value=True)
    @patch("job_watcher.fetch_source")
    def test_every_overflow_job_stays_queued_until_its_own_alert(self, fetch, configured, send):
        jobs = [Job("Acme", f"Software Engineer Intern {i:02}", "New York, NY",
                    f"https://example.test/jobs/{i}", "Jobs") for i in range(47)]
        control = Job("Acme", "Marketing Intern", "New York, NY", "https://example.test/control", "Jobs")
        fetch.return_value = jobs
        send.return_value = {"provider": "telegram", "message_id": 123}
        with tempfile.TemporaryDirectory() as directory:
            config, state = self.fixture(Path(directory))
            self.assertEqual(0, check_jobs(config, state))
            saved = json.loads(state.read_text())
            self.assertEqual(20, len(saved["seen"]))
            self.assertEqual(27, len(saved["pending_notifications"]))
            self.assertEqual(27, saved["last_pending_notification_count"])
            fetch.return_value = [control]
            self.assertEqual(0, check_jobs(config, state))
            saved = json.loads(state.read_text())
            self.assertEqual(40, len(saved["seen"]))
            self.assertEqual(7, len(saved["pending_notifications"]))
            self.assertEqual(0, check_jobs(config, state))
            saved = json.loads(state.read_text())
            self.assertEqual(47, len(saved["seen"]))
            self.assertEqual({}, saved["pending_notifications"])
            self.assertEqual(47, send.call_count)
            self.assertEqual({job.url for job in jobs}, {call.args[3] for call in send.call_args_list})
            self.assertTrue(all(call.args[3] for call in send.call_args_list))

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "test-chat"})
    @patch("job_watcher.urlopen")
    def test_telegram_stores_api_acceptance_receipt(self, open_url):
        response = io.BytesIO(json.dumps({"ok": True, "result": {"message_id": 456, "date": 1792800000}}).encode())
        response.status = 200
        open_url.return_value.__enter__.return_value = response
        receipt = notify_telegram("New internship", "Software Engineer Intern", self.amazon.url)
        self.assertEqual(456, receipt["message_id"])
        self.assertEqual("telegram", receipt["provider"])
        self.assertIn("accepted_at", receipt)

    @patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "test-chat"})
    @patch("job_watcher.urlopen")
    def test_telegram_requires_confirmed_message_not_just_http_200(self, open_url):
        for payload in [{"ok": False}, {"ok": True, "result": {}}, {"ok": True, "result": {"message_id": True}}]:
            with self.subTest(payload=payload):
                response = io.BytesIO(json.dumps(payload).encode())
                response.status = 200
                open_url.return_value.__enter__.return_value = response
                with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                    notify_telegram("New internship", "Software Engineer Intern", self.amazon.url)

    @patch("job_watcher.send_notification")
    @patch("job_watcher.provider_is_configured", return_value=True)
    @patch("job_watcher.fetch_source")
    def test_requested_replay_retries_once_and_preserves_original_history(self, fetch, configured, send):
        fetch.return_value = [self.amazon]
        send.side_effect = [RuntimeError("delivery failed"), {"provider": "telegram", "message_id": 789}]
        original_time = "2026-09-30T17:01:21+00:00"
        entry = {"first_seen": original_time, "last_seen": original_time,
                 "notified_at": original_time, "job": {
                     "company": self.amazon.company, "title": self.amazon.title,
                     "location": self.amazon.location, "url": self.amazon.url,
                     "source": self.amazon.source,
                 }}
        with tempfile.TemporaryDirectory() as directory:
            config, state = self.fixture(Path(directory), {
                "provider": "telegram",
                "replay_requests": [{"id": "amazon-recovery", "url": self.account_url}],
            }, {self.amazon.key: entry})
            self.assertEqual(1, check_jobs(config, state))
            self.assertNotIn("amazon-recovery", json.loads(state.read_text())["completed_replay_requests"])
            self.assertEqual(0, check_jobs(config, state))
            self.assertEqual(0, check_jobs(config, state))
            saved = json.loads(state.read_text())
            self.assertEqual(2, send.call_count)
            self.assertEqual(original_time, saved["seen"][self.amazon.key]["notified_at"])
            self.assertEqual(789, saved["seen"][self.amazon.key]["notification_receipt"]["message_id"])
            self.assertIn("amazon-recovery", saved["completed_replay_requests"])
            self.assertEqual(0, saved["last_pending_notification_count"])

    @patch("job_watcher.send_notification")
    @patch("job_watcher.fetch_source")
    def test_no_new_job_run_refreshes_health_timestamp_and_errors(self, fetch, send):
        fetch.return_value = [Job("Acme", "Marketing Intern", "New York, NY", "https://example.test/control", "Jobs")]
        with tempfile.TemporaryDirectory() as directory:
            config, state = self.fixture(Path(directory))
            self.assertEqual(0, check_jobs(config, state))
            saved = json.loads(state.read_text())
            self.assertNotEqual("2026-09-10T20:01:13+00:00", saved["last_checked_at"])
            self.assertEqual([], saved["last_notification_errors"])
            self.assertEqual("ok", saved["last_check_status"])
            send.assert_not_called()

    @patch("job_watcher.send_notification")
    @patch("job_watcher.fetch_source")
    def test_dry_run_never_consumes_replay_or_changes_history(self, fetch, send):
        fetch.return_value = [self.amazon]
        with tempfile.TemporaryDirectory() as directory:
            config, state = self.fixture(Path(directory), {
                "provider": "telegram", "replay_requests": [{"id": "replay", "url": self.account_url}],
            })
            before = state.read_bytes()
            self.assertEqual(0, check_jobs(config, state, dry_run=True))
            self.assertEqual(before, state.read_bytes())
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
