"""
Read the REAL, already-drawn cutline shapes (and the flattened print image
they sit on top of) out of a finished .ai file (PDF-compatible) -- the
reverse direction of core.ai_layer_export, which only ever WRITES a new
layer. This is what lets core.margin_inspector answer "how many mm of
margin does THIS vendor's own sample file actually use?" from a real
example file instead of a number someone remembers or guesses.

Only ever point this at a COPY of a file, same as ai_layer_export -- this
module only reads, but callers are still responsible for never treating
the artist's original client files as something to publish or share
outside her own machine/conversation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from shapely.geometry import Polygon
from shapely.ops import unary_union


def _force_layer_visible(doc, layer_name: str) -> None:
    """2026-09-08(9차) 실제 라쿤 패턴 시트 파일로 확인된 버그: 그 파일은
    '칼선레이어'가 기본적으로 꺼져 있었는데
    (제출 전에 작가가 미리보기용으로 꺼 두는 게 실제로 흔한 상태로 보임),
    PyMuPDF의 get_drawings()는 꺼져 있는(OCG off) 레이어의 도형을 그냥
    빈 목록으로 돌려준다 -- load_real_cutlines가 "해당 레이어에 도형이
    없다"고 오판해서 실제로는 있는 85개의 진짜 칼선을 통째로 놓치는
    상황이 실제 파일로 확인됨.

    이 함수는 읽기 전용으로 연 `doc`에서 `layer_name`을 찾아 강제로 켠다
    (action=0). 저장하지 않고 doc.close()로 버려지는 임시 상태 변경이라,
    원본 파일이나 그 안의 레이어 on/off 상태에는 전혀 영향이 없다. 이름이
    안 보이면(레이어가 정말 없는 경우) 조용히 아무것도 하지 않고 넘어가서
    -- 호출부의 "그 레이어에 도형이 없다"는 기존 에러 메시지가 그대로
    나오게 둔다."""
    try:
        for cfg in doc.layer_ui_configs():
            if cfg.get("text") == layer_name:
                doc.set_layer_ui_config(cfg["number"], 0)
                return
    except Exception:  # noqa: BLE001
        pass


def _flatten_bezier(p0, p1, p2, p3, n=24):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = (mt**3) * p0[0] + 3 * (mt**2) * t * p1[0] + 3 * mt * (t**2) * p2[0] + (t**3) * p3[0]
        y = (mt**3) * p0[1] + 3 * (mt**2) * t * p1[1] + 3 * mt * (t**2) * p2[1] + (t**3) * p3[1]
        pts.append((x, y))
    return pts


def _items_to_rings(items):
    """One drawing's `items` list -> list of closed point-rings. PyMuPDF's
    get_drawings() doesn't emit explicit moveto ops, so a new ring starts
    whenever the current point doesn't connect to the previous segment's
    end (or on an explicit 're' -- a rectangle is always its own ring)."""
    rings = []
    current = []
    last_end = None

    def start_new(p0):
        nonlocal current
        if len(current) >= 3:
            rings.append(current)
        current = [p0]

    for item in items:
        op = item[0]
        if op == "re":
            rect = item[1]
            if len(current) >= 3:
                rings.append(current)
            rings.append(
                [
                    (rect.x0, rect.y0),
                    (rect.x1, rect.y0),
                    (rect.x1, rect.y1),
                    (rect.x0, rect.y1),
                    (rect.x0, rect.y0),
                ]
            )
            current = []
            last_end = None
            continue
        elif op == "l":
            p0, p1 = item[1], item[2]
        elif op == "c":
            p0, p1, p2, p3 = item[1], item[2], item[3], item[4]
        elif op == "qu":
            quad = item[1]
            if len(current) >= 3:
                rings.append(current)
            rings.append(list(quad) + [quad[0]])
            current = []
            last_end = None
            continue
        else:
            continue

        p0t = (p0.x, p0.y) if hasattr(p0, "x") else tuple(p0)
        if last_end is None or (abs(p0t[0] - last_end[0]) > 1e-3 or abs(p0t[1] - last_end[1]) > 1e-3):
            start_new(p0t)

        if op == "l":
            p1t = (p1.x, p1.y) if hasattr(p1, "x") else tuple(p1)
            current.append(p1t)
            last_end = p1t
        else:
            p1t = (p1.x, p1.y) if hasattr(p1, "x") else tuple(p1)
            p2t = (p2.x, p2.y) if hasattr(p2, "x") else tuple(p2)
            p3t = (p3.x, p3.y) if hasattr(p3, "x") else tuple(p3)
            pts = _flatten_bezier(p0t, p1t, p2t, p3t)
            current.extend(pts[1:])
            last_end = p3t

    if len(current) >= 3:
        rings.append(current)
    return rings


def _drawing_to_polygon(drawing):
    rings = _items_to_rings(drawing["items"])
    polys = []
    for ring in rings:
        if len(ring) < 3:
            continue
        try:
            p = Polygon(ring)
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty and p.area > 0.01:
                polys.append(p)
        except Exception:
            continue
    if not polys:
        return None
    if len(polys) == 1:
        return polys[0]
    try:
        return unary_union(polys)
    except Exception:
        return polys[0]


@dataclass
class RealCutlineFile:
    """Everything needed to compare a real file's own cutlines against its
    own artwork: the embedded print image (saved to disk once, reusable
    for GrabCut/margin measurement) and each real cutline shape, in that
    SAME image's pixel space."""

    print_image_path: str
    image_w_px: int
    image_h_px: int
    image_placement_rect_pt: tuple  # (x0,y0,x1,y1), page points
    dpi: float  # effective px-per-inch this image was placed at
    cutlines_px: list  # list of shapely Polygon/MultiPolygon, in image px space


