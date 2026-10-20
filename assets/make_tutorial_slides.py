"""
Generate the illustrated first-run tutorial slides (2026-08-31 피드백:
"처음 화면에 뜨는 튜토리얼을 예시 이미지를 제작해서 사용법을 이미지와
문장으로 슬라이드 구성으로 만들어").

이 이미지들은 실제 고객 도안/칼선 판정 로직을 검증하기 위한 자료가 아니라,
순전히 앱 사용법을 보여주기 위한 아이콘식 도식(schematic icon) 그림이다 --
make_icon.py와 같은 성격의, PIL로 직접 그리는 장식/설명용 그래픽.

  assets/tutorial/step1.png ~ step6.png  (각 560x260, gui/app.py의
  튜토리얼 슬라이드 대화상자에서 사용)
"""

import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "tutorial")

W, H = 560, 260

BG = (243, 245, 244, 255)       # BG_APP
CARD = (255, 255, 255, 255)     # BG_CARD
BORDER = (226, 226, 229, 255)   # BORDER
INK = (0, 0, 0, 255)            # TEXT_PRIMARY
GRAY = (69, 69, 74, 255)        # TEXT_SECONDARY
COBALT = (30, 135, 112, 255)    # ACCENT(2026-10-20: 앱 포인트 색 진한 민트에 맞춤)
COBALT_SOFT = (227, 245, 239, 255)  # ACCENT_SOFT
WHITE = (255, 255, 255, 255)
SAFETY = (22, 164, 74, 255)
CUT = (220, 38, 38, 255)
BLEED = (37, 99, 235, 255)


def _font(size, bold=True):
    # 한글 라벨(조각 스티커/스티커/도무송/유테/무테 등)을 그대로 그려 넣으려면
    # 한글 글리프가 있는 폰트가 필요함 -- 이 스크립트는 생성(빌드) 시점에만
    # 폰트가 필요하고, 완성된 PNG에는 이미 글자가 이미지로 구워져 있으므로
    # 그 PNG를 "쓰는" 실행 환경(고객 Windows PC)에는 이 폰트가 없어도 무방함.
    #
    # 2026-08-31 발견한 실제 버그: 이 스크립트는 개발 중엔 이 리눅스 샌드박스
    # 안에서만 실행돼봤지만, 실제로는 exe_빌드.bat이 "고객의 Windows PC"에서
    # 매번 다시 이 스크립트를 실행해서 PNG를 새로 만든다. 예전 코드는 리눅스
    # 전용 경로 하나만 확인했기 때문에, Windows에서는 그 경로가 존재하지 않아
    # 조용히 ImageFont.load_default()로 넘어갔고, 그 기본 폰트는 한글 글리프가
    # 전혀 없어 라벨 글자가 아예 안 보이는(칸이 비어 보이는) 결과가 됐음 --
    # "버튼 안의 글자가 안 보여" 피드백의 원인. 이제 리눅스(개발/CI)와
    # Windows(실제 빌드 환경) 양쪽의 흔한 한글 폰트 경로를 모두 확인한다.
    if bold:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            r"C:\Windows\Fonts\malgunbd.ttf",   # 맑은 고딕 Bold (Windows 기본 내장)
            r"C:\Windows\Fonts\msyhbd.ttc",      # 예비: 마이크로소프트 야헤이 Bold
        ]
    else:
        candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            r"C:\Windows\Fonts\malgun.ttf",      # 맑은 고딕 (Windows 기본 내장)
            r"C:\Windows\Fonts\msyh.ttc",        # 예비: 마이크로소프트 야헤이
        ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size, index=0)
            except Exception:
                continue
    # 위 후보 전부 실패 -- 이 경우 한글이 안 보이는 이미지가 만들어지므로,
    # 콘솔에 눈에 띄게 경고해서 다음에 같은 문제가 생기면 바로 알아챌 수
    # 있게 함(치명적 오류로 멈추지는 않음 -- 장식 이미지일 뿐이므로).
    print(
        "[경고] 한글을 그릴 수 있는 폰트를 찾지 못했습니다 -- 튜토리얼 이미지의 "
        "한글 라벨이 비어 보일 수 있습니다."
    )
    return ImageFont.load_default()


def _canvas():
    im = Image.new("RGBA", (W, H), BG)
    return im, ImageDraw.Draw(im)


def _label(d, cx, cy, text, fill=COBALT, size=20):
    f = _font(size)
    bbox = d.textbbox((0, 0), text, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - w / 2 - bbox[0], cy - h / 2 - bbox[1]), text, font=f, fill=fill)


