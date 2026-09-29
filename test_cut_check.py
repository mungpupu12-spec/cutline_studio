"""칼선 겹침·이중 칼선·간격 점검(내보내기 직전 안전 검사) 테스트."""
from shapely.geometry import MultiPolygon, box

from core.cut_check import check_cut_spacing, summarize_cut_spacing

DPI = 300.0
MM = DPI / 25.4


def test_well_spaced_cuts_have_no_issues():
    a = box(0, 0, 100, 100)
    b = box(100 + 3 * MM, 0, 200 + 3 * MM, 100)  # 3mm 간격
    r = check_cut_spacing(MultiPolygon([a, b]), DPI)
    assert r == {"crossing": [], "nested": [], "too_close": []}
    assert summarize_cut_spacing(r) == []


def test_crossing_nested_and_close_are_reported():
    a = box(0, 0, 100, 100)
    crossing = box(80, 80, 180, 180)
    nested = box(30, 30, 50, 50)
    close = box(100 + 1 * MM, 0, 150 + 1 * MM, 60)  # 1mm 간격
    r = check_cut_spacing(MultiPolygon([a, crossing, nested, close]), DPI)
    assert len(r["crossing"]) >= 1
    assert len(r["nested"]) == 1
    assert any(abs(g - 1.0) < 0.05 for _, _, g in r["too_close"])
    text = "\n".join(summarize_cut_spacing(r))
    assert "교차" in text and "이중" in text and "가까운" in text


def test_two_mm_gap_like_real_files_is_accepted():
    """실제 손 칼선의 최소 간격(약 1.98mm)은 경고하지 않는다."""
    a = box(0, 0, 100, 100)
    b = box(100 + 1.98 * MM, 0, 200, 100)
    assert check_cut_spacing(MultiPolygon([a, b]), DPI)["too_close"] == []
