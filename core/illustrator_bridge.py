"""일러스트레이터에서 칼선 레이어를 직접 넣는다(2026-10-20).

왜 필요한가(멍푸 PC의 일러스트레이터 2026에서 실제로 확인): AI 파일은 PDF 내용과 별도로 일러스트레이터 고유
데이터를 함께 담고 있고, 일러스트레이터는 그 고유 데이터만 읽는다. 그래서 AI 사본의 PDF 쪽에 칼선 레이어를 더해도
일러스트레이터에서는 안 보인다. 고유 데이터 연결을 끊으면 칼선은 보이지만 원래 레이어가 모두 "레이어 1" 하나로
합쳐진다. 일러스트레이터가 직접 레이어를 더하고 저장해야 원래 레이어가 그대로 남는다.

방법: 칼선·화이트 좌표를 담은 일러스트레이터 스크립트(.jsx)를 만들어, 윈도우 COM(PowerShell)으로 일러스트레이터에
실행시킨다. 일러스트레이터가 없거나 실행이 실패하면 False -- 부르는 쪽이 PDF로 대신 저장한다.
좌표: 칼선은 PDF 페이지 좌표(pt, 왼쪽 위 원점)로 받아, 첫 대지의 왼쪽 위를 기준으로 일러스트레이터 좌표(위로 +y)로 바꾼다.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from typing import Optional

CUT_LAYER = "칼선"
WHITE_LAYER = "화이트레이어"
ART_LAYER = "도안"

_JSX_COMMON = r"""
#target illustrator
var RES = new File(%(res)s);
function done(msg) { RES.encoding = "UTF-8"; RES.open("w"); RES.write(msg); RES.close(); }
function layerNamed(doc, name) {
    for (var i = 0; i < doc.layers.length; i++) { if (doc.layers[i].name == name) return doc.layers[i]; }
    var L = doc.layers.add(); L.name = name; return L;
}
// 2026-10-27(멍푸: "컷라인 스튜디오에선 칼선이 잘 만들어졌는데 ai 파일 여니까 이중 칼선이야"): 원본에 이미 같은 이름의
// 레이어(예: 작가가 그린 칼선 -- 그림으로 들어 있는 경우도 있음)가 있으면 거기에 더하면 칼선이 두 겹이 된다. 원래
// 레이어는 지우지 않고 이름 뒤에 "(원본)"을 붙여 숨기고 잠근 뒤, 새 레이어에 넣는다. 숨긴 원래 레이어 수를 센다.
var N_HIDDEN = 0;
function freshLayer(doc, name) {
    for (var i = 0; i < doc.layers.length; i++) {
        var L = doc.layers[i];
        if (L.name == name && (L.pageItems.length + L.layers.length) > 0) {
            L.locked = false; L.name = name + "(원본)"; L.visible = false; L.locked = true; N_HIDDEN++;
        }
    }
    return layerNamed(doc, name);
}
function cmyk(c, m, y, k) { var x = new CMYKColor(); x.cyan = c; x.magenta = m; x.yellow = y; x.black = k; return x; }
function toDoc(ab, ring) {
    var out = [];
    for (var i = 0; i < ring.length; i++) out.push([ab[0] + ring[i][0], ab[1] - ring[i][1]]);
    return out;
}
// setEntirePath는 점이 1000개를 넘으면 Illegal Argument(멍푸 PC 일러스트레이터 2026에서 확인: 1000 성공, 2000 실패).
// 앞 1000개만 setEntirePath로 넣고 나머지는 하나씩 더한다.
function setRing(p, pts) {
    var CH = 1000;
    p.setEntirePath(pts.length > CH ? pts.slice(0, CH) : pts);
    for (var i = CH; i < pts.length; i++) {
        var q = p.pathPoints.add();
        q.anchor = pts[i]; q.leftDirection = pts[i]; q.rightDirection = pts[i]; q.pointType = PointType.CORNER;
    }
    p.closed = true;
}
function drawCut(L, ab, polys) {
    var mag = cmyk(0, 100, 0, 0), n = 0;
    for (var i = 0; i < polys.length; i++) for (var j = 0; j < polys[i].length; j++) {
        var p = L.pathItems.add(); setRing(p, toDoc(ab, polys[i][j]));
        p.filled = false; p.stroked = true; p.strokeColor = mag; p.strokeWidth = 0.5; n++;
    }
    return n;
}
function drawWhite(L, ab, polys) {
    var w = cmyk(0, 0, 0, 0), n = 0;
    for (var i = 0; i < polys.length; i++) {
        var cp = L.compoundPathItems.add();
        for (var j = 0; j < polys[i].length; j++) {
            var p = cp.pathItems.add(); setRing(p, toDoc(ab, polys[i][j])); n++;
        }
        for (var k = 0; k < cp.pathItems.length; k++) {
            cp.pathItems[k].filled = true; cp.pathItems[k].fillColor = w; cp.pathItems[k].stroked = false;
        }
    }
    return n;
}
"""

_JSX_ADD_TO_AI = _JSX_COMMON + r"""
try {
    app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
    var D = %(data)s;
    var F = new File(%(path)s);
    for (var d = app.documents.length - 1; d >= 0; d--) {   // 같은 파일이 이미 열려 있으면 저장하지 않고 닫는다
        try { if (app.documents[d].fullName.fsName == F.fsName) app.documents[d].close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {}
    }
    var doc = app.open(F);
    var ab = doc.artboards[0].artboardRect;
    var nw = 0, nc = 0;
    if (D.white.length) {
        var W = freshLayer(doc, D.white_layer); W.locked = false; W.visible = true;
        W.zOrder(ZOrderMethod.SENDTOBACK); nw = drawWhite(W, ab, D.white);
    }
    var C = freshLayer(doc, D.cut_layer); C.locked = false; C.visible = true;
    C.zOrder(ZOrderMethod.BRINGTOFRONT); nc = drawCut(C, ab, D.cut);
    doc.save(); doc.close(SaveOptions.DONOTSAVECHANGES);
    done("OK " + nc + " " + nw + " " + N_HIDDEN);
} catch (e) { done("ERR " + e + " (line " + e.line + ")"); }
"""

_JSX_NEW_PDF = _JSX_COMMON + r"""
try {
    app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
    var D = %(data)s;
    var doc = app.documents.add(DocumentColorSpace.CMYK, D.w_pt, D.h_pt);
    var ab = doc.artboards[0].artboardRect;
    var A = doc.layers[0]; A.name = D.art_layer;
    var pi = A.placedItems.add(); pi.file = new File(%(image)s);
    pi.width = D.w_pt; pi.height = D.h_pt; pi.position = [ab[0], ab[1]]; pi.embed();
    var nw = 0;
    if (D.white.length) {
        var W = layerNamed(doc, D.white_layer); W.zOrder(ZOrderMethod.SENDTOBACK); nw = drawWhite(W, ab, D.white);
    }
    var C = layerNamed(doc, D.cut_layer); C.zOrder(ZOrderMethod.BRINGTOFRONT);
    var nc = drawCut(C, ab, D.cut);
    var o = new PDFSaveOptions(); o.preserveEditability = true; o.acrobatLayers = true;
    doc.saveAs(new File(%(out)s), o); doc.close(SaveOptions.DONOTSAVECHANGES);
    done("OK " + nc + " " + nw);
} catch (e) { done("ERR " + e + " (line " + e.line + ")"); }
"""


# 2026-10-25(멍푸: "칼선만 따로 저장하는 옵션"): 빈 새 문서(원본과 같은 크기)에 '칼선'(+'화이트레이어')만 넣어 AI로 저장
_JSX_NEW_CUT_AI = _JSX_COMMON + r"""
try {
    app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
    var D = %(data)s;
    var doc = app.documents.add(DocumentColorSpace.CMYK, D.w_pt, D.h_pt);
    var ab = doc.artboards[0].artboardRect;
    var first = doc.layers[0];
    var nw = 0;
    if (D.white.length) {
        var W = layerNamed(doc, D.white_layer); nw = drawWhite(W, ab, D.white);
    }
    var C = layerNamed(doc, D.cut_layer); C.zOrder(ZOrderMethod.BRINGTOFRONT);
    var nc = drawCut(C, ab, D.cut);
    if (first.name != D.cut_layer && first.name != D.white_layer && first.pageItems.length == 0) first.remove();
    if (D.white.length) { layerNamed(doc, D.white_layer).zOrder(ZOrderMethod.SENDTOBACK); }
    var o = new IllustratorSaveOptions(); o.pdfCompatible = true;
    doc.saveAs(new File(%(out)s), o); doc.close(SaveOptions.DONOTSAVECHANGES);
    done("OK " + nc + " " + nw);
} catch (e) { done("ERR " + e + " (line " + e.line + ")"); }
"""


def _js_str(s: str) -> str:
    return json.dumps(s.replace("\\", "/"), ensure_ascii=True)


def polys_to_pt(geom, to_pt, simplify_px: float = 0.8) -> list:
    """shapely (Multi)Polygon(픽셀) -> [[고리(pt 좌표 목록), 구멍...], ...]"""
    out = []
    if geom is None or geom.is_empty:
        return out
    for p in (list(geom.geoms) if hasattr(geom, "geoms") else [geom]):
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        if simplify_px:
            p = p.simplify(simplify_px, preserve_topology=True)
        rings = []
        for ring in [p.exterior] + list(p.interiors):
            pts = [[round(a, 3), round(b, 3)] for a, b in (to_pt(x, y) for x, y in list(ring.coords)[:-1])]
            if len(pts) >= 3:
                rings.append(pts)
        if rings:
            out.append(rings)
    return out


def run_jsx(jsx_text: str, timeout: float = 600.0) -> str:
    """일러스트레이터에 스크립트를 실행시키고 결과 문자열("OK ..."/"ERR ...")을 돌려준다. 일러스트레이터가 없으면
    "NOAPP ..."."""
    if os.name != "nt":
        return "NOAPP not windows"
    work = tempfile.mkdtemp(prefix="cutline_ai_")
    res = os.path.join(work, "result.txt")
    jsx = os.path.join(work, "run.jsx")
    with open(jsx, "w", encoding="utf-8-sig") as f:
        f.write(jsx_text.replace("%(res)s", _js_str(res)))
    ps = ("$ErrorActionPreference='Stop'; try { $a = New-Object -ComObject Illustrator.Application; "
          f"[void]$a.DoJavaScriptFile('{jsx}') }} catch {{ Write-Output ('NOAPP ' + $_.Exception.Message) }}")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           capture_output=True, timeout=timeout, creationflags=0x08000000)
        so = r.stdout.decode("mbcs", "replace").strip() if r.stdout else ""
    except Exception as e:  # noqa: BLE001
        return f"NOAPP {e}"
    if os.path.isfile(res):
        with open(res, encoding="utf-8-sig") as f:
            return f.read().strip()
    return so or "ERR no result"


def add_layers_to_ai(ai_copy_path: str, cut_polys_pt: list, white_polys_pt: Optional[list] = None,
                     timeout: float = 600.0) -> str:
    data = {"cut": cut_polys_pt, "white": white_polys_pt or [], "cut_layer": CUT_LAYER, "white_layer": WHITE_LAYER}
    jsx = _JSX_ADD_TO_AI.replace("%(data)s", json.dumps(data)).replace("%(path)s", _js_str(ai_copy_path))
    return run_jsx(jsx, timeout)


def make_layered_pdf(image_path: str, out_pdf: str, w_pt: float, h_pt: float, cut_polys_pt: list,
                     white_polys_pt: Optional[list] = None, timeout: float = 600.0) -> str:
    data = {"cut": cut_polys_pt, "white": white_polys_pt or [], "w_pt": w_pt, "h_pt": h_pt,
            "cut_layer": CUT_LAYER, "white_layer": WHITE_LAYER, "art_layer": ART_LAYER}
    jsx = (_JSX_NEW_PDF.replace("%(data)s", json.dumps(data)).replace("%(image)s", _js_str(image_path))
           .replace("%(out)s", _js_str(out_pdf)))
    return run_jsx(jsx, timeout)


def make_cut_only_ai(out_ai: str, w_pt: float, h_pt: float, cut_polys_pt: list,
                     white_polys_pt: Optional[list] = None, timeout: float = 600.0) -> str:
    """원본과 같은 크기의 새 AI에 칼선(+화이트)만. 결과 문자열("OK ..."/"ERR ..."/"NOAPP ...")."""
    data = {"cut": cut_polys_pt, "white": white_polys_pt or [], "w_pt": w_pt, "h_pt": h_pt,
            "cut_layer": CUT_LAYER, "white_layer": WHITE_LAYER}
    jsx = _JSX_NEW_CUT_AI.replace("%(data)s", json.dumps(data)).replace("%(out)s", _js_str(out_ai))
    return run_jsx(jsx, timeout)
