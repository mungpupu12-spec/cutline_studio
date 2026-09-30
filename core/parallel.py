"""여러 칸/조각을 동시에 처리하는 작은 도우미(2026-09-30 속도 개선).

OpenCV·numpy·shapely는 계산하는 동안 파이썬 잠금(GIL)을 풀기 때문에, 칸마다 독립적인 계산은
스레드로 나눠 돌리면 CPU 코어 수만큼 빨라진다. 결과는 항상 입력 순서 그대로 돌려준다."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor


def workers() -> int:
    return max(1, min(8, os.cpu_count() or 2))


def pmap(fn, items):
    """[fn(x) for x in items]와 같은 결과(순서 유지). 예외는 그대로 다시 던진다."""
    items = list(items)
    if len(items) <= 1 or workers() == 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers()) as ex:
        return list(ex.map(fn, items))