def _label_fit(d, cx, cy, text, fill=COBALT, size=19, max_width=None, min_size=11):
    """_label과 같지만, max_width가 주어지면 글자가 그 안에 들어갈 때까지
    폰트 크기를 줄여본다 -- 긴 라벨("조각 스티커" 등)이 좁은 버튼/도형
    바깥으로 삐져나오거나 잘려서 안 보이는 일이 없게 하기 위함."""
    s = size
    if max_width is not None:
        while s > min_size:
            f = _font(s)
            bbox = d.textbbox((0, 0), text, font=f)
            if (bbox[2] - bbox[0]) <= max_width:
                break
            s -= 1
    _label(d, cx, cy, text, fill=fill, size=s)


def _dashed_rect(d, box, color, width=3, dash=10, gap=7):
    x0, y0, x1, y1 = box
    edges = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    for (sx, sy), (ex, ey) in edges:
        length = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
        if length == 0:
            continue
        ux, uy = (ex - sx) / length, (ey - sy) / length
        pos = 0.0
        while pos < length:
            seg_end = min(pos + dash, length)
            d.line(
                [sx + ux * pos, sy + uy * pos, sx + ux * seg_end, sy + uy * seg_end],
                fill=color, width=width,
            )
            pos = seg_end + gap


def _arrow(d, x0, y0, x1, y1, color, width=4, head=10):
    d.line([x0, y0, x1, y1], fill=color, width=width)
    import math
    ang = math.atan2(y1 - y0, x1 - x0)
    for side in (-1, 1):   # 2026-10-20: 화살촉이 거꾸로(꼬리 쪽으로) 그려지던 것 바로잡음
        a = ang + side * 0.6
        d.line([x1, y1, x1 - head * math.cos(a), y1 - head * math.sin(a)], fill=color, width=width)


def step1():
    """도안 파일(.ai / PNG / JPG) 선택."""
    im, d = _canvas()
    fx0, fy0, fx1, fy1 = 190, 40, 340, 210
    fold = 26
    d.polygon(
        [(fx0, fy0), (fx1 - fold, fy0), (fx1, fy0 + fold), (fx1, fy1), (fx0, fy1)],
        fill=CARD, outline=BORDER, width=2,
    )
    d.polygon([(fx1 - fold, fy0), (fx1, fy0 + fold), (fx1 - fold, fy0 + fold)], fill=BORDER)
    badge_box = (fx0 + 14, fy0 + 24, fx0 + 78, fy0 + 58)
    d.rounded_rectangle(badge_box, radius=8, fill=COBALT)
    _label(d, (badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2, "AI", fill=WHITE, size=20)
    pill_box = (fx0 + 14, fy0 + 74, fx0 + 122, fy0 + 100)
    d.rounded_rectangle(pill_box, radius=12, outline=GRAY, width=2)
    _label(d, (pill_box[0] + pill_box[2]) / 2, (pill_box[1] + pill_box[3]) / 2, "PNG / JPG", fill=GRAY, size=13)
    cx, cy, r = fx1 + 60, fy1 - 6, 22
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=COBALT, width=4)
    d.line([cx - 8, cy, cx - 2, cy + 6, cx + 9, cy - 9], fill=COBALT, width=4, joint="curve")
    return im


def step2():
    """도무송 여부와 작업 종류 고르기(2026-10-20: 지금 화면의 작업 종류 이름으로)."""
    im, d = _canvas()
    labels = ["무테", "유테", "키스컷"]
    selected = 1
    pw, ph, gap = 140, 58, 22
    total = pw * 3 + gap * 2
    x0 = (W - total) / 2
    y0 = H / 2 - ph / 2 - 14
    for i, text in enumerate(labels):
        bx0 = x0 + i * (pw + gap)
        box = (bx0, y0, bx0 + pw, y0 + ph)
        if i == selected:
            d.rounded_rectangle(box, radius=29, fill=COBALT)
            fill = WHITE
        else:
            d.rounded_rectangle(box, radius=29, outline=BORDER, width=2, fill=CARD)
            fill = GRAY
        _label_fit(d, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, text, fill=fill, size=20, max_width=pw - 16)
    cx, cy = x0 + selected * (pw + gap) + pw / 2, y0 + ph + 34
    d.polygon([(cx - 10, cy - 10), (cx + 10, cy - 10), (cx, cy + 6)], fill=COBALT)
    return im


def _blob(d, cx, cy, rx, ry, fill):
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill)