@dataclass
class RealGridFile:
    """`load_real_grid_cells`의 결과 -- 실제 인쇄 이미지 + 그 안에 실제로
    그려진 '재단선(격자)'을 셀 사각형 목록으로 재구성한 것, 전부 그 이미지
    자신의 픽셀 공간 기준."""

    print_image_path: str
    image_w_px: int
    image_h_px: int
    dpi: float
    cells_px: list  # [(x0,y0,x1,y1), ...] in image px space


def _classify_grid_lines(drawings, tol_pt: float = 1.0):
    """`drawings`(한 레이어의 get_drawings() 결과)를 세로선/가로선으로 분류.
    2026-09-07 피드백("모든 스티커는 인쇄 여백을 아끼려고 서로 거의 맞닿을
    만큼 촘촘하게 배치해, 격자는 모든 스티커 발주 도안에 있어... 재단선
    이라고 해")에서 확인된 실제 파일 구조: '개별재단 레이어'는 실루엣을
    감싸는 닫힌 도형이 아니라, 시트 전체를 여러 칸으로 나누는 순수한 직선
    (폭 또는 높이가 거의 0인 사각형)들의 모음이다. 각 선을 (위치, 시작,
    끝) 튜플로 돌려준다 -- 선이 실제로는 시트 전체 폭/높이를 다 가로지르지
    않고 일부 구간만 가로지르는 경우(아래 _reconstruct_grid_cells 참고)도
    있으므로 그 시작/끝 좌표까지 함께 보존."""
    v_lines, h_lines = [], []
    for d in drawings:
        r = d.get("rect")
        if r is None:
            continue
        w, h = r.x1 - r.x0, r.y1 - r.y0
        if w <= tol_pt and h > tol_pt:
            v_lines.append((r.x0, r.y0, r.y1))
        elif h <= tol_pt and w > tol_pt:
            h_lines.append((r.y0, r.x0, r.x1))
    return v_lines, h_lines


