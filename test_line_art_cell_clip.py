"""
Regression test for 2026-09-10(41차) 피드백(200% 확대 스크린샷에서 동그라미로
가리킨, 서로 인접한 두 도안 경계에서 칼선이 옆 도안 쪽으로 침범/교차하던
문제): 자동 인식 배치 처리(gui.app._generate_one_item_for_auto_detect)에서
AUTO_STYLE(무테+유테 자동 생성)이나 LINE_ART(유테)로 판정된 도안은
core.interactive_cutline.generate_cutline_auto/generate_cutline_by_style를
호출하면서 정작 bounds_px를 넘기지 않고 있었다.

core.image_style.generate_style_cutline의 LINE_ART(유테) 분기는
`design.buffer(margin_px...).intersection(bounds_rect)`로 최종 선을 자르는데,
`bounds_rect`는 bounds_px가 없으면 이미지 전체로 기본값이 잡힌다(도무송/
완칼과 달리 그 칸 자체로 좁혀지지 않음) -- 그래서 실제 그림 경계가 칸
가장자리에 거의 닿아 있어(마진만큼의 여유가 없어) 바깥으로 버퍼링한 선이
그 칸을 넘어서면, 옆에 붙어 있는 다른 도안의 영역까지 그대로 침범할 수
있었다(바로 이 케이스가 화면 스크린샷에서 실제로 확인된 증상과 일치).

도무송/완칼 경로는 이미 bounds_px_override=self._selection_px로 이 문제를
막고 있었다(test_domusong_cell_clip.py 참고) -- 이번 수정은 AUTO_STYLE/
LINE_ART/BORDERLESS/MASKING_TAPE 자동 인식 경로에도 똑같이
bounds_px=self._selection_px를 넘기도록 gui/app.py를 맞춘 것.

작은 합성 도형(알려진 원 하나, 칸 가장자리에 거의 닿게 배치)만 사용 --
실제 도안 파일로 칼선 "품질"을 판단하는 용도가 아니라, "bounds_px를 넘기면
실제로 칸 밖으로 못 나가는가"라는 순수 배선/알고리즘 확인용 테스트.
"""

import os
import sys
import tempfile

sys.path.insert(0, "/root/cutline_studio")
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

from core.image_style import ImageStyle
from core.interactive_cutline import generate_cutline_by_style


def _make_cell_with_tight_content(path):
    """cell = (20, 20, 180, 180) 안에, 그 칸의 오른쪽/아래쪽 가장자리에
    거의 닿을 만큼(마진보다 훨씬 좁은 여유만 남기고) 원을 그려 넣는다 --
    실제 인쇄 시트에서 종이를 아끼려고 캐릭터를 칸 가장자리 가깝게 배치한
    상황을 known-geometry로 재현."""
    img = Image.new("RGB", (400, 200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # 칸(cell) 자체는 그림에 보이지 않는 논리적 좌표일 뿐 -- 실제로 그려지는
    # 것은 원 하나뿐이다(GrabCut이 배경과 구분할 색 대비만 있으면 됨).
    d.ellipse((30, 30, 175, 175), fill=(60, 120, 200))
    img.save(path)


def test_unclipped_line_art_can_overflow_its_own_cell():
    """Establishes the bug: without bounds_px, 유테(LINE_ART)가 칼선을
    바깥으로 버퍼링할 때 칸 가장자리를 넘어갈 수 있다."""
    tmp = os.path.join(tempfile.gettempdir(), "_test_line_art_clip_sheet.png")
    _make_cell_with_tight_content(tmp)
    cell = (20, 20, 180, 180)
    try:
        result = generate_cutline_by_style(
            image_path=tmp,
            style=ImageStyle.LINE_ART,
            dpi=300.0,
            selection_px=cell,
            bounds_px=None,
            margin_mm=3.0,
        )
    finally:
        os.remove(tmp)
    _minx, _miny, maxx, maxy = result.design.bounds
    assert maxx > 180 or maxy > 180, (
        f"expected the unclipped case to actually overflow past the cell (180,180), got bounds up to ({maxx:.1f},{maxy:.1f})"
    )
    print(f"[OK] confirmed baseline: unclipped LINE_ART overflows its cell (design bounds maxx={maxx:.1f}, maxy={maxy:.1f} > 180)")


def test_bounds_px_clips_line_art_to_its_own_cell():
    """실제 수정: bounds_px=그 칸 자체를 넘기면, 아무리 버퍼링해도 절대 그
    칸 경계를 넘지 못하고 정확히 그 안에서 잘려야 한다."""
    tmp = os.path.join(tempfile.gettempdir(), "_test_line_art_clip_sheet2.png")
    _make_cell_with_tight_content(tmp)
    cell = (20, 20, 180, 180)
    try:
        result = generate_cutline_by_style(
            image_path=tmp,
            style=ImageStyle.LINE_ART,
            dpi=300.0,
            selection_px=cell,
            bounds_px=cell,
            margin_mm=3.0,
        )
    finally:
        os.remove(tmp)

    x0, y0, x1, y1 = cell
    minx, miny, maxx, maxy = result.design.bounds
    print(f"design bounds=({minx:.1f},{miny:.1f},{maxx:.1f},{maxy:.1f})  cell={cell}")
    assert minx >= x0 - 0.5, "유테 칼선이 칸의 왼쪽 경계를 넘어갔음"
    assert maxx <= x1 + 0.5, "유테 칼선이 칸의 오른쪽 경계를 넘어 옆 도안 쪽을 침범했음"
    assert miny >= y0 - 0.5, "유테 칼선이 칸의 위쪽 경계를 넘어갔음"
    assert maxy <= y1 + 0.5, "유테 칼선이 칸의 아래쪽 경계를 넘어갔음"
    print("[OK] bounds_px=cell을 넘기면 유테 칼선이 정확히 그 칸 안에만 그려짐(옆 도안 침범 없음)")


def main():
    test_unclipped_line_art_can_overflow_its_own_cell()
    test_bounds_px_clips_line_art_to_its_own_cell()
    print("\nAll LINE_ART cell-clip checks passed.")


if __name__ == "__main__":
    main()
