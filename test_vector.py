import sys
sys.path.insert(0, "/root/cutline_studio")

from core.cutline_core import generate_cutlines, OffsetSpec
from core.svg_export import export_svg

offset_mm = OffsetSpec(safety_mm=1.0, cut_mm=2.5, bleed_mm=4.5)

result = generate_cutlines(
    image_path="/root/cutline_studio/samples/vector_sample.svg",
    is_vector=True,
    dpi=200,
    offset_mm=offset_mm,
)
print("design shapes:", len(result.design.geoms))
for k, v in result.offsets.items():
    n = 1 if v.geom_type == "Polygon" else len(v.geoms)
    print(f"  {k}: {n} region(s)")

export_svg(result, "/root/cutline_studio/output/vector_cutlines.svg")
print("wrote output/vector_cutlines.svg")
