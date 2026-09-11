import pytest

from ai_film.camera_variants import DEFAULT_CAMERA_VARIANTS, resolve_camera_variants


def test_original_camera_reserved_as_first_variant():
    result = resolve_camera_variants("medium", count=4, override=None)
    assert result[0] == "medium"
    assert len(result) == 4


def test_original_camera_never_duplicated_in_pool():
    result = resolve_camera_variants("medium", count=4, override=None)
    assert result == ["medium"] + [
        v for v in DEFAULT_CAMERA_VARIANTS if v != "medium"
    ][:3]
    assert result.count("medium") == 1


def test_no_original_camera_takes_pool_from_the_start():
    result = resolve_camera_variants(None, count=4, override=None)
    assert result == DEFAULT_CAMERA_VARIANTS[:4]


def test_override_list_replaces_default_pool():
    result = resolve_camera_variants("medium", count=3, override=["wide", "close-up"])
    assert result == ["medium", "wide", "close-up"]


def test_override_list_is_deduplicated_preserving_order():
    result = resolve_camera_variants(
        None, count=3, override=["wide", "close-up", "wide", "medium"]
    )
    assert result == ["wide", "close-up", "medium"]


def test_override_matching_original_is_not_duplicated():
    result = resolve_camera_variants(
        "medium", count=3, override=["medium", "wide", "close-up"]
    )
    assert result == ["medium", "wide", "close-up"]


def test_cycles_through_distinct_pool_once_exhausted_without_reintroducing_original():
    result = resolve_camera_variants(
        "medium", count=6, override=["wide", "medium", "close-up"]
    )
    assert result == ["medium", "wide", "close-up", "wide", "close-up", "wide"]


def test_cycles_through_pool_without_original():
    result = resolve_camera_variants(None, count=5, override=["wide", "close-up"])
    assert result == ["wide", "close-up", "wide", "close-up", "wide"]


def test_single_count_returns_only_original():
    result = resolve_camera_variants("medium", count=1, override=None)
    assert result == ["medium"]


def test_override_collapsing_entirely_into_original_raises_instead_of_duplicating():
    """An override pool that (after dedup) contains nothing but the shot's own
    original camera.shot must never silently reintroduce that original as a
    'variant' — that's exactly the same-framing-N-times behavior this feature
    replaces. A clear error beats a silent duplicate."""
    with pytest.raises(ValueError, match="no distinct camera variant"):
        resolve_camera_variants("medium", count=3, override=["medium"])


def test_empty_override_list_raises_a_clear_error_not_a_crash():
    with pytest.raises(ValueError, match="no distinct camera variant"):
        resolve_camera_variants(None, count=3, override=[])


def test_override_collapsing_into_original_with_count_one_still_returns_original():
    """count=1 never needs to draw from the (empty) remaining pool at all, so
    this must succeed even though the override fully collapses into original."""
    result = resolve_camera_variants("medium", count=1, override=["medium"])
    assert result == ["medium"]
