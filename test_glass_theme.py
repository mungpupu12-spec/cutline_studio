"""2026-09-29 글래스 디자인: 배경 그림/유리 판 그리기와 상태 문구 줄이기."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from core import glass_theme as gt


def test_backdrop_has_requested_size_and_soft_colors():
    img = gt.make_backdrop(640, 400)
    assert img.size == (640, 400)
    lo, hi = zip(*img.getextrema())
    assert min(lo) > 90          # 검은 부분 없이 밝은 파스텔
    assert img.getpixel((5, 5)) != img.getpixel((635, 5))  # 왼쪽 민트, 오른쪽 핑크


def test_square_widget_corners_are_hidden_inside_rounded_glass_panel():
    fill = (241, 245, 244)
    radius, outset = 22, 7
    assert outset >= gt.min_outset_for_radius(radius)
    bg = gt.make_backdrop(400, 300)
    x, y, w, h = 40, 30, 200, 150
    img = gt.paint_glass_panels(bg, [(x, y, w, h)], fill, radius=radius, outset=outset)
    # 위젯(각진 사각형)의 네 모서리 픽셀이 모두 판 안쪽 색과 같아야 이음새가 안 보인다
    for px, py in [(x, y), (x + w - 1, y), (x, y + h - 1), (x + w - 1, y + h - 1)]:
        assert img.getpixel((px, py)) == fill
    # 판 바깥(여백)은 배경이 비쳐 보인다
    assert img.getpixel((x - outset - 12, y + h // 2)) != fill


def test_status_text_is_shortened_for_display():
    from gui.app import CutLineApp

    short = CutLineApp._shorten_status_text
    assert short("저장됨: C:\\Users\\me\\Documents\\작업 폴더\\결과 파일.svg") == "저장됨: 결과 파일.svg"
    assert short("불러옴: /tmp/a/b/sheet.png") == "불러옴: sheet.png"
    keep = "파일을 선택하세요 (주 형식: 어도비 일러스트 .ai / PNG / JPG)"
    assert short(keep) == keep
    long = "오류 " + "가" * 300
    out = short(long, 90)
    assert len(out) == 90 and out.endswith("…")
    assert short("첫 줄\n둘째 줄") == "첫 줄 둘째 줄"