def step3():
    """자동으로 여러 개 인식 + 추가: 그림마다 칼선(빨강)이 생김."""
    im, d = _canvas()
    outer = (60, 30, 500, 230)
    d.rounded_rectangle(outer, radius=14, fill=CARD, outline=BORDER, width=2)
    for cx, cy, rx, ry, col in [(150, 130, 46, 58, (247, 196, 120, 255)), (280, 120, 40, 40, (150, 200, 235, 255)),
                                (410, 135, 50, 42, (240, 160, 180, 255))]:
        m = 12
        d.ellipse([cx - rx - m, cy - ry - m, cx + rx + m, cy + ry + m], outline=CUT, width=3)
        _blob(d, cx, cy, rx, ry, col)
    return im


def step4():
    """미리보기에서 칼선 확인·수정."""
    im, d = _canvas()
    outer = (60, 30, 500, 230)
    d.rounded_rectangle(outer, radius=14, fill=CARD, outline=BORDER, width=2)
    cx, cy = 230, 130
    d.ellipse([cx - 80, cy - 66, cx + 80, cy + 66], outline=CUT, width=3)
    _blob(d, cx, cy, 66, 52, (150, 200, 235, 255))
    for a in (0, 90, 180, 270):
        import math
        x = cx + 80 * math.cos(math.radians(a))
        y = cy + 66 * math.sin(math.radians(a))
        d.rectangle([x - 6, y - 6, x + 6, y + 6], fill=WHITE, outline=COBALT, width=2)
    px, py = 390, 110
    d.rounded_rectangle([px - 10, py - 40, px + 10, py + 40], radius=6, fill=COBALT)
    d.polygon([(px - 10, py + 40), (px + 10, py + 40), (px, py + 62)], fill=COBALT)
    return im


def step5():
    """이 영역 추가 + 미리보기로 계속 쌓기(누적) -- 예전 슬라이드(지금은 쓰지 않음)."""
    im, d = _canvas()
    base_x, base_y, w, h = 170, 150, 170, 60
    colors = [BORDER, (200, 200, 205, 255), COBALT_SOFT]
    for i in range(3):
        ox, oy = i * 16, -i * 26
        box = (base_x + ox, base_y + oy - h, base_x + ox + w, base_y + oy)
        d.rounded_rectangle(box, radius=12, fill=CARD, outline=colors[i], width=3)
    cx, cy, r = 410, 90, 34
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=COBALT)
    d.line([cx - 16, cy, cx + 16, cy], fill=WHITE, width=6)
    d.line([cx, cy - 16, cx, cy + 16], fill=WHITE, width=6)
    return im


def _file(d, x0, y0, x1, y1, fold=20):
    d.polygon([(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)], fill=CARD, outline=BORDER, width=2)
    d.polygon([(x1 - fold, y0), (x1, y0 + fold), (x1 - fold, y0 + fold)], fill=BORDER)


def step6():
    """원본은 그대로 두고, 사본 파일에 칼선 레이어를 만들어 저장(2026-10-20)."""
    im, d = _canvas()
    _file(d, 50, 60, 160, 200)
    _label(d, 105, 112, "원본", fill=GRAY, size=18)
    _blob(d, 105, 155, 30, 22, (150, 200, 235, 255))
    _arrow(d, 180, 130, 250, 130, GRAY, width=5, head=14)
    _file(d, 270, 40, 510, 220)
    _label(d, 330, 64, "사본", fill=GRAY, size=16)
    rows = [("칼선", CUT, True), ("도안", (150, 200, 235, 255), False), ("화이트레이어", (200, 200, 205, 255), False)]
    for k, (name, col, line) in enumerate(rows):
        y = 92 + k * 40
        d.rounded_rectangle([290, y, 490, y + 32], radius=8, fill=CARD, outline=BORDER, width=2)
        if line:
            d.ellipse([302, y + 6, 322, y + 26], outline=col, width=3)
        else:
            d.rounded_rectangle([302, y + 6, 322, y + 26], radius=4, fill=col)
        _label_fit(d, 400, y + 16, name, fill=CUT if line else GRAY, size=16, max_width=150)
    return im


STEPS = [step1, step2, step3, step4, step5, step6]


def make_tutorial_slides(out_dir=OUT_DIR):
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for i, fn in enumerate(STEPS, start=1):
        im = fn()
        path = os.path.join(out_dir, f"step{i}.png")
        im.save(path)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for p in make_tutorial_slides():
        print(f"wrote {p}")
