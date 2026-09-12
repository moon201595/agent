"""테스트는 임시 DB 를 매번 새로 만든다 — schema_guard(§8-98)의 "빈 DB 가 아니면 DDL 을 안
돌린다" 규칙은 운영 DB 를 지키기 위한 것이지 테스트를 막기 위한 것이 아니다. 여기서 허용
플래그를 켠다. 가드 자체를 검사하는 테스트는 monkeypatch.delenv 로 다시 끈다."""
import os

os.environ.setdefault("PAPER_HARNESS_APPLY_DDL", "1")
