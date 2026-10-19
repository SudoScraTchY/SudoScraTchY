#!/usr/bin/env python3
"""Render self-hosted GitHub stats SVGs (light + dark) for the profile README.

Data comes from the GitHub GraphQL API with GITHUB_TOKEN. No third-party
stat cards, no pip dependencies — Python standard library only.

Usage:
  GITHUB_TOKEN=... python scripts/generate_stats.py --user SudoScraTchY --out-dir assets
  python scripts/generate_stats.py --sample --out-dir /tmp/preview   # sample data, no token
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

GRAPHQL_URL = "https://api.github.com/graphql"
UA = "profile-stats-generator (self-hosted)"

QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    followers { totalCount }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      contributionCalendar {
        weeks {
          contributionDays {
            contributionCount
            date
          }
        }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

FALLBACK_COLORS = ["#2FC1B0", "#F0B25A", "#8B7CF0", "#E06C75", "#56B6C2", "#98C379"]

THEMES = {
    "light": {
        "bg": "#FAF6EF",
        "ink": "#16233F",
        "muted": "#5C6678",
        "line": "#16233F",
        "line_op": "0.18",
        "grid_op": "0.05",
        "accent": "#1FA396",
        "accent2": "#D98E2B",
    },
    "dark": {
        "bg": "#0D1321",
        "ink": "#E8E3D6",
        "muted": "#93A0B8",
        "line": "#E8E3D6",
        "line_op": "0.20",
        "grid_op": "0.04",
        "accent": "#2FC1B0",
        "accent2": "#F0B25A",
    },
}


def fetch_stats(token: str, login: str) -> dict:
    payload = json.dumps({"query": QUERY, "variables": {"login": login}}).encode("utf-8")
    last_error = None
    for attempt in range(3):
        req = urllib.request.Request(
            GRAPHQL_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": UA,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if body.get("errors"):
                raise RuntimeError(f"GraphQL errors: {body['errors']}")
            if not body.get("data", {}).get("user"):
                raise RuntimeError(f"user '{login}' not found")
            return body["data"]["user"]
        except (urllib.error.URLError, RuntimeError, ValueError) as exc:  # noqa: PERF203
            last_error = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GitHub API failed after 3 attempts: {last_error}")


def sample_stats() -> dict:
    """Deterministic fake data for offline previews — clearly marked in the SVG."""
    today = dt.date.today()
    days = []
    for i in range(365):
        d = today - dt.timedelta(days=364 - i)
        count = (i * 7 + (i % 11) * 3) % 9
        days.append((d.isoformat(), count))
    langs = [
        ("C#", 2398, "#178600"),
        ("TypeScript", 594, "#3178c6"),
        ("JavaScript", 116, "#f1e05a"),
        ("HTML", 298, "#e34c26"),
        ("CSS", 131, "#663399"),
        ("Python", 66, "#3572A5"),
        ("PowerShell", 16, "#012456"),
    ]
    return {
        "login": "SudoScraTchY",
        "followers": 2,
        "commits": 412,
        "prs": 38,
        "issues": 21,
        "reviews": 17,
        "stars": 5,
        "repos": 11,
        "longest_streak": 12,
        "current_streak": 3,
        "languages": langs,
        "_sample": True,
    }


def compute_streaks(days: list[tuple[str, int]]) -> tuple[int, int]:
    """Return (longest streak, current streak) over contribution days."""
    counts = [c for _, c in sorted(days)]
    longest = cur = 0
    for c in counts:
        cur = cur + 1 if c > 0 else 0
        longest = max(longest, cur)
    current = 0
    for c in reversed(counts):
        if c > 0:
            current += 1
        else:
            break
    return longest, current


def aggregate(user: dict) -> dict:
    cal = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = [
        (d["date"], d["contributionCount"])
        for week in cal
        for d in week["contributionDays"]
    ]
    longest, current = compute_streaks(days)
    langs: dict[str, list] = {}
    stars = 0
    for repo in user["repositories"]["nodes"]:
        stars += repo["stargazerCount"]
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            entry = langs.setdefault(name, [0, edge["node"].get("color") or ""])
            entry[0] += edge["size"]
    ordered = sorted(langs.items(), key=lambda kv: kv[1][0], reverse=True)
    return {
        "login": user["login"],
        "followers": user["followers"]["totalCount"],
        "commits": sum(c for _, c in days),
        "prs": user["contributionsCollection"]["totalPullRequestContributions"],
        "issues": user["contributionsCollection"]["totalIssueContributions"],
        "reviews": user["contributionsCollection"]["totalPullRequestReviewContributions"],
        "stars": stars,
        "repos": user["repositories"]["totalCount"],
        "longest_streak": longest,
        "current_streak": current,
        "languages": [
            (name, sizes, color) for name, (sizes, color) in ordered
        ],
        "_sample": False,
    }


def fmt(n: int) -> str:
    return f"{n:,}"


def lang_slices(languages: list, max_slices: int = 6) -> list[tuple[str, int, str, float]]:
    total = sum(size for _, size, _ in languages) or 1
    top = languages[:max_slices]
    rest = languages[max_slices:]
    slices = [
        (name, size, color or FALLBACK_COLORS[i % len(FALLBACK_COLORS)], size / total)
        for i, (name, size, color) in enumerate(top)
    ]
    if rest:
        rest_size = sum(size for _, size, _ in rest)
        slices.append((f"Other ({len(rest)})", rest_size, "#8A93A6", rest_size / total))
    return slices


MONO = "'Cascadia Code','JetBrains Mono','Fira Code',Consolas,monospace"
SANS = "'Avenir Next','Segoe UI','Helvetica Neue',Arial,sans-serif"

W, H = 760, 420


def render(theme: str, s: dict) -> str:
    t = THEMES[theme]
    tiles = [
        ("COMMITS", fmt(s["commits"]), "last 12 months"),
        ("LONGEST STREAK", f'{s["longest_streak"]}d', f'current {s["current_streak"]}d'),
        ("PULL REQUESTS", fmt(s["prs"]), "opened"),
        ("ISSUES", fmt(s["issues"]), "opened"),
        ("REVIEWS", fmt(s["reviews"]), "code review"),
        ("STARS EARNED", fmt(s["stars"]), f'across {fmt(s["repos"])} repos'),
    ]
    x0, y0, tw, th, gap = 32, 76, 222, 88, 16

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="st sd">',
        f'<title id="st">GitHub activity for @{escape(s["login"])}</title>',
        f'<desc id="sd">Six activity tiles for the last 12 months — commits, longest streak, pull requests, issues, reviews, stars earned — plus a language distribution bar. Generated by scripts/generate_stats.py.</desc>',
        '<defs>',
        '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">'
        f'<path d="M40 0H0V40" fill="none" stroke="{t["line"]}" stroke-opacity="{t["grid_op"]}" stroke-width="1"/>'
        '</pattern>',
        f'<clipPath id="bar"><rect x="{x0}" y="318" width="696" height="14" rx="4"/></clipPath>',
        '</defs>',
        f'<rect width="{W}" height="{H}" fill="{t["bg"]}"/>',
        f'<rect width="{W}" height="{H}" fill="url(#grid)"/>',
        f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" fill="none" stroke="{t["line"]}" stroke-opacity="{t["line_op"]}"/>',
        # header
        f'<text x="{x0}" y="46" font-family="{MONO}" font-size="12" letter-spacing="3" fill="{t["ink"]}">ACTIVITY · LAST 12 MONTHS</text>',
        f'<text x="{W - 32}" y="46" text-anchor="end" font-family="{MONO}" font-size="11" fill="{t["muted"]}">@{escape(s["login"])} · {fmt(s["followers"])} followers</text>',
        f'<line x1="{x0}" y1="58" x2="{W - 32}" y2="58" stroke="{t["line"]}" stroke-opacity="{t["line_op"]}"/>',
    ]

    # tiles
    for i, (label, value, sub) in enumerate(tiles):
        col, row = i % 3, i // 3
        x = x0 + col * (tw + gap)
        y = y0 + row * (th + gap)
        parts.append(f'<rect x="{x}" y="{y}" width="{tw}" height="{th}" fill="none" stroke="{t["line"]}" stroke-opacity="{t["line_op"]}"/>')
        parts.append(f'<rect x="{x}" y="{y}" width="3" height="{th}" fill="{t["accent"] if i % 2 == 0 else t["accent2"]}"/>')
        parts.append(f'<text x="{x + 14}" y="{y + 24}" font-family="{MONO}" font-size="10" letter-spacing="2" fill="{t["muted"]}">{escape(label)}</text>')
        parts.append(f'<text x="{x + 14}" y="{y + 58}" font-family="{MONO}" font-size="30" font-weight="700" fill="{t["ink"]}">{escape(value)}</text>')
        parts.append(f'<text x="{x + 14}" y="{y + 76}" font-family="{MONO}" font-size="10" fill="{t["muted"]}">{escape(sub)}</text>')

    # language bar + legend
    parts.append(f'<text x="{x0}" y="308" font-family="{MONO}" font-size="10" letter-spacing="2" fill="{t["muted"]}">LANGUAGES · BYTES ACROSS OWN REPOS</text>')
    slices = lang_slices(s["languages"])
    bar_x, bar_w = x0, 696
    offset = 0.0
    segs = []
    for name, size, color, share in slices:
        seg_w = bar_w * share
        segs.append(f'<rect x="{bar_x + offset:.1f}" y="318" width="{max(seg_w, 1):.1f}" height="14" fill="{color}"/>')
        offset += seg_w
    parts.append(f'<g clip-path="url(#bar)">{"".join(segs)}</g>')

    legend = []
    lx = x0
    for name, _size, color, share in slices[:5]:
        label = f'{name} {share * 100:.0f}%'
        legend.append(f'<rect x="{lx}" y="348" width="8" height="8" fill="{color}"/>')
        legend.append(f'<text x="{lx + 14}" y="356" font-family="{MONO}" font-size="11" fill="{t["ink"]}">{escape(label)}</text>')
        lx += 14 + int(len(label) * 7.2) + 22

    parts.append("".join(legend))

    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    footer = f'auto-generated by scripts/generate_stats.py · {generated}'
    if s.get("_sample"):
        footer += ' · SAMPLE DATA'
        parts.append(
            f'<text x="{W // 2}" y="{H // 2}" text-anchor="middle" transform="rotate(-14 {W // 2} {H // 2})" '
            f'font-family="{SANS}" font-size="56" font-weight="800" fill="{t["ink"]}" fill-opacity="0.08">SAMPLE</text>'
        )
    parts.append(f'<text x="{x0}" y="398" font-family="{MONO}" font-size="10" fill="{t["muted"]}" fill-opacity="0.8">{escape(footer)}</text>')
    parts.append('</svg>')
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=os.environ.get("GITHUB_USER", "SudoScraTchY"))
    parser.add_argument("--out-dir", default="assets")
    parser.add_argument("--theme", choices=["light", "dark", "both"], default="both")
    parser.add_argument("--sample", action="store_true", help="render sample data without touching the API")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if not args.sample and not token:
        print("error: GITHUB_TOKEN is not set (or pass --sample)", file=sys.stderr)
        return 2

    if args.sample:
        stats = sample_stats()
    else:
        stats = aggregate(fetch_stats(token, args.user))
        if stats["login"].lower() != args.user.lower():
            print(f'error: authenticated identity mismatch ({stats["login"]} != {args.user})', file=sys.stderr)
            return 3

    os.makedirs(args.out_dir, exist_ok=True)
    themes = ["light", "dark"] if args.theme == "both" else [args.theme]
    for theme in themes:
        path = os.path.join(args.out_dir, f"stats-{theme}.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render(theme, stats))
        print(f"written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
