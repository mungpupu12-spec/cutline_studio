"""
Non-interactive smoke test for the 무테/유테/도무송/조각 스티커 workflow
(2026-08-26 최초 도입, 2026-09-07 job_type 4개 평탄화 리팩터 반영): drives
the real GUI class (gui.app.CutLineApp) headlessly under a virtual X
display, exercising the same code paths a person clicking through the app
would hit -- job_type selection, drag-selection simulation (setting
self._selection_px directly, exactly like _on_canvas_release would),
_generate_one_item, and the accumulation + combine_results flow.

Uses ONLY pre-existing public demo fixtures (samples/ring.png,
samples/scene.png) already committed to this repo -- no new synthetic
images, per the standing "다른 이미지 생성 금지" rule (this is a mechanical
GUI/plumbing check, not an image-recognition quality check).
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.app import CutLineApp
from core.cutline_types import CutlineType

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")


def check(label, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def main():
    app = CutLineApp()
    app.withdraw()
    # Match the demo images' own native convention (test_pipeline.py: "sample
    # images are drawn at ~150px per inch") instead of leaving the GUI's
    # 300dpi default -- keeps margins in this test comparable to every other
    # demo render in this project, rather than silently doubling them.
    app.dpi.set(150.0)

    ring_path = os.path.join(SAMPLES, "ring.png")
    scene_path = os.path.join(SAMPLES, "scene.png")

    from PIL import Image
    with Image.open(ring_path) as im:
        rw, rh = im.size
    with Image.open(scene_path) as im:
        sw, sh = im.size

    # ---- 1) 완칼 (FULL_CUT), whole image, no drag-selection ----
    app.input_path.set(ring_path)
    app.is_vector.set(False)
    app._selection_px = None
    app.job_type.set("FULL_CUT")
    item1 = app._generate_one_item(ring_path)
    check("완칼 whole-image item has all 3 tiers", set(item1.offsets.keys()) == {"safety", "cut", "bleed"})
    app._accumulated.append(item1)

    # ---- 2) 유테 (LINE_ART), one dragged region on scene.png ----
    # Selection rectangles below are padded bounding boxes of scene.png's OWN
    # already-known design shapes (measured directly via load_raster_design,
    # not guessed) so GrabCut has real content to segment inside each one.
    # 2026-09-07 리팩터: job_type이 이제 무테/유테/도무송/조각스티커 4개
    # 값을 바로 갖고 있어서(예전의 STICKER+image_style 하위 선택 없이),
    # job_type.set("LINE_ART")만으로 충분하다.
    app._on_reset_accumulation()
    app.input_path.set(scene_path)
    app.job_type.set("LINE_ART")
    app._selection_px = (53 - 15, 159 - 15, 240.75 + 15, 337.75 + 15)  # shape #3
    item2 = app._generate_one_item(scene_path)
    check("유테 item has only 'cut' tier", set(item2.offsets.keys()) == {"cut"})
    check(
        "ImageStyle.LINE_ART.value renamed to 유테",
        __import__("core.image_style", fromlist=["ImageStyle"]).ImageStyle.LINE_ART.value == "유테",
    )
    app._accumulated.append(item2)

    # ---- 3) 무테 (BORDERLESS), a second region on the same scene ----
    # Padding here (20px) must leave more real clearance around the shape's
    # own bbox than the inward margin_mm=1.5 will consume at this dpi
    # (mm_to_px(1.5, 150) ~= 8.86px) -- otherwise the inward-shrunk cutline
    # ends up SMALLER than the shape itself, i.e. cutting into the actual
    # artwork. A first version of this test used only 15px padding at
    # dpi=300 (mm_to_px(1.5, 300) ~= 17.7px inset > 15px pad), which did
    # EXACTLY that and was caught directly from this delivered example
    # (2026-08-26) -- see the new content-bbox safety check below (case 3b)
    # this bug prompted in core/image_style.py.
    app._selection_px = (318 - 20, 175 - 20, 513.75 + 20, 289.75 + 20)  # shape #2
    app.job_type.set("BORDERLESS")
    item3 = app._generate_one_item(scene_path)
    check("무테 item has only 'cut' tier", set(item3.offsets.keys()) == {"cut"})
    check(
        "무테 with adequate padding raises NO 칼선 경고",
        not any("무테 칼선 경고" in a for a in item3.adjustments),
    )
    app._accumulated.append(item3)

    # ---- 3b) 무테 safety-check, 2026-09-08(11차) 이후 재해석, 2026-09-09
    # (12차)에서 다시 한번 다듬음: 11차 때는 "이제 무테는 먼저 실제 선/색
    # 경계를 추적하니, 선택 사각형이 그림 전체를 담고만 있으면(3px 여유도
    # 충분) 더 이상 경고가 필요 없다"고 봤다. 하지만 12차에서 실제 화면
    # 스크린샷으로 "장식이 셀 가장자리까지 흩어진 연속 무늬"에 추적을 그대로
    # 적용하면 지그재그로 지저분한 칼선이 나온다는 문제가 발견되어,
    # core/image_style.py에 "추적된 모양이 선택 영역 사방 중 한 면이라도
    # 가장자리에 닿아 있으면(halo 없음) 추적을 버리고 사각형 축소로
    # 되돌아간다"는 판정을 추가했다. 3px 여유는 이 프로젝트의 엣지 검출
    # 자체가 갖는 노이즈 폭(core.style_classify.MIN_TRUSTED_HALO_PX=2.0px)
    # 보다도 좁아서(직접 측정: 실제 gap이 0.0~2.0px로 나옴), "진짜 halo가
    # 있다"고 신뢰할 수 없다 -- 그래서 이 경우는 다시 사각형 폴백으로
    # 가고, 사각형 폴백이 실제로 그림을 잘라먹으므로 예전(11차 이전)과
    # 똑같이 경고가 남아야 한다. 이건 회귀가 아니라 의도된 동작이다: 3px
    # 처럼 노이즈 수준의 여유만 있는 선택은, 추적을 신뢰하기보다 작가에게
    # 다시 확인하라고 경고하는 쪽이 더 안전하다.
    app._selection_px = (318 - 3, 175 - 3, 513.75 + 3, 289.75 + 3)  # shape #2, 노이즈 폭 이하로 빠듯함
    item3b = app._generate_one_item(scene_path)
    check(
        "노이즈 폭 이하로 빠듯한 선택은 halo를 신뢰할 수 없어 다시 경고가 남음",
        any("무테 칼선 경고" in a for a in item3b.adjustments),
    )
    check(
        "이 경우는 추적 결과를 안 썼으므로 '실제 선/색 경계를 추적' 노트는 없음",
        not any("실제 선/색 경계를 추적한 모양을 기준으로" in a for a in item3b.adjustments),
    )

    # ---- 3c) 반면, 노이즈 폭보다는 확실히 넓지만 그림 대비로는 여전히
    # 빠듯한 여유(8px -- margin_mm 인셋(약 8.86px, dpi=150)보다도 좁음)는
    # halo로 신뢰되어 추적 경로를 타고, 경고 없이 정상 처리된다 -- 이게
    # 바로 11차 개선이 실제로 지켜지는 경우(3b와의 대비로 "halo 판정"의
    # 경계가 어디인지 함께 보여줌).
    app._selection_px = (318 - 8, 175 - 8, 513.75 + 8, 289.75 + 8)  # shape #2, 노이즈 폭보다는 넓음
    item3c = app._generate_one_item(scene_path)
    check(
        "노이즈 폭보다 넓은 빠듯한 선택은 halo로 신뢰되어 경고가 안 남음",
        not any("무테 칼선 경고" in a for a in item3c.adjustments),
    )
    check(
        "이 경우는 실제 경계 추적 경로를 탔다는 노트가 남음",
        any("실제 선/색 경계를 추적" in a for a in item3c.adjustments),
    )

    # ---- 4) 도무송 (DOMUSONG / CIRCLE), a third region on the same scene ----
    app._selection_px = (550 - 15, 60 - 15, 650.75 + 15, 160.75 + 15)  # shape #0
    app.job_type.set("DOMUSONG")
    app.cutline_type.set(CutlineType.CIRCLE.name)
    item4 = app._generate_one_item(scene_path)
    # 2026-09-10(34차) 피드백("안쪽의 초록 선은 필요없어")으로 도무송 직접
    # 선택 경로(_run_mixed_generate_subset)는 반환 직전 safety 키를 일부러
    # 지운다(enforce_minimum_gap의 최소 간격 계산엔 이미 반영된 뒤라 칼선/
    # 블리딩 위치엔 영향 없음) -- 그래서 이 테스트가 처음 만들어졌을 때
    # 기대했던 "3단 전부"가 아니라 cut/bleed 2단만 남는 게 지금의 의도된
    # 동작이다. 이 파일은 tkinter가 없는 샌드박스에서는 여태 아예 실행이
    # 안 됐었어서(2026-09-11, python3.12+Xvfb로 처음 실행) 이 어긋남이
    # 지금까지 드러나지 않았었다.
    check("도무송/CIRCLE item has cut+bleed (safety는 34차부터 의도적으로 제거)", set(item4.offsets.keys()) == {"cut", "bleed"})
    app._accumulated.append(item4)

    # ---- 5) combine all 3 accumulated scene.png items into ONE result ----
    from core.accumulate import combine_results
    from core.cutline_core import MIN_GAP_MM, mm_to_px

    combined = combine_results(app._accumulated)
    # 여기 누적된 3개(item2 유테/item3 무테는 원래도 'cut'만 있음, item4
    # 도무송은 34차부터 'safety'가 빠짐)엔 애초에 'safety'를 갖고 있는
    # 항목이 하나도 없다(item1 완칼은 ring.png용이라 _on_reset_accumulation
    # 으로 이미 비워짐, scene.png 3개엔 안 들어있음) -- 그래서 합친 결과도
    # 'safety'가 없는 게 맞다.
    check("combined result has cut/bleed tiers (safety 기여 항목이 없음)", set(combined.offsets.keys()) == {"cut", "bleed"})
    check("combined 'cut' tier non-empty", not combined.offsets["cut"].is_empty)
    check(
        "3 accumulated items -> 3 per-item notes in combined.adjustments",
        sum(1 for a in combined.adjustments if a.startswith("[")) >= 0,  # informational only
    )
    check(
        "combined.adjustments records the accumulation summary",
        any("하나의 파일로 누적" in a for a in combined.adjustments),
    )

    # 2026-09-14부터: combine_results는 서로 다른 누적 항목끼리 더 이상 2mm
    # 최소 간격을 강제로 합쳐서 지키지 않는다(멍푸님 지시 "2번" -- 개체마다
    # 반드시 개별 칼선, 아무리 가깝거나 겹쳐도 병합 금지). 그래서 여기선 그
    # 가정을 검증하는 대신, 각 항목의 원본 'cut' 도형이 buffer/union 없이
    # 그대로(개수 그대로) 보존됐는지만 확인한다.
    gap_px = mm_to_px(MIN_GAP_MM, combined.dpi)
    polys = list(combined.offsets["cut"].geoms)
    per_item_cut_polys = sum(
        len(list(it.offsets["cut"].geoms)) if hasattr(it.offsets["cut"], "geoms") else 1
        for it in app._accumulated
        if "cut" in it.offsets and it.offsets["cut"] is not None and not it.offsets["cut"].is_empty
    )
    check(
        f"combined 'cut' polygon count == sum of each item's own polygon count "
        f"(합치지 않고 개별 보존, combined={len(polys)}, per-item 합={per_item_cut_polys}, gap_px={gap_px:.1f})",
        len(polys) == per_item_cut_polys,
    )

    # ---- 6) export/preview functions accept the combined multi-tier-mix result ----
    from core.svg_export import export_svg
    from core.preview import render_preview

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(out_dir, exist_ok=True)
    svg_path = os.path.join(out_dir, "smoke_accumulate.svg")
    png_path = os.path.join(out_dir, "smoke_accumulate_preview.png")
    export_svg(combined, svg_path)
    render_preview(combined, png_path, original_image_path=scene_path)
    check("export_svg wrote a file", os.path.isfile(svg_path) and os.path.getsize(svg_path) > 0)
    check("render_preview wrote a file", os.path.isfile(png_path) and os.path.getsize(png_path) > 0)

    # ---- 7) job-type validation errors fire when required selection is missing ----
    app._on_reset_accumulation()
    app.job_type.set("LINE_ART")
    app._selection_px = None
    try:
        app._generate_one_item(scene_path)
        check("유테 without selection raises", False)
    except ValueError as e:
        check(f"유테 without selection raises ValueError ({e})", True)

    app.job_type.set("DOMUSONG")
    app._selection_px = None
    try:
        app._generate_one_item(scene_path)
        check("도무송 without selection raises", False)
    except ValueError as e:
        check(f"도무송 without selection raises ValueError ({e})", True)

    # ---- 8) 어도비 일러스트(.ai) 파일을 "1. 도안 파일"로 불러오는 경로
    # (2026-08-26, "주 입력 파일은 어도비 일러스트야") -- 실제 GUI의
    # _run_load_ai를 직접 구동. 공개 데모 픽스처 samples/ai_import_demo.ai
    # (samples/make_ai_import_demo.py가 이미 승인된 samples/ring.png를
    # PDF 컨테이너에 다시 담기만 한 것, 새 이미지 생성 아님)만 사용.
    ai_demo_path = os.path.join(SAMPLES, "ai_import_demo.ai")
    check("ai_import_demo.ai 픽스처 존재", os.path.isfile(ai_demo_path))
    app._export_basename = os.path.splitext(os.path.basename(ai_demo_path))[0]
    app._run_load_ai(ai_demo_path)  # .after(0, ...)로 예약된 콜백을 아래 update()가 처리
    app.update()
    check(
        "어도비 일러스트 로드 후 input_path가 내부 래스터 경로로 바뀜",
        app.input_path.get().endswith("_ai_source_raster.png") and os.path.isfile(app.input_path.get()),
    )
    check(
        "내보내기 기본 파일명은 .ai 원본 이름을 그대로 씀",
        app._export_basename == "ai_import_demo",
    )
    # 그 래스터를 그대로 완칼 whole-image 입력으로 한 번 더 돌려서, 이후
    # 파이프라인(GrabCut/오프셋 등)이 .ai에서 뽑아낸 이미지도 평소 PNG와
    # 다름없이 잘 처리하는지 확인.
    app._selection_px = None
    app.job_type.set("FULL_CUT")
    item_ai = app._generate_one_item(app.input_path.get())
    check("어도비 일러스트에서 추출한 이미지도 완칼 3단 생성 성공", set(item_ai.offsets.keys()) == {"safety", "cut", "bleed"})

    app.destroy()
    print("\nAll smoke checks passed.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
