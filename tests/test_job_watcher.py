import sys
import tempfile
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_watcher import (
    Job,
    canonical_url,
    check_jobs,
    deduplicate,
    fetch_ashby,
    fetch_amazon_jobs,
    fetch_apple_careers,
    fetch_google_careers,
    fetch_microsoft_careers,
    fetch_meta_careers,
    fetch_phenom,
    fetch_smartrecruiters,
    fetch_workday,
    is_us_location,
    is_active_notification_time,
    job_matches,
    load_watchlist_sources,
    notification_text,
    validate_coverage_config,
    parse_github_readme,
    parse_github_markdown,
    seen_key_for_job,
    strip_legacy_compensation,
)


class JobWatcherTests(unittest.TestCase):
    @patch("job_watcher.time.sleep")
    @patch("job_watcher._fetch_meta_careers_once")
    def test_meta_retries_missing_results_with_fresh_page(self, mock_once, mock_sleep):
        job = Job("Meta", "Software Engineer Intern", "Menlo Park, CA",
                  "https://www.metacareers.com/jobs/123", "Meta Careers")
        mock_once.side_effect = [
            RuntimeError("Meta careers search results missing"),
            [job],
        ]
        self.assertEqual([job], fetch_meta_careers({"name": "Meta Careers"}))
        self.assertEqual(2, mock_once.call_count)
        mock_sleep.assert_called_once()

    @patch("job_watcher.send_notification")
    @patch("job_watcher.provider_is_configured", return_value=True)
    @patch("job_watcher.fetch_source")
    def test_transient_rate_limit_needs_three_failures_before_alert(
        self, mock_fetch, _configured, mock_send
    ):
        fallback = Job("Acme", "Marketing Intern", "New York, NY",
                       "https://example.com/marketing", "Fallback")
        failing = True

        def fetch(source):
            if source["name"] == "Microsoft Careers" and failing:
                raise RuntimeError("HTTP Error 429: Too Many Requests")
            return [fallback]

        mock_fetch.side_effect = fetch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            state_path = root / "state.json"
            config_path.write_text(json.dumps({
                "sources": [
                    {"type": "fixture", "name": "Fallback", "url": "https://example.com/a"},
                    {"type": "fixture", "name": "Microsoft Careers",
                     "url": "https://example.com/b", "critical": True},
                ],
                "filters": {"required_role_words": ["intern"],
                            "technical_title_words": ["software"], "us_only": True},
                "notification": {"provider": "telegram"},
            }), encoding="utf-8")
            state_path.write_text(json.dumps({"version": 1,
                "initialized_at": "2026-01-01T00:00:00Z", "seen": {}}), encoding="utf-8")

            self.assertEqual(0, check_jobs(config_path, state_path))
            self.assertEqual(0, check_jobs(config_path, state_path))
            self.assertEqual(0, mock_send.call_count)
            self.assertEqual(1, check_jobs(config_path, state_path))
            self.assertEqual(1, mock_send.call_count)
            failing = False
            self.assertEqual(0, check_jobs(config_path, state_path))
            self.assertEqual(2, mock_send.call_count)
            self.assertIn("responding again", mock_send.call_args.args[2])

    @patch("job_watcher.send_notification")
    @patch("job_watcher.provider_is_configured", return_value=True)
    @patch("job_watcher.fetch_source")
    def test_new_direct_source_baselines_then_alerts(self, mock_fetch, _configured, mock_send):
        old = Job("Acme", "Software Engineer Intern", "New York, NY",
                  "https://acme.example/jobs/1", "Acme Careers")
        new = Job("Acme", "Backend Engineer Intern", "New York, NY",
                  "https://acme.example/jobs/2", "Acme Careers")
        mock_fetch.return_value = [old]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            state_path = root / "state.json"
            config_path.write_text(json.dumps({
                "sources": [{"type": "fixture", "name": "Acme Careers",
                             "baseline_on_first_check": True}],
                "filters": {"required_role_words": ["intern"],
                            "technical_title_words": ["software", "backend"],
                            "us_only": True},
                "notification": {"provider": "telegram"},
            }), encoding="utf-8")
            state_path.write_text(json.dumps({"version": 1,
                "initialized_at": "2026-01-01T00:00:00Z", "seen": {}}), encoding="utf-8")
            self.assertEqual(0, check_jobs(config_path, state_path))
            self.assertEqual(0, mock_send.call_count)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("Acme Careers", saved["source_baselines"])
            self.assertEqual(1, len(saved["seen"]))
            mock_fetch.return_value = [old, new]
            self.assertEqual(0, check_jobs(config_path, state_path))
            self.assertEqual(1, mock_send.call_count)

    @patch("job_watcher.fetch_bytes")
    def test_google_careers_embedded_results(self, mock_fetch):
        row = ["123", "Software Engineering Intern, BS", None, None, None,
               None, None, None, None, [["New York, NY, USA"]]]
        payload = json.dumps([[row], None, 1])
        mock_fetch.return_value = (
            "AF_initDataCallback({key: 'ds:1', data:" + payload +
            ", sideChannel: {}});"
        ).encode()
        jobs = fetch_google_careers({"name": "Google Careers", "company": "Google"})
        self.assertEqual(1, len(jobs))
        self.assertEqual("https://www.google.com/about/careers/applications/jobs/results/123", jobs[0].url)

    @patch("job_watcher.fetch_json_post")
    def test_apple_careers_results(self, mock_fetch):
        mock_fetch.return_value = {"res": {"totalRecords": 1, "searchResults": [
            {"positionId": "123", "postingTitle": "Software Engineering Intern",
             "transformedPostingTitle": "software-engineering-intern",
             "locations": [{"name": "Cupertino, CA"}]}
        ]}}
        jobs = fetch_apple_careers({"name": "Apple Careers"})
        self.assertEqual("Cupertino, CA", jobs[0].location)

    @patch("job_watcher.fetch_json")
    def test_microsoft_careers_results(self, mock_fetch):
        mock_fetch.return_value = {"data": {"count": 1, "positions": [
            {"id": "123", "name": "Software Engineer Intern", "locations": ["Redmond, WA"]}
        ]}}
        jobs = fetch_microsoft_careers({"name": "Microsoft Careers", "queries": ["intern"]})
        self.assertEqual(1, len(jobs))

    @patch("job_watcher.fetch_json_post")
    def test_phenom_results(self, mock_fetch):
        mock_fetch.return_value = {"refineSearch": {"totalHits": 1, "data": {"jobs": [
            {"jobId": "123", "title": "Software Engineer Intern",
             "cityStateCountry": "Bellevue, WA, USA"}
        ]}}}
        jobs = fetch_phenom({"name": "Snowflake Careers", "company": "Snowflake",
                             "host": "careers.snowflake.com",
                             "job_url": "https://careers.snowflake.com/us/en/job/{id}"})
        self.assertEqual("https://careers.snowflake.com/us/en/job/123", jobs[0].url)

    def test_parses_github_table_and_inherits_company(self):
        fixture = """
        <table><tbody>
          <tr><td><strong>Acme</strong></td><td>Software Engineer Intern</td>
          <td>Gainesville, FL</td><td><a href="https://acme.example/jobs/1?utm_source=x">Apply</a></td><td>0d</td></tr>
          <tr><td>↳</td><td>Backend Intern</td><td>Remote</td>
          <td><a href="https://acme.example/jobs/2">Apply</a></td><td>1d</td></tr>
        </tbody></table>
        """
        jobs = parse_github_readme(fixture, "fixture")
        self.assertEqual(2, len(jobs))
        self.assertEqual("Acme", jobs[1].company)
        self.assertEqual("https://acme.example/jobs/1", jobs[0].url)

    def test_parses_off_season_six_column_table(self):
        fixture = """
        <table><tbody><tr><td>Acme</td><td>Software Engineer Intern</td>
        <td>NYC</td><td>Spring 2027</td><td><a href="https://acme.example/jobs/3">Apply</a></td>
        <td>0d</td></tr></tbody></table>
        """
        jobs = parse_github_readme(fixture, "fixture")
        self.assertEqual(1, len(jobs))
        self.assertEqual("https://acme.example/jobs/3", jobs[0].url)

    def test_parses_speedyapply_markdown_and_age_limit(self):
        fixture = """
        | Company | Position | Location | Salary | Posting | Age |
        |---|---|---|---|---|---|
        | <a href="https://acme.example"><strong>Acme</strong></a> | Software Engineer Intern | Austin, TX | $50/hr | <a href="https://acme.example/jobs/1">Apply</a> | 1d |
        | OldCo | Software Intern | NYC | $40/hr | <a href="https://old.example/jobs/2">Apply</a> | 9d |
        """
        jobs = parse_github_markdown(
            fixture,
            {"name": "Speedy", "layout": "company_column", "max_age_days": 3},
        )
        self.assertEqual(1, len(jobs))
        self.assertEqual("Acme", jobs[0].company)
        self.assertFalse(hasattr(jobs[0], "compensation"))

    def test_parses_sectioned_faang_markdown(self):
        fixture = """
        ## Google
        | Role | Category | Location | Posted | First seen |
        |---|---|---|---|---|
        | [Software Engineering Intern](https://google.example/jobs/1) 🆕 | Software | Austin, Texas, USA | 2026-09-17 | 2026-09-17 |
        """
        jobs = parse_github_markdown(
            fixture,
            {"name": "FAANG", "layout": "sectioned", "posted_after": "2026-09-17"},
        )
        self.assertEqual(1, len(jobs))
        self.assertEqual("Google", jobs[0].company)
        self.assertEqual("Software Engineering Intern", jobs[0].title)

    def test_filters_for_technical_us_internships(self):
        filters = {
            "required_role_words": ["intern", "co-op"],
            "technical_title_words": ["software", "backend"],
            "exclude_words": ["senior"],
            "exclude_advanced_degree": True,
            "us_only": True,
        }
        good = Job("Acme", "Backend Software Intern", "New York, NY", "https://a.test/1", "test")
        abroad = Job("Acme", "Software Intern", "London, UK", "https://a.test/2", "test")
        wrong_role = Job("Acme", "Product Intern", "Remote", "https://a.test/3", "test")
        graduate_only = Job("Acme", "Software Intern 🎓", "Remote", "https://a.test/4", "test")
        graduate_title = Job("Acme", "Graduate Software Intern", "Remote", "https://a.test/5", "test")
        undergraduate = Job("Acme", "Undergraduate Software Intern", "Remote", "https://a.test/6", "test")
        self.assertTrue(job_matches(good, filters))
        self.assertFalse(job_matches(abroad, filters))
        self.assertFalse(job_matches(wrong_role, filters))
        self.assertFalse(job_matches(graduate_only, filters))
        self.assertFalse(job_matches(graduate_title, filters))
        self.assertTrue(job_matches(undergraduate, filters))

    def test_resume_targeted_filters_exclude_unwanted_specialties_and_rtx(self):
        filters = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text())["filters"]

        def matches(company, title):
            return job_matches(Job(company, title, "Austin, TX", "https://example.com/job", "test"), filters)

        self.assertTrue(matches("Cloudflare", "Backend Software Engineering Intern"))
        self.assertTrue(matches("Cloudflare", "Full Stack Engineering Intern"))
        self.assertTrue(matches("Cloudflare", "Cloud Platform Engineering Intern"))
        self.assertTrue(matches("Cloudflare", "Software Engineering Intern, Distributed Systems"))
        self.assertTrue(matches("Cloudflare", "HTML Software Intern"))
        self.assertFalse(matches("RTX", "Software Engineering Intern"))
        self.assertFalse(matches("Raytheon", "Backend Software Engineering Intern"))
        self.assertFalse(matches("L3Harris Technologies", "Software Engineering Intern"))
        self.assertFalse(matches("L3 Harris", "Backend Software Engineering Intern"))
        for title in (
            "Machine Learning Software Engineering Intern",
            "ML Platform Software Intern",
            "Research Software Engineering Intern",
            "Data Engineering Intern",
            "Frontend Software Engineering Intern",
            "Design Engineering Intern",
            "Embedded Software Engineering Intern",
            "Firmware Software Intern",
            "Hardware Systems Engineering Intern",
        ):
            with self.subTest(title=title):
                self.assertFalse(matches("Cloudflare", title))

    def test_250_company_roster_and_required_direct_feeds(self):
        root = Path(__file__).resolve().parents[1]
        config_path = root / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        sources = load_watchlist_sources(config, config_path)
        roster = json.loads((root / "coverage_watchlist.json").read_text(encoding="utf-8"))
        names = [name for tier in roster["tiers"].values() for name in tier]
        self.assertEqual(250, len(names))
        self.assertEqual(250, len({name.casefold() for name in names}))
        self.assertNotIn("RTX", names)
        self.assertIn("Cloudflare", names)
        validate_coverage_config(config, config_path, sources)

    def test_canonical_url_removes_tracking_and_fragment(self):
        actual = canonical_url("https://Example.com/jobs/7/?utm_source=x&gh_src=y&keep=1#top")
        self.assertEqual("https://example.com/jobs/7?keep=1", actual)

    def test_canonical_url_uses_amazon_job_id_not_changing_slug(self):
        first = canonical_url(
            "https://amazon.jobs/en/jobs/10529525/software-development-engineer-intern-robotics-2027"
        )
        second = canonical_url(
            "https://www.amazon.jobs/en/jobs/10529525/software-development-engineer-intern-co-op-robotics-2027"
        )
        self.assertEqual("https://www.amazon.jobs/en/jobs/10529525", first)
        self.assertEqual(first, second)

    def test_deduplicates_same_application_url(self):
        one = Job("Acme", "Software Intern", "Remote", "https://a.test/1?utm_source=x", "A")
        two = Job("Acme", "Software Intern", "Remote", "https://a.test/1?ref=y", "B")
        jobs = deduplicate([one, two])
        self.assertEqual(1, len(jobs))
        self.assertEqual("A + B", jobs[0].source)

    def test_us_location_detection(self):
        self.assertTrue(is_us_location("Austin, TX"))
        self.assertTrue(is_us_location("Remote - United States"))
        self.assertTrue(is_us_location("NYC"))
        self.assertFalse(is_us_location("Toronto, Canada"))

    def test_notification_has_no_pay_field(self):
        job = Job("Acme", "Software Intern", "Remote", "https://a.test/1", "test")
        _, body = notification_text(job)
        self.assertIn("Software Intern", body)
        self.assertNotIn("Pay:", body)
        filters = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text())["filters"]
        self.assertNotIn("minimum_hourly_pay", filters)
        self.assertNotIn("unpaid", filters["exclude_words"])

    def test_removes_legacy_pay_from_saved_history(self):
        seen = {"one": {"job": {"title": "Software Intern", "compensation": "$25/hr"}}}
        self.assertTrue(strip_legacy_compensation(seen))
        self.assertEqual({"title": "Software Intern"}, seen["one"]["job"])
        self.assertFalse(strip_legacy_compensation(seen))

    @patch("job_watcher.fetch_json")
    def test_maps_ashby_jobs(self, mock_fetch_json):
        mock_fetch_json.return_value = {
            "jobs": [{
                "title": "Backend Software Engineer Intern",
                "location": "New York, New York",
                "secondaryLocations": [{"location": "Remote - United States"}],
                "team": "Platform",
                "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                "compensation": {"scrapeableCompensationSalarySummary": "$45 per hour"},
            }]
        }
        jobs = fetch_ashby({"board": "acme", "company": "Acme"})
        mock_fetch_json.assert_called_once_with(
            "https://api.ashbyhq.com/posting-api/job-board/acme"
        )
        self.assertEqual(1, len(jobs))
        self.assertIn("Remote - United States", jobs[0].location)
        self.assertFalse(hasattr(jobs[0], "compensation"))

    @patch("job_watcher.fetch_json")
    def test_maps_smartrecruiters_jobs(self, mock_fetch_json):
        mock_fetch_json.return_value = {
            "totalFound": 1,
            "content": [{
                "id": "123",
                "name": "Software Engineer Intern",
                "location": {"fullLocation": "Orlando, FL, United States"},
                "department": {"label": "Engineering"},
            }],
        }
        jobs = fetch_smartrecruiters({"board": "Acme", "company": "Acme"})
        self.assertEqual("https://jobs.smartrecruiters.com/Acme/123-software-engineer-intern", jobs[0].url)
        self.assertEqual("Engineering", jobs[0].team)

    @patch("job_watcher.fetch_json")
    def test_maps_and_paginates_amazon_jobs(self, mock_fetch_json):
        mock_fetch_json.side_effect = [
            {
                "hits": 2,
                "jobs": [{
                    "title": "Software Development Engineer Internship - Summer 2027 (USA)",
                    "posted_date": "September 17, 2026",
                    "job_path": "/en/jobs/10552937/software-development-engineer-internship",
                    "location": "US, WA, Seattle",
                    "locations": [json.dumps({
                        "normalizedLocation": "Seattle, Washington, USA"
                    })],
                    "job_category": "Software Development",
                }],
            },
            {
                "hits": 2,
                "jobs": [{
                    "title": "Software Development Engineer Intern",
                    "posted_date": "September 16, 2026",
                    "job_path": "/en/jobs/2/software-development-engineer-intern",
                    "normalized_location": "Austin, Texas, USA",
                }],
            },
        ]
        jobs = fetch_amazon_jobs({
            "company": "Amazon",
            "name": "Amazon Jobs",
            "query": "intern",
            "posted_after": "2026-09-17",
        })
        self.assertEqual(1, len(jobs))
        self.assertEqual(
            "https://www.amazon.jobs/en/jobs/10552937",
            jobs[0].url,
        )
        self.assertIn("Seattle, Washington, USA", jobs[0].location)
        self.assertIn("offset=1", mock_fetch_json.call_args_list[1].args[0])

    @patch("job_watcher.fetch_json_post")
    def test_maps_workday_jobs(self, mock_fetch_json_post):
        mock_fetch_json_post.return_value = {
            "total": 1,
            "jobPostings": [{
                "title": "Software Engineering Intern",
                "locationsText": "US, CA, Santa Clara",
                "externalPath": "/job/Santa-Clara/Software-Intern_JR1",
            }],
        }
        jobs = fetch_workday({
            "host": "acme.wd5.myworkdayjobs.com",
            "tenant": "acme",
            "site": "External",
            "company": "Acme",
        })
        self.assertEqual(1, len(jobs))
        self.assertEqual(
            "https://acme.wd5.myworkdayjobs.com/External/job/Santa-Clara/Software-Intern_JR1",
            jobs[0].url,
        )

    def test_loads_direct_sources_from_watchlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watchlist = root / "watchlist.json"
            watchlist.write_text(json.dumps({
                "companies": [
                    {"source": {"type": "ashby", "board": "acme", "enabled": True}},
                    {"source": None},
                ]
            }), encoding="utf-8")
            sources = load_watchlist_sources(
                {"company_watchlist": "watchlist.json", "sources": []},
                root / "config.json",
            )
        self.assertEqual(1, len(sources))
        self.assertEqual("acme", sources[0]["board"])

    def test_enforces_eastern_notification_window(self):
        config = {
            "notification": {
                "active_hours": {
                    "timezone": "America/New_York",
                    "start": "10:00",
                    "end": "00:00",
                }
            }
        }
        self.assertTrue(
            is_active_notification_time(
                config, datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
            )
        )
        self.assertTrue(
            is_active_notification_time(
                config, datetime(2026, 9, 12, 3, 59, tzinfo=timezone.utc)
            )
        )
        self.assertFalse(
            is_active_notification_time(
                config, datetime(2026, 9, 11, 6, 36, tzinfo=timezone.utc)
            )
        )
        self.assertFalse(
            is_active_notification_time(
                config, datetime(2026, 9, 12, 4, 0, tzinfo=timezone.utc)
            )
        )

    def test_prior_url_prevents_false_new_job_after_metadata_change(self):
        seen = {
            "old-key": {
                "job": {
                    "url": "https://acme.example/jobs/1?utm_source=old",
                    "title": "Software Intern",
                    "location": "Remote",
                }
            }
        }
        changed = Job(
            "Acme",
            "Software Engineering Intern",
            "New York, NY",
            "https://acme.example/jobs/1?utm_source=new",
            "test",
        )
        self.assertEqual("old-key", seen_key_for_job(changed, seen))

    @patch("job_watcher.fetch_source")
    def test_baseline_current_records_without_notifying(self, mock_fetch_source):
        mock_fetch_source.return_value = [
            Job(
                "Acme",
                "Software Engineer Intern",
                "New York, NY",
                "https://acme.example/jobs/1",
                "test",
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            state_path = root / "state.json"
            config_path.write_text(
                json.dumps(
                    {
                        "sources": [{"type": "fixture", "enabled": True}],
                        "filters": {
                            "required_role_words": ["intern"],
                            "technical_title_words": ["software"],
                            "us_only": True,
                        },
                        "notification": {"provider": "telegram"},
                    }
                ),
                encoding="utf-8",
            )
            result = check_jobs(
                config_path,
                state_path,
                baseline_current=True,
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(0, result)
        self.assertEqual(1, len(state["seen"]))
        record = next(iter(state["seen"].values()))
        self.assertIn("baselined_at", record)
        self.assertNotIn("notified_at", record)


if __name__ == "__main__":
    unittest.main()
