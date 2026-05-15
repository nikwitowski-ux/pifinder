"""Unit tests for the pure-HTML parser. No I/O. Fixture HTML is inline + minimal."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

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


# ---- false-positive defenses for the heuristic ----

NON_NAME_HEADINGS = [
    "Who We Are",
    "About Our Firm",
    "About The Firm",
    "Practice Areas",
    "Case Results",
    "Free Case Evaluation",
    "Schedule A Consultation",
    "Call Us Today",
    "Get Started Today",
    "Why Choose Us",
    "What We Do",
    "How We Help",
    "Our Practice Areas",
    "Contact Us Today",
    "Personal Injury Lawyers",
    "Car Accident Lawyers",
    "Free Consultation",
]


@pytest.mark.parametrize("heading", NON_NAME_HEADINGS)
def test_non_name_headings_not_classified_as_attorneys(heading):
    """No 'attorney' should be extracted from a page whose only headings are
    marketing copy. These are common patterns on real PI firm sites."""
    html = f"""
    <html><body>
      <section>
        <h2>{heading}</h2>
        <p>We help injured clients recover what they deserve.</p>
      </section>
    </body></html>
    """
    res = extract_page(html, "https://x.example.com/")
    names = [a.name for a in res.attorneys]
    assert heading not in names, f"heading {heading!r} was wrongly classified as an attorney"
    assert names == [], f"unexpected attorneys: {names}"


def test_promotional_three_token_headings_without_title_are_rejected():
    """Real-world TORKLAW leak: '3-token title-cased' isn't enough.
    Without a title hint sibling, candidates should be rejected."""
    html = """
    <html><body>
      <div><h3>Christmas Gift Giveaway</h3><p>Win a free consultation!</p></div>
      <div><h3>Holiday Charity Drive</h3><p>Supporting local families.</p></div>
    </body></html>
    """
    res = extract_page(html, "https://x.example.com/")
    assert res.attorneys == [], f"unexpected: {[a.name for a in res.attorneys]}"


def test_uppercase_brand_tokens_in_heading_are_rejected():
    """Real-world TORKLAW leak: 'TORKLAW Action Center'. Tokens >2 chars
    in ALL CAPS are firm/brand markers, not human names."""
    html = """
    <html><body>
      <div><h3>TORKLAW Action Center</h3><p>Submit your case online.</p></div>
      <div><h3>FOO Holdings Group</h3><p>Sister company.</p></div>
    </body></html>
    """
    res = extract_page(html, "https://x.example.com/")
    assert res.attorneys == []


def test_three_token_real_name_with_title_still_passes():
    """We've tightened the rule. Real names with role labels still work."""
    html = """
    <html><body>
      <div class="card">
        <h3>Jane A. Doe</h3>
        <p>Founding Partner</p>
      </div>
    </body></html>
    """
    res = extract_page(html, "https://x.example.com/")
    names = [a.name for a in res.attorneys]
    assert names == ["Jane A. Doe"]


def test_marketing_heading_alongside_real_attorney_card():
    """The page has both — only the real card should yield an attorney."""
    html = """
    <html><body>
      <header><h1>Personal Injury Lawyers</h1></header>
      <section>
        <h2>Who We Are</h2>
        <p>A team of attorneys serving Orange County since 2008.</p>
      </section>
      <section class="attorneys">
        <div class="card">
          <h3>Jane A. Doe</h3>
          <p>Founding Partner</p>
        </div>
      </section>
      <h2>Practice Areas</h2>
      <ul><li>Car accidents</li><li>Slip and fall</li></ul>
    </body></html>
    """
    res = extract_page(html, "https://x.example.com/")
    names = [a.name for a in res.attorneys]
    assert names == ["Jane A. Doe"], f"got {names}"
