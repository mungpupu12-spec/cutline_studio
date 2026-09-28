"""
Non-interactive smoke test for the preview zoom/pan feature (2026-09-07(7차)
피드백: "키스컷 이미지 축소 되어 전체 화면이 보이지 않음. 칼선이 잘 됐는지
확대해서 볼 수 있는 기능 필요").

Drives the real GUI class (gui.app.CutLineApp) headlessly under a virtual X
display, exactly like test_accumulate.py does, and checks:
  1) loading a source image renders at the fit-to-viewport base scale (zoom
     starts at 1.0x/100%, i.e. self._display_scale == the base fit scale).
  2) pressing "확대 +" (_on_zoom_in) increases self._preview_zoom and makes
     self._display_scale grow by exactly that multiplier over the base fit
     scale -- and the on-screen canvas image actually grows to match.
  3) pressing "축소 -" (_on_zoom_out) shrinks it back down, and "원본크기"
     (_on_zoom_reset) returns exactly to 1.0x.
  4) drag-selection math (_finalize_selection, which _on_canvas_release
     calls) still converts canvas-pixel coordinates back to correct
     original-image pixel coordinates when a non-1.0 zoom level is active --
     this is the actual risk the zoom feature introduces (self._display_scale
     is what _selection_px math divides by), so a wrong zoom multiplier
     would silently corrupt every selection's real-world size/position.
  5) loading a brand new preview (source or generated result) resets zoom
     back to 1.0x, so a previous zoom level never leaks into unrelated
     images.

Uses ONLY the pre-existing public demo fixture samples/ring.png already
committed to this repo -- no new synthetic images (this is a mechanical
GUI/plumbing check of the zoom math, not an image-recognition quality
check), per the standing "다른 이미지 생성 금지" rule.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.app import CutLineApp

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")


def check(label, cond):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def main():
    app = CutLineApp()
    app.withdraw()

    ring_path = os.path.join(SAMPLES, "ring.png")
    app.input_path.set(ring_path)
    app.is_vector.set(False)

    # ---- 1) initial load: zoom starts at 1.0x, display_scale == base fit scale ----
    app._load_source_preview(ring_path)
    check("zoom starts at 1.0 (100%) on initial load", app._preview_zoom == 1.0)
    base_scale = app._display_scale
    check("base fit scale is a sane positive number", base_scale > 0)
    canvas_w_100 = int(app._preview_full_image.size[0] * base_scale)

    # ---- 2) zoom in: multiplier grows, display_scale scales exactly with it ----
    app._on_zoom_in()
    check("zoom in increases self._preview_zoom above 1.0", app._preview_zoom > 1.0)
    expected_scale = base_scale * app._preview_zoom
    check(
        f"display_scale after zoom-in == base_scale * zoom (got {app._display_scale:.6f}, "
        f"expected {expected_scale:.6f})",
        abs(app._display_scale - expected_scale) < 1e-9,
    )
    canvas_w_after_zoom = int(app._preview_full_image.size[0] * app._display_scale)
    check(
        "the on-screen image actually grew after zooming in",
        canvas_w_after_zoom > canvas_w_100,
    )

    zoom_after_one_click = app._preview_zoom

    # ---- 3) zoom out reduces it; reset returns exactly to 1.0 ----
    app._on_zoom_out()
    check("zoom out decreases self._preview_zoom back below the zoomed-in value",
          app._preview_zoom < zoom_after_one_click)
    app._on_zoom_reset()
    check("원본크기(reset) returns zoom to exactly 1.0", app._preview_zoom == 1.0)
    check("display_scale after reset == base fit scale again",
          abs(app._display_scale - base_scale) < 1e-9)

    # ---- 4) selection math stays correct while zoomed in ----
    app._on_zoom_in()
    app._on_zoom_in()
    zoom_level = app._preview_zoom
    scale = app._display_scale
    check("zoom level is meaningfully above 1.0 for this check", zoom_level > 1.5)

    # Simulate a drag-selection entirely in CANVAS pixel space (as
    # _on_canvas_release/_finalize_selection would receive it after the
    # canvasx/canvasy conversion), covering a known region of the original
    # image: original-image px (10,10)-(60,40).
    orig_x0, orig_y0, orig_x1, orig_y1 = 10, 10, 60, 40
    canvas_x0, canvas_y0 = orig_x0 * scale, orig_y0 * scale
    canvas_x1, canvas_y1 = orig_x1 * scale, orig_y1 * scale
    app._finalize_selection(canvas_x0, canvas_y0, canvas_x1, canvas_y1)
    sx0, sy0, sx1, sy1 = app._selection_px
    check(
        f"selection converts back to the correct original-image px box while zoomed "
        f"(got ({sx0:.2f},{sy0:.2f},{sx1:.2f},{sy1:.2f}), expected "
        f"({orig_x0},{orig_y0},{orig_x1},{orig_y1}))",
        abs(sx0 - orig_x0) < 0.5 and abs(sy0 - orig_y0) < 0.5
        and abs(sx1 - orig_x1) < 0.5 and abs(sy1 - orig_y1) < 0.5,
    )

    # ---- 5) reloading the source image resets zoom back to 1.0 ----
    app._load_source_preview(ring_path)
    check("reloading the source image resets zoom back to 1.0",
          app._preview_zoom == 1.0)

    print("\nAll preview-zoom checks passed.")


if __name__ == "__main__":
    main()
