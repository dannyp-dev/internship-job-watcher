import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_watcher import (
    Job, canonical_url, check_jobs, fetch_greenhouse, fetch_jibe,
    fetch_jobsyn, fetch_workday, is_us_location, job_matches,
)


class FutureCoverageTests(unittest.TestCase):
    def setUp(self):
        self.filters = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text())["filters"]

    def test_named_missed_roles_pass_without_allowing_mba_or_hardware(self):
        for company, title, location in [
            ("H&R Block", "Embark Software Engineering Intern - S", "Kansas City, Missouri, United States"),
            ("Zillow", "Software Development Engineer - Intern", "Seattle-WA-2nd Ave"),
            ("Old Mission Capital", "Software Engineer - 2027 Internship Program (June Start)", "Chicago, IL"),
            ("Siemens", "Strategic Student Program: Software Engineering Intern (LCS, Summer 2027)", "State College, PA"),
        ]:
            with self.subTest(company=company):
                self.assertTrue(job_matches(Job(company, title, location, "https://example.test/job", "test"), self.filters))
        for title in ["MBA Software Engineering Intern", "Senior Software Intern", "Electronics Reliability Product Engineering Internship", "Reliability Test Engineer Intern", "AI & Data Automation Developer Co-op", "GNC Software Engineer Intern", "Software Engineering Intern - Vehicle Controls", "Manufacturing Test Software Intern"]:
            self.assertFalse(job_matches(Job("Acme", title, "Seattle-WA-2nd Ave", "https://a.test/job", "test"), self.filters))
        self.assertFalse(is_us_location("London, United Kingdom"))

    def test_workday_locale_and_hr_tracking_do_not_duplicate_alerts(self):
        self.assertEqual(
            canonical_url("https://zillow.wd5.myworkdayjobs.com/en-US/Zillow_Group_External/job/Seattle-WA/Software-Intern_P1"),
            canonical_url("https://zillow.wd5.myworkdayjobs.com/Zillow_Group_External/job/Seattle-WA/Software-Intern_P1"),
        )
        self.assertEqual(canonical_url("https://careers.hrblock.com/jobs/76987?icims=1"),
                         "https://careers.hrblock.com/jobs/76987")

    @patch("job_watcher.fetch_json")
    def test_jane_street_internship_type_does_not_require_intern_in_title(self, fetch):
        fetch.return_value = {"jobs": [
            {"title": "Software Engineer", "absolute_url": "https://a.test/intern", "location": {"name": "New York, NY"}, "metadata": [{"name": "Employment Type", "value": "Summer Internship"}]},
            {"title": "Software Engineer", "absolute_url": "https://a.test/fulltime", "location": {"name": "New York, NY"}, "metadata": [{"name": "Employment Type", "value": "Full-Time: Experienced"}]},
        ]}
        jobs = fetch_greenhouse({"board": "janestreet", "company": "Jane Street"})
        self.assertTrue(job_matches(jobs[0], self.filters))
        self.assertFalse(job_matches(jobs[1], self.filters))
        self.assertEqual("Software Engineer", jobs[0].title)

    @patch("job_watcher.fetch_json")
    def test_jibe_paginates_through_a_short_last_page(self, fetch):
        def record(number):
            return {"data": {"slug": str(number), "title": "Software Engineering Intern", "city": "Kansas City", "state": "Missouri", "country": "United States"}}
        fetch.side_effect = [
            {"jobs": [record(1), record(2)], "count": 2, "totalCount": 3},
            {"jobs": [record(3)], "count": 1, "totalCount": 3},
        ]
        jobs = fetch_jibe({"company": "H&R Block", "name": "Jibe: H&R Block", "host": "careers.hrblock.com"})
        self.assertEqual(3, len(jobs))
        self.assertIn("page=2", fetch.call_args_list[1].args[0])
        fetch.return_value = {"jobs": [], "totalCount": 0}
        fetch.side_effect = None
        self.assertEqual([], fetch_jibe({"company": "H&R Block", "name": "Jibe: H&R Block", "host": "careers.hrblock.com"}))
        fetch.return_value = {"error": "search unavailable"}
        with self.assertRaisesRegex(RuntimeError, "missing"):
            fetch_jibe({"company": "H&R Block", "name": "Jibe: H&R Block", "host": "careers.hrblock.com"})

    @patch("job_watcher.fetch_json")
    def test_jobsyn_paginates_and_allows_overlapping_queries(self, fetch):
        def result(identifier, more=False):
            return {"jobs": [{"job": {"name": f"jobs/{identifier}", "title": "Software Engineering Intern", "addresses": ["State College, PA, USA"], "customAttributes": {"reqid": {"stringValues": [identifier]}}}}], "pagination": {"has_more_pages": more}}
        fetch.side_effect = [result("525455", True), result("525814"), result("525455", True), result("525814")]
        source = {"company": "Siemens", "name": "Siemens Software", "host": "jobs.sw.siemens.com", "queries": ["intern", "co-op"]}
        jobs = fetch_jobsyn(source)
        self.assertEqual(2, len(jobs))
        self.assertEqual({"X-Origin": "jobs.sw.siemens.com"}, fetch.call_args_list[0].kwargs["headers"])
        self.assertTrue(jobs[0].url.endswith("/525455"))
        fetch.side_effect = [result("525455", True), result("525455", True)]
        with self.assertRaisesRegex(RuntimeError, "repeated"):
            fetch_jobsyn(source)

    @patch("job_watcher.fetch_json")
    def test_greenhouse_uses_office_country_and_distinguishes_empty_from_broken(self, fetch):
        fetch.return_value = {"jobs": [{"title": "Software Engineer Intern", "absolute_url": "https://a.test/1", "location": {"name": "New York"}, "offices": [{"location": "New York, NY, United States"}]}]}
        jobs = fetch_greenhouse({"board": "acme", "company": "Acme"})
        self.assertTrue(job_matches(jobs[0], self.filters))
        fetch.return_value = {"jobs": []}
        self.assertEqual([], fetch_greenhouse({"board": "acme"}))
        fetch.return_value = {"error": "unavailable"}
        with self.assertRaisesRegex(RuntimeError, "missing"):
            fetch_greenhouse({"board": "acme"})

    @patch("job_watcher.fetch_json_post", return_value={"error": "unavailable"})
    def test_invalid_workday_payload_cannot_look_like_an_empty_board(self, _fetch):
        with self.assertRaisesRegex(RuntimeError, "missing"):
            fetch_workday({"host": "acme.wd1.myworkdayjobs.com", "tenant": "acme", "site": "Jobs"})

    @patch("job_watcher.send_notification")
    @patch("job_watcher.provider_is_configured", return_value=True)
    @patch("job_watcher.fetch_source")
    def test_first_new_post_is_not_swallowed_by_onboarding_baseline(self, fetch, _configured, send):
        old = Job("Future", "Software Engineer Intern", "Seattle, WA", "https://a.test/old", "Future")
        new = Job("Future", "Backend Engineer Intern", "Seattle, WA", "https://a.test/new", "Future")
        control = Job("Control", "Accountant", "Seattle, WA", "https://a.test/control", "Control")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            state = root / "state.json"
            config.write_text(json.dumps({"sources": [
                {"type": "fixture", "name": "Future", "critical": True, "minimum_jobs": 0, "initial_baseline_urls": [old.url]},
                {"type": "fixture", "name": "Control"},
            ], "filters": self.filters, "notification": {"provider": "telegram"}}))
            state.write_text(json.dumps({"initialized_at": "2026-01-01T00:00:00Z", "seen": {}}))
            fetch.side_effect = lambda source: [] if source["name"] == "Future" else [control]
            self.assertEqual(0, check_jobs(config, state))
            self.assertEqual(0, send.call_count)
            fetch.side_effect = lambda source: [old] if source["name"] == "Future" else [control]
            self.assertEqual(0, check_jobs(config, state))
            self.assertEqual(0, send.call_count)
            self.assertIn("baselined_at", json.loads(state.read_text())["seen"][old.key])
            fetch.side_effect = lambda source: [old, new] if source["name"] == "Future" else [control]
            self.assertEqual(0, check_jobs(config, state))
            self.assertEqual(1, send.call_count)
            self.assertEqual(new.url, send.call_args.args[3])
            saved = json.loads(state.read_text())
            self.assertIn("baselined_at", saved["seen"][old.key])
            self.assertIn("notified_at", saved["seen"][new.key])


    @patch("job_watcher.send_notification")
    @patch("job_watcher.provider_is_configured", return_value=True)
    @patch("job_watcher.fetch_source")
    def test_partial_source_outage_keeps_successful_alerts_and_health_state(self, fetch, configured, send):
        job = Job("Acme", "Software Engineer Intern", "New York, NY",
                  "https://example.test/intern", "Healthy")
        def fetch_one(source):
            if source["name"] == "Unavailable":
                raise RuntimeError("HTTP Error 504: Gateway Timeout")
            return [job]
        fetch.side_effect = fetch_one
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, state = root / "config.json", root / "state.json"
            config.write_text(json.dumps({
                "sources": [{"type": "fixture", "name": "Healthy"},
                            {"type": "fixture", "name": "Unavailable", "critical": True}],
                "filters": self.filters, "notification": {"provider": "telegram"},
            }))
            state.write_text(json.dumps({"initialized_at": "2026-01-01T00:00:00Z", "seen": {}}))
            self.assertEqual(0, check_jobs(config, state))
            saved = json.loads(state.read_text())
            self.assertEqual(["Unavailable"], saved["critical_source_failures"])
            self.assertIn("notified_at", saved["seen"][job.key])
            self.assertEqual([], saved["last_notification_errors"])
            self.assertEqual(2, send.call_count)

    @patch("job_watcher.send_notification", side_effect=RuntimeError("Telegram unavailable"))
    @patch("job_watcher.provider_is_configured", return_value=True)
    @patch("job_watcher.fetch_source")
    def test_failed_job_delivery_still_fails_check(self, fetch, configured, send):
        job = Job("Acme", "Software Engineer Intern", "New York, NY",
                  "https://example.test/pending", "Healthy")
        fetch.return_value = [job]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, state = root / "config.json", root / "state.json"
            config.write_text(json.dumps({
                "sources": [{"type": "fixture", "name": "Healthy"}],
                "filters": self.filters, "notification": {"provider": "telegram"},
            }))
            state.write_text(json.dumps({"initialized_at": "2026-01-01T00:00:00Z", "seen": {}}))
            self.assertEqual(1, check_jobs(config, state))
            self.assertNotIn(job.key, json.loads(state.read_text())["seen"])

    @patch("job_watcher.fetch_source", side_effect=RuntimeError("Source unreachable"))
    def test_total_source_outage_still_stops_check(self, fetch):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, state = root / "config.json", root / "state.json"
            config.write_text(json.dumps({"sources": [{"type": "fixture", "name": "Only source"}]}))
            with self.assertRaisesRegex(RuntimeError, "No jobs were fetched"):
                check_jobs(config, state)

    def test_ashby_embed_application_links_canonicalize_to_job(self):
        base = "https://jobs.ashbyhq.com/1password/67bfbd8b-aba9-42b1-a8a8-833367888fba"
        embedded = base + "/application?embed=true"
        self.assertEqual(canonical_url(base), canonical_url(embedded))

    def test_twitch_missed_posting_is_new_and_notification_eligible(self):
        twitch = Job(
            "Twitch",
            "Undergrad Software Engineering Intern (Grad Year: Spring 2028)",
            "San Francisco, CA",
            "https://job-boards.greenhouse.io/twitch/jobs/8886748002",
            "Greenhouse: Twitch",
            team="Early Careers",
            employment_type="Internship",
        )
        self.assertTrue(job_matches(twitch, self.filters))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            state = root / "state.json"
            config.write_text(json.dumps({
                "sources": [{"type": "greenhouse", "name": "Greenhouse: Twitch",
                             "company": "Twitch", "board": "twitch", "enabled": True}],
                "coverage": {"required_direct_companies": ["Twitch"]},
                "filters": self.filters,
                "notification": {"provider": "telegram"},
            }))
            state.write_text(json.dumps({
                "initialized_at": "2026-09-10T20:01:13+00:00",
                "seen": {},
            }))
            with patch("job_watcher.fetch_source", return_value=[twitch]), \
                 patch("job_watcher.provider_is_configured", return_value=True), \
                 patch("job_watcher.send_notification") as send:
                self.assertEqual(0, check_jobs(config, state))
            self.assertEqual(1, send.call_count)
            self.assertEqual(twitch.url, send.call_args.args[3])
            saved = json.loads(state.read_text())
            self.assertIn(twitch.key, saved["seen"])
            self.assertIn("notified_at", saved["seen"][twitch.key])

if __name__ == "__main__":
    unittest.main()

