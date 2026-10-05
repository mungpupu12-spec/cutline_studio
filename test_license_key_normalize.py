"""2026-10-05("새로 업데이트한 설치 파일 라이선스 안 된대"): 붙여넣은 라이선스 키 정리(core.license_client.
normalize_license_key) -- 소문자·공백·긴 줄표·보이지 않는 문자·앞뒤 글이 섞여도 서버에는 원래 키 그대로 보낸다."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import license_client as lic  # noqa: E402


def _make_key(tag="PERS"):
    body14 = "ABCDE23456FGHJ"
    body = body14 + lic._key_checksum_char(body14)
    return f"CLS-{tag}-{body[:5]}-{body[5:10]}-{body[10:]}"


def test_normalize_variants_give_same_key():
    k = _make_key()
    assert lic._looks_like_valid_key(k)
    variants = [
        k, k.lower(), f"  {k}\n", k.replace("-", "–"), k.replace("-", " - "),
        "​" + k + "﻿", f"라이선스 키: {k}", f"키는 {k.lower()} 입니다.", k.replace("-", "－"),
    ]
    for v in variants:
        assert lic.normalize_license_key(v) == k, repr(v)
        assert lic._looks_like_valid_key(v), repr(v)
        assert lic.looks_like_license_key(v), repr(v)


def test_beta_code_is_not_taken_for_key_and_typo_still_rejected():
    assert not lic.looks_like_license_key("SUMMER-BETA")
    k = _make_key("ENT")
    bad = k[:-1] + ("2" if k[-1] != "2" else "3")
    assert not lic._looks_like_valid_key(bad)


def test_activate_sends_normalized_key(monkeypatch):
    k = _make_key()
    sent = {}

    class _Resp:
        status_code = 404

    def fake_post(path, body):
        sent.update(body)
        return _Resp()

    monkeypatch.setattr(lic, "_post_with_wake_retry", fake_post)
    r = lic.activate(f" {k.lower()} ")
    assert sent["license_key"] == k
    assert not r.ok and r.state == "invalid_key"
