"""요소 칼선 계산을 여러 *프로세스*로 동시에 돌리는 풀(2026-09-30 속도).

스레드(core.parallel)는 파이썬 코드가 한 번에 한 줄씩만 돌아(GIL) 8코어 PC에서도 요소 37개에
3.8초가 걸렸다. 프로세스는 코어마다 진짜로 동시에 계산한다. 프로그램을 켤 때 뒤에서 미리
만들어 두고(warm_up_async), 칼선을 만들 때마다 재사용한다.

작업 프로세스마다 계산 결과(GrabCut 실루엣, 배경 채우기 등)를 기억하므로, 같은 요소는 항상
같은 작업 프로세스로 보낸다(요소 위치로 정함). 그래서 ① 인식 직후 뒤에서 미리 한 번 계산해
두면(prefetch), 사용자가 ③을 누를 때는 대부분 기억해 둔 결과를 꺼내 쓰기만 한다.
풀을 쓸 수 없는 환경이면 스레드로 돌린다(결과는 같고 속도만 다름).
"""
from __future__ import annotations

import os
import sys
import threading

_executors = None
_pool_lock = threading.Lock()
_pool_broken = False


def workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _context():
    import multiprocessing as mp

    if sys.platform.startswith("win"):
        return mp.get_context("spawn")
    try:
        ctx = mp.get_context("forkserver")
        # 기본값은 __main__을 미리 불러오는데, 그러면 실행한 스크립트 전체가 다시 돌 수 있다 --
        # 칼선 계산 모듈만 미리 불러 둔다.
        ctx.set_forkserver_preload(["core.element_jobs"])
        return ctx
    except ValueError:
        return mp.get_context("spawn")


def _warm():
    # 무거운 라이브러리(OpenCV/shapely/skimage 등)를 미리 불러 둔다. 작업 프로세스 여러 개가
    # 동시에 도는데 각자 OpenCV 내부 스레드까지 코어 수만큼 쓰면 서로 자리를 뺏어 오히려
    # 느려진다(멍푸 PC 실측) -- 작업 프로세스 안에서는 OpenCV 스레드를 1개로.
    try:
        import cv2

        cv2.setNumThreads(1)
    except Exception:  # noqa: BLE001
        pass
    from . import element_jobs  # noqa: F401
    return os.getpid()


def get_executors():
    """작업 프로세스 하나씩 가진 실행기 목록(요소마다 늘 같은 프로세스로 보내려고)."""
    global _executors
    if _pool_broken:
        return None
    with _pool_lock:
        if _executors is None:
            try:
                from concurrent.futures import ProcessPoolExecutor

                ctx = _context()
                _executors = [ProcessPoolExecutor(max_workers=1, mp_context=ctx, initializer=_warm)
                              for _ in range(workers())]
            except Exception:  # noqa: BLE001
                return None
        return _executors


def get_pool():  # 예전 이름(진단용)
    ex = get_executors()
    return ex[0] if ex else None


def _route(task, n):
    """같은 요소(같은 선택 박스)는 늘 같은 작업 프로세스로."""
    kind, args = task
    try:
        sel = args[2] if kind == "auto" else args[1]
        key = tuple(int(round(float(v))) for v in sel)
        return hash(key) % n
    except Exception:  # noqa: BLE001
        return 0


def warm_up_async():
    """프로그램 시작 직후 뒤에서 작업 프로세스들을 미리 띄운다(화면은 기다리지 않음)."""
    def _run():
        exs = get_executors()
        if not exs:
            return
        try:
            from . import element_jobs

            futs = [ex.submit(element_jobs.run_task, ("noop", ())) for ex in exs]
            for f in futs:
                f.result(timeout=180)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_run, daemon=True).start()


