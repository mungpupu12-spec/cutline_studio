import sys
sys.path.insert(0, "/root/cutline_studio")

from core.cutline_core import generate_cutlines, OffsetSpec
from core.svg_export import export_svg
from core.preview import render_preview

DPI = 150  # sample images are drawn at ~150px per inch of a small mock scene

offset_mm = OffsetSpec(safety_mm=1.0, cut_mm=2.5, bleed_mm=4.5)

for name, path in [("scene", "/root/cutline_studio/samples/scene.png"),
                    ("ring", "/root/cutline_studio/samples/ring.png")]:
    print(f"--- {name} ---")
    result = generate_cutlines(
        image_path=path,
        is_vector=False,
        dpi=DPI,
        offset_mm=offset_mm,
    )
    print("design shapes:", len(result.design.geoms))
    for k, v in result.offsets.items():
        n = 1 if v.geom_type == "Polygon" else len(v.geoms)
        print(f"  {k}: {n} merged region(s)")

    svg_path = f"/root/cutline_studio/output/{name}_cutlines.svg"
    export_svg(result, svg_path)
    print("  wrote", svg_path)

    png_path = f"/root/cutline_studio/output/{name}_preview.png"
    render_preview(result, png_path, original_image_path=path)
    print("  wrote", png_path)

print("\nAll good.")
