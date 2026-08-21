import threading
import time

from ai_film.batch import run_bounded


def test_run_bounded_executes_all_tasks():
    calls: list[int] = []
    lock = threading.Lock()

    def make_task(n: int):
        def task() -> None:
            with lock:
                calls.append(n)
        return task

    results = run_bounded([make_task(i) for i in range(5)], max_workers=2)
    assert sorted(calls) == [0, 1, 2, 3, 4]
    assert results == [None] * 5


def test_run_bounded_never_exceeds_max_workers():
    current = {"value": 0}
    peak = {"value": 0}
    lock = threading.Lock()

    def task() -> None:
        with lock:
            current["value"] += 1
            peak["value"] = max(peak["value"], current["value"])
        time.sleep(0.05)
        with lock:
            current["value"] -= 1

    run_bounded([task for _ in range(6)], max_workers=2)
    assert peak["value"] <= 2


def test_run_bounded_captures_exceptions_without_stopping_other_tasks():
    def failing() -> None:
        raise ValueError("boom")

    def succeeding() -> None:
        pass

    results = run_bounded([failing, succeeding, failing], max_workers=3)
    assert isinstance(results[0], ValueError)
    assert results[1] is None
    assert isinstance(results[2], ValueError)
