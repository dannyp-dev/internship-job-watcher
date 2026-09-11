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
    confirmed_hourly_low,
    deduplicate,
    fetch_ashby,
    fetch_smartrecruiters,
    is_us_location,
    is_active_notification_time,
    job_matches,
    load_watchlist_sources,
    parse_github_readme,
    seen_key_for_job,
)


class JobWatcherTests(unittest.TestCase):
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

    def test_canonical_url_removes_tracking_and_fragment(self):
        actual = canonical_url("https://Example.com/jobs/7/?utm_source=x&gh_src=y&keep=1#top")
        self.assertEqual("https://example.com/jobs/7?keep=1", actual)

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

    def test_excludes_confirmed_hourly_pay_below_floor(self):
        filters = {
            "required_role_words": ["intern"],
            "technical_title_words": ["software"],
            "minimum_hourly_pay": 30,
        }
        low = Job("Acme", "Software Intern", "Remote", "https://a.test/1", "test", compensation="$24 - $28 per hour")
        unknown = Job("Acme", "Software Intern", "Remote", "https://a.test/2", "test")
        self.assertEqual(24.0, confirmed_hourly_low(low.compensation))
        self.assertFalse(job_matches(low, filters))
        self.assertTrue(job_matches(unknown, filters))

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
        self.assertEqual(1, len(jobs))
        self.assertIn("Remote - United States", jobs[0].location)
        self.assertEqual("$45 per hour", jobs[0].compensation)

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


if __name__ == "__main__":
    unittest.main()
