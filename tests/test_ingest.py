"""Tests for the fetch layer.

The two parsers are tested against saved real responses rather than hand-written markup.
A portal changing its layout is the expected failure mode here, and a fixture someone
wrote from memory would keep passing while the live parser returned nothing — which is
precisely the silence this project records in ``fetch_errors`` instead of ignoring.

``canonical_url`` carries the deduplication guarantee: it is what makes one article one
row, so the cases that must collapse to a single key are asserted explicitly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

import pytest

from ingest.fetch import (
    ROBOTS_AGENT,
    RSS_FETCH_LEVEL,
    STATIC_FETCH_LEVEL,
    Source,
    _conditional_headers,
    canonical_url,
    digest,
    entry_lead,
    parse_feed,
    parse_listing,
    robots_allows,
    user_agent,
)

FIXTURES: Final = Path(__file__).parent / "fixtures"
REPUBLIKA_SELECTOR: Final = 'h2[class*="__text"]'
REPUBLIKA_BASE: Final = "https://tvrepublika.pl/"
INTERIA_BASE: Final = "https://wydarzenia.interia.pl/"


@pytest.fixture(scope="module")
def republika_html() -> str:
    return (FIXTURES / "tvrepublika-listing.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def interia_feed() -> bytes:
    return (FIXTURES / "interia-feed.xml").read_bytes()


class TestCanonicalUrl:
    def test_tracking_parameters_collapse_to_one_key(self) -> None:
        # The same article reached from a feed and from a homepage differs only here. If
        # these produced different hashes, every pass would re-insert what it already had.
        from_feed = "https://wydarzenia.interia.pl/news-abc,nId,123"
        from_homepage = "https://wydarzenia.interia.pl/news-abc,nId,123?srcc=ust&utm_source=box"

        assert digest(canonical_url(from_feed)) == digest(canonical_url(from_homepage))

    def test_meaningful_query_parameters_are_kept(self) -> None:
        # Paged and identified resources are different articles, not the same one twice.
        first = canonical_url("https://example.pl/a?id=1")
        second = canonical_url("https://example.pl/a?id=2")

        assert first != second
        assert "id=1" in first

    def test_fragment_is_dropped(self) -> None:
        assert canonical_url("https://example.pl/a#comments") == "https://example.pl/a"


class TestEntryLead:
    def test_summary_repeating_the_headline_is_not_a_lead(self) -> None:
        # Counting it would feed the model the same sentence twice under two labels, and
        # would promote a headline-only portal to the tier that exposes leads.
        title = "Ciężarówka zderzyła się z busem w Małopolsce"

        assert entry_lead({"summary": title}, title) == ""

    def test_headline_plus_a_scrap_is_not_a_lead(self) -> None:
        title = "Ciężarówka zderzyła się z busem"

        assert entry_lead({"summary": f"{title} Droga zablokowana."}, title) == ""

    def test_genuine_lead_survives(self) -> None:
        title = "Ciężarówka zderzyła się z busem"
        lead = (
            "Dwie osoby zostały ranne w zderzeniu samochodu ciężarowego z busem w Łazanach "
            "w Małopolsce. Droga jest całkowicie zablokowana, trwa akcja ratunkowa."
        )

        assert entry_lead({"summary": lead}, title) == lead

    def test_html_is_reduced_to_readable_text(self) -> None:
        lead = "a" * 100

        assert entry_lead({"summary": f"<p>{lead}</p>"}, "Tytuł") == lead


class TestParseFeed:
    def test_reads_the_saved_interia_feed(self, interia_feed: bytes) -> None:
        articles = parse_feed(interia_feed, INTERIA_BASE)

        assert len(articles) > 10
        assert all(article.fetch_level == RSS_FETCH_LEVEL for article in articles)
        assert all(article.url.startswith("https://") for article in articles)
        assert all(article.title for article in articles)

    def test_every_entry_carries_a_lead_and_a_timestamp(self, interia_feed: bytes) -> None:
        # The audit measured 100% lead coverage on this feed. A regression here would
        # silently move the portal to the headline-only input variant.
        articles = parse_feed(interia_feed, INTERIA_BASE)

        assert all(article.lead for article in articles)
        assert all(article.published_at is not None for article in articles)

    def test_urls_are_unique(self, interia_feed: bytes) -> None:
        articles = parse_feed(interia_feed, INTERIA_BASE)

        assert len({article.url for article in articles}) == len(articles)

    def test_a_body_that_is_not_a_feed_yields_nothing(self) -> None:
        # The caller turns an empty result into a fetch_errors row; it must not raise.
        assert parse_feed(b"<html><body>nope</body></html>", INTERIA_BASE) == []


class TestParseListing:
    def test_reads_the_saved_republika_listing(self, republika_html: str) -> None:
        articles = parse_listing(republika_html, REPUBLIKA_BASE, REPUBLIKA_SELECTOR)

        assert len(articles) > 30
        assert all(article.fetch_level == STATIC_FETCH_LEVEL for article in articles)

    def test_urls_are_absolute_and_unique(self, republika_html: str) -> None:
        articles = parse_listing(republika_html, REPUBLIKA_BASE, REPUBLIKA_SELECTOR)
        urls = [article.url for article in articles]

        assert all(url.startswith("https://tvrepublika.pl/") for url in urls)
        assert len(set(urls)) == len(urls)

    def test_category_tag_links_are_not_collected(self, republika_html: str) -> None:
        # The cards contain tag links next to the article link. Matching anchors directly
        # would pick those up; walking up from the headline cannot reach them.
        articles = parse_listing(republika_html, REPUBLIKA_BASE, REPUBLIKA_SELECTOR)

        assert not [article for article in articles if "/tag/" in article.url]

    def test_titles_are_clean_text(self, republika_html: str) -> None:
        # Two known ways this breaks: a time prefix from the card's badge, and adjacent
        # words fused together when a decorative element splits the heading's text nodes.
        articles = parse_listing(republika_html, REPUBLIKA_BASE, REPUBLIKA_SELECTOR)

        for article in articles:
            assert article.title == article.title.strip()
            assert "  " not in article.title
            assert not article.title[:5].strip().rstrip(":").isdigit()

    def test_the_listing_carries_neither_lead_nor_date(self, republika_html: str) -> None:
        # The source exposes no publication time anywhere, so fetched_at is the only
        # approximation. Writing now() into published_at would disguise a guess as a fact.
        articles = parse_listing(republika_html, REPUBLIKA_BASE, REPUBLIKA_SELECTOR)

        assert all(article.lead is None for article in articles)
        assert all(article.published_at is None for article in articles)

    def test_a_stale_selector_yields_nothing_rather_than_raising(self, republika_html: str) -> None:
        # What a Drupal theme change looks like. The pass must record it, not crash.
        assert parse_listing(republika_html, REPUBLIKA_BASE, "h2.gone-after-redesign") == []


class TestUserAgent:
    def test_contact_is_included_when_configured(self) -> None:
        agent = user_agent("crawler@example.org", "ingest")

        assert agent.startswith(f"{ROBOTS_AGENT}/")
        assert "ingest" in agent
        assert "+crawler@example.org" in agent

    def test_placeholder_contact_is_omitted(self) -> None:
        # Publishing the unedited sample address would be worse than publishing none.
        assert "+" not in user_agent("you@example.com", "ingest")

    def test_component_stays_out_of_the_product_token(self) -> None:
        # The token is what robots.txt is matched against, and matching is substring-based.
        # Appending the component put "es" inside "news-aggregator-ingest", which collided
        # with a real Disallow group on Gazeta.pl for a bot of that name.
        token = user_agent("", "ingest").split("/")[0]

        assert token == ROBOTS_AGENT
        assert "es" not in token


class TestConditionalHeaders:
    def test_a_non_utc_timestamp_still_produces_a_gmt_http_date(self) -> None:
        # Postgres returns timestamptz in the session's zone. Handing that straight to
        # format_datetime(usegmt=True) raises, which took down an entire pass the first
        # time a source had ever been fetched before.
        warsaw = timezone(timedelta(hours=2))
        source = Source(
            id=uuid.uuid4(),
            name="Interia",
            base_url="https://wydarzenia.interia.pl/",
            rss_url="https://wydarzenia.interia.pl/feed",
            strategy="rss",
            last_fetch_at=datetime(2026, 8, 18, 11, 30, tzinfo=warsaw),
        )

        headers = _conditional_headers(source)

        assert headers["If-Modified-Since"] == "Tue, 18 Aug 2026 09:30:00 GMT"

    def test_nothing_is_sent_before_the_first_fetch(self) -> None:
        source = Source(
            id=uuid.uuid4(),
            name="Interia",
            base_url="https://wydarzenia.interia.pl/",
            rss_url="https://wydarzenia.interia.pl/feed",
            strategy="rss",
        )

        assert _conditional_headers(source) == {}


class TestRobots:
    # Both rules are taken verbatim from Gazeta.pl's robots.txt, which is where each of
    # these failure modes was actually observed.
    RULES = """
User-agent: *
Disallow: /pub/ips/*
Disallow: /*servlet

User-agent: es
Disallow: /
"""

    def test_wildcard_paths_are_honoured(self) -> None:
        # The standard library's parser ignores * and $ in paths and reports both of these
        # as permitted. A robots check that under-blocks is worse than none, because it is
        # trusted; this is the reason the ingest layer uses protego instead.
        assert not robots_allows(self.RULES, "https://x.pl/pub/ips/anything")
        assert not robots_allows(self.RULES, "https://x.pl/getFile.servlet?a=1")

    def test_permitted_paths_stay_permitted(self) -> None:
        assert robots_allows(self.RULES, "https://x.pl/pub/rss/wiadomosci.htm")

    def test_a_group_for_another_bot_does_not_capture_us(self) -> None:
        # The whole point of the short-token group above: our product token must not be
        # read as the bot named "es".
        assert robots_allows(self.RULES, "https://x.pl/")
