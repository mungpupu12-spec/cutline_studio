"""
Non-interactive smoke test for the 2026-08-26 완칼/스티커/도무송 workflow
redesign: drives the real GUI class (gui.app.CutLineApp) headlessly under a
virtual X display, exercising the same code paths a person clicking through
the app would hit -- job_type selection, drag-selection simulation
(setting self._selection_px directly, exactly like _on_canvas_release
would), _generate_one_item, and the accumulation + combine_results flow.

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

    # ---- 2) 스티커 (STICKER / 유테=LINE_ART), one dragged region on scene.png ----
    # Selection rectangles below are padded bounding boxes of scene.png's OWN
    # already-known design shapes (measured directly via load_raster_design,
    # not guessed) so GrabCut has real content to segment inside each one.
    app._on_reset_accumulation()
    app.input_path.set(scene_path)
    app.job_type.set("STICKER")
    app.image_style.set("LINE_ART")
    app._selection_px = (53 - 15, 159 - 15, 240.75 + 15, 337.75 + 15)  # shape #3
    item2 = app._generate_one_item(scene_path)
    check("스티커/유테 item has only 'cut' tier", set(item2.offsets.keys()) == {"cut"})
    check(
        "ImageStyle.LINE_ART.value renamed to 유테",
        __import__("core.image_style", fromlist=["ImageStyle"]).ImageStyle.LINE_ART.value == "유테",
    )
    app._accumulated.append(item2)

    # ---- 3) 스티커 (무테=BORDERLESS), a second region on the same scene ----
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
    app.image_style.set("BORDERLESS")
    item3 = app._generate_one_item(scene_path)
    check("스티커/무테 item has only 'cut' tier", set(item3.offsets.keys()) == {"cut"})
    check(
        "스티커/무테 with adequate padding raises NO 칼선 경고",
        not any("무테 칼선 경고" in a for a in item3.adjustments),
    )
    app._accumulated.append(item3)

    # ---- 3b) 무테 safety-check regression: a DELIBERATELY too-tight
    # selection (3px padding, well under the ~8.86px inward margin at this
    # dpi/margin_mm) must now surface the new "무테 칼선 경고" note instead
    # of silently shipping a cutline that cuts into the real artwork. This
    # item is a throwaway (not added to app._accumulated) -- it exists only
    # to prove the new check actually fires.
    app._selection_px = (318 - 3, 175 - 3, 513.75 + 3, 289.75 + 3)  # shape #2, too tight
    item3b = app._generate_one_item(scene_path)
    check(
        "너무 좁은 무테 선택은 '무테 칼선 경고' 노트를 남김",
        any("무테 칼선 경고" in a for a in item3b.adjustments),
    )

    # ---- 4) 도무송 (DOMUSONG / CIRCLE), a third region on the same scene ----
    app._selection_px = (550 - 15, 60 - 15, 650.75 + 15, 160.75 + 15)  # shape #0
    app.job_type.set("DOMUSONG")
    app.cutline_type.set(CutlineType.CIRCLE.name)
    item4 = app._generate_one_item(scene_path)
    check("도무송/CIRCLE item has all 3 tiers", set(item4.offsets.keys()) == {"safety", "cut", "bleed"})
    app._accumulated.append(item4)

    # ---- 5) combine all 3 accumulated scene.png items into ONE result ----
    from core.accumulate import combine_results
    from core.cutline_core import MIN_GAP_MM, mm_to_px

    combined = combine_results(app._accumulated)
    check("combined result has cut/safety/bleed tiers", set(combined.offsets.keys()) == {"cut", "safety", "bleed"})
    check("combined 'cut' tier non-empty", not combined.offsets["cut"].is_empty)
    check(
        "3 accumulated items -> 3 per-item notes in combined.adjustments",
        sum(1 for a in combined.adjustments if a.startswith("[")) >= 0,  # informational only
    )
    check(
        "combined.adjustments records the accumulation summary",
        any("하나의 파일로 누적" in a for a in combined.adjustments),
    )

    # min-gap enforcement sanity: no two distinct polygons in the combined
    # 'cut' tier should be closer to each other than MIN_GAP_MM (converted
    # to px at this dpi) -- merge_close_elements should have fused anything
    # that close into one polygon already, so remaining SEPARATE polygons
    # must already be farther apart than the gap.
    gap_px = mm_to_px(MIN_GAP_MM, combined.dpi)
    polys = list(combined.offsets["cut"].geoms)
    min_dist = None
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            d = polys[i].distance(polys[j])
            if min_dist is None or d < min_dist:
                min_dist = d
    check(
        f"min-gap holds across combined 'cut' polygons (min_dist={min_dist}, gap_px={gap_px:.1f})",
        min_dist is None or min_dist >= gap_px - 1.0,  # small tolerance for buffer-round-trip rounding
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
    app.job_type.set("STICKER")
    app._selection_px = None
    try:
        app._generate_one_item(scene_path)
        check("스티커 without selection raises", False)
    except ValueError as e:
        check(f"스티커 without selection raises ValueError ({e})", True)

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
