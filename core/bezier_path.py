"""칼선을 일러스트레이터 패스처럼 다루기 위한 베지어 곡선 도구.

2026-10-02(멍푸: "수정 하는 거 너무 어려워, 어도비 패스 기능 같은거로 만들어" -- 점 끌기·삭제와
어도비 핸들까지): 프로그램이 만든 칼선은 수백~수천 개 점으로 된 다각형이라 그대로는 고칠 수
없다. 다각형을 몇십 개 기준점 + 방향 핸들로 된 닫힌 3차 베지어 패스로 바꾸고(Schneider 곡선
맞춤), 고친 패스는 다시 촘촘한 다각형으로 펴서(flatten) 저장·점검·PNG에 그대로 쓴다. SVG로
내보낼 때도 같은 패스를 C 명령으로 써서 일러스트레이터에서 열면 바로 손볼 수 있는 패스가 된다.

패스 = 기준점(Node) 목록(닫힌 패스, 마지막 점 다음은 첫 점). 각 기준점은 위치 p, 들어오는 핸들
h_in, 나가는 핸들 h_out(모두 절대 좌표), 부드러운 점 여부 smooth(부드러운 점은 한쪽 핸들을 움직이면
반대쪽 핸들이 일직선을 유지). 구간 i = p[i] -> h_out[i] -> h_in[i+1] -> p[i+1].
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

Pt = Tuple[float, float]


@dataclass
class Node:
    p: np.ndarray
    h_in: np.ndarray
    h_out: np.ndarray
    smooth: bool = True

    def copy(self) -> "Node":
        return Node(self.p.copy(), self.h_in.copy(), self.h_out.copy(), self.smooth)

    def move(self, dx: float, dy: float) -> None:
        d = np.array([dx, dy], float)
        self.p = self.p + d
        self.h_in = self.h_in + d
        self.h_out = self.h_out + d


@dataclass
class BezierPath:
    nodes: List[Node] = field(default_factory=list)

    def copy(self) -> "BezierPath":
        return BezierPath([n.copy() for n in self.nodes])

    def __len__(self) -> int:
        return len(self.nodes)

    # ---- 구간 -------------------------------------------------------------
    def segment(self, i: int):
        n = len(self.nodes)
        a, b = self.nodes[i % n], self.nodes[(i + 1) % n]
        return a.p, a.h_out, b.h_in, b.p

    def segments(self):
        return [self.segment(i) for i in range(len(self.nodes))]

    # ---- 다각형으로 펴기 ---------------------------------------------------
    def flatten(self, step: float = 1.0) -> List[Pt]:
        """패스를 촘촘한 점 목록으로(닫힘, 첫 점을 끝에 반복하지 않음). step = 대략 점 간격(px)."""
        pts: List[Pt] = []
        for P0, P1, P2, P3 in self.segments():
            approx = (np.linalg.norm(P1 - P0) + np.linalg.norm(P2 - P1) + np.linalg.norm(P3 - P2))
            k = max(2, int(math.ceil(approx / max(0.2, step))))
            t = np.linspace(0.0, 1.0, k, endpoint=False)[:, None]
            seg = ((1 - t) ** 3) * P0 + 3 * ((1 - t) ** 2) * t * P1 + 3 * (1 - t) * (t ** 2) * P2 + (t ** 3) * P3
            pts.extend((float(x), float(y)) for x, y in seg)
        return pts

    def to_polygon(self, step: float = 1.0):
        from shapely.geometry import Polygon

        pts = self.flatten(step)
        if len(pts) < 3:
            return Polygon()
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
            if poly.geom_type == "MultiPolygon":
                poly = max(poly.geoms, key=lambda g: g.area)
        return poly

    # ---- SVG --------------------------------------------------------------
    def svg_d(self, fmt: str = "{:.2f}") -> str:
        if not self.nodes:
            return ""
        f = lambda v: fmt.format(float(v))  # noqa: E731
        p0 = self.nodes[0].p
        out = [f"M {f(p0[0])},{f(p0[1])}"]
        for P0, P1, P2, P3 in self.segments():
            out.append(
                f"C {f(P1[0])},{f(P1[1])} {f(P2[0])},{f(P2[1])} {f(P3[0])},{f(P3[1])}"
            )
        out.append("Z")
        return " ".join(out)

    # ---- 편집 ---------------------------------------------------------------
    def nearest(self, pt: Pt, samples: int = 24):
        """pt에서 가장 가까운 패스 위 지점 -> (구간 번호, t, 거리)."""
        q = np.asarray(pt, float)
        best = (0, 0.0, float("inf"))
        ts = np.linspace(0.0, 1.0, samples + 1)
        for i, (P0, P1, P2, P3) in enumerate(self.segments()):
            tt = ts[:, None]
            seg = ((1 - tt) ** 3) * P0 + 3 * ((1 - tt) ** 2) * tt * P1 + 3 * (1 - tt) * (tt ** 2) * P2 + (tt ** 3) * P3
            d = np.linalg.norm(seg - q, axis=1)
            j = int(np.argmin(d))
            if d[j] < best[2]:
                # 주변을 조금 더 촘촘히
                lo, hi = ts[max(0, j - 1)], ts[min(samples, j + 1)]
                t2 = np.linspace(lo, hi, 21)[:, None]
                seg2 = ((1 - t2) ** 3) * P0 + 3 * ((1 - t2) ** 2) * t2 * P1 + 3 * (1 - t2) * (t2 ** 2) * P2 + (t2 ** 3) * P3
                d2 = np.linalg.norm(seg2 - q, axis=1)
                k = int(np.argmin(d2))
                best = (i, float(t2[k, 0]), float(d2[k]))
        return best

    def insert_at(self, seg_index: int, t: float) -> int:
        """구간 seg_index의 t 지점에 기준점을 넣는다(모양은 그대로, de Casteljau 분할).
        새 기준점의 번호를 돌려준다."""
        n = len(self.nodes)
        a, b = self.nodes[seg_index % n], self.nodes[(seg_index + 1) % n]
        P0, P1, P2, P3 = a.p, a.h_out, b.h_in, b.p
        P01 = P0 + (P1 - P0) * t
        P12 = P1 + (P2 - P1) * t
        P23 = P2 + (P3 - P2) * t
        P012 = P01 + (P12 - P01) * t
        P123 = P12 + (P23 - P12) * t
        M = P012 + (P123 - P012) * t
        a.h_out = P01
        b.h_in = P23
        new = Node(M, P012, P123, True)
        pos = seg_index + 1
        self.nodes.insert(pos, new)
        return pos

    def delete(self, i: int) -> bool:
        """기준점 i를 지운다. 닫힌 패스는 최소 3개 점을 남긴다. 이웃 두 점의 핸들은 지운 점
        양쪽 곡선을 대략 이어 붙이도록 늘린다."""
        n = len(self.nodes)
        if n <= 3:
            return False
        i %= n
        prev, nxt = self.nodes[(i - 1) % n], self.nodes[(i + 1) % n]
        # 지운 점 양쪽 두 구간을 한 구간으로: 이웃 핸들 방향은 유지, 길이는 두 구간 길이에 맞춰
        L1 = _seg_len(*self.segment((i - 1) % n))
        L2 = _seg_len(*self.segment(i))
        total = L1 + L2
        d_out = prev.h_out - prev.p
        d_in = nxt.h_in - nxt.p
        lo, li = np.linalg.norm(d_out), np.linalg.norm(d_in)
        if lo > 1e-9:
            prev.h_out = prev.p + d_out / lo * (total / 3.0)
        if li > 1e-9:
            nxt.h_in = nxt.p + d_in / li * (total / 3.0)
        del self.nodes[i]
        return True

    def move_node(self, i: int, x: float, y: float) -> None:
        nd = self.nodes[i]
        nd.move(x - nd.p[0], y - nd.p[1])

    def move_handle(self, i: int, which: str, x: float, y: float, break_smooth: bool = False) -> None:
        """기준점 i의 핸들('in'/'out')을 (x, y)로. 부드러운 점이면 반대쪽 핸들을 일직선으로
        (길이는 그대로) 맞춘다. break_smooth면 이 점을 뾰족한 점으로 바꿔 한쪽만 움직인다."""
        nd = self.nodes[i]
        q = np.array([x, y], float)
        if break_smooth:
            nd.smooth = False
        if which == "in":
            nd.h_in = q
            other, vec = "out", nd.p - q
        else:
            nd.h_out = q
            other, vec = "in", nd.p - q
        if nd.smooth:
            L = np.linalg.norm((nd.h_out if other == "out" else nd.h_in) - nd.p)
            v = np.linalg.norm(vec)
            if v > 1e-9:
                tgt = nd.p + vec / v * L
                if other == "out":
                    nd.h_out = tgt
                else:
                    nd.h_in = tgt

    def toggle_smooth(self, i: int) -> bool:
        """뾰족한 점 <-> 부드러운 점. 부드럽게 바꾸면 이웃 점 방향으로 핸들을 새로 만든다.
        뾰족하게 바꾸면 핸들을 기준점으로 접는다(직선 모서리). 바뀐 뒤 smooth 값을 돌려준다."""
        n = len(self.nodes)
        nd = self.nodes[i]
        prev, nxt = self.nodes[(i - 1) % n], self.nodes[(i + 1) % n]
        if nd.smooth:
            nd.smooth = False
            nd.h_in = nd.p.copy()
            nd.h_out = nd.p.copy()
        else:
            nd.smooth = True
            tdir = nxt.p - prev.p
            L = np.linalg.norm(tdir)
            if L < 1e-9:
                return nd.smooth
            tdir = tdir / L
            l_in = np.linalg.norm(nd.p - prev.p) / 3.0
            l_out = np.linalg.norm(nxt.p - nd.p) / 3.0
            nd.h_in = nd.p - tdir * l_in
            nd.h_out = nd.p + tdir * l_out
        return nd.smooth

    def translate(self, dx: float, dy: float) -> None:
        for nd in self.nodes:
            nd.move(dx, dy)

    def transformed(self, sx: float, sy: float, tx: float, ty: float) -> "BezierPath":
        """x' = sx*x + tx, y' = sy*y + ty 로 옮긴 복사본(같은 그림 반복 칸에 적용할 때)."""
        out = []
        for nd in self.nodes:
            f = lambda v: np.array([sx * v[0] + tx, sy * v[1] + ty], float)  # noqa: E731
            out.append(Node(f(nd.p), f(nd.h_in), f(nd.h_out), nd.smooth))
        return BezierPath(out)


def _seg_len(P0, P1, P2, P3, k: int = 16) -> float:
    t = np.linspace(0.0, 1.0, k + 1)[:, None]
    seg = ((1 - t) ** 3) * P0 + 3 * ((1 - t) ** 2) * t * P1 + 3 * (1 - t) * (t ** 2) * P2 + (t ** 3) * P3
    return float(np.linalg.norm(np.diff(seg, axis=0), axis=1).sum())


# ---------------------------------------------------------------------------
# 다각형 -> 베지어 패스 (Philip J. Schneider, "An Algorithm for Automatically Fitting Digitized
# Curves", Graphics Gems 1990 -- 널리 쓰이는 공개 알고리즘을 닫힌 패스용으로 구현)
# ---------------------------------------------------------------------------

def _bez(ctrl, t):
    t = np.asarray(t, float)[:, None]
    return ((1 - t) ** 3) * ctrl[0] + 3 * ((1 - t) ** 2) * t * ctrl[1] + 3 * (1 - t) * (t ** 2) * ctrl[2] + (t ** 3) * ctrl[3]


def _bez_d1(ctrl, t):
    t = np.asarray(t, float)[:, None]
    return 3 * ((1 - t) ** 2) * (ctrl[1] - ctrl[0]) + 6 * (1 - t) * t * (ctrl[2] - ctrl[1]) + 3 * (t ** 2) * (ctrl[3] - ctrl[2])


def _bez_d2(ctrl, t):
    t = np.asarray(t, float)[:, None]
    return 6 * (1 - t) * (ctrl[2] - 2 * ctrl[1] + ctrl[0]) + 6 * t * (ctrl[3] - 2 * ctrl[2] + ctrl[1])


def _chord_params(pts):
    d = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    return d / d[-1] if d[-1] > 0 else np.linspace(0, 1, len(pts))


def _generate(pts, u, t1, t2):
    p0, p3 = pts[0], pts[-1]
    A1 = t1[None, :] * (3 * (1 - u) ** 2 * u)[:, None]
    A2 = t2[None, :] * (3 * (1 - u) * u ** 2)[:, None]
    C = np.array([[np.sum(A1 * A1), np.sum(A1 * A2)], [np.sum(A1 * A2), np.sum(A2 * A2)]])
    base = (((1 - u) ** 3 + 3 * (1 - u) ** 2 * u)[:, None] * p0 + (3 * (1 - u) * u ** 2 + u ** 3)[:, None] * p3)
    tmp = pts - base
    X = np.array([np.sum(A1 * tmp), np.sum(A2 * tmp)])
    det = C[0, 0] * C[1, 1] - C[0, 1] * C[1, 0]
    seg_len = float(np.linalg.norm(p3 - p0))
    eps = 1e-6 * seg_len
    if abs(det) > 1e-12:
        a1 = (X[0] * C[1, 1] - X[1] * C[0, 1]) / det
        a2 = (C[0, 0] * X[1] - C[1, 0] * X[0]) / det
    else:
        a1 = a2 = seg_len / 3.0
    if a1 < eps or a2 < eps or a1 > 2.0 * seg_len or a2 > 2.0 * seg_len:
        a1 = a2 = seg_len / 3.0
    return np.array([p0, p0 + t1 * a1, p3 + t2 * a2, p3])


def _reparam(ctrl, pts, u):
    q = _bez(ctrl, u)
    d1 = _bez_d1(ctrl, u)
    d2 = _bez_d2(ctrl, u)
    num = np.sum((q - pts) * d1, axis=1)
    den = np.sum(d1 * d1, axis=1) + np.sum((q - pts) * d2, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        nu = np.where(np.abs(den) > 1e-12, u - num / den, u)
    nu = np.clip(nu, 0.0, 1.0)
    nu[0], nu[-1] = 0.0, 1.0
    return np.maximum.accumulate(nu)


def _max_err(ctrl, pts, u):
    d = np.sum((_bez(ctrl, u) - pts) ** 2, axis=1)
    i = int(np.argmax(d))
    return float(d[i]), i


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def _fit_cubic(pts, t1, t2, err2, depth=0):
    if len(pts) == 2 or depth > 12:
        dist = np.linalg.norm(pts[-1] - pts[0]) / 3.0
        return [np.array([pts[0], pts[0] + t1 * dist, pts[-1] + t2 * dist, pts[-1]])]
    u = _chord_params(pts)
    ctrl = _generate(pts, u, t1, t2)
    e, split = _max_err(ctrl, pts, u)
    if e < err2:
        return [ctrl]
    if e < err2 * 16:
        for _ in range(6):
            u = _reparam(ctrl, pts, u)
            ctrl = _generate(pts, u, t1, t2)
            e, split = _max_err(ctrl, pts, u)
            if e < err2:
                return [ctrl]
    split = min(max(split, 1), len(pts) - 2)
    tc = _unit(pts[split - 1] - pts[split + 1])
    return _fit_cubic(pts[: split + 1], t1, tc, err2, depth + 1) + _fit_cubic(pts[split:], -tc, t2, err2, depth + 1)


def _rdp(pts: np.ndarray, eps: float) -> np.ndarray:
    """Douglas-Peucker 단순화(열린 점열)."""
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        A, B = pts[a], pts[b]
        AB = B - A
        L = np.linalg.norm(AB)
        seg = pts[a + 1:b]
        if L < 1e-12:
            d = np.linalg.norm(seg - A, axis=1)
        else:
            d = np.abs(AB[0] * (seg[:, 1] - A[1]) - AB[1] * (seg[:, 0] - A[0])) / L
        j = int(np.argmax(d))
        if d[j] > eps:
            k = a + 1 + j
            keep[k] = True
            stack.append((a, k))
            stack.append((k, b))
    return pts[keep]


def _resample(ring: np.ndarray, step: float) -> np.ndarray:
    """닫힌 점열(마지막 != 처음)을 둘레를 따라 거의 같은 간격으로 다시 뽑는다."""
    closed = np.vstack([ring, ring[:1]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    total = float(seg.sum())
    if total <= 0:
        return ring
    n = max(8, int(round(total / step)))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    targets = np.linspace(0.0, total, n, endpoint=False)
    x = np.interp(targets, s, closed[:, 0])
    y = np.interp(targets, s, closed[:, 1])
    return np.stack([x, y], axis=1)


def fit_ring(coords: Sequence[Pt], tol: float = 1.2, corner_deg: float = 50.0,
             smooth_px: float = 0.0) -> BezierPath:
    """닫힌 다각형 꼭짓점들 -> 닫힌 베지어 패스. tol = 원래 선에서 벗어나도 되는 최대 거리(px).

    꺾이는 각도가 corner_deg보다 큰 곳은 뾰족한 점, 나머지는 부드러운 점(이어지는 곳 핸들
    일직선). 뾰족한 점이 없으면 둘레를 두 토막으로 나눠 맞추되 이음매 방향을 같게 한다."""
    pts = np.asarray(coords, float)
    if len(pts) >= 2 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 3:
        return BezierPath([])
    # 픽셀 계단을 없애려고 고른 간격(약 tol/2, 최소 0.5px)으로 다시 뽑은 뒤 살짝 평균
    step = max(0.5, 0.5 * tol)
    pts = _resample(pts, step)
    n = len(pts)
    if n < 6:
        nodes = [Node(p.copy(), p.copy(), p.copy(), False) for p in pts]
        return BezierPath(nodes)
    w = max(1, int(round((smooth_px or tol) / step)))
    if w > 0:
        kern = np.ones(2 * w + 1) / (2 * w + 1)
        padded = np.vstack([pts[-w:], pts, pts[:w]])
        pts = np.stack([np.convolve(padded[:, 0], kern, "valid"), np.convolve(padded[:, 1], kern, "valid")], axis=1)
    # 꺾임 판정: 앞뒤로 약 4*tol 떨어진 점 방향 차이
    k = max(2, int(round(4.0 * tol / step)))
    fwd = np.roll(pts, -k, axis=0) - pts
    bwd = pts - np.roll(pts, k, axis=0)
    cosang = np.sum(fwd * bwd, axis=1) / (np.linalg.norm(fwd, axis=1) * np.linalg.norm(bwd, axis=1) + 1e-12)
    ang = np.degrees(np.arccos(np.clip(cosang, -1.0, 1.0)))
    corners = []
    is_c = ang > corner_deg
    # 연속 구간마다 가장 많이 꺾인 점 하나만
    i = 0
    visited = np.zeros(n, bool)
    for i in range(n):
        if is_c[i] and not visited[i]:
            j = i
            run = []
            while is_c[j % n] and not visited[j % n] and len(run) < n:
                visited[j % n] = True
                run.append(j % n)
                j += 1
            corners.append(max(run, key=lambda q: ang[q]))
    corners = sorted(set(corners))
    # 둘레 처음/끝에 걸친 한 모서리가 둘로 잡히는 것 등: 가까운(k칸 안) 모서리는 하나로
    merged = []
    for c in sorted(corners, key=lambda q: -ang[q]):
        if all(min(abs(c - m), n - abs(c - m)) > k for m in merged):
            merged.append(c)
    corners = sorted(merged)
    err2 = tol * tol
    curves = []
    if not corners:
        h = n // 2
        ring = np.vstack([pts, pts[:1]])

        def tan_at(idx):
            return _unit(pts[(idx + 1) % n] - pts[(idx - 1) % n])

        # 네 토막(일러스트레이터 원처럼 기준점 4개 이상 -- 고치기 쉽게), 이음매 방향은 같게
        cuts = [0, n // 4, n // 2, (3 * n) // 4, n]
        for a_i in range(4):
            a, b = cuts[a_i], cuts[a_i + 1]
            ta, tb = tan_at(a % n), tan_at(b % n)
            curves += _fit_cubic(ring[a: b + 1], ta, -tb, err2)
        smooth_flags = None
    else:
        m = len(corners)
        for a_i in range(m):
            a = corners[a_i]
            b = corners[(a_i + 1) % m]
            if b <= a:
                seg = np.vstack([pts[a:], pts[: b + 1]])
            else:
                seg = pts[a: b + 1]
            if len(seg) < 2:
                continue
            kk = min(3, len(seg) - 1)
            t1 = _unit(seg[kk] - seg[0])
            t2 = _unit(seg[-1 - kk] - seg[-1])
            curves += _fit_cubic(seg, t1, t2, err2)
        smooth_flags = set(tuple(np.round(pts[c], 6)) for c in corners)
    nodes: List[Node] = []
    for c in curves:
        nodes.append(Node(c[0].copy(), c[0].copy(), c[1].copy(), True))
    # 들어오는 핸들 = 앞 구간의 세 번째 조절점
    for idx, c in enumerate(curves):
        nodes[(idx + 1) % len(nodes)].h_in = c[2].copy()
    if smooth_flags:
        for nd in nodes:
            if tuple(np.round(nd.p, 6)) in smooth_flags:
                nd.smooth = False
    return BezierPath(nodes)


def fit_polygon(poly, tol: float = 1.2) -> Optional[BezierPath]:
    """shapely Polygon(바깥 테두리) -> BezierPath."""
    if poly is None or poly.is_empty or poly.geom_type != "Polygon":
        return None
    return fit_ring(list(poly.exterior.coords), tol=tol)


# 고친(또는 한 번 맞춘) 패스 기억: 다각형 모양(WKB) -> 패스. 같은 칼선을 다시 고르거나 SVG로
# 내보낼 때 같은 기준점을 그대로 쓴다.
_MEMO: dict = {}


def _key(poly) -> Optional[bytes]:
    try:
        return poly.wkb
    except Exception:  # noqa: BLE001
        return None


def remember(poly, path: BezierPath) -> None:
    k = _key(poly)
    if k is not None:
        if len(_MEMO) > 5000:
            _MEMO.clear()
        _MEMO[k] = path.copy()


def path_for_polygon(poly, tol: float = 1.2) -> Optional[BezierPath]:
    k = _key(poly)
    if k is not None and k in _MEMO:
        return _MEMO[k].copy()
    path = fit_polygon(poly, tol=tol)
    if path is not None and k is not None:
        _MEMO[k] = path.copy()
    return path
