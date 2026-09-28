"""
2026-09-15: 여러 테스트 파일이 각자 headless로 gui.app.CutLineApp() (Tk 루트
창)을 새로 만들어서 쓰는데, 이전 테스트가 만든 인스턴스를 destroy()하지 않고
그냥 끝나버리면 tkinter의 전역 기본 root(tk._default_root)가 이미 죽거나
곧 사라질 인스턴스를 계속 가리키게 된다. 그 상태에서 다음 테스트가
ImageTk.PhotoImage(...)를 master 없이 만들면(코드 대부분이 그렇게 씀) 그
낡은 기본 root 쪽 Tcl 인터프리터에 이미지가 만들어지고, 정작 새 테스트의
canvas는 자기 자신의(다른) 인터프리터를 쓰므로 "image pyimageN doesn't
exist"로 깨진다.

실제로 전체 스위트를 한 프로세스로 돌리면 이 순서 의존성 때문에 매번 다른
조합의 GUI 테스트가 실패하는 게 확인됐다(파일별로 몇 개의 CutLineApp()
인스턴스가 그 앞에 쌓였는지에 따라 실패 패턴이 달라짐) -- 칼선 알고리즘
쪽 문제가 아니라 순수 테스트 격리 문제.

고침: 테스트 하나가 끝날 때마다(성공/실패 무관) 그 시점의 tk._default_root를
확실히 destroy()하고 포인터를 비워서, 다음 테스트가 완전히 깨끗한 상태에서
새 CutLineApp()을 만들도록 한다.
"""
import tkinter as tk

import pytest


@pytest.fixture(autouse=True)
def _destroy_stray_tk_root_after_each_test():
    yield
    root = getattr(tk, "_default_root", None)
    if root is not None:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001 -- 이미 죽어있거나 위젯이 아니어도 무시
            pass
    tk._default_root = None
