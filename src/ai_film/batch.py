from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable


def run_bounded(tasks: list[Callable[[], None]], max_workers: int) -> list[Exception | None]:
    """Run each zero-arg callable with bounded concurrency.

    Returns per-task exceptions (or None on success), index-aligned with `tasks`.
    A failing task never stops or cancels the others.
    """
    results: list[Exception | None] = [None] * len(tasks)
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        future_to_index = {executor.submit(task): i for i, task in enumerate(tasks)}
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - captured per-task, not re-raised
                results[index] = exc
    return results
