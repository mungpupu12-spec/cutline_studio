"""
CLI: "이 파일(업체)은 실제로 칼선 여백을 몇 mm로 그렸는지" 한 번에 확인.

실제로 완성된 .ai(PDF 호환) 파일 하나를 가리키면, 그 파일의 칼선레이어에
이미 그려진 모든 칼선 각각에 대해 -- 실제 도안 가장자리로부터 몇 mm
떨어져 있는지(중앙값을 대표값으로), 그리고 무테/유테 중 어느 쪽에 더
가까운 모양인지 -- 보고합니다. 업체마다 요구하는 여백이 달라서 파일마다
손으로 하나씩 확인해야 했던 작업을 대신합니다.

사용법:
    python inspect_cutline_margins.py "경로/파일.ai" [--layer 칼선레이어] [--top N]

주의: 이 스크립트는 파일을 읽기만 합니다 (수정하지 않음). 그래도 원본이
아니라 사본을 가리키는 것을 권장합니다 -- 열려 있는 상태에서 다른 도구가
저장을 시도하는 등의 우연한 충돌을 피하기 위해서입니다.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.ai_cutline_reader import load_real_cutlines
from core.margin_inspector import measure_margin_from_image
from core.style_classify import classify_style
from core.segmentation import segment_design_in_region

# 2026-09-09 "교재" 폴더의 새 실제 파일들로 확인: 작가님의 실제 파일들이
# 전부 "칼선레이어"라는 이름을 쓰는 게 아니라, 일부는 그냥 "칼선"이라는
# 짧은 이름을 쓰기도 함(같은 실제 층위, 이름만 다름) -- 이 스크립트는 사람이
# 직접 실행하는 진단용 CLI라서(실시간 칼선 생성 파이프라인과는 무관, 그
# 파이프라인은 작가의 실제 레이어 이름을 아예 읽지 않음), 매번 --layer를
# 손으로 다시 넣지 않아도 되도록 자주 쓰이는 이름들을 순서대로 시도해본다.
# 그래도 안 되면 마지막엔 원래 지정한(또는 기본) 이름으로 낸 에러를 그대로
# 보여준다 -- 조용히 다른 레이어를 골랐다고 착각하게 두지 않기 위해서다.
COMMON_CUTLINE_LAYER_NAMES = ["칼선레이어", "칼선", "재단선", "cutline", "Cutline"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ai_path", help="실제 .ai(PDF 호환) 파일 경로"
    )
    parser.add_argument(
        "--layer", default=None,
        help="칼선이 들어있는 레이어 이름 (지정 안 하면 흔한 이름들을 순서대로 시도: "
        + ", ".join(COMMON_CUTLINE_LAYER_NAMES),
    )
    parser.add_argument("--top", type=int, default=20, help="큰 도형부터 최대 몇 개까지 보고할지 (기본 20)")
    parser.add_argument("--min-area-px", type=float, default=2000.0, help="이보다 작은 조각(점/노이즈)은 건너뜀")
    args = parser.parse_args()

    candidate_names = [args.layer] if args.layer else COMMON_CUTLINE_LAYER_NAMES

    with tempfile.TemporaryDirectory() as tmp:
        print_img = os.path.join(tmp, "print.png")
        print(f"'{args.ai_path}' 여는 중...")
        rc = None
        last_error = None
        for name in candidate_names:
            try:
                rc = load_real_cutlines(args.ai_path, print_img, layer_name=name)
                if name != candidate_names[0]:
                    print(f"('{candidate_names[0]}' 레이어는 없어서, '{name}' 레이어를 대신 찾음)")
                break
            except Exception as e:  # noqa: BLE001
                last_error = e
                continue
        if rc is None:
            tried = ", ".join(f"'{n}'" for n in candidate_names)
            raise ValueError(
                f"{args.ai_path}: 다음 레이어 이름을 전부 시도했지만 칼선을 찾지 못했습니다: "
                f"{tried} (--layer로 실제 이름을 직접 지정해 보세요). 마지막 시도의 원래 오류: {last_error}"
            )
        print(
            f"실제 칼선 {len(rc.cutlines_px)}개 발견 "
            f"(이미지 {rc.image_w_px}x{rc.image_h_px}px, 유효 DPI {rc.dpi:.1f})\n"
        )

        shapes = [p for p in rc.cutlines_px if p.area >= args.min_area_px]
        shapes.sort(key=lambda p: -p.area)

        print(f"{'번호':>4}  {'가로x세로(mm)':>16}  {'판정':>6}  {'중앙값':>8}  {'평균':>8}  {'최소':>7}  {'최대':>7}  {'편차':>7}")
        print("-" * 78)
        for i, shape in enumerate(shapes[: args.top]):
            try:
                stats = measure_margin_from_image(print_img, shape, rc.dpi)
            except Exception as e:
                print(f"{i+1:>4}  (측정 실패: {e})")
                continue

            x0, y0, x1, y1 = shape.bounds
            from core.cutline_core import px_to_mm

            w_mm = px_to_mm(x1 - x0, rc.dpi)
            h_mm = px_to_mm(y1 - y0, rc.dpi)

            try:
                content = segment_design_in_region(print_img, tuple(shape.bounds), margin_px=40)
                verdict = classify_style(content).style.value
            except Exception:
                verdict = "?"

            print(
                f"{i+1:>4}  {w_mm:>7.1f}x{h_mm:<7.1f}  {verdict:>6}  "
                f"{stats.median_mm:>7.2f}mm  {stats.mean_mm:>7.2f}mm  "
                f"{stats.min_mm:>6.2f}mm  {stats.max_mm:>6.2f}mm  {stats.std_mm:>6.2f}mm"
            )

        print(
            "\n(중앙값을 대표 여백으로 보세요 -- 평균은 GrabCut이 일부만 잘못 잡은 "
            "지점 때문에 크게 튈 수 있습니다. '편차'가 크면 그 도형은 결과를 곧이곧대로 "
            "믿기보다 직접 눈으로 한 번 더 확인하는 걸 권장합니다.)"
        )


if __name__ == "__main__":
    main()
