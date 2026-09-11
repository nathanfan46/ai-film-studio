from __future__ import annotations

DEFAULT_CAMERA_VARIANTS = [
    "wide",
    "medium",
    "close-up",
    "extreme-close-up",
    "over-the-shoulder",
    "low-angle",
    "high-angle",
]


def resolve_camera_variants(
    original: str | None, count: int, override: list[str] | None,
) -> list[str]:
    """`count` camera.shot labels for camera-variant candidate generation.
    `original` (the shot's already-authored camera.shot, if any) is always
    first — Storyboard's own judgment stays in the running instead of being
    discarded. The remaining slots draw from `override` (deduplicated,
    order preserved) or DEFAULT_CAMERA_VARIANTS, excluding `original` so it
    is never offered twice, cycling through that distinct pool if `count`
    exceeds it."""
    pool = override if override is not None else DEFAULT_CAMERA_VARIANTS
    seen: set[str] = set()
    distinct_pool = []
    for label in pool:
        if label not in seen:
            seen.add(label)
            distinct_pool.append(label)

    variants = [original] if original else []
    remaining_pool = [label for label in distinct_pool if label != original]

    if len(variants) < count and not remaining_pool:
        raise ValueError(
            "no distinct camera variants available beyond the shot's original "
            f"camera.shot (original={original!r}, override={override!r}) — "
            "widen the --cameras override or drop --cameras to use the default pool"
        )

    i = 0
    while len(variants) < count:
        variants.append(remaining_pool[i % len(remaining_pool)])
        i += 1

    return variants[:count]
