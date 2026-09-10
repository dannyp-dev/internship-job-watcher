#!/usr/bin/env python3
"""Personal internship watcher with ntfy and Telegram phone alerts.

Uses only the Python standard library so it runs cleanly in GitHub Actions.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


USER_AGENT = "Danny-Internship-Watcher/1.0 (+personal job search)"
TRACKING_QUERY_KEYS = {
    "ref",
    "source",
    "gh_src",
    "lever-source",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
    "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}


@dataclass(frozen=True)
class Job:
    company: str
    title: str
    location: str
    url: str
    source: str
    team: str = ""
    compensation: str = ""

    @property
    def key(self) -> str:
        canonical = canonical_url(self.url)
        raw = "|".join(
            normalize(value)
            for value in (canonical, self.company, self.title, self.location)
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class TableParser(HTMLParser):
    """Extract cells and links from GitHub's HTML job tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict[str, Any]]] = []
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = {"text": [], "links": []}
        elif tag == "a" and self._cell is not None and attrs_dict.get("href"):
            self._cell["links"].append(attrs_dict["href"])
        elif tag == "br" and self._cell is not None:
            self._cell["text"].append(" | ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            text = clean_text("".join(self._cell["text"]))
            self._row.append({"text": text, "links": self._cell["links"]})
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def normalize(value: str) -> str:
    value = html.unescape(value).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def canonical_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        query = [
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_QUERY_KEYS
            and not key.casefold().startswith("utm_")
        ]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, urlencode(query), ""))
    except ValueError:
        return value.strip()


