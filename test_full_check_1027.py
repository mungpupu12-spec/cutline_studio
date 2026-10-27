"""2026-10-27 전체 점검에서 고친 것들(알고리즘 확인용 합성 이미지 -- 칼선 품질은 실제 파일로 확인함).
  - 꽉 찬 사각형 카드는 카드가 아니라 카드 안 주인공 그림 둘레로(실제 완성 파일 칼선과 대조).
  - 장면이 카드 끝까지 퍼진 카드는 카드 모양 그대로.
  - 마스킹테이프 템플릿의 가는 안내선은 그림이 아니다.
  - 오목한 영역에서도 도무송 도형을 만든다.
  - 인쇄소 맞춤표·바코드 레이어는 그림이 아니다."""
import cv2
import numpy as np
from shapely.geometry import Polygon

from core import white_layer as wl
from core.ai_import import _is_non_art_layer
from core.cutline_types import CutlineType
from core.interactive_cutline import _inscribed_domusong_shape

DPI = 300.0


def _patterned_card(size=600, figure=True, scene=False):
    img = np.full((size, size, 3), (200, 210, 100), np.uint8)          # 카드 바탕색
    for y in range(20, size, 70):                                       # 카드 끝까지 이어지는 꽃무늬
        for x in range(20, size, 70):
            cv2.circle(img, (x, y), 14, (250, 250, 240), -1)
    fig = np.zeros((size, size), np.uint8)
    if figure:
        cv2.ellipse(fig, (size // 2, size // 2 + 20), (150, 190), 0, 0, 360, 1, -1)   # 가운데 캐릭터
        img[fig > 0] = (230, 160, 70)
        cv2.circle(img, (size // 2, size // 2 - 40), 40, (40, 40, 160), -1)
    if scene:                                                           # 카드 끝에서 끝까지 퍼진 장면
        cv2.rectangle(img, (30, 40), (220, 330), (60, 90, 200), -1)
        cv2.rectangle(img, (40, 380), (230, 570), (40, 180, 200), -1)
        tri = np.array([[460, 60], [580, 400], [340, 400]], np.int32)
        cv2.fillPoly(img, [tri], (30, 120, 40))
        cv2.rectangle(img, (250, 300), (360, 560), (230, 160, 70), -1)
    return img, fig.astype(bool)


def test_card_figure_mask_finds_the_character_not_the_card():
    img, fig = _patterned_card()
    m = wl.card_figure_mask(img, DPI)
    assert m is not None
    inter = (m & fig).sum()
    union = (m | fig).sum()
    assert inter / union > 0.85
    assert m.mean() < 0.5                                              # 카드 전체가 아니다


def test_scene_card_keeps_card_shape():
    img, _ = _patterned_card(figure=False, scene=True)
    assert wl.card_figure_mask(img, DPI) is None


def test_replace_cards_with_figures_on_sheet():
    card, fig = _patterned_card()
    sheet = np.full((800, 1500, 3), 255, np.uint8)
    sheet[100:700, 100:700] = card
    sheet[100:700, 800:1400] = card                                     # 같은 카드 반복
    mask = np.zeros(sheet.shape[:2], bool)
    mask[100:700, 100:700] = True
    mask[100:700, 800:1400] = True
    new, n = wl.replace_cards_with_figures(sheet, mask, DPI)
    assert n == 2
    for x0 in (100, 800):
        sub = new[100:700, x0:x0 + 600]
        assert (sub & fig).sum() / (sub | fig).sum() > 0.85
    assert not new[110, 110] and not new[110, 810]                      # 카드 바탕은 칼선 대상이 아님


def test_is_card_shape():
    rect = np.zeros((300, 300), np.uint8)
    rect[10:290, 20:280] = 1
    circ = np.zeros((300, 300), np.uint8)
    cv2.circle(circ, (150, 150), 140, 1, -1)
    assert wl.is_card_shape(rect, DPI)
    assert not wl.is_card_shape(circ, DPI)


def test_drop_guide_lines_keeps_motifs():
    m = np.zeros((180, 3000), bool)
    m[10:12, :] = True                                                  # 테이프 위·아래 안내선(2px)
    m[168:170, :] = True
    m[10:170, 0:3] = True                                               # 왼쪽 끝 이음선
    for x in range(100, 2900, 300):
        cv2.circle(m.view(np.uint8), (x, 90), 50, 1, -1)
    out = wl.drop_guide_lines(m, DPI)
    assert not out[11, 1500] and not out[169, 1500]
    assert out[90, 100] and out[90, 2800]
    assert cv2.connectedComponents(out.astype(np.uint8))[0] - 1 == 10


def test_domusong_shape_in_concave_region():
    # 그림 세 개가 이어진 오목한 모양 -- 예전에는 "도형을 만들지 못했습니다"로 실패
    region = Polygon([(0, 0), (300, 0), (300, 100), (120, 100), (120, 400), (0, 400)])
    for ct in (CutlineType.RECTANGLE, CutlineType.SQUARE, CutlineType.ELLIPSE):
        shape = _inscribed_domusong_shape(region, ct)
        assert not shape.is_empty
        assert region.buffer(1.0).contains(shape)


def test_regmark_and_barcode_layers_are_not_art():
    assert _is_non_art_layer("scpro2_regmarks")
    assert _is_non_art_layer("SA regmark type2 auto 33")
    assert _is_non_art_layer("바코드(잠김)")
    assert not _is_non_art_layer("스티커")
    assert not _is_non_art_layer("인쇄레이어")


def test_long_strip_image_has_no_price_band():
    # 테이프 줄처럼 가로로 아주 긴 그림: 테이프 줄 전체가 가격표 띠로 잡혀 칼선이 하나도 안 생겼다
    m = np.zeros((300, 6000), bool)
    m[40:200, 30:5970] = True
    assert wl.find_label_bands(m, DPI) == []
    sheet = np.zeros((3000, 2000), bool)                                # 보통 시트의 가격표 띠는 그대로
    sheet[2850:2950, 20:1980] = True
    sheet[500:900, 300:700] = True
    assert len(wl.find_label_bands(sheet, DPI)) == 1
