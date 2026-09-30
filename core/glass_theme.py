"""유리(글래스) 느낌 화면 배경 그리기 -- 순수 PIL 함수만 모아 둔 곳.

2026-09-29 멍푸 요청("첨부한 파일처럼 블러 효과와 투명 글래스로 프로그램 디자인
변경"): 참고 이미지는 흐릿하게 번진 파스텔 색 덩어리 위에 반투명한 둥근 유리
카드가 떠 있는 모습이다.

Tk/customtkinter 위젯은 진짜 반투명(뒤가 비쳐 보이는 것)을 지원하지 않는다 --
모든 위젯은 불투명한 사각형이다. 그래서 다음처럼 흉내 낸다.

  1. 창 전체 크기의 배경 그림을 PIL로 만든다: 연한 바탕 + 크게 번진 파스텔
     덩어리(민트/핑크/피치/라벤더).
  2. 화면의 큰 패널(헤더, 왼쪽 조작 패널, 오른쪽 미리보기) 자리에 둥근 유리
     판을 그 배경 그림 안에 직접 그린다(부드러운 그림자 + 반쯤 비치는 흰 판 +
     흰 테두리). 패널 위젯 자체는 모서리가 각진 사각형이지만, 그림 속 둥근 판을
     패널보다 `outset`만큼 크게 그리므로 위젯의 각진 모서리가 둥근 판 안쪽에
     완전히 숨는다(모서리 반지름 R일 때 outset >= 0.293R이면 된다).
  3. 유리 판 안쪽은 한 가지 색으로 칠해야 그 위의 위젯(같은 색)과 이음새가 안
     보인다 -- 그래서 판의 색은 GLASS_FILL로 고정하고, "뒤가 비치는" 느낌은 판
     가장자리 테두리와 판 바깥의 번진 색으로 낸다.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

# 바탕과 번진 색 덩어리 -- 참고 이미지(민트/핑크 글래스 UI, 번진 음료 사진,
# 빛나는 색 도형 포스터)에서 뽑은 파스텔 톤.
BACKDROP_BASE = (243, 245, 244)
BLOBS = [
    # (중심 x 비율, 중심 y 비율, 반지름(짧은 변 대비), RGB)
    (0.10, 0.18, 0.55, (150, 226, 204)),   # 민트
    (0.92, 0.12, 0.50, (247, 188, 211)),   # 핑크
    (0.62, 1.02, 0.55, (255, 214, 170)),   # 피치
    (0.30, 0.95, 0.45, (208, 198, 247)),   # 라벤더
    (0.98, 0.70, 0.40, (170, 222, 240)),   # 하늘
]


def hex_to_rgb(color: str):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def make_backdrop(width: int, height: int, blobs=BLOBS, base=BACKDROP_BASE) -> Image.Image:
    """연한 바탕 위에 크게 번진 파스텔 덩어리. 작은 크기로 그려 흐리게 한 뒤
    키우므로(어차피 흐린 그림이라 손실 없음) 창 크기가 커도 빠르다."""
    width = max(1, int(width))
    height = max(1, int(height))
    scale = 8
    sw, sh = max(8, width // scale), max(8, height // scale)
    img = Image.new("RGB", (sw, sh), base)
    short = min(sw, sh)
    for cx, cy, r, rgb in blobs:
        layer = Image.new("L", (sw, sh), 0)
        rr = r * short
        x, y = cx * sw, cy * sh
        ImageDraw.Draw(layer).ellipse((x - rr, y - rr, x + rr, y + rr), fill=200)
        layer = layer.filter(ImageFilter.GaussianBlur(max(2.0, rr * 0.55)))
        img = Image.composite(Image.new("RGB", (sw, sh), rgb), img, layer)
    img = img.filter(ImageFilter.GaussianBlur(max(1.0, short / 14)))
    return img.resize((width, height), Image.BICUBIC)


def _rounded_mask(size, rects, radius, supersample=2):
    """rects: [(x0, y0, x1, y1)] -- 둥근 사각형들의 가장자리가 매끈한 L 마스크."""
    w, h = size
    ss = supersample
    mask = Image.new("L", (w * ss, h * ss), 0)
    d = ImageDraw.Draw(mask)
    for x0, y0, x1, y1 in rects:
        d.rounded_rectangle((x0 * ss, y0 * ss, x1 * ss - 1, y1 * ss - 1), radius=radius * ss, fill=255)
    return mask.resize((w, h), Image.LANCZOS)


def min_outset_for_radius(radius: float) -> float:
    """각진 위젯의 모서리가 둥근 판 안에 들어가려면 판을 얼마나 크게 그려야 하는지."""
    return radius * (1.0 - 2 ** -0.5)


def paint_glass_panels(backdrop: Image.Image, panel_rects, fill_rgb, radius=20, outset=7,
                       shadow_alpha=0.10, border_rgb=(255, 255, 255)) -> Image.Image:
    """backdrop 위에 panel_rects(위젯 자리, (x, y, w, h))마다 둥근 유리 판을 그린다.
    판은 위젯보다 outset만큼 사방으로 크고, 안쪽은 fill_rgb 한 가지 색(위젯과 같은
    색)이라 위젯과 이음새가 보이지 않는다."""
    img = backdrop.convert("RGB").copy()
    w, h = img.size
    rects = [(x - outset, y - outset, x + pw + outset, y + ph + outset) for x, y, pw, ph in panel_rects
             if pw > 2 and ph > 2]
    if not rects:
        return img
    # 부드러운 그림자(아래로 살짝) -- 반 크기로 흐리게 해서 빠르게
    half = (max(1, w // 2), max(1, h // 2))
    sh_mask = Image.new("L", half, 0)
    sd = ImageDraw.Draw(sh_mask)
    for x0, y0, x1, y1 in rects:
        sd.rounded_rectangle((x0 / 2, (y0 + 6) / 2, x1 / 2, (y1 + 8) / 2), radius=radius / 2,
                             fill=int(255 * shadow_alpha))
    sh_mask = sh_mask.filter(ImageFilter.GaussianBlur(7)).resize((w, h), Image.BICUBIC)
    img = Image.composite(Image.new("RGB", (w, h), (40, 60, 55)), img, sh_mask)
    # 흰 테두리(판보다 1.5px 크게) 그다음 판 안쪽
    border_rects = [(x0 - 1.5, y0 - 1.5, x1 + 1.5, y1 + 1.5) for x0, y0, x1, y1 in rects]
    img = Image.composite(Image.new("RGB", (w, h), border_rgb), img,
                          _rounded_mask((w, h), border_rects, radius + 1.5))
    img = Image.composite(Image.new("RGB", (w, h), tuple(fill_rgb)), img,
                          _rounded_mask((w, h), rects, radius))
    return img


def make_placeholder_art(width: int, height: int, base_rgb=(250, 251, 251)) -> Image.Image:
    """빈 미리보기 칸에 깔리는 그림 -- 가운데에 부드럽게 빛나는 색 덩어리 두 개
    (참고 이미지의 'LIGHT BOX' 포스터 느낌). 도안을 열면 사라진다."""
    blobs = [
        (0.42, 0.46, 0.30, (150, 226, 204)),
        (0.60, 0.56, 0.26, (247, 188, 211)),
        (0.50, 0.40, 0.18, (255, 214, 170)),
    ]
    return make_backdrop(width, height, blobs=blobs, base=base_rgb)
