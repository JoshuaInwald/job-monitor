#!/usr/bin/env python3
"""
Job monitor v2.1.
Sources are failure-isolated: any source erroring is logged and skipped.
Pipeline: fetch -> weighted keyword scoring -> Core/Adjacent tiers ->
diff vs seen_jobs.json -> digest.md -> history.jsonl -> Sunday roll-up ->
closed-posting detection. Digest auto-truncates to fit GitHub's issue limit.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
CONFIG = yaml.safe_load((ROOT / "config.yml").read_text())
SEEN_PATH = ROOT / "seen_jobs.json"
HISTORY_PATH = ROOT / "history.jsonl"
OPEN_PATH = ROOT / "open_matches.json"
DIGEST_PATH = ROOT / "digest.md"

HEADERS = {"User-Agent": "Mozilla/5.0 (job-monitor; personal job alert script)"}
TIMEOUT = 30
NOW = datetime.now(timezone.utc)
MAX_DIGEST_CHARS = 60000


# ============================================================== fetchers ===

def fetch_greenhouse(c):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        yield {"id": f"gh-{c['slug']}-{j['id']}", "title": j["title"],
               "location": (j.get("location") or {}).get("name", "") or "",
               "url": j["absolute_url"]}


def fetch_lever(c):
    r = requests.get(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json():
        yield {"id": f"lv-{c['slug']}-{j['id']}", "title": j["text"],
               "location": (j.get("categories") or {}).get("location") or "",
               "url": j["hostedUrl"]}


def fetch_ashby(c):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        yield {"id": f"ab-{c['slug']}-{j['id']}", "title": j["title"],
               "location": j.get("location") or "",
               "url": j.get("jobUrl") or j.get("applyUrl") or ""}


def fetch_workday(c):
    base = f"https://{c['tenant']}.{c['host']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    offset = 0
    while offset < 200:
        r = requests.post(api, json={"appliedFacets": {}, "limit": 20,
                                     "offset": offset, "searchText": ""},
                          headers={**HEADERS, "Content-Type": "application/json"},
                          timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for j in postings:
            path = j.get("externalPath", "")
            yield {"id": f"wd-{c['tenant']}-{path}", "title": j.get("title", ""),
                   "location": j.get("locationsText", "") or "",
                   "url": f"{base}/{c['site']}{path}"}
        offset += 20
        if offset >= data.get("total", 0):
            break


COMPANY_FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever,
                    "ashby": fetch_ashby, "workday": fetch_workday}


def fetch_usajobs(cfg):
    email, key = os.environ.get("USAJOBS_EMAIL"), os.environ.get("USAJOBS_KEY")
    if not (email and key):
        print("USAJobs: secrets not set, skipping.")
        return
    h = {"User-Agent": email, "Authorization-Key": key, "Host": "data.usajobs.gov"}
    locs = cfg.get("locations", []) + (["Remote"] if cfg.get("remote_ok") else [])
    for term in cfg.get("search_terms", []):
        r = requests.get("https://data.usajobs.gov/api/search",
                         params={"Keyword": term, "ResultsPerPage": 25},
                         headers=h, timeout=TIMEOUT)
        r.raise_for_status()
        for item in r.json().get("SearchResult", {}).get("SearchResultItems", []):
            d = item.get("MatchedObjectDescriptor", {})
            loc = "; ".join(x.get("LocationName", "") for x in d.get("PositionLocation", [])[:3])
            if locs and not any(l.lower() in loc.lower() for l in locs) and "remote" not in loc.lower():
                continue
            yield {"id": f"usa-{item.get('MatchedObjectId')}",
                   "title": d.get("PositionTitle", ""), "location": loc,
                   "url": d.get("PositionURI", ""),
                   "company": d.get("OrganizationName", "US Gov")}


def fetch_adzuna(cfg):
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        print("Adzuna: secrets not set, skipping.")
        return
    for q in cfg.get("queries", []):
        r = requests.get("https://api.adzuna.com/v1/api/jobs/us/search/1",
                         params={"app_id": app_id, "app_key": app_key, "what": q,
                                 "where": cfg.get("where", ""),
                                 "results_per_page": cfg.get("max_per_query", 10)},
                         headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        for j in r.json().get("results", []):
            yield {"id": f"adz-{j.get('id')}", "title": j.get("title", ""),
                   "location": (j.get("location") or {}).get("display_name", ""),
                   "url": j.get("redirect_url", ""),
                   "company": (j.get("company") or {}).get("display_name", "via Adzuna")}


def fetch_80k(cfg):
    r = requests.get("https://api.80000hours.org/job-board/vacancies",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    vacancies = data.get("data", {}).get("vacancies", data if isinstance(data, list) else [])
    for j in vacancies:
        title = j.get("title") or j.get("Job title") or ""
        org = j.get("organisation", {})
        org_name = org.get("name") if isinstance(org, dict) else (j.get("Hiring organisation") or "80k board")
        yield {"id": f"80k-{j.get('id') or title}", "title": title,
               "location": j.get("Locations", j.get("location", "")) or "",
               "url": j.get("Link to apply") or j.get("url", "https://jobs.80000hours.org"),
               "company": org_name}


# ============================================================== scoring ===

W = CONFIG["scoring"]["weights"]
HARD = [re.compile(rf"\b{re.escape(t)}\b", re.I) for t in CONFIG["scoring"]["hard_exclude"]]
TERMS = [(re.compile(rf"\b{re.escape(t)}\b", re.I), w) for t, w in W.items()]
CORE_T = CONFIG["scoring"]["core_threshold"]
ADJ_T = CONFIG["scoring"]["adjacent_threshold"]


def score(job):
    text = f"{job['title']} {job.get('location', '')}"
    if any(p.search(job["title"]) for p in HARD):
        return None
    s = sum(w for p, w in TERMS if p.search(text))
    return s if s >= ADJ_T else None


# ============================================================== pipeline ===

def collect():
    jobs, errors = [], []

    for c in CONFIG.get("companies", []):
        try:
            for j in COMPANY_FETCHERS[c["board"]](c):
                j.setdefault("company", c["name"])
                jobs.append(j)
        except Exception as e:
            errors.append(f"{c['name']}: {type(e).__name__}: {e}")

    for name, cfg, fn in [("usajobs", CONFIG.get("usajobs", {}), fetch_usajobs),
                          ("adzuna", CONFIG.get("adzuna", {}), fetch_adzuna),
                          ("80000hours", CONFIG.get("eightyk", {}), fetch_80k)]:
        if not cfg.get("enabled"):
            continue
        try:
            jobs.extend(fn(cfg))
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")

    uniq = {}
    for j in jobs:
        uniq.setdefault(j["id"], j)
    return list(uniq.values()), errors


def main():
    first_run = not SEEN_PATH.exists()
    seen = set(json.loads(SEEN_PATH.read_text())) if not first_run else set()
    prev_open = json.loads(OPEN_PATH.read_text()) if OPEN_PATH.exists() else {}

    jobs, errors = collect()
    current_ids = {j["id"] for j in jobs}

    new_matches = []
    for j in jobs:
        s = score(j)
        if s is None:
            continue
        j["score"] = s
        j["tier"] = "core" if s >= CORE_T else "adjacent"
        if j["id"] not in seen:
            new_matches.append(j)
        prev_open[j["id"]] = {"title": j["title"], "company": j["company"],
                              "url": j["url"], "last_seen": NOW.isoformat()}

    closed = {k: v for k, v in prev_open.items()
              if k not in current_ids
              and (NOW - datetime.fromisoformat(v["last_seen"])).days >= 2}
    for k in closed:
        prev_open.pop(k)

    SEEN_PATH.write_text(json.dumps(sorted(seen | current_ids)))
    OPEN_PATH.write_text(json.dumps(prev_open, indent=1))
    with HISTORY_PATH.open("a") as f:
        for j in new_matches:
            f.write(json.dumps({**j, "found": NOW.isoformat()}) + "\n")

    print(f"Fetched {len(jobs)} postings | {len(new_matches)} new matches | "
          f"{len(closed)} closed | {len(errors)} source errors")
    for e in errors:
        print(f"  SOURCE ERROR: {e}", file=sys.stderr)

    digest = build_digest(new_matches, closed, errors, first_run)
    if len(digest) > MAX_DIGEST_CHARS:
        cut = digest[:MAX_DIGEST_CHARS]
        cut = cut[:cut.rfind("\n")]
        digest = cut + ("\n\n---\n*Digest truncated to fit GitHub's issue size "
                        "limit; lower-scored matches omitted. Full record is in "
                        "`history.jsonl`.*")
    DIGEST_PATH.write_text(digest)


def build_digest(new_matches, closed, errors, first_run):
    parts = []
    today = NOW.strftime("%Y-%m-%d")

    if new_matches:
        title = "Baseline snapshot (all currently-open matches)" if first_run else "New matches"
        parts.append(f"# {title} — {today}\n")
        for tier, label in [("core", "Core matches"), ("adjacent", "Adjacent / worth a look")]:
            tj = sorted([j for j in new_matches if j["tier"] == tier],
                        key=lambda x: -x["score"])
            if tj:
                parts.append(f"## {label}\n")
                for j in tj:
                    loc = f" — {j['location']}" if j.get("location") else ""
                    parts.append(f"- **[{j['score']}]** [{j['title']}]({j['url']}) @ {j['company']}{loc}")
                parts.append("")

    if closed and not first_run:
        parts.append("## Likely closed/filled since last check\n")
        for v in closed.values():
            parts.append(f"- ~~{v['title']}~~ @ {v['company']}")
        parts.append("")

    if NOW.weekday() == 6 and HISTORY_PATH.exists():
        week_ago = NOW - timedelta(days=7)
        week = [json.loads(l) for l in HISTORY_PATH.read_text().splitlines() if l.strip()]
        week = [j for j in week if datetime.fromisoformat(j["found"]) >= week_ago]
        if week:
            parts.append(f"## Weekly roll-up ({len(week)} matches in past 7 days)\n")
            for j in sorted(week, key=lambda x: -x.get("score", 0)):
                parts.append(f"- [{j.get('score', '?')}] [{j['title']}]({j['url']}) @ {j['company']}")
            parts.append("")

    if errors and parts:
        parts.append("<details><summary>Source errors this run</summary>\n")
        parts.extend(f"- {e}" for e in errors)
        parts.append("\n</details>")

    if parts:
        parts.append("\n---\n*Tip: paste this issue into claude.ai with the "
                     "prompt in `claude_scoring_prompt.md` for a deeper fit review.*")
    return "\n".join(parts)


if __name__ == "__main__":
    main()