def fetch_bytes(url: str, attempts: int = 3, timeout: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def fetch_json(url: str) -> Any:
    return json.loads(fetch_bytes(url).decode("utf-8"))


def choose_application_link(links: Iterable[str]) -> str:
    candidates = [link for link in links if link.startswith("http")]
    for link in candidates:
        host = urlsplit(link).netloc.casefold()
        if "simplify.jobs" not in host and "i.imgur.com" not in host:
            return canonical_url(link)
    for link in candidates:
        if "i.imgur.com" not in urlsplit(link).netloc.casefold():
            return canonical_url(link)
    return ""


def parse_github_readme(text: str, source_name: str) -> list[Job]:
    parser = TableParser()
    parser.feed(text)
    jobs: list[Job] = []
    current_company = ""
    for row in parser.rows:
        if len(row) not in {5, 6}:
            continue
        company = clean_text(row[0]["text"])
        title = clean_text(row[1]["text"])
        location = clean_text(row[2]["text"])
        if not company or normalize(company) == "company" or normalize(title) == "role":
            continue
        if company in {"↳", "↳"}:
            company = current_company
        else:
            current_company = company.replace("🔥", "").strip()
            company = current_company
        application_cell = row[3] if len(row) == 5 else row[4]
        url = choose_application_link(application_cell["links"])
        if company and title and url and "closed" not in normalize(application_cell["text"]):
            jobs.append(Job(company, title, location, url, source_name))
    return jobs


def fetch_github_readme(source: dict[str, Any]) -> list[Job]:
    text = fetch_bytes(source["url"]).decode("utf-8", errors="replace")
    return parse_github_readme(text, source.get("name", "GitHub list"))


def fetch_greenhouse(source: dict[str, Any]) -> list[Job]:
    board = source["board"]
    data = fetch_json(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    )
    company = source.get("company", board)
    jobs = []
    for item in data.get("jobs", []):
        departments = ", ".join(d.get("name", "") for d in item.get("departments", []))
        jobs.append(
            Job(
                company=company,
                title=clean_text(item.get("title", "")),
                location=clean_text(item.get("location", {}).get("name", "")),
                url=canonical_url(item.get("absolute_url", "")),
                source=source.get("name", f"Greenhouse: {company}"),
                team=clean_text(departments),
            )
        )
    return [job for job in jobs if job.title and job.url]


def fetch_lever(source: dict[str, Any]) -> list[Job]:
    site = source["site"]
    data = fetch_json(f"https://api.lever.co/v0/postings/{site}?mode=json")
    company = source.get("company", site)
    jobs = []
    for item in data:
        categories = item.get("categories", {})
        jobs.append(
            Job(
                company=company,
                title=clean_text(item.get("text", "")),
                location=clean_text(categories.get("location", "")),
                url=canonical_url(item.get("hostedUrl") or item.get("applyUrl", "")),
                source=source.get("name", f"Lever: {company}"),
                team=clean_text(categories.get("team", "")),
            )
        )
    return [job for job in jobs if job.title and job.url]


def format_ashby_compensation(value: Any) -> str:
    if not isinstance(value, dict):
        return clean_text(str(value or ""))
    salary = value.get("scrapeableCompensationSalarySummary")
    if salary:
        return clean_text(str(salary))
    tier = value.get("compensationTierSummary")
    if tier:
        return clean_text(str(tier))
    summaries = []
    for component in value.get("summaryComponents", []):
        if isinstance(component, dict):
            summary = component.get("summary") or component.get("value")
            if summary:
                summaries.append(clean_text(str(summary)))
        elif component:
            summaries.append(clean_text(str(component)))
    return "; ".join(summaries)


def fetch_ashby(source: dict[str, Any]) -> list[Job]:
    board = source["board"]
    data = fetch_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
    )
    company = source.get("company", board)
    jobs = []
    for item in data.get("jobs", []):
        secondary = [
            location.get("location", "")
            for location in item.get("secondaryLocations", [])
            if isinstance(location, dict)
        ]
        location = " | ".join(
            part for part in [item.get("location", ""), *secondary] if part
        )
        team = item.get("team") or item.get("department") or ""
        jobs.append(
            Job(
                company=company,
                title=clean_text(item.get("title", "")),
                location=clean_text(location),
                url=canonical_url(item.get("jobUrl") or item.get("applyUrl", "")),
                source=source.get("name", f"Ashby: {company}"),
                team=clean_text(team),
                compensation=format_ashby_compensation(item.get("compensation")),
            )
        )
    return [job for job in jobs if job.title and job.url]


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def fetch_smartrecruiters(source: dict[str, Any]) -> list[Job]:
    board = source["board"]
    company = source.get("company", board)
    jobs = []
    offset = 0
    limit = 100
    while True:
        data = fetch_json(
            f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
            f"?limit={limit}&offset={offset}"
        )
        items = data.get("content", [])
        for item in items:
            location_data = item.get("location", {})
            location = location_data.get("fullLocation") or ", ".join(
                part
                for part in (
                    location_data.get("city", ""),
                    location_data.get("region", ""),
                    location_data.get("country", ""),
                )
                if part
            )
            title = clean_text(item.get("name", ""))
            identifier = str(item.get("id", ""))
            apply_url = (
                f"https://jobs.smartrecruiters.com/{board}/{identifier}-{slugify(title)}"
                if identifier and title
                else ""
            )
            department = item.get("department") or item.get("function") or {}
            jobs.append(
                Job(
                    company=company,
                    title=title,
                    location=clean_text(location),
                    url=canonical_url(apply_url),
                    source=source.get("name", f"SmartRecruiters: {company}"),
                    team=clean_text(department.get("label", "")),
                )
            )
        offset += len(items)
        total = int(data.get("totalFound", offset))
        if not items or offset >= total:
            break
    return [job for job in jobs if job.title and job.url]


def fetch_source(source: dict[str, Any]) -> list[Job]:
    source_type = source.get("type")
    if source_type == "github_readme":
        return fetch_github_readme(source)
    if source_type == "greenhouse":
        return fetch_greenhouse(source)
    if source_type == "lever":
        return fetch_lever(source)
    if source_type == "ashby":
        return fetch_ashby(source)
    if source_type == "smartrecruiters":
        return fetch_smartrecruiters(source)
    raise ValueError(f"Unknown source type: {source_type}")


def contains_any(haystack: str, needles: Iterable[str]) -> bool:
    normalized = normalize(haystack)
    return any(normalize(needle) in normalized for needle in needles if needle.strip())


