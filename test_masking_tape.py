"""
Non-interactive smoke test for the new "MASKING_TAPE"(키스컷 마스킹테이프)
job_type (2026-09-08(9차) 피드백: "칼선 종류에 키스컷 마스킹 테이프도
추가해", "연속된 롤(시트) 전체를 고려해야 함").

Real "키스컷 마테 좋은예/나쁜예" 참고 파일을 스크래치패드에서만 직접 열어
확인한 결과, 마스킹테이프 롤은 (1) 개별 모티프 각각의 촘촘하고 일정한
키스컷(유테/LINE_ART와 완전히 동일한 방식) + (2) 롤 전체 폭을 정의하는,
모티프와는 별개인 세이프티/칼선/블리딩 3단 테두리로 구성되어 있었다. 이
테스트는 그 두 가지 동작을 검증한다:

  1) job_type="MASKING_TAPE"로 한 요소를 선택해 생성하면, job_type=
     "LINE_ART"로 똑같은 선택을 생성했을 때와 완전히 같은 기하(polygon)가
     나온다 -- "개별 모티프는 유테와 동일하게 처리한다"는 설계를 그대로
     검증.
  2) "롤 전체 테두리 추가" 버튼의 실제 로직(_run_add_roll_border)이 -- 롤
     안의 인쇄 내용 경계(content bbox)를 사각형(RECTANGLE, GrabCut
     미사용)으로 잡아 safety/cut/bleed 3단을 만들고, 이미지 전체 경계
     안으로 클리핑되는지.
  3) 위 두 가지를 같은 파일에 함께 누적했을 때(개별 모티프 + 롤 테두리)
     combine_results가 문제없이 하나로 합쳐지는지.

표준 규칙("실제 도안 이미지로 칼선 품질을 판단하지 않는다")에 따라, 이
테스트는 test_domusong_cell_clip.py/test_fidelity.py와 같은 패턴으로 순수
합성(알려진 기하) 이미지만 사용한다 -- 실제 고객 파일은 전혀 쓰지 않고,
이 테스트는 "알고리즘이 올바르게 배선되어 있는가"만 확인하는 것이지 실제
도안에 대한 칼선 품질 판단이 아니다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CUTLINE_LICENSE_DEV_BYPASS", "1")

from PIL import Image, ImageDraw

from gui.app import CutLineApp
from core.image_style import ImageStyle, _measure_content_bbox_px
from core.cutline_core import OffsetSpec, mm_to_px

SCRATCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def check(label, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def _make_motif_sheet(path):
    """작은 모티프 하나가 그려진 합성 시트 -- 순수 알려진 기하(별 모양 대신
    간단한 채색 사각형)로, 순전히 배선/기하 확인용."""
    img = Image.new("RGB", (300, 200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 50, 180, 140], fill=(40, 160, 90))
    img.save(path)


def _make_roll_strip(path):
    """"연속된 롤(시트) 전체"를 흉내낸 길고 얇은 합성 시트 -- 실제 참고
    파일처럼 인쇄 내용(모티프 3개)이 캔버스 전체가 아니라 그 안의 한 영역
    (알려진 content bbox)에만 있도록 만든다. 캔버스 여백을 넉넉히 둬서
    (offset 오프셋을 적용해도 이미지 경계에 걸리지 않게) -- 경계
    클리핑(clip_bounds) 자체는 이미 test_domusong_cell_clip.py가 같은
    메커니즘(bounds_px)으로 확인하고 있으므로, 이 테스트는 "content bbox
    -> RECTANGLE -> 3단 오프셋"이라는 새 배선 자체의 기하가 정확한지에
    집중한다."""
    W, H = 2050, 300
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # 인쇄 내용(모티프 3개)은 x=100..1900, y=100..200 안에만 존재 (알려진
    # content bbox) -- 나머지는 순백 배경.
    d.rectangle([100, 100, 500, 200], fill=(200, 80, 80))
    d.rectangle([700, 100, 1200, 200], fill=(80, 120, 200))
    d.rectangle([1500, 100, 1900, 200], fill=(90, 170, 90))
    img.save(path)
    return (100, 100, 1900, 200)  # known content bbox for assertions


def test_masking_tape_per_motif_matches_line_art():
    os.makedirs(SCRATCH_DIR, exist_ok=True)
    sheet_path = os.path.join(SCRATCH_DIR, "_mt_motif_sheet.png")
    _make_motif_sheet(sheet_path)

    app = CutLineApp()
    app.withdraw()
    app.dpi.set(150.0)
    app.input_path.set(sheet_path)
    app.is_vector.set(False)
    selection = (60 - 15, 50 - 15, 180 + 15, 140 + 15)

    app._selection_px = selection
    app.job_type.set("LINE_ART")
    line_art_item = app._generate_one_item(sheet_path)

    app._selection_px = selection
    app.job_type.set("MASKING_TAPE")
    masking_tape_item = app._generate_one_item(sheet_path)

    check(
        "마스킹테이프 개별 모티프도 유테처럼 'cut' 단 하나만 생성",
        set(masking_tape_item.offsets.keys()) == {"cut"} == set(line_art_item.offsets.keys()),
    )
    check(
        "마스킹테이프 개별 모티프 기하가 유테와 완전히 동일 (동일한 margin_mm/스타일 적용)",
        masking_tape_item.offsets["cut"].equals_exact(line_art_item.offsets["cut"], tolerance=1e-6)
        if hasattr(masking_tape_item.offsets["cut"], "equals_exact")
        else masking_tape_item.offsets["cut"].symmetric_difference(line_art_item.offsets["cut"]).area < 1e-6,
    )

    # job_type 매핑이 실제로 ImageStyle.LINE_ART를 쓰는지 소스 레벨로도 재확인
    # (요약: "ImageStyle에는 새 항목을 추가하지 않고 여기서 LINE_ART로 매핑").
    check(
        "ImageStyle enum에는 여전히 LINE_ART/BORDERLESS 둘만 존재 (마스킹테이프용 새 항목 없음)",
        {m.name for m in ImageStyle} == {"LINE_ART", "BORDERLESS"},
    )
    print("[OK] MASKING_TAPE 개별 모티프는 LINE_ART(유테)와 기하까지 동일")


def test_roll_border_content_bbox_rectangle_clipped_to_image():
    os.makedirs(SCRATCH_DIR, exist_ok=True)
    strip_path = os.path.join(SCRATCH_DIR, "_mt_roll_strip.png")
    content_bbox = _make_roll_strip(strip_path)

    app = CutLineApp()
    app.withdraw()
    app.dpi.set(150.0)
    app.input_path.set(strip_path)
    app.is_vector.set(False)
    app.job_type.set("MASKING_TAPE")
    # safety_mm을 core.cutline_core.MIN_GAP_MM(2.0mm) 이상으로 둬서
    # enforce_minimum_gap의 자동 보정이 끼지 않게 하고, 기대값을 offset_mm
    # 그대로(mm_to_px 변환만)로 단순하게 계산할 수 있게 함.
    app.safety_mm.set(2.0)
    app.cut_mm.set(4.0)
    app.bleed_mm.set(6.0)

    with Image.open(strip_path) as im:
        W, H = im.size

    # _on_add_roll_border는 스레드를 띄우고 _validate_inputs()의 확인
    # 다이얼로그를 탈 수 있으므로, 여기서는 실제 버튼 핸들러가 호출하는
    # 핵심 로직(_run_add_roll_border)을 헤드리스 테스트답게 직접 호출한다
    # (test_accumulate.py가 _run_load_ai를 직접 호출하는 것과 동일한 패턴).
    app._on_reset_accumulation()
    app._run_add_roll_border(strip_path)
    app.update()  # self.after(0, self._show_preview, ...) 콜백 처리

    check("롤 테두리 추가 후 누적 항목이 1개 생김", len(app._accumulated) == 1)
    item = app._accumulated[0]
    check(
        "롤 테두리는 safety/cut/bleed 3단 모두 생성",
        set(item.offsets.keys()) == {"safety", "cut", "bleed"},
    )

    # _run_add_roll_border는 이 파일이 만든 3개의 채색 사각형 "인쇄 내용"의
    # 경계를 core.image_style._measure_content_bbox_px로 직접 재서 쓴다 --
    # 그 함수의 정확한 픽셀 인덱싱 관례(포함/제외 경계 등)까지 이 테스트가
    # 다시 가정하지 않도록, 기대값도 같은 함수를 직접 호출해 구한다(이미
    # 알려진 합성 사각형이므로 여전히 순수 기하 검증). content_bbox(고정된
    # 사각형 좌표로부터 계산한 값)와 크게 다르면 안 됨을 우선 확인.
    measured_bbox = _measure_content_bbox_px(strip_path, (0, 0, W, H))
    check(
        f"측정된 content bbox({measured_bbox})가 의도한 합성 사각형 경계({content_bbox})와 근접",
        measured_bbox is not None and all(abs(a - b) <= 2.0 for a, b in zip(measured_bbox, content_bbox)),
    )
    x0, y0, x1, y1 = measured_bbox
    dpi = app.dpi.get()
    offset_mm_adj, _notes = OffsetSpec(safety_mm=2.0, cut_mm=4.0, bleed_mm=6.0).enforce_minimum_gap()

    # RECTANGLE + use_grabcut=False라 각 단은 content_bbox 그대로를
    # 각자의 offset_mm(px 환산)만큼 바깥으로 buffer한 사각형이어야 한다
    # (core.cutline_core.compute_offsets와 완전히 동일한 계산으로 기대값을
    # 직접 산출 -- "이미 알려진 정답과 비교"하는 순수 기하 검증).
    for name, this_mm in (
        ("safety", offset_mm_adj.safety_mm),
        ("cut", offset_mm_adj.cut_mm),
        ("bleed", offset_mm_adj.bleed_mm),
    ):
        pad = mm_to_px(this_mm, dpi)
        exp = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
        minx, miny, maxx, maxy = item.offsets[name].bounds
        check(
            f"'{name}' 단이 content bbox({content_bbox})를 {this_mm}mm({pad:.1f}px) 바깥으로 "
            f"buffer한 사각형과 일치 (expected={tuple(round(v, 1) for v in exp)}, "
            f"got=({minx:.1f},{miny:.1f},{maxx:.1f},{maxy:.1f}))",
            abs(minx - exp[0]) < 1.0 and abs(miny - exp[1]) < 1.0
            and abs(maxx - exp[2]) < 1.0 and abs(maxy - exp[3]) < 1.0,
        )

    # 이미지 전체 경계 밖으로는 절대 못 나가야 함 (bounds_px=(0,0,W,H) 클리핑 --
    # 이 클리핑 메커니즘 자체는 test_domusong_cell_clip.py가 이미 자세히
    # 검증하므로, 여기서는 "혹시라도 벗어나지 않는지"만 한 번 더 확인).
    for name, mp in item.offsets.items():
        nminx, nminy, nmaxx, nmaxy = mp.bounds
        check(
            f"{name} 단이 이미지 캔버스({W}x{H}) 밖으로 나가지 않음 "
            f"(got=({nminx:.1f},{nminy:.1f},{nmaxx:.1f},{nmaxy:.1f}))",
            nminx >= -0.5 and nminy >= -0.5 and nmaxx <= W + 0.5 and nmaxy <= H + 0.5,
        )

    print("[OK] 롤 전체 테두리: content bbox 기준 RECTANGLE 3단, 이미지 경계로 클리핑 확인")


def test_motif_plus_roll_border_accumulate_together():
    """실제 사용 흐름: 같은 롤 파일에서 모티프 하나(유테와 동일 처리)를
    선택해 추가한 뒤, "롤 전체 테두리 추가"까지 같은 누적에 더했을 때
    combine_results가 정상적으로 하나로 합쳐지는지."""
    strip_path = os.path.join(SCRATCH_DIR, "_mt_roll_strip.png")
    check("이전 테스트에서 만든 롤 스트립 픽스처가 존재", os.path.isfile(strip_path))

    app = CutLineApp()
    app.withdraw()
    app.dpi.set(150.0)
    app.input_path.set(strip_path)
    app.is_vector.set(False)
    app.job_type.set("MASKING_TAPE")
    app.safety_mm.set(2.0)
    app.cut_mm.set(4.0)
    app.bleed_mm.set(6.0)
    app._on_reset_accumulation()

    # 모티프 하나 (첫 번째 사각형 근처를 선택)
    app._selection_px = (100 - 5, 100 - 5, 500 + 5, 200 + 5)
    motif_item = app._generate_one_item(strip_path)
    check("모티프 항목은 'cut' 단 하나만 가짐 (유테 동일)", set(motif_item.offsets.keys()) == {"cut"})
    app._accumulated.append(motif_item)

    # 롤 전체 테두리
    app._run_add_roll_border(strip_path)
    app.update()
    check("모티프 + 롤 테두리 = 누적 항목 2개", len(app._accumulated) == 2)

    from core.accumulate import combine_results

    combined = combine_results(app._accumulated)
    check(
        "combine_results가 모티프(cut만)와 롤 테두리(safety/cut/bleed)를 문제없이 합침",
        set(combined.offsets.keys()) == {"safety", "cut", "bleed"},
    )
    check("합쳐진 결과의 'cut' 단이 비어있지 않음", not combined.offsets["cut"].is_empty)

    print("[OK] 모티프(유테 방식) + 롤 전체 테두리가 같은 파일에서 정상적으로 함께 누적됨")


def main():
    test_masking_tape_per_motif_matches_line_art()
    test_roll_border_content_bbox_rectangle_clipped_to_image()
    test_motif_plus_roll_border_accumulate_together()
    print("\nAll MASKING_TAPE checks passed.")


if __name__ == "__main__":
    main()
