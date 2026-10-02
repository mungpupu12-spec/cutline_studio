"""2026-09-30 베타 코드 자동 발급(클라이언트 쪽): 서버 응답에 따라 키를 저장하고 결과를 알린다.
서버 호출은 가짜 응답으로 바꿔 네트워크 없이 확인한다."""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import license_client as lc


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


def _setup(tmp_path, monkeypatch, resp):
    monkeypatch.setattr(lc, "_cache_dir", lambda: str(tmp_path))
    sent = {}

    def fake_post(path, body, timeout=None):
        sent["path"], sent["body"] = path, body
        return resp

    monkeypatch.setattr(lc, "_post", fake_post)
    return sent


def test_beta_code_success_saves_issued_key(tmp_path, monkeypatch):
    token = base64.urlsafe_b64encode(json.dumps({"a": 1}).encode()).decode() + "." + base64.urlsafe_b64encode(b"x").decode()
    sent = _setup(tmp_path, monkeypatch, _Resp(200, {"ok": True, "license_key": "CLS-PERS-AAAAA-BBBBB-CCCCC", "token": token}))
    r = lc.claim_beta("  beta-2026 ")
    assert r.ok and sent["path"] == "/beta/claim" and sent["body"]["invite_code"] == "beta-2026"
    assert sent["body"]["device_fingerprint"] == lc.get_device_fingerprint()
    assert lc.get_cached_license_key() == "CLS-PERS-AAAAA-BBBBB-CCCCC"


def test_wrong_code_and_full_are_reported_and_nothing_saved(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, _Resp(200, {"ok": False, "reason": "invalid_code", "message": "베타 코드가 올바르지 않습니다."}))
    r = lc.claim_beta("nope")
    assert not r.ok and r.state == "invalid_code" and lc.get_cached_license_key() == ""
    _setup(tmp_path, monkeypatch, _Resp(200, {"ok": False, "reason": "beta_full", "message": "모집 인원이 다 찼습니다."}))
    assert lc.claim_beta("x").state == "beta_full"
    _setup(tmp_path, monkeypatch, _Resp(404, {}))
    assert lc.claim_beta("x").state == "beta_unavailable"
    assert lc.claim_beta("   ").state == "invalid_code"


def test_license_key_vs_beta_code_input():
    assert lc.looks_like_license_key(" cls-pers-aaaaa-bbbbb-ccccc")
    assert not lc.looks_like_license_key("BETA2026")
