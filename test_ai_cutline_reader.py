"""
Regression test for a real bug found this round (2026-09-08(9차)) while
comparing 멍푸님의 실제 라쿤 패턴 시트(실제 작업 파일 한 장)의
'칼선레이어'(진짜 손으로 그린 칼선)와 자동 인식 결과를 비교하던 중:

그 실제 파일은 '칼선레이어'가 기본적으로 꺼져 있었다(제출 전 미리보기용
으로 꺼 둔 것으로 보임 -- 실제로 매우 흔한 상태). PyMuPDF의
page.get_drawings()는 꺼져 있는(OCG off) 레이어의 도형을 아예 빈 목록으로
돌려주는데, core.ai_cutline_reader.load_real_cutlines()는 그 결과만 보고
"해당 레이어에 도형이 없다"고 오판해서, 실제로는 그 레이어에 있는 진짜
칼선 85개를 통째로 놓쳤다(get_drawings()가 반환한 개수: 켜기 전 0개 ->
강제로 켠 뒤 85개, 실제 파일로 직접 확인). load_real_grid_cells도 같은
get_drawings() 경로를 쓰므로 동일한 문제에 노출되어 있었다.

수정(_force_layer_visible): 두 함수 모두, 읽기 전용으로 연 문서에서 목표
레이어를 get_drawings() 호출 전에 강제로 켠다(저장하지 않으므로 원본
파일에는 전혀 영향 없음).

같은 라쿤 시트 비교 작업 중 두 번째로 발견한 별개의 실제 버그(다른 실제
작업 파일 2개에서 확인): '개별재단 레이어'가 항상
시트 전체를 가로지르는 순수한 선으로만 그려진 게 아니라, 어떤 파일은 각
칸을 처음부터 "닫힌 사각형 하나"로(칸마다 따로) 그려 놓았다. 기존
load_real_grid_cells는 오직 "선(폭/높이가 거의 0인 사각형)"만 격자로
인식했기 때문에, 이런 파일에서는 그 레이어에 있는 무관한 다른 얇은 선(예:
범례/눈금 표시)을 기준으로 "칸 1개 = 시트 전체"로 잘못 재구성하거나
(실제 파일로 확인: 진짜 칼선은 12칸 x 칸마다 2~3개씩 총
38개인데 "칸 1개"로 뭉쳐버렸었다), 아예 실패했다.

수정(_extract_closed_rect_cells): '개별재단 레이어'의 도형 중 "선이 아니라
실제로 폭/높이가 있는 사각형"만 모아서(페이지 전체를 거의 다 덮는 사각형은
배경 프레임으로 보고 제외), 2개 이상이면 그 사각형들 자체를 칸으로 쓴다 --
선 기반 재구성보다 먼저 시도해서, 레이어에 무관한 얇은 선이 섞여 있어도
더 이상 잘못된 결과로 이어지지 않는다.

두 테스트 모두 실제 파일이 아니라, PyMuPDF로 직접 만든 합성 PDF로 정확히
같은 상황을 재현해서 확인한다 -- 표준 규칙("실제 도안으로 판단하지
않는다")에 따라 순수 합성 기하만 사용.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fitz
from PIL import Image

from core.ai_cutline_reader import load_real_cutlines, load_real_grid_cells

SCRATCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def check(label, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def _make_synthetic_ai(path, image_w=400, image_h=300):
    """알려진 기하만으로 된 합성 .ai(PDF 호환) 파일 -- 인쇄레이어(켜짐) +
    칼선레이어(꺼짐, 알려진 사각형 하나) + 개별재단 레이어(꺼짐, 알려진
    세로 격자선 하나로 좌/우 2칸)."""
    img = Image.new("RGB", (image_w, image_h), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    doc = fitz.open()
    page = doc.new_page(width=image_w, height=image_h)  # 1pt == 1px (72dpi), 1:1 매핑

    print_ocg = doc.add_ocg("인쇄레이어", on=True)
    cut_ocg = doc.add_ocg("칼선레이어", on=False)  # 실제 파일과 동일하게 기본 꺼짐
    grid_ocg = doc.add_ocg("개별재단 레이어", on=False)  # 역시 기본 꺼짐

    page.insert_image(fitz.Rect(0, 0, image_w, image_h), stream=img_bytes, oc=print_ocg)

    cut_bbox = (50, 40, 150, 140)
    cut_shape = page.new_shape()
    cut_shape.draw_rect(fitz.Rect(*cut_bbox))
    cut_shape.finish(oc=cut_ocg)
    cut_shape.commit()

    grid_x = image_w / 2
    grid_shape = page.new_shape()
    grid_shape.draw_rect(fitz.Rect(grid_x, 0, grid_x + 0.01, image_h))  # 폭이 거의 0인 세로선
    grid_shape.finish(oc=grid_ocg, fill=(0, 0, 0))
    grid_shape.commit()

    doc.save(path)
    doc.close()
    return cut_bbox, grid_x


def test_layers_are_really_off_by_default():
    """수정 전 상황을 정확히 재현하기 위한 전제 확인: 이 합성 파일의
    '칼선레이어'/'개별재단 레이어'는 실제로 기본 꺼짐 상태이고, 강제로
    켜지 않으면 get_drawings()가 그 레이어의 도형을 정말 하나도 안
    돌려준다는 것부터 확인한다(그래야 아래 성공이 "우연히 이미 켜져
    있었다"가 아니라 진짜 고친 것임을 보증)."""
    os.makedirs(SCRATCH_DIR, exist_ok=True)
    ai_path = os.path.join(SCRATCH_DIR, "_synthetic_hidden_layers.ai")
    _make_synthetic_ai(ai_path)

    doc = fitz.open(ai_path)
    cfgs = {c["text"]: c for c in doc.layer_ui_configs()}
    check("칼선레이어가 기본적으로 꺼져 있음(on=0)", cfgs["칼선레이어"]["on"] == 0)
    check("개별재단 레이어도 기본적으로 꺼져 있음(on=0)", cfgs["개별재단 레이어"]["on"] == 0)

    page = doc[0]
    drawings_before = [d for d in page.get_drawings() if d.get("layer") == "칼선레이어"]
    check(
        "강제로 켜기 전에는 get_drawings()가 꺼진 레이어의 도형을 정말 하나도 안 돌려줌 "
        "(이게 바로 실제 라쿤 시트 파일에서 벌어졌던 상황)",
        len(drawings_before) == 0,
    )
    doc.close()
    print("[OK] 전제 확인: 꺼진 레이어는 강제로 켜지 않으면 get_drawings()에 안 잡힘")


def test_load_real_cutlines_finds_shapes_on_a_layer_off_by_default():
    ai_path = os.path.join(SCRATCH_DIR, "_synthetic_hidden_layers.ai")
    check("합성 파일이 존재", os.path.isfile(ai_path))

    expected_bbox, _grid_x = (50, 40, 150, 140), None
    real = load_real_cutlines(
        ai_path, os.path.join(SCRATCH_DIR, "_syn_print_cut.png"), layer_name="칼선레이어"
    )
    check("load_real_cutlines가 꺼진 레이어에서도 칼선 1개를 찾음", len(real.cutlines_px) == 1)
    minx, miny, maxx, maxy = real.cutlines_px[0].bounds
    check(
        f"찾은 칼선 경계가 알려진 사각형({expected_bbox})과 정확히 일치 "
        f"(got=({minx:.1f},{miny:.1f},{maxx:.1f},{maxy:.1f}))",
        abs(minx - expected_bbox[0]) < 0.5 and abs(miny - expected_bbox[1]) < 0.5
        and abs(maxx - expected_bbox[2]) < 0.5 and abs(maxy - expected_bbox[3]) < 0.5,
    )
    print("[OK] load_real_cutlines: 기본적으로 꺼진 레이어에서도 실제 칼선을 정확히 찾음")


def test_load_real_grid_cells_finds_grid_on_a_layer_off_by_default():
    ai_path = os.path.join(SCRATCH_DIR, "_synthetic_hidden_layers.ai")
    grid = load_real_grid_cells(
        ai_path, os.path.join(SCRATCH_DIR, "_syn_print_grid.png"), layer_name="개별재단 레이어"
    )
    check("load_real_grid_cells가 꺼진 레이어에서도 격자를 찾아 2칸으로 나눔", len(grid.cells_px) == 2)
    xs = sorted(c[0] for c in grid.cells_px)
    check(
        "왼쪽 칸은 x=0에서 시작, 오른쪽 칸은 이미지 가운데(폭의 절반)에서 시작",
        xs[0] == 0 and abs(xs[1] - grid.image_w_px / 2) <= 1,
    )
    print("[OK] load_real_grid_cells: 기본적으로 꺼진 레이어에서도 실제 격자를 정확히 찾음")


def _make_synthetic_closed_rect_grid_ai(path, image_w=600, image_h=400):
    """실제 작업 파일 2개에서 확인된 구조를 합성으로 재현:
    '개별재단 레이어'에 2x2 = 4개의 닫힌 사각형(칸마다
    하나씩) + 그 칸들과는 무관한 얇은 선 하나(범례/눈금 표시 흉내)를
    같이 둔다 -- 기존 버그라면 이 무관한 선 때문에 "칸 1개 = 전체 이미지"
    로 잘못 뭉쳐졌을 상황."""
    img = Image.new("RGB", (image_w, image_h), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    doc = fitz.open()
    page = doc.new_page(width=image_w, height=image_h)
    print_ocg = doc.add_ocg("인쇄레이어", on=True)
    grid_ocg = doc.add_ocg("개별재단 레이어", on=True)
    page.insert_image(fitz.Rect(0, 0, image_w, image_h), stream=img_bytes, oc=print_ocg)

    cw, ch = image_w / 2, image_h / 2
    cells = [
        (0, 0, cw, ch),
        (cw, 0, image_w, ch),
        (0, ch, cw, image_h),
        (cw, ch, image_w, image_h),
    ]
    # 실제 파일(get_drawings()로 확인)처럼 사각형 하나마다 그 자체로 별도
    # drawing(개별 finish/commit)이 되게 함 -- 하나의 Shape에 여러 개를
    # 몰아넣고 한 번만 finish()하면 PyMuPDF가 하나의 합쳐진 path로 묶어
    # 버려서(그 경우 "rect"가 전체를 감싸는 하나의 큰 사각형이 되어버림)
    # 실제 파일의 "칸마다 따로 그려진 사각형" 구조를 재현하지 못한다.
    for (x0, y0, x1, y1) in cells:
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(x0, y0, x1, y1))
        shape.finish(oc=grid_ocg, fill=(0, 0, 0))
        shape.commit()
    # 칸들과는 완전히 무관한, 시트 일부만 가로지르는 얇은 선(범례/눈금 표시
    # 흉내) -- _classify_grid_lines라면 이걸 v_line 후보로 잡을 수 있는
    # 모양이지만, 실제 칸 경계와는 아무 관계가 없다.
    line_shape = page.new_shape()
    line_shape.draw_rect(fitz.Rect(image_w - 5, 10, image_w - 5 + 0.01, 60))
    line_shape.finish(oc=grid_ocg, fill=(0, 0, 0))
    line_shape.commit()

    doc.save(path)
    doc.close()
    return cells


def test_load_real_grid_cells_uses_closed_rectangles_when_layer_draws_cells_directly():
    ai_path = os.path.join(SCRATCH_DIR, "_synthetic_closed_rect_grid.ai")
    expected_cells = _make_synthetic_closed_rect_grid_ai(ai_path)

    grid = load_real_grid_cells(
        ai_path, os.path.join(SCRATCH_DIR, "_syn_print_rectgrid.png"), layer_name="개별재단 레이어"
    )
    check(
        f"닫힌 사각형 4개를 그대로 4개 칸으로 인식 (무관한 얇은 선에 낚여 "
        f"'칸 1개'로 뭉쳐지지 않음) -- got {len(grid.cells_px)} cells",
        len(grid.cells_px) == 4,
    )
    # 각 발견된 칸이 알려진 4개 칸 중 하나와 (그림 픽셀 변환 오차 감안해)
    # 거의 정확히 일치하는지 확인.
    sx = grid.image_w_px / 600.0
    sy = grid.image_h_px / 400.0
    for found in grid.cells_px:
        matched = any(
            abs(found[0] - ex[0] * sx) < 3 and abs(found[1] - ex[1] * sy) < 3
            and abs(found[2] - ex[2] * sx) < 3 and abs(found[3] - ex[3] * sy) < 3
            for ex in expected_cells
        )
        check(f"찾은 칸 {found}이 알려진 4개 칸 중 하나와 일치", matched)
    print("[OK] load_real_grid_cells: 칸마다 닫힌 사각형으로 그려진 레이어도 무관한 선에 낚이지 않고 정확히 인식")


def main():
    test_layers_are_really_off_by_default()
    test_load_real_cutlines_finds_shapes_on_a_layer_off_by_default()
    test_load_real_grid_cells_finds_grid_on_a_layer_off_by_default()
    test_load_real_grid_cells_uses_closed_rectangles_when_layer_draws_cells_directly()
    print("\nAll ai_cutline_reader hidden-layer checks passed.")


if __name__ == "__main__":
    main()
