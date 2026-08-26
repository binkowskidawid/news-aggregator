"""Turning one configured source into articles, by the cheapest route that works.

Two strategies, both reading a single address per pass:

``rss``
    Five of the six portals expose a feed carrying headline, lead and publication time.
    Conditional GET is used where the portal supports it, so an unchanged feed costs a
    304 instead of a body.
``static``
    TV Republika publishes no feed. Its homepage is server-rendered Drupal, so the
    listing is read directly — a few requests per pass rather than one per article.

Article text is untrusted input for the entire pipeline. Nothing here interprets it;
titles and leads are whitespace-normalised and passed on verbatim.

The helpers below are shared with ``evals.audit_sources``, which measured the feeds this
module now reads. They live here rather than there because the audit is a diagnostic and
this is the product — and because the "a summary repeating the headline is not a lead"
rule decides what lands in ``articles.lead``. Two copies of that rule would drift apart.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import feedparser
import httpx
from protego import Protego
from selectolax.parser import HTMLParser

ROBOTS_AGENT: Final = "news-aggregator"
"""The name robots.txt rules are matched against, per RFC 9309.

Kept free of the component suffix on purpose. Matching in both available parsers is
substring-based against the token, and Gazeta.pl's robots.txt denies a bot literally
named ``es`` — which "news-aggregator-ingest" contains. The descriptive part of the
User-Agent header therefore goes in the comment, where no matcher looks at it.
"""

CONNECT_TIMEOUT_S: Final = 15.0
READ_TIMEOUT_S: Final = 30.0
CRAWL_DELAY_S: Final = 2.0
"""One request per two seconds per domain, per the specification's fetch hygiene."""

MIN_LEAD_CHARS: Final = 80
"""Below this a ``description`` is a teaser fragment, not a lead worth analysing.

Interia's feed carries 300 to 600 characters. The threshold is low enough that a shorter
but genuine lead still counts, and high enough to exclude a repeated headline.
"""

RAW_RESPONSE_LIMIT: Final = 20_000
"""How much of a failing response is kept for diagnosis.

A broken selector fails on every pass, so an unbounded body would put megabytes a day
into ``fetch_errors``. Enough to see which markup changed is enough.
"""

MAX_ANCHOR_HOPS: Final = 4
"""How far up from a headline element to look for the link wrapping it.

TV Republika's cards nest the heading one to three levels inside the anchor depending on
the slot. Searching without a bound would eventually find the page's navigation.
"""

RSS_FETCH_LEVEL: Final = 1
STATIC_FETCH_LEVEL: Final = 2
"""Which tier produced a row, mirroring ``articles.fetch_level``. This is a cost metric,
so it records what the fetch actually took, not what the portal was once expected to need.
"""

TRACKING_PARAMS: Final = ("srcc", "s", "utm_source", "utm_medium", "utm_campaign", "utm_term")
"""Query parameters that identify the referrer rather than the article."""


def canonical_url(url: str) -> str:
    """Strip tracking parameters and the fragment, so one article is one key.

    The same article reached from a feed and from a homepage differs only in these
    parameters. Without stripping them the pool double-counts, and later the same
    article would occupy two rows in ``articles`` under two different ``url_hash``es.
    """
    parts = urlparse(url)
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    return urlunparse(parts._replace(query=urlencode(kept), fragment=""))


def digest(*parts: str) -> str:
    """Hash the parts of an article's identity into one key.

    Shared with the gold loader on purpose: both must produce the same ``url_hash`` for
    the same article, or an article already in the evaluation set would be inserted a
    second time when ingest happens to pick it up.
    """
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def user_agent(contact: str, component: str) -> str:
    """Identify the crawler with a contactable address, per the fetch hygiene rules.

    The product token stays constant across components; which part of the pipeline is
    calling is a comment. See :data:`ROBOTS_AGENT` for why that separation matters.
    """
    parts = [component, "Polish press research"]
    if contact and "example.com" not in contact:
        parts.append(f"+{contact}")
    return f"{ROBOTS_AGENT}/0.1 ({'; '.join(parts)})"


class Throttle:
    """One request per domain per ``CRAWL_DELAY_S``, tracked per host."""

    def __init__(self, delay: float = CRAWL_DELAY_S) -> None:
        self._delay = delay
        self._last: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        previous = self._last.get(host)
        now = time.monotonic()
        if previous is not None:
            remaining = self._delay - (now - previous)
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last[host] = time.monotonic()


def clean_html(raw: str) -> str:
    """Reduce a feed's HTML summary to the text a reader would see."""
    if not raw:
        return ""
    return " ".join(HTMLParser(raw).text().split())