def _reconstruct_grid_cells(v_lines, h_lines, bounds, tol_pt: float = 3.0):
    """실제로 그려진 세로선/가로선 목록으로부터, 그 선들이 나누는 실제
    사각형 칸(셀)들을 재구성한다("길로틴 컷" 방식 -- 인쇄/제본 업계에서
    큰 시트를 여러 번의 직선 절단으로 나누는 것과 같은 방식).

    2026-09-07 실제 사용자 파일(눈송이 시트)로 검증된 이유: 이 파일의
    실제 재단선은 균등한 격자가 아니라, 왼쪽 2칸은 가로선 3개로 4행으로
    나뉘고 오른쪽 1칸은 별도로 가로선 1개로 2행으로만 나뉘는 등 "구역마다
    다른 나누기"가 섞여 있다 -- 그래서 단순히 "가로선 N개 x 세로선 M개 =
    (N+1)x(M+1)칸" 격자 공식으로는 못 풀고, 매 단계마다 "지금 보고 있는
    구역을 실제로 끝까지 가로지르는 선"을 찾아 그 자리에서 둘로 쪼갠 뒤
    각 조각에서 다시 반복해야 한다(재귀). 이렇게 하면 왼쪽/오른쪽처럼
    서로 다르게 나뉜 구역도 정확히 재구성된다 -- 실제 파일(10개 칸)로
    검증 완료."""
    x0, y0, x1, y1 = bounds
    for (vx, vy0, vy1) in v_lines:
        if x0 + tol_pt < vx < x1 - tol_pt and vy0 <= y0 + tol_pt and vy1 >= y1 - tol_pt:
            left = (x0, y0, vx, y1)
            right = (vx, y0, x1, y1)
            return (
                _reconstruct_grid_cells(v_lines, h_lines, left, tol_pt)
                + _reconstruct_grid_cells(v_lines, h_lines, right, tol_pt)
            )
    for (hy, hx0, hx1) in h_lines:
        if y0 + tol_pt < hy < y1 - tol_pt and hx0 <= x0 + tol_pt and hx1 >= x1 - tol_pt:
            top = (x0, y0, x1, hy)
            bottom = (x0, hy, x1, y1)
            return (
                _reconstruct_grid_cells(v_lines, h_lines, top, tol_pt)
                + _reconstruct_grid_cells(v_lines, h_lines, bottom, tol_pt)
            )
    return [bounds]


def _extract_closed_rect_cells(drawings, page_bounds, tol_pt: float = 3.0):
    """2026-09-08(9차) 발견: '개별재단 레이어'가 항상 시트 전체를 가로/세로로
    가로지르는 순수한 선(_classify_grid_lines가 다루는 형태)으로만 그려져
    있는 게 아니다 -- 실제 파일 2개(작가의 실제 작업 파일)를 직접 열어
    확인한 결과, 그 레이어는 대신 각 칸을 처음부터
    "닫힌 사각형 하나"로(칸마다 따로) 그려 놓았다(선이 아니라 실제 폭/높이가
    있는 사각형 12개, 8개 등). 이 형태에서는 v_lines/h_lines가 둘 다
    비어서 _reconstruct_grid_cells가 아무것도 못 찾거나("selection 전체가
    시트 전체를 가로지르는 선"이 하나도 없으므로), 반대로 레이어 안에
    무관한 다른 얇은 선(예: 범례/눈금 표시)이 섞여 있으면 그 무관한 선
    기준으로 엉뚱하게 "칸 1개 = 시트 전체"로 잘못 재구성되기도 한다(실제
    실제 작업 파일로 확인: 그 파일 안의 진짜 칼선은 12칸 x 칸마다
    2~3개씩 총 38개인데, 기존 로직은 이걸 못 보고 "칸 1개"로 뭉쳐버렸다).

    그래서 이 함수는 레이어 안의 도형들 중 "선이 아니라 실제로 폭과 높이가
    있는 사각형"(width/height 모두 tol_pt보다 큼)만 모아서, 페이지 전체를
    거의 다 덮는 사각형(전체 면적의 90% 이상 -- 배경 프레임/외곽선으로
    추정)은 제외하고, 나머지가 서로 다른 사각형 2개 이상이면 그 자체를
    각 칸으로 쓴다(부동소수점 반올림 오차만 다른 거의 동일한 사각형은
    하나로 합침). 이런 사각형이 2개 미만이면 None을 돌려줘서, 호출부가
    기존 선-기반 재구성으로 넘어가게 한다."""
    px0, py0, px1, py1 = page_bounds
    page_area = max(1e-6, (px1 - px0) * (py1 - py0))

    rects = []
    for d in drawings:
        r = d.get("rect")
        if r is None:
            continue
        w, h = r.x1 - r.x0, r.y1 - r.y0
        if w <= tol_pt or h <= tol_pt:
            continue  # 선(_classify_grid_lines가 이미 다룸), 여기선 제외
        if (w * h) / page_area >= 0.9:
            continue  # 배경 프레임/외곽선으로 보이는 전체 크기 사각형은 제외
        rects.append((r.x0, r.y0, r.x1, r.y1))

    dedup = []
    for r in rects:
        if not any(
            abs(r[0] - u[0]) < tol_pt and abs(r[1] - u[1]) < tol_pt
            and abs(r[2] - u[2]) < tol_pt and abs(r[3] - u[3]) < tol_pt
            for u in dedup
        ):
            dedup.append(r)

    if len(dedup) < 2:
        return None
    # 왼쪽 위 -> 오른쪽 아래로 훑는 순서(같은 열이면 위->아래) -- 기존
    # 선-기반 재구성이 왼쪽/오른쪽으로 먼저 나눈 뒤 위/아래로 나누는 순서와
    # 같은 방향이라, 두 형태가 섞여 있어도 "N번째 칸"의 의미가 최대한
    # 일관되게 유지된다.
    return sorted(dedup, key=lambda r: (r[0], r[1]))


