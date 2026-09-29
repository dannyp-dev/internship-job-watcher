"""Check researched Workday career sites without changing the watcher config."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_watcher import fetch_json_post  # noqa: E402


def candidate(item):
    url = urlsplit(item.get("careers_url", ""))
    host = url.hostname or ""
    path = [part for part in url.path.split("/") if part]
    if ".myworkdayjobs.com" not in host:
        return None
    site = next((part for part in path if part.lower() not in ("en-us", "en")), None)
    if not site:
        return None
    return {
        "type": "workday",
        "company": item["company"],
        "host": host,
        "tenant": host.split(".")[0],
        "site": site,
        "query": "intern",
    }


def validate(source):
    endpoint = f"https://{source['host']}/wday/cxs/{source['tenant']}/{source['site']}/jobs"
    try:
        data = fetch_json_post(endpoint, {
            "appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "intern"
        })
        return {**source, "valid": "jobPostings" in data, "jobs": data.get("total", 0)}
    except Exception as exc:
        return {**source, "valid": False, "error": str(exc)[:120]}


watchlist_path = Path(__file__).resolve().parents[1] / "company_watchlist.json"
watchlist = json.loads(watchlist_path.read_text(encoding="utf-8"))
sources = [source for item in watchlist["companies"] if (source := candidate(item))]
with ThreadPoolExecutor(max_workers=8) as pool:
    for future in as_completed(pool.submit(validate, source) for source in sources):
        print(json.dumps(future.result(), sort_keys=True))
