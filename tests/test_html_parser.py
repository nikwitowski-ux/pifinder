"""Unit tests for the pure-HTML parser. No I/O. Fixture HTML is inline + minimal."""
from __future__ import annotations

from datetime import datetime, timezone

from pifinder.sources._html_parser import (
    BLOG_PATHS,
    ATTORNEY_PATHS,
    extract_page,
    select_candidate_paths,
)


def test_extracts_attorneys_from_jsonld():
    html = """
    <html><head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "LegalService",
        "name": "Doe Injury Law",
        "employee": [
          {"@type": "Attorney", "name": "Jane A. Doe", "jobTitle": "Founding Partner",
           "url": "/attorneys/jane-doe"},
          {"@type": "Person", "name": "John Q. Smith", "jobTitle": "Associate"}
        ]
      }
      </script>
    </head><body>Personal injury attorneys serving Orange County.</body></html>
    """
    res = extract_page(html, "https://doelaw.example.com/")
    names = {a.name for a in res.attorneys}
    assert names == {"Jane A. Doe", "John Q. Smith"}
    jane = next(a for a in res.attorneys if a.name == "Jane A. Doe")
    assert jane.title == "Founding Partner"
    assert jane.bio_url == "https://doelaw.example.com/attorneys/jane-doe"


def test_heuristic_attorneys_when_no_jsonld():
    html = """
    <html><body>
      <div class="attorney-card">
        <h3><a href="/attorneys/jane-doe">Jane A. Doe</a></h3>
        <p>Founding Partner — 22 years of trial experience.</p>
      </div>
      <div class="attorney-card">
        <h3>John Q. Smith</h3>
        <p>Associate Attorney handling personal injury cases.</p>
      </div>
      <h2>About Us</h2>
      <p>This heading is not a person.</p>
    </body></html>
    """
    res = extract_page(html, "https://example.com/")
    names = {a.name for a in res.attorneys}
    assert names == {"Jane A. Doe", "John Q. Smith"}
    jane = next(a for a in res.attorneys if a.name == "Jane A. Doe")
    assert jane.title == "Founding Partner"
    assert jane.bio_url == "https://example.com/attorneys/jane-doe"


def test_detects_practice_areas_and_pi_terms():
    html = """
    <html><body>
      <h1>Our Practice Areas</h1>
      <ul>
        <li>Car accidents</li>
        <li>Slip and fall</li>
        <li>Medical malpractice</li>
        <li>Wrongful death claims</li>
      </ul>
      <p>Personal injury attorneys in Anaheim.</p>
    </body></html>
    """
    res = extract_page(html, "https://x.example.com/")
    assert "car accident" in res.practice_areas
    assert "slip and fall" in res.practice_areas
    assert "medical malpractice" in res.practice_areas
    assert "wrongful death" in res.practice_areas
    assert res.has_pi_terms is True


def test_no_pi_terms_means_false():
    html = "<html><body><p>Estate planning and probate law in Anaheim.</p></body></html>"
    res = extract_page(html, "https://x.example.com/")
    assert res.has_pi_terms is False
    assert res.practice_areas == []


def test_last_post_date_from_time_tag():
    html = """
    <html><body>
      <article>
        <h2>5 things to know after a crash</h2>
        <time datetime="2026-03-12T09:00:00Z">March 12, 2026</time>
      </article>
      <article>
        <h2>Older post</h2>
        <time datetime="2024-08-01T00:00:00Z">Aug 2024</time>
      </article>
    </body></html>
    """
    res = extract_page(html, "https://x.example.com/blog")
    assert res.last_post_at == datetime(2026, 3, 12, 9, 0, tzinfo=timezone.utc)


def test_last_post_date_from_text_pattern():
    html = """
    <html><body>
      <h1>News</h1>
      <p>Posted on March 12, 2026 — major verdict.</p>
      <p>Earlier coverage on 02/01/2025.</p>
    </body></html>
    """
    res = extract_page(html, "https://x.example.com/news")
    assert res.last_post_at is not None
    assert res.last_post_at.year == 2026
    assert res.last_post_at.month == 3


def test_established_year_detected():
    html = "<html><body><footer>Serving clients since 1987.</footer></body></html>"
    res = extract_page(html, "https://x.example.com/")
    assert res.established_year == 1987

    html2 = '<html><body><p>Est. 2010.</p></body></html>'
    assert extract_page(html2, "https://x.example.com/").established_year == 2010

    html3 = "<html><body><p>Founded in 1965 in Los Angeles.</p></body></html>"
    assert extract_page(html3, "https://x.example.com/").established_year == 1965


def test_established_year_rejects_out_of_range():
    html = "<html><body><p>Since 1850.</p></body></html>"
    assert extract_page(html, "https://x.example.com/").established_year is None


def test_same_origin_link_extraction_and_candidate_selection():
    html = """
    <html><body>
      <a href="/attorneys">Our Attorneys</a>
      <a href="/our-team/jane">Jane Doe</a>
      <a href="https://other.example.com/whatever">External</a>
      <a href="/contact">Contact</a>
      <a href="/blog/latest-verdict">Blog post</a>
    </body></html>
    """
    res = extract_page(html, "https://x.example.com/")
    # External link filtered
    assert "https://other.example.com/whatever" not in res.same_origin_links
    assert "https://x.example.com/attorneys" in res.same_origin_links

    chosen_attorneys = select_candidate_paths(res.same_origin_links, "https://x.example.com/", ATTORNEY_PATHS)
    assert "https://x.example.com/attorneys" in chosen_attorneys
    assert "https://x.example.com/our-team/jane" in chosen_attorneys
    assert "https://x.example.com/contact" not in chosen_attorneys

    chosen_blog = select_candidate_paths(res.same_origin_links, "https://x.example.com/", BLOG_PATHS)
    assert "https://x.example.com/blog/latest-verdict" in chosen_blog


def test_extract_does_not_crash_on_garbage():
    res = extract_page("not really html <<<", "https://x.example.com/")
    assert res.attorneys == []
    assert res.practice_areas == []
