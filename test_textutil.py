"""textutil.esc — HTML 이스케이프가 하나뿐임을 지킨다(2026-09-17).

망가뜨리면 실패하는 것: digest·review_app 이 다시 자체 사본을 만들거나, 다섯 글자 중 하나라도 안 바꾸는 것(작은따옴표 속성이 끊긴다)."""
from __future__ import annotations

import digest
import review_app
import textutil


def test_esc_escapes_all_five():
    assert textutil.esc('''R&D <b> "x" it's''') == "R&amp;D &lt;b&gt; &quot;x&quot; it&#x27;s"
    assert textutil.esc(123) == "123"


def test_digest_and_review_app_use_the_shared_helper():
    assert digest._esc is textutil.esc
    assert review_app._h is textutil.esc