def confirmed_hourly_low(compensation: str) -> float | None:
    normalized = compensation.casefold()
    if not any(marker in normalized for marker in ("hour", "/hr", "hourly")):
        return None
    amounts = re.findall(r"\$\s*([0-9]+(?:\.[0-9]+)?)", compensation)
    return min((float(amount) for amount in amounts), default=None)


def is_us_location(location: str) -> bool:
    normalized = normalize(location)
    if any(
        term in normalized
        for term in ("united states", "usa", "u s ", "remote", "nyc", "new york city")
    ):
        return True
    upper = location.upper()
    return any(re.search(rf"(?:,|\s)\s*{code}(?:\b|\s|$)", upper) for code in US_STATE_CODES)


def job_matches(job: Job, filters: dict[str, Any]) -> bool:
    title = job.title
    all_text = " | ".join((job.company, job.title, job.location, job.team))
    if filters.get("required_role_words") and not contains_any(
        title, filters["required_role_words"]
    ):
        return False
    if filters.get("technical_title_words") and not contains_any(
        title, filters["technical_title_words"]
    ):
        return False
    if filters.get("exclude_words") and contains_any(all_text, filters["exclude_words"]):
        return False
    if filters.get("exclude_advanced_degree", False):
        advanced_degree_pattern = r"\b(?:graduate|phd|ph d|doctoral|masters?|mba)\b"
        if "🎓" in all_text or re.search(advanced_degree_pattern, normalize(title)):
            return False
    if filters.get("company_allowlist") and not contains_any(
        job.company, filters["company_allowlist"]
    ):
        return False
    if filters.get("company_blocklist") and contains_any(
        job.company, filters["company_blocklist"]
    ):
        return False
    if filters.get("location_words") and not contains_any(
        job.location, filters["location_words"]
    ):
        return False
    if filters.get("us_only", False) and not is_us_location(job.location):
        return False
    minimum_hourly_pay = filters.get("minimum_hourly_pay")
    confirmed_low = confirmed_hourly_low(job.compensation)
    if minimum_hourly_pay is not None and confirmed_low is not None:
        if confirmed_low < float(minimum_hourly_pay):
            return False
    return True


def deduplicate(jobs: Iterable[Job]) -> list[Job]:
    result: dict[str, Job] = {}
    url_index: dict[str, str] = {}
    for job in jobs:
        canonical = canonical_url(job.url)
        existing_key = url_index.get(canonical)
        if existing_key:
            existing = result[existing_key]
            sources = sorted(set(existing.source.split(" + ") + job.source.split(" + ")))
            result[existing_key] = Job(
                company=existing.company,
                title=existing.title,
                location=existing.location,
                url=existing.url,
                source=" + ".join(sources),
                team=existing.team or job.team,
                compensation=existing.compensation or job.compensation,
            )
            continue
        result[job.key] = job
        url_index[canonical] = job.key
    return list(result.values())


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_watchlist_sources(config: dict[str, Any], config_path: Path) -> list[dict[str, Any]]:
    configured = list(config.get("sources", []))
    watchlist_name = config.get("company_watchlist")
    if not watchlist_name:
        return configured
    watchlist_path = Path(watchlist_name)
    if not watchlist_path.is_absolute():
        watchlist_path = config_path.resolve().parent / watchlist_path
    watchlist = load_json(watchlist_path, {})
    direct = [
        item.get("source")
        for item in watchlist.get("companies", [])
        if item.get("source") and item.get("source", {}).get("enabled", True)
    ]
    sources = configured + direct
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for source in sources:
        identity = str(source.get("board") or source.get("site") or source.get("url", ""))
        unique[(str(source.get("type", "")), identity)] = source
    return list(unique.values())


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    merged_headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    merged_headers.update(headers or {})
    request = Request(url, data=body, method="POST", headers=merged_headers)
    with urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"Notification service returned HTTP {response.status}")


