import sys
sys.path.insert(0, "/root/cutline_studio")

import numpy as np
from PIL import Image

from core.cutline_core import generate_cutlines, OffsetSpec, load_raster_design, mm_to_px
from core.svg_export import export_svg
from core.preview import render_preview
from core.risk_analysis import analyze_design_detail_risk, analyze_cutline_bridge_risk

PATH = "/root/cutline_studio/samples/thin_test.png"
DPI = 150

offset_mm = OffsetSpec(safety_mm=1.0, cut_mm=2.0, bleed_mm=3.5)

result = generate_cutlines(image_path=PATH, is_vector=False, dpi=DPI, offset_mm=offset_mm)
print("design shapes:", len(result.design.geoms))

# 1) detail-loss risk on the ORIGINAL artwork (is there a whisker thinner than we can safely offset?)
detail_risk = analyze_design_detail_risk(result.design, result.width_px, result.height_px, DPI, min_safe_radius_mm=1.0)
print(f"[artwork] min local feature radius: {detail_risk.min_feature_radius_mm:.2f}mm  risky_px={detail_risk.risk_pixel_count}  ok={detail_risk.ok}")

# 2) bridge/tear risk on the FINAL cut-line region
cut_geom = result.offsets["cut"]
bridge_risk = analyze_cutline_bridge_risk(cut_geom, result.width_px + 200, result.height_px + 200, DPI, min_safe_radius_mm=0.4)
print(f"[cutline]  min local feature radius: {bridge_risk.min_feature_radius_mm:.2f}mm  risky_px={bridge_risk.risk_pixel_count}  ok={bridge_risk.ok}")

# Render a preview with risk zones overlaid in yellow
preview_path = "/root/cutline_studio/output/thin_test_preview.png"
render_preview(result, preview_path, original_image_path=PATH)

base = Image.open(preview_path).convert("RGBA")
# overlay artwork detail-risk mask (offset by the same margin_px=40 used in render_preview)
margin = 40
overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
risk_rgba = np.zeros((*detail_risk.risk_mask.shape, 4), dtype=np.uint8)
risk_rgba[detail_risk.risk_mask > 0] = [255, 210, 0, 160]
risk_img = Image.fromarray(risk_rgba, mode="RGBA")
overlay.paste(risk_img, (margin, margin), risk_img)
combined = Image.alpha_composite(base, overlay)
combined.convert("RGB").save("/root/cutline_studio/output/thin_test_risk_overlay.png")
print("wrote output/thin_test_risk_overlay.png")

export_svg(result, "/root/cutline_studio/output/thin_test_cutlines.svg")