def load_real_grid_cells(
    ai_path: str,
    print_image_out_path: str,
    layer_name: str = "개별재단 레이어",
    page_index: int = 0,
    tol_pt: float = 3.0,
) -> RealGridFile:
    """
    2026-09-07 피드백("모든 스티커는 인쇄 여백을 아끼려고 서로 거의
    맞닿을 만큼 촘촘하게 배치해, 격자는 모든 스티커 발주 도안에 있어...
    재단선이라고 해", "도안을 하나의 덩어리로 보지 말고 사각형의 틀에
    스티커 요소가 그려져 있다고 기본 값을 지정해"): 이런 '반복 패턴/여러
    칸' 시트는 실루엣을 추적하는 대신, 이미 작가 본인이 `layer_name`
    레이어에 직접 그려 놓은 진짜 재단 격자선을 그대로 읽어서 각 칸을
    사각형 그대로 재단선으로 쓰는 게 정답이다(픽셀에서 추측하는 것보다
    훨씬 정확하고 안전함).

    `load_real_cutlines`(닫힌 실루엣 도형 전용)의 자매 함수 -- 이쪽은
    "선(width/height가 거의 0인 사각형)"만 다룬다. 해당 레이어에 선이
    하나도 없으면(그 레이어가 실제로는 닫힌 도형 전용이라면) ValueError.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(ai_path)
    try:
        _force_layer_visible(doc, layer_name)
        page = doc[page_index]

        images = page.get_images(full=True)
        if not images:
            raise ValueError(f"{ai_path}: no embedded raster image found on page {page_index}.")

        from PIL import Image as PILImage

        if len(images) == 1:
            xref = images[0][0]
            info = doc.extract_image(xref)
            with open(print_image_out_path, "wb") as f:
                f.write(info["image"])
            rect = page.get_image_rects(xref)[0]
            rx0, ry0, rx1, ry1 = rect.x0, rect.y0, rect.x1, rect.y1
            with PILImage.open(print_image_out_path) as im:
                iw, ih = im.size
            sx = iw / (rx1 - rx0)
            sy = ih / (ry1 - ry0)
            dpi = 72.0 * sx
        else:
            # 2026-09-15(실제 파일로 발견 -- 같은 소스 이미지가 한 페이지
            # 안에 여러 번(실측 8번) 배치된 경우):
            # images[0]의 배치 사각형 하나만 "페이지 전체 기준"으로 삼으면
            # 나머지 배치들이 전부 빠져서 격자 칸 재구성이 깨진다
            # (load_real_cutlines의 다중 배치 처리와 동일한 이유 --
            # 그 함수 문서 참고). 페이지 전체를 하나의 픽스맵으로 렌더링해
            # "페이지 전체 = 이미지 전체"로 맞춘다.
            xref0 = images[0][0]
            info0 = doc.extract_image(xref0)
            rect0 = page.get_image_rects(xref0)[0]
            with PILImage.open(__import__("io").BytesIO(info0["image"])) as im0:
                iw0, ih0 = im0.size
            sx0 = iw0 / (rect0.x1 - rect0.x0)
            dpi = 72.0 * sx0
            zoom = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
            pix.save(print_image_out_path)
            with PILImage.open(print_image_out_path) as im:
                iw, ih = im.size
            sx = sy = zoom
            rx0, ry0, rx1, ry1 = page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1

        drawings = [d for d in page.get_drawings() if d.get("layer") == layer_name]
        if not drawings:
            raise ValueError(f"{ai_path}: no shapes found on layer '{layer_name}'.")

        # 2026-09-08(9차): 칸을 "시트 전체를 가로지르는 선"이 아니라 칸마다
        # 이미 닫힌 사각형 하나로 그려 놓은 형태(_extract_closed_rect_cells
        # 참고)를 먼저 확인한다 -- 실제 파일로 확인된, 최소 2개 파일에서
        # 쓰인 진짜 형식이라 우선한다. 이 형태가 아니면(None) 기존
        # 선-기반(_classify_grid_lines + _reconstruct_grid_cells) 재구성으로
        # 넘어간다.
        cells_pt = _extract_closed_rect_cells(drawings, (rx0, ry0, rx1, ry1), tol_pt)
        if cells_pt is None:
            v_lines, h_lines = _classify_grid_lines(drawings)
            if not v_lines and not h_lines:
                raise ValueError(
                    f"{ai_path}: layer '{layer_name}' has shapes but none look like plain "
                    f"grid lines or per-cell rectangles -- this layer may hold closed "
                    f"silhouette cutlines instead; try load_real_cutlines()."
                )
            cells_pt = _reconstruct_grid_cells(v_lines, h_lines, (rx0, ry0, rx1, ry1), tol_pt)

        cells_px = []
        for (cx0, cy0, cx1, cy1) in cells_pt:
            px0, py0 = (cx0 - rx0) * sx, (cy0 - ry0) * sy
            px1, py1 = (cx1 - rx0) * sx, (cy1 - ry0) * sy
            cells_px.append((
                max(0, int(round(px0))), max(0, int(round(py0))),
                min(iw, int(round(px1))), min(ih, int(round(py1))),
            ))

        return RealGridFile(
            print_image_path=print_image_out_path,
            image_w_px=iw,
            image_h_px=ih,
            dpi=dpi,
            cells_px=cells_px,
        )
    finally:
        doc.close()


def load_real_cutlines(ai_path: str, print_image_out_path: str, layer_name: str = "칼선레이어", page_index: int = 0) -> RealCutlineFile:
    """
    Opens `ai_path` (read-only), extracts the single embedded flattened
    print-layer raster (saved to `print_image_out_path` so downstream
    GrabCut-based tools can work with a plain image file) and every real
    cutline shape found on `layer_name`, mapped into that SAME image's own
    pixel coordinate space (origin top-left, y down -- matching
    core.cutline_core/core.segmentation's convention).

    Raises ValueError if the file has no embedded image or no shapes on
    `layer_name` -- both are expected of a real finished production file
    of the kind this project has been validated against (one flattened
    CMYK print image + one 칼선레이어 with the artist's real cut paths).
    """
    import fitz  # PyMuPDF

    doc = fitz.open(ai_path)
    page = doc[page_index]

    images = page.get_images(full=True)
    if not images:
        raise ValueError(f"{ai_path}: no embedded raster image found on page {page_index}.")

    from PIL import Image as PILImage

    distinct_xrefs = {entry[0] for entry in images}
    if len(distinct_xrefs) == 1 and len(images) == 1:
        xref = images[0][0]
        info = doc.extract_image(xref)
        with open(print_image_out_path, "wb") as f:
            f.write(info["image"])

        rect = page.get_image_rects(xref)[0]
        rx0, ry0, rx1, ry1 = rect.x0, rect.y0, rect.x1, rect.y1

        with PILImage.open(print_image_out_path) as im:
            iw, ih = im.size

        sx = iw / (rx1 - rx0)
        sy = ih / (ry1 - ry0)
        # Page units are always points (72/inch); this image's OWN effective
        # DPI is whatever scale it ended up placed at on the page.
        dpi = 72.0 * sx
    else:
        # 2026-09-26 실제 파일로 발견(같은 소스 이미지가 한 페이지에 여러
        # 번 배치됨 -- 실측 8번): core.ai_import.
        # load_ai_as_raster/load_real_grid_cells가 이미 이 경우를 "페이지
        # 전체를 하나의 픽스맵으로 렌더링"해서 처리하는 것과 똑같은 이유로
        # 여기도 고쳐야 한다 -- 그 두 함수와 달리 이 함수는 지금까지
        # 무조건 images[0]의 "그 한 배치"만 기준 삼아 왔는데, 정작
        # `layer_name` 레이어의 칼선 도형들은 페이지 전체(8번 배치 전부)에
        # 걸쳐 그려져 있으므로, 첫 배치 하나의 좁은 사각형/축척만으로
        # 매핑하면 다른 7개 배치에 속한 칼선들은 전부 그 좁은 이미지 픽셀
        # 범위를 한참 벗어난 엉뚱한 좌표로 계산된다(실측: 이미지 자체는
        # 1146x1571px인데 칼선 좌표가 4547px까지 나옴 -- 8배치가 대략
        # 4x2 격자로 늘어선 페이지 전체 크기였다는 뜻).
        #
        # 2026-09-26(2차, 실제 파일로 재발견): 이 픽스맵 렌더링은 반드시
        # _force_layer_visible(아래, get_drawings 바로 직전으로 옮김)보다
        # 먼저 해야 한다 -- 순서가 바뀌면(처음 이 함수를 고쳤을 때 실제로
        # 이 순서였음) `layer_name`(칼선/가이드 레이어)이 미리 강제로
        # 켜진 채로 페이지 전체가 렌더링돼, 원래는 인쇄용이 아닌 그 칼선
        # 안내선(예: 자홍색 타원)이 "인쇄 이미지"에 그대로 구워져 들어간다.
        # 실측으로 확인된 실제 피해: 그 구워진 안내선이 서로 다른 스티커
        # 도안끼리 Canny 엣지로 이어붙게 만들어, detect_design_bboxes_px가
        # 실제로는 72개인 도안을 7개(한 세로줄이 통째로 하나)로 잘못
        # 뭉쳐버렸다 -- "인식이 아예 안 됨" 증상의 진짜 원인 중 하나.
        xref0 = images[0][0]
        info0 = doc.extract_image(xref0)
        rect0 = page.get_image_rects(xref0)[0]
        with PILImage.open(__import__("io").BytesIO(info0["image"])) as im0:
            iw0, ih0 = im0.size
        sx0 = iw0 / (rect0.x1 - rect0.x0)
        dpi = 72.0 * sx0
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
        pix.save(print_image_out_path)
        with PILImage.open(print_image_out_path) as im:
            iw, ih = im.size
        sx = sy = zoom
        rx0, ry0, rx1, ry1 = page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1

    _force_layer_visible(doc, layer_name)
    drawings = [d for d in page.get_drawings() if d.get("layer") == layer_name]
    if not drawings:
        raise ValueError(f"{ai_path}: no shapes found on layer '{layer_name}'.")

    cutlines_px = []
    for d in drawings:
        poly = _drawing_to_polygon(d)
        if poly is None or poly.is_empty:
            continue
        # map page-point coords -> this image's own pixel space
        from shapely.affinity import affine_transform

        # affine_transform matrix: [a, b, d, e, xoff, yoff] for
        # x' = a*x + b*y + xoff ; y' = d*x + e*y + yoff
        mapped = affine_transform(poly, [sx, 0, 0, sy, -rx0 * sx, -ry0 * sy])
        cutlines_px.append(mapped)

    doc.close()

    return RealCutlineFile(
        print_image_path=print_image_out_path,
        image_w_px=iw,
        image_h_px=ih,
        image_placement_rect_pt=(rx0, ry0, rx1, ry1),
        dpi=dpi,
        cutlines_px=cutlines_px,
    )
