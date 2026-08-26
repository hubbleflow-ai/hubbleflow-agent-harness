"""Reading an OKF bundle, forgivingly, the way the spec requires of a consumer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hubbleflow import knowledge


def _page(root, path, frontmatter: str = "", body: str = "body text"):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\n{frontmatter}\n---\n\n{body}\n" if frontmatter else body)
    return target


@pytest.fixture
def bundle(tmp_path):
    _page(tmp_path, "orders.md", 'type: BigQuery Table\ntitle: Orders\n'
          'description: One row per completed order.\ntags: [sales, revenue]')
    _page(tmp_path, "runbooks/restart.md", 'type: Runbook\ntitle: Restart the ingester\n'
          'status: deprecated\ntags: [ops]')
    _page(tmp_path, "runbooks/backfill.md", 'type: Runbook\ntitle: Backfill\n'
          'sources:\n  - resource: orders.md\n    title: Orders')
    _page(tmp_path, "index.md", body="# not a concept")
    _page(tmp_path, "log.md", body="## 2026-08-01\nchanged things")
    return knowledge.load(tmp_path)


def test_reserved_filenames_are_not_concepts(bundle):
    paths = {c.path for c in bundle.concepts}
    assert "index.md" not in paths and "log.md" not in paths
    assert len(bundle.concepts) == 3


def test_frontmatter_is_read(bundle):
    orders = next(c for c in bundle.concepts if c.path == "orders.md")
    assert orders.type == "BigQuery Table"
    assert orders.title == "Orders"
    assert orders.tags == ("sales", "revenue")


def test_nested_directories_are_walked(bundle):
    assert any(c.path == "runbooks/backfill.md" for c in bundle.concepts)


def test_sources_naming_a_page_become_edges(bundle):
    assert ("runbooks/backfill.md", "orders.md") in bundle.edges()


def test_a_source_that_is_not_a_page_is_not_an_edge(tmp_path):
    _page(tmp_path, "a.md", 'type: Doc\nsources:\n  - resource: https://example.com/x')
    assert knowledge.load(tmp_path).edges() == []


def test_find_narrows_by_type_and_tag(bundle):
    assert len(bundle.find(type="Runbook")) == 2
    assert len(bundle.find(tag="ops")) == 1
    assert len(bundle.find("orders")) == 1


def test_find_does_not_match_on_provenance(bundle):
    """`sources` records where a page came from; matching text against it would
    surface pages that merely cite the thing you searched for."""
    assert [c.path for c in bundle.find("orders")] == ["orders.md"]


def test_types_and_tags_are_counted(bundle):
    assert bundle.types()["Runbook"] == 2
    assert bundle.tags()["sales"] == 1


# --------------------------------------------------------------------------
# section 11: a consumer must be forgiving
# --------------------------------------------------------------------------

def test_only_type_is_required(tmp_path):
    _page(tmp_path, "bare.md", "type: Anything")
    concept = knowledge.load(tmp_path).concepts[0]
    assert concept.type == "Anything"
    assert concept.name == "bare"  # falls back to the filename


def test_an_unknown_type_is_not_rejected(tmp_path):
    _page(tmp_path, "odd.md", "type: Something Nobody Registered")
    assert len(knowledge.load(tmp_path).concepts) == 1


def test_unknown_frontmatter_keys_are_ignored_not_fatal(tmp_path):
    _page(tmp_path, "x.md", "type: Doc\nproducer_specific_field: {a: [1, 2]}")
    assert knowledge.load(tmp_path).concepts[0].type == "Doc"


def test_broken_yaml_keeps_the_page(tmp_path):
    _page(tmp_path, "broken.md", "type: [unclosed\n  bad: : :")
    concepts = knowledge.load(tmp_path).concepts
    assert len(concepts) == 1 and concepts[0].path == "broken.md"


def test_a_page_with_no_frontmatter_at_all_is_kept(tmp_path):
    _page(tmp_path, "plain.md", body="just prose")
    assert knowledge.load(tmp_path).concepts[0].path == "plain.md"


def test_a_missing_index_is_fine(tmp_path):
    _page(tmp_path, "a.md", "type: Doc")
    assert len(knowledge.load(tmp_path).concepts) == 1


def test_a_missing_bundle_loads_empty_rather_than_raising(tmp_path):
    assert not knowledge.load(tmp_path / "nope")
    assert not knowledge.load(None)


def test_an_unreadable_page_is_reported_not_dropped_silently(tmp_path):
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe\x00\x00")
    _page(tmp_path, "good.md", "type: Doc")
    loaded = knowledge.load(tmp_path)
    assert [c.path for c in loaded.concepts] == ["good.md"]
    assert loaded.unreadable == ["bad.md"]


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------

def test_a_page_past_stale_after_is_stale(tmp_path):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _page(tmp_path, "old.md", f"type: Doc\nstale_after: {past}")
    concept = knowledge.load(tmp_path).concepts[0]
    assert concept.stale() and "stale since" in concept.suspect()


def test_a_page_before_stale_after_is_not(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    _page(tmp_path, "fresh.md", f"type: Doc\nstale_after: {future}")
    assert not knowledge.load(tmp_path).concepts[0].stale()


def test_a_page_with_no_expiry_never_goes_stale(tmp_path):
    _page(tmp_path, "x.md", "type: Doc")
    assert not knowledge.load(tmp_path).concepts[0].stale()


def test_an_unparseable_expiry_is_ignored_rather_than_treated_as_expired(tmp_path):
    _page(tmp_path, "x.md", "type: Doc\nstale_after: sometime next year")
    assert not knowledge.load(tmp_path).concepts[0].stale()


@pytest.mark.parametrize(("status", "expected"), [
    ("draft", "draft"), ("deprecated", "deprecated"), ("stable", ""), ("nonsense", ""),
])
def test_status_is_surfaced_and_unknown_values_fall_back(tmp_path, status, expected):
    _page(tmp_path, "x.md", f"type: Doc\nstatus: {status}")
    assert knowledge.load(tmp_path).concepts[0].suspect() == expected


def test_a_bare_verified_mapping_is_accepted(tmp_path):
    """Section 11: a consumer must treat it as a one-element list."""
    _page(tmp_path, "x.md", "type: Doc\nverified: {by: 'openwiki/0.2', at: 2026-08-01T00:00:00Z}")
    assert len(knowledge.load(tmp_path).concepts) == 1
