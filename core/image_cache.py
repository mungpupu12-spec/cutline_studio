"""같은 도안 이미지를 반복해서 디스크에서 읽지 않도록 하는 작은 캐시.

2026-09-30(멍푸: "칼선 생성 시간이 3초를 넘으면 안 돼. 왜 느려졌는지 조사"): 실제 격자 파일로
①②③ 흐름을 측정해 보니 ③ 47초 중 약 26초가 cv2.imread(219번)였다 -- 요소 하나를 처리할
때마다 가로 4500px짜리 시트 전체를 여러 번 새로 읽고 있었다. 파일 경로 + 수정 시각 + 크기가
같으면 이미 읽은 배열을 그대로 돌려준다(파일이 바뀌면 다시 읽음).

돌려주는 numpy 배열은 읽기 전용(write=False)이다 -- 여러 곳이 같은 배열을 나눠 쓰므로, 제자리에서
고쳐야 하는 곳은 반드시 .copy()해서 쓴다(실수로 고치려 하면 바로 오류가 나서 알 수 있음).
PIL 이미지는 호출할 때마다 복사본을 준다.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict

import cv2

_MAX_ENTRIES = 6
_lock = threading.Lock()
_cache: "OrderedDict[tuple, tuple]" = OrderedDict()


def _signature(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _get(key, sig):
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == sig:
            _cache.move_to_end(key)
            return hit[1]
    return None


def _put(key, sig, value):
    with _lock:
        _cache[key] = (sig, value)
        _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)


def imread(path, flags=cv2.IMREAD_COLOR):
    """cv2.imread와 같지만 캐시된 읽기 전용 배열을 돌려준다(읽기 실패 시 None)."""
    sig = _signature(path)
    if sig is None:
        return cv2.imread(path, flags)
    key = ("cv2", os.path.abspath(path), int(flags))
    hit = _get(key, sig)
    if hit is not None:
        return hit
    img = cv2.imread(path, flags)
    if img is None:
        return None
    img.setflags(write=False)
    _put(key, sig, img)
    return img


def pil_open(path, mode="RGB"):
    """Image.open(path).convert(mode)와 같은 결과의 복사본(캐시에서)."""
    from PIL import Image

    sig = _signature(path)
    if sig is None:
        with Image.open(path) as im:
            return im.convert(mode)
    key = ("pil", os.path.abspath(path), mode)
    hit = _get(key, sig)
    if hit is None:
        with Image.open(path) as im:
            hit = im.convert(mode)
            hit.load()
        _put(key, sig, hit)
    return hit.copy()


def clear():
    with _lock:
        _cache.clear()


def file_memo(fn):
    """첫 인자가 이미지 경로인 순수 계산 함수의 결과를 (파일 서명 + 나머지 인자)로 기억한다
    (2026-09-30 속도: 같은 칸을 ①·③·보충 단계에서 여러 번 다시 계산하던 것). 돌려주는 값은
    바꾸지 않는(불변) 값이어야 한다."""
    import functools

    memo: dict = {}
    memo_lock = threading.Lock()

    @functools.wraps(fn)
    def wrapper(image_path, *args, **kwargs):
        sig = _signature(image_path)
        try:
            key = (os.path.abspath(image_path), sig, repr(args), repr(sorted(kwargs.items())))
        except Exception:  # noqa: BLE001
            key = None
        if sig is not None and key is not None:
            with memo_lock:
                if key in memo:
                    return memo[key]
        out = fn(image_path, *args, **kwargs)
        if sig is not None and key is not None:
            with memo_lock:
                if len(memo) > 4096:
                    memo.clear()
                memo[key] = out
        return out

    wrapper.uncached = fn
    return wrapper