def entry_lead(entry: Any, title: str) -> str:
    """Return the lead a feed entry carries, or an empty string if it carries none.

    A summary that merely repeats the headline is treated as absent. Counting it as a
    lead would overstate the portal's tier and, downstream, feed the model the same
    sentence twice under two different labels.
    """
    # feedparser is untyped and returns a mapping whose keys depend on the feed dialect,
    # so entries stay Any and every field access is guarded.
    raw = entry.get("summary") or entry.get("description") or ""
    lead = clean_html(str(raw))
    if not lead or lead == title:
        return ""
    if lead.startswith(title) and len(lead) - len(title) < MIN_LEAD_CHARS:
        return ""
    return lead if len(lead) >= MIN_LEAD_CHARS else ""


def entry_published(entry: Any) -> tuple[str, datetime | None]:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is None:
        return "", None
    # Written out rather than unpacked: feedparser hands back a time.struct_time whose
    # length mypy cannot see, and *parsed[:6] then collides with the tzinfo keyword.
    moment = datetime(parsed[0], parsed[1], parsed[2], parsed[3], parsed[4], parsed[5], tzinfo=UTC)
    return moment.isoformat(), moment


@dataclass(frozen=True, slots=True)
class Source:
    """One row of ``sources``: where to fetch and what was learned last time."""

    id: uuid.UUID
    name: str
    base_url: str
    rss_url: str | None
    strategy: str
    selectors: dict[str, str] = field(default_factory=dict)
    last_etag: str | None = None
    last_fetch_at: datetime | None = None

    @property
    def address(self) -> str:
        """The single address this source is read from."""
        return self.rss_url or self.base_url


@dataclass(frozen=True, slots=True)
class FetchedArticle:
    url: str
    title: str
    lead: str | None
    published_at: datetime | None
    fetch_level: int


@dataclass(frozen=True, slots=True)
class FetchResult:
    """What one source yielded, including the reasons it yielded nothing."""

    articles: tuple[FetchedArticle, ...] = ()
    etag: str | None = None
    not_modified: bool = False
    error_type: str | None = None
    error_message: str | None = None
    raw_response: str | None = None


def robots_allows(robots_txt: str, url: str) -> bool:
    """Whether these rules permit this address for :data:`ROBOTS_AGENT`.

    ``protego`` rather than the standard library's ``RobotFileParser``, because the
    difference is not cosmetic: stdlib does not implement the ``*`` and ``$`` wildcards
    that RFC 9309 defines for paths, so on Gazeta.pl — whose rules use them — it reports
    ``/pub/ips/x`` and ``/getFile.servlet?a=1`` as permitted when the portal has denied
    both. A robots check that under-blocks is worse than none, because it is trusted.
    """
    return bool(Protego.parse(robots_txt).can_fetch(url, ROBOTS_AGENT))


async def robots_verdict(client: httpx.AsyncClient, throttle: Throttle, url: str) -> str:
    """Fetch robots.txt for this address's host and report what it says."""
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        await throttle.wait(robots_url)
        response = await client.get(robots_url)
    except httpx.HTTPError as exc:
        return f"unreachable ({type(exc).__name__})"
    if response.status_code != 200:
        return f"absent (HTTP {response.status_code})"

    # Parsing text we fetched ourselves, rather than letting a parser open its own
    # connection: that path would send neither our user agent nor our timeouts, so it
    # would be answering a question about a different client.
    return "allowed" if robots_allows(response.text, url) else "DISALLOWED"


def parse_feed(body: bytes, base_url: str) -> list[FetchedArticle]:
    """Turn a feed body into articles, deduplicated on the canonical URL.

    Portals publish the same story on several channels within one feed, so the pass
    would otherwise report more articles than it found.
    """
    parsed = feedparser.parse(body)
    seen: set[str] = set()
    articles: list[FetchedArticle] = []

    for raw in list(parsed.entries):
        title = " ".join(str(raw.get("title") or "").split())
        link = str(raw.get("link") or "")
        if not title or not link:
            continue
        url = canonical_url(urljoin(base_url, link))
        if url in seen:
            continue
        seen.add(url)
        lead = entry_lead(raw, title)
        _, published = entry_published(raw)
        articles.append(
            FetchedArticle(
                url=url,
                title=title,
                lead=lead or None,
                published_at=published,
                fetch_level=RSS_FETCH_LEVEL,
            )
        )
    return articles