def notify_ntfy(title: str, message: str, click_url: str) -> None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        raise RuntimeError("NTFY_TOPIC is not configured")
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    payload: dict[str, Any] = {
        "topic": topic,
        "title": title[:120],
        "message": message[:4000],
        "priority": 4,
        "tags": ["briefcase"],
    }
    if click_url:
        payload["click"] = click_url
        payload["actions"] = [{"action": "view", "label": "Apply", "url": click_url}]
    token = os.environ.get("NTFY_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    post_json(server, payload, headers)


def notify_telegram(title: str, message: str, click_url: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not configured")
    text = f"<b>{html.escape(title)}</b>\n\n{html.escape(message)}"
    fields: dict[str, str] = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if click_url:
        fields["reply_markup"] = json.dumps(
            {"inline_keyboard": [[{"text": "Open application", "url": click_url}]]}
        )
    payload = urlencode(fields).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"Telegram returned HTTP {response.status}")


def provider_is_configured(provider: str) -> bool:
    if provider == "ntfy":
        return bool(os.environ.get("NTFY_TOPIC", "").strip())
    if provider == "telegram":
        return bool(
            os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            and os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        )
    return False


def send_notification(provider: str, title: str, message: str, click_url: str = "") -> None:
    if provider == "ntfy":
        notify_ntfy(title, message, click_url)
    elif provider == "telegram":
        notify_telegram(title, message, click_url)
    else:
        raise ValueError(f"Unknown notification provider: {provider}")


def notification_text(job: Job) -> tuple[str, str]:
    title = f"New internship: {job.company}"
    lines = [job.title, job.location or "Location not listed", f"Source: {job.source}"]
    lines.append(f"Pay: {job.compensation or 'unverified'}")
    return title, "\n".join(lines)


def telegram_chat_id() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    request = Request(
        f"https://api.telegram.org/bot{token}/getUpdates",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            data = json.load(response)
    except Exception as exc:
        raise RuntimeError(f"Telegram getUpdates failed: {type(exc).__name__}") from None
    chats = []
    for update in data.get("result", []):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("id") is not None:
            chats.append(chat)
    if not chats:
        raise RuntimeError("No Telegram chat found. Open your bot in Telegram, press Start, then try again.")
    chat = chats[-1]
    label = chat.get("username") or chat.get("first_name") or chat.get("title") or "private chat"
    print(f"Telegram chat found: {label}")
    print(f"TELEGRAM_CHAT_ID={chat['id']}")
    return 0


def prune_seen(seen: dict[str, Any], retention_days: int) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept: dict[str, Any] = {}
    for key, value in seen.items():
        try:
            last_seen = datetime.fromisoformat(value["last_seen"])
            if last_seen >= cutoff:
                kept[key] = value
        except (KeyError, TypeError, ValueError):
            continue
    return kept


def check_jobs(config_path: Path, state_path: Path, dry_run: bool = False) -> int:
    config = load_json(config_path, None)
    if not config:
        raise RuntimeError(f"Config file not found or empty: {config_path}")

    gathered: list[Job] = []
    failures: list[str] = []
    sources = [
        source
        for source in load_watchlist_sources(config, config_path)
        if source.get("enabled", True)
    ]
    max_workers = min(10, max(1, len(sources)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_source, source): source for source in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                source_jobs = future.result()
                gathered.extend(source_jobs)
                print(f"Fetched {len(source_jobs)} jobs from {source.get('name', source.get('type'))}")
            except Exception as exc:  # A single board should not break every other source.
                failures.append(f"{source.get('name', source.get('type'))}: {exc}")

    if not gathered:
        detail = "; ".join(failures) if failures else "No enabled sources"
        raise RuntimeError(f"No jobs were fetched. {detail}")

    matched = deduplicate(job for job in gathered if job_matches(job, config.get("filters", {})))
    matched.sort(key=lambda job: (normalize(job.company), normalize(job.title), job.url))
    state = load_json(state_path, {"version": 1, "initialized_at": None, "seen": {}})
    seen: dict[str, Any] = state.setdefault("seen", {})
    initialized = bool(state.get("initialized_at"))
    new_jobs = [job for job in matched if job.key not in seen]

    print(f"Matched {len(matched)} jobs after filtering; {len(new_jobs)} are new")
    for job in new_jobs:
        print(f"NEW | {job.company} | {job.title} | {job.location} | {job.url}")

    timestamp = now_iso()
    if not initialized:
        for job in matched:
            seen[job.key] = {
                "first_seen": timestamp,
                "last_seen": timestamp,
                "job": asdict(job),
            }
        state["initialized_at"] = timestamp
        state["last_checked_at"] = timestamp
        state["last_source_failures"] = failures
        if not dry_run:
            save_json(state_path, state)
        print("Baseline created. Existing listings were recorded without sending job alerts.")
        return 0

    for job in matched:
        if job.key in seen:
            seen[job.key]["last_seen"] = timestamp
            seen[job.key]["job"] = asdict(job)

    provider = config.get("notification", {}).get("provider", "ntfy")
    max_per_run = int(config.get("notification", {}).get("max_per_run", 20))
    successfully_handled: set[str] = set()
    notification_errors: list[str] = []

    if dry_run:
        print("Dry run enabled. Notifications and state changes were skipped.")
    elif new_jobs and not provider_is_configured(provider):
        notification_errors.append(f"{provider} is not configured, so new jobs remain pending")
    else:
        for job in new_jobs[:max_per_run]:
            title, message = notification_text(job)
            try:
                send_notification(provider, title, message, job.url)
                successfully_handled.add(job.key)
            except Exception as exc:
                notification_errors.append(f"{job.company} / {job.title}: {exc}")

        overflow = new_jobs[max_per_run:]
        if overflow:
            digest_lines = [f"{job.company}: {job.title}" for job in overflow[:25]]
            digest = "\n".join(digest_lines)
            if len(overflow) > 25:
                digest += f"\nPlus {len(overflow) - 25} more."
            try:
                send_notification(
                    provider,
                    f"{len(overflow)} more new internship matches",
                    digest,
                    "",
                )
                successfully_handled.update(job.key for job in overflow)
            except Exception as exc:
                notification_errors.append(f"overflow digest: {exc}")

        for job in new_jobs:
            if job.key in successfully_handled:
                seen[job.key] = {
                    "first_seen": timestamp,
                    "last_seen": timestamp,
                    "notified_at": timestamp,
                    "job": asdict(job),
                }

        if successfully_handled:
            retention_days = int(config.get("state_retention_days", 180))
            state["seen"] = prune_seen(seen, retention_days)
            state["last_checked_at"] = timestamp
            state["last_source_failures"] = failures
            state["last_notification_errors"] = notification_errors
            save_json(state_path, state)
        elif not new_jobs:
            print("No new matches. Saved history did not need to change.")

    for failure in failures:
        print(f"SOURCE WARNING | {failure}", file=sys.stderr)
    for error in notification_errors:
        print(f"NOTIFICATION WARNING | {error}", file=sys.stderr)
    return 1 if notification_errors else 0


def test_notification(config_path: Path) -> int:
    config = load_json(config_path, None)
    if not config:
        raise RuntimeError(f"Config file not found or empty: {config_path}")
    provider = config.get("notification", {}).get("provider", "ntfy")
    send_notification(
        provider,
        "Internship watcher is ready",
        "Phone alerts are connected. New matching roles will appear here.",
        "https://github.com/SimplifyJobs/Summer2027-Internships",
    )
    print(f"Test notification sent through {provider}.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch internship sources and alert on new matches.")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--state", type=Path, default=Path("data/state.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-notification", action="store_true")
    parser.add_argument(
        "--telegram-chat-id",
        action="store_true",
        help="Show the latest Telegram chat ID after you press Start in the bot",
    )
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        if args.telegram_chat_id:
            return telegram_chat_id()
        if args.test_notification:
            return test_notification(args.config)
        return check_jobs(args.config, args.state, args.dry_run)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"ERROR | {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