def prefetch_async(tasks):
    """결과는 버리고 작업 프로세스들이 계산 결과를 미리 기억해 두게만 한다(뒤에서)."""
    tasks = list(tasks)
    if not tasks or workers() <= 1:
        return

    def _run():
        exs = get_executors()
        if not exs:
            return
        try:
            from . import element_jobs

            for t in tasks:
                exs[_route(t, len(exs))].submit(element_jobs.run_task, t)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_run, daemon=True).start()


_thread_pool = None


def submit(task):
    """작업 하나를 뒤에서 계산하도록 맡기고 Future를 돌려준다(작업 프로세스가 없으면 스레드)."""
    global _thread_pool
    from . import element_jobs

    exs = get_executors() if workers() > 1 else None
    if exs:
        try:
            return exs[_route(task, len(exs))].submit(element_jobs.run_task, task)
        except Exception:  # noqa: BLE001
            pass
    with _pool_lock:
        if _thread_pool is None:
            from concurrent.futures import ThreadPoolExecutor
            from .parallel import workers as tworkers

            _thread_pool = ThreadPoolExecutor(max_workers=tworkers())
    return _thread_pool.submit(element_jobs.run_task, task)


def local_task_key(task, radius_px: float = 400.0):
    """작업 결과를 다시 써도 되는지 비교하는 열쇠: 요소 자신·경계·설정과 *가까운(반경 400px)*
    이웃 박스만 본다. 요소 칼선 계산은 이웃 박스를 요소 근처(최대 수십 px)에서만 쓰므로, 먼
    이웃 목록이 달라도 결과가 같다(실제 4개 파일 114개 요소로 결과 완전 동일 확인, 2026-09-30).
    그래서 ③에서 카드 칸을 뺀 경우에도 카드와 먼 요소는 미리 계산한 결과를 그대로 쓴다."""
    from shapely.geometry import box as _box

    kind, args = task
    if kind != "rest":
        return None
    path, sel, bounds, siblings, dpi, margin, precision = args
    zone = _box(*sel).buffer(radius_px)
    near = tuple(sorted(tuple(round(float(v), 3) for v in b) for b in siblings if _box(*b).intersects(zone)))
    return (path, tuple(round(float(v), 3) for v in sel), tuple(round(float(v), 3) for v in bounds),
            near, bool(siblings), float(dpi), float(margin), int(precision))


def run_tasks(tasks, on_progress=None):
    """[(종류, 인자)] -> [(결과, None) 또는 (None, 예외)] (입력 순서 그대로)."""
    global _pool_broken
    from . import element_jobs

    tasks = list(tasks)
    results = [None] * len(tasks)
    exs = get_executors() if len(tasks) > 1 and workers() > 1 else None
    if exs:
        try:
            from concurrent.futures import as_completed

            futs = {exs[_route(t, len(exs))].submit(element_jobs.run_task, t): i for i, t in enumerate(tasks)}
            n = 0
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = (fut.result(), None)
                except Exception as e:  # noqa: BLE001
                    if type(e).__name__ == "BrokenProcessPool":
                        raise
                    results[i] = (None, e)
                n += 1
                if on_progress:
                    on_progress(n)
            return results
        except Exception:  # noqa: BLE001 -- 풀이 깨지면 스레드로(결과는 같음)
            _pool_broken = True
    from .parallel import workers as tworkers
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _safe(t):
        try:
            return element_jobs.run_task(t), None
        except Exception as e:  # noqa: BLE001
            return None, e

    with ThreadPoolExecutor(max_workers=tworkers()) as ex:
        futs = {ex.submit(_safe, t): i for i, t in enumerate(tasks)}
        for n, fut in enumerate(as_completed(futs), start=1):
            results[futs[fut]] = fut.result()
            if on_progress:
                on_progress(n)
    return results


def shutdown():
    global _executors
    with _pool_lock:
        if _executors is not None:
            for ex in _executors:
                try:
                    ex.shutdown(wait=False, cancel_futures=True)
                except Exception:  # noqa: BLE001
                    pass
            _executors = None