def parse_listing(html: str, base_url: str, selector: str) -> list[FetchedArticle]:
    """Turn a homepage listing into articles.

    Each headline is matched, then the anchor wrapping it is found by walking up. Cards
    without one are promotional slots rather than articles, and dropping them is the
    filter: matching anchors directly would also collect the category tag links that sit
    inside the same card.

    The text taken is the card's, which for the largest slots is a teaser the newsroom
    wrote rather than the article's own H1. That is still this outlet's editorial
    language, but it is not the same object as an RSS headline, and any comparison of
    technique density against the feed-based portals has to say so.
    """
    tree = HTMLParser(html)
    seen: set[str] = set()
    articles: list[FetchedArticle] = []

    for heading in tree.css(selector):
        node = heading.parent
        hops = 0
        while node is not None and node.tag != "a" and hops < MAX_ANCHOR_HOPS:
            node = node.parent
            hops += 1
        if node is None or node.tag != "a":
            continue
        href = node.attributes.get("href")
        # Normalised rather than stripped per node: the headings wrap decorative elements,
        # and concatenating their text without separators would fuse adjacent words.
        title = " ".join(heading.text().split())
        if not href or not title:
            continue
        url = canonical_url(urljoin(base_url, href))
        if url in seen:
            continue
        seen.add(url)
        articles.append(
            FetchedArticle(
                url=url,
                title=title,
                # The listing carries neither a lead nor a publication date — no JSON-LD,
                # no article:published_time, no <time>. `articles.fetched_at` is the only
                # approximation available, and writing now() into published_at would
                # dress a guess up as a fact.
                lead=None,
                published_at=None,
                fetch_level=STATIC_FETCH_LEVEL,
            )
        )
    return articles


def _conditional_headers(source: Source) -> dict[str, str]:
    """Ask the portal to answer 304 when nothing changed.

    Onet and TVN24 return an ETag, Gazeta.pl a Last-Modified, Interia and WP neither.
    Both validators are sent because ``sources`` already stores both — the fetch time is
    exactly what If-Modified-Since wants.
    """
    headers: dict[str, str] = {}
    if source.last_etag:
        headers["If-None-Match"] = source.last_etag
    if source.last_fetch_at:
        # Converted rather than passed through: Postgres hands back timestamptz in the
        # session's zone, and an HTTP-date has to be GMT. format_datetime(usegmt=True)
        # rejects anything else outright, which took down the whole pass.
        headers["If-Modified-Since"] = format_datetime(
            source.last_fetch_at.astimezone(UTC), usegmt=True
        )
    return headers


async def _get(
    client: httpx.AsyncClient, throttle: Throttle, url: str, headers: dict[str, str]
) -> httpx.Response:
    await throttle.wait(url)
    return await client.get(url, headers=headers)


async def fetch_source(
    client: httpx.AsyncClient, throttle: Throttle, source: Source
) -> FetchResult:
    """Read one source, reporting failure as data rather than raising."""
    if source.strategy not in {"rss", "static"}:
        return FetchResult(
            error_type="unsupported_strategy",
            error_message=f"no fetcher for strategy {source.strategy!r}",
        )
    if source.strategy == "rss" and not source.rss_url:
        return FetchResult(
            error_type="unsupported_strategy", error_message="strategy is rss but rss_url is null"
        )
    if source.strategy == "static" and not source.selectors.get("card"):
        return FetchResult(
            error_type="unsupported_strategy",
            error_message="strategy is static but selectors.card is missing",
        )

    url = source.address
    try:
        response = await _get(client, throttle, url, _conditional_headers(source))
    except httpx.HTTPError as exc:
        # Collapsed to one line: httpx errors carry a documentation URL on a second line.
        return FetchResult(
            error_type="network",
            error_message=" ".join(f"{type(exc).__name__}: {exc}".split())[:200],
        )

    if response.status_code == 304:
        return FetchResult(not_modified=True)
    if response.status_code >= 400:
        return FetchResult(
            error_type="http_status",
            error_message=f"HTTP {response.status_code}",
            raw_response=response.text[:RAW_RESPONSE_LIMIT],
        )

    if source.strategy == "rss":
        articles = parse_feed(response.content, source.base_url)
    else:
        articles = parse_listing(response.text, source.base_url, source.selectors["card"])

    etag = response.headers.get("etag")
    if not articles:
        # A source that answers 200 with nothing usable is the signature of a parser that
        # broke, which is the standing risk for the CSS-dependent listing. The body is
        # kept because a report that a parser broke is not the evidence needed to fix it.
        return FetchResult(
            etag=etag,
            error_type="empty_result",
            error_message="response parsed but yielded no articles",
            raw_response=response.text[:RAW_RESPONSE_LIMIT],
        )
    return FetchResult(articles=tuple(articles), etag=etag)
