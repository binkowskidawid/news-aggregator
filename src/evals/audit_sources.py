"""Probe every portal on the candidate list for RSS viability.

This answers the first go/no-go criterion — whether automated fetching works for at least
five of six portals — and it is the cheapest information in the project, which is why it
runs before anything expensive.

The probe deliberately reports more than "does a feed exist". A feed whose ``description``
repeats the headline looks like a success in a status table and behaves like a failure in
production: it silently forces every article to the headline-only input variant, and
technique density per unit of text is not comparable between a portal that exposes a lead
and one that does not. So lead coverage is measured, not assumed.

    make audit-sources

Writes ``docs/sources-audit.md`` (the audit itself) and ``eval/candidates.csv`` (the pool
the gold set is chosen from). Nothing is written to the database: articles enter it as
part of the gold set, where they arrive with labels attached.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import statistics
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import urljoin

import feedparser
import httpx
from selectolax.parser import HTMLParser

from config import Settings, load_dotenv

# The fetch helpers live with the ingest pipeline, which is what actually reads these
# feeds. This module measured them; it does not own them.
from ingest.fetch import (
    CONNECT_TIMEOUT_S,
    READ_TIMEOUT_S,
    Throttle,
    canonical_url,
    entry_lead,
    entry_published,
    robots_verdict,
    user_agent,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
AUDIT_PATH: Final = REPO_ROOT / "docs" / "sources-audit.md"
CANDIDATES_PATH: Final = REPO_ROOT / "eval" / "candidates.csv"


@dataclass(frozen=True, slots=True)
class Portal:
    """One outlet, with every address worth trying before declaring it unfetchable."""

    name: str
    homepage: str
    feed_candidates: tuple[str, ...]
    note: str = ""


PORTALS: Final[tuple[Portal, ...]] = (
    Portal(
        name="Interia",
        homepage="https://wydarzenia.interia.pl/",
        feed_candidates=(
            "https://wydarzenia.interia.pl/feed",
            "https://fakty.interia.pl/feed",
        ),
        note="Confirmed working in the August probe; the reference case.",
    ),
    Portal(
        name="Onet",
        homepage="https://wiadomosci.onet.pl/",
        feed_candidates=(
            "https://wiadomosci.onet.pl/.feed",
            "https://wiadomosci.onet.pl/rss.xml",
            "https://www.onet.pl/.feed",
        ),
        note="Reportedly retired most channels in 2023, keeping Wiadomości.",
    ),
    Portal(
        name="Gazeta.pl",
        homepage="https://wiadomosci.gazeta.pl/",
        feed_candidates=(
            "https://wiadomosci.gazeta.pl/pub/rss/wiadomosci.htm",
            "https://rss.gazeta.pl/pub/rss/wiadomosci.htm",
            "https://wiadomosci.gazeta.pl/pub/rss/najnowsze_wiadomosci.htm",
        ),
    ),
    Portal(
        name="WP",
        homepage="https://wiadomosci.wp.pl/",
        feed_candidates=(
            "https://wiadomosci.wp.pl/rss.xml",
            "https://www.wp.pl/rss.xml",
        ),
    ),
    Portal(
        name="TVN24",
        homepage="https://tvn24.pl/",
        feed_candidates=(
            "https://tvn24.pl/najnowsze.xml",
            "https://tvn24.pl/najwazniejsze.xml",
        ),
        note="Returned an anti-bot response to the earlier probe; the one real refusal.",
    ),
    Portal(
        name="TV Republika",
        homepage="https://tvrepublika.pl/",
        feed_candidates=(
            "https://tvrepublika.pl/rss.xml",
            "https://tvrepublika.pl/feed",
            "https://tvrepublika.pl/rss",
        ),
        note="Drupal/Thunder. Homepage lists headlines without leads.",
    ),
)


@dataclass(frozen=True, slots=True)
class Entry:
    """One article as the feed presented it."""

    portal: str
    url: str
    title: str
    lead: str
    source_category: str
    published: str


@dataclass(frozen=True, slots=True)
class FeedReport:
    """Everything one candidate address told us."""

    portal: str
    url: str
    status: int | None
    error: str | None
    items: int
    with_lead: int
    median_lead_chars: int
    has_source_category: bool
    conditional_get: str
    span_hours: float | None
    discovered: bool = False

    @property
    def usable(self) -> bool:
        """A feed is usable when it parsed and actually carried articles."""
        return self.error is None and self.items > 0

    @property
    def lead_coverage(self) -> float:
        return self.with_lead / self.items if self.items else 0.0

    @property
    def tier(self) -> str:
        """Which fetch level this portal lands on, which is what drives cost.

        A feed without leads is not a failure — it is level 2, one extra request per
        article, and the estimate has to carry that.
        """
        if not self.usable:
            return "3 (browser/scraping)"
        if self.lead_coverage >= 0.8:
            return "1 (RSS)"
        return "2 (RSS + article fetch)"


def _entry_category(entry: Any) -> str:
    tags = entry.get("tags") or []
    if tags:
        term = tags[0].get("term")
        if isinstance(term, str):
            return term
    category = entry.get("category")
    return category if isinstance(category, str) else ""


def _analyse(portal: Portal, url: str, response: httpx.Response) -> tuple[FeedReport, list[Entry]]:
    """Parse one feed body and measure what it actually offers."""
    parsed = feedparser.parse(response.content)
    raw_entries: list[Any] = list(parsed.entries)

    entries: list[Entry] = []
    lead_lengths: list[int] = []
    moments: list[datetime] = []
    has_category = False

    for raw in raw_entries:
        title = " ".join(str(raw.get("title") or "").split())
        link = str(raw.get("link") or "")
        if not title or not link:
            continue
        lead = entry_lead(raw, title)
        if lead:
            lead_lengths.append(len(lead))
        category = _entry_category(raw)
        has_category = has_category or bool(category)
        published, moment = entry_published(raw)
        if moment is not None:
            moments.append(moment)
        entries.append(
            Entry(
                portal=portal.name,
                url=link,
                title=title,
                lead=lead,
                source_category=category,
                published=published,
            )
        )

    span = None
    if len(moments) >= 2:
        span = round((max(moments) - min(moments)).total_seconds() / 3600, 1)

    conditional = ", ".join(
        header
        for header in ("ETag", "Last-Modified")
        if response.headers.get(header.lower()) is not None
    )

    report = FeedReport(
        portal=portal.name,
        url=url,
        status=response.status_code,
        error=None if entries else "parsed, but no usable entries",
        items=len(entries),
        with_lead=len(lead_lengths),
        median_lead_chars=int(statistics.median(lead_lengths)) if lead_lengths else 0,
        has_source_category=has_category,
        conditional_get=conditional or "none",
        span_hours=span,
    )
    return report, entries


async def _get(client: httpx.AsyncClient, throttle: Throttle, url: str) -> httpx.Response:
    await throttle.wait(url)
    return await client.get(url)


async def _discover_feeds(
    client: httpx.AsyncClient, throttle: Throttle, portal: Portal
) -> list[str]:
    """Read the homepage's ``<link rel="alternate">`` declarations.

    The reason TV Republika is worth a second look: a Drupal site commonly serves a feed
    that no visible link points at, so an address list assembled by hand can miss one that
    the markup declares.
    """
    try:
        response = await _get(client, throttle, portal.homepage)
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    found: list[str] = []
    for node in HTMLParser(response.text).css('link[rel="alternate"]'):
        media_type = node.attributes.get("type") or ""
        href = node.attributes.get("href")
        if href and ("rss" in media_type or "atom" in media_type):
            found.append(urljoin(portal.homepage, href))
    return found


async def _probe_portal(
    client: httpx.AsyncClient, throttle: Throttle, portal: Portal, agent: str
) -> tuple[list[FeedReport], list[Entry], str]:
    """Try every known address for one portal, then whatever its homepage declares."""
    reports: list[FeedReport] = []
    entries: list[Entry] = []

    candidates = [(url, False) for url in portal.feed_candidates]
    discovered = await _discover_feeds(client, throttle, portal)
    candidates += [(url, True) for url in discovered if url not in portal.feed_candidates]

    for url, was_discovered in candidates:
        try:
            response = await _get(client, throttle, url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            reports.append(
                FeedReport(
                    portal=portal.name,
                    url=url,
                    status=status,
                    # Collapsed to one line: httpx errors carry a documentation URL on a
                    # second line, which would break out of the markdown table cell.
                    error=" ".join(f"{type(exc).__name__}: {exc}".split())[:120],
                    items=0,
                    with_lead=0,
                    median_lead_chars=0,
                    has_source_category=False,
                    conditional_get="none",
                    span_hours=None,
                    discovered=was_discovered,
                )
            )
            continue

        report, feed_entries = _analyse(portal, url, response)
        reports.append(replace(report, discovered=was_discovered))
        entries.extend(feed_entries)

    best = next((report for report in reports if report.usable), None)
    robots = await robots_verdict(client, throttle, best.url if best else portal.homepage)
    return reports, entries, robots


def _render(results: list[tuple[Portal, list[FeedReport], str]], agent: str) -> str:
    """Render the audit as the document the go/no-go decision cites."""
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Audyt źródeł",
        "",
        f"Wygenerowane: {generated} przez `make audit-sources`.",
        f"User-Agent: `{agent}`",
        "",
        "Kryterium wyjścia nr 1 ze spec §9: pipeline pobierania działa automatycznie dla",
        "co najmniej 5 z 6 portali.",
        "",
        "## Podsumowanie",
        "",
        "| Portal | Poziom | Feed | Pozycji | Z leadem | Mediana leadu | Kategoria | robots.txt |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]

    usable_count = 0
    for portal, reports, robots in results:
        best = next((report for report in reports if report.usable), None)
        if best is not None:
            usable_count += 1
            lines.append(
                f"| {portal.name} | {best.tier} | `{best.url}` | {best.items} | "
                f"{best.with_lead} ({best.lead_coverage:.0%}) | {best.median_lead_chars} zn. | "
                f"{'tak' if best.has_source_category else 'nie'} | {robots} |"
            )
        else:
            lines.append(
                f"| {portal.name} | 3 (browser/scraping) | — | 0 | 0 | 0 zn. | nie | {robots} |"
            )

    lines += [
        "",
        f"**Portale z działającym feedem: {usable_count} z {len(results)}.**",
        "",
        "Poziom to najtańszy działający sposób pobrania artykułu. Poziom 2 oznacza, że feed",
        "podaje tytuły bez leadu — każdy artykuł wymaga wtedy osobnego żądania, co zmienia",
        "koszt pobierania z tego portalu, a nie jest detalem technicznym.",
        "",
        "## Szczegóły prób",
        "",
    ]

    for portal, reports, robots in results:
        lines += [f"### {portal.name}", ""]
        if portal.note:
            lines += [f"> {portal.note}", ""]
        lines += [
            "| Adres | HTTP | Wynik | Pozycji | Z leadem | Okno czasowe | Conditional GET |",
            "| --- | ---: | --- | ---: | ---: | --- | --- |",
        ]
        for report in reports:
            outcome = report.error or "OK"
            span = f"{report.span_hours} h" if report.span_hours is not None else "—"
            marker = " *(z HTML)*" if report.discovered else ""
            lines.append(
                f"| `{report.url}`{marker} | {report.status or '—'} | {outcome} | "
                f"{report.items} | {report.with_lead} | {span} | {report.conditional_get} |"
            )
        lines += ["", f"robots.txt: **{robots}**", ""]

    return "\n".join(lines) + "\n"


def _write_candidates(entries: list[Entry]) -> int:
    """Persist the article pool the gold set will be hand-picked from."""
    # Portals publish the same story on several channels, so the pool is deduplicated on
    # the canonical URL before it is written. First occurrence wins; ordering is stable.
    unique: dict[str, Entry] = {}
    for entry in entries:
        unique.setdefault(canonical_url(entry.url), entry)

    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CANDIDATES_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["portal", "url", "title", "lead", "source_category", "published"])
        for url, entry in unique.items():
            writer.writerow(
                [
                    entry.portal,
                    url,
                    entry.title,
                    entry.lead,
                    entry.source_category,
                    entry.published,
                ]
            )
    return len(unique)


async def _run(args: argparse.Namespace) -> int:
    load_dotenv()
    settings = Settings.from_env()
    agent = user_agent(settings.contact_email, "audit")

    selected = [p for p in PORTALS if not args.portal or p.name.lower() in args.portal]
    if not selected:
        print(f"no portal matched {args.portal}")
        return 2

    throttle = Throttle()
    results: list[tuple[Portal, list[FeedReport], str]] = []
    all_entries: list[Entry] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": agent, "Accept": "application/rss+xml, application/xml, text/html"},
        timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        follow_redirects=True,
    ) as client:
        for portal in selected:
            print(f"-> {portal.name}")
            reports, entries, robots = await _probe_portal(client, throttle, portal, agent)
            results.append((portal, reports, robots))
            all_entries.extend(entries)
            best = next((report for report in reports if report.usable), None)
            if best is None:
                print(f"   no usable feed among {len(reports)} candidate(s); robots={robots}")
            else:
                print(
                    f"   {best.url} -> {best.items} items, "
                    f"{best.lead_coverage:.0%} with a lead, tier {best.tier}"
                )

    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.write_text(_render(results, agent), encoding="utf-8")
    written = _write_candidates(all_entries)

    usable = sum(1 for _, reports, _ in results if any(r.usable for r in reports))
    print(f"\n{usable}/{len(results)} portals expose a usable feed")
    print(f"{AUDIT_PATH.relative_to(REPO_ROOT)} written")
    print(f"{CANDIDATES_PATH.relative_to(REPO_ROOT)} written ({written} articles)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--portal",
        nargs="*",
        default=[],
        type=str.lower,
        help="limit the probe to these portals (lowercase names)",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
