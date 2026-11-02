"""② 키워드 매처의 -ies 복수형(match-v3, 2026-11-02) — 자음 뒤 -y 로 끝나는 키워드가 논문의 복수 표기를 잡는지 지킨다."""

from __future__ import annotations

import pytest

import agent_maintenance as am
import profile_scoring as ps
import research_profile as rp


@pytest.mark.parametrize("keyword, text", [
    ("robot policy", "We learn robot policies from human video."),
    ("robot policy", "Generalist Robot-Policies for manipulation"),
    ("visuomotor policy", "visuomotor policies under unknown resistance"),
    ("logical anomaly", "detecting logical anomalies in MVTec LOCO"),
    ("smart factory", "Smart Factories need agents"),
    ("agent memory", "long-horizon agent memories"),
    ("robot policy", "a robot policy"),                       # 단수는 그대로
])
def test_consonant_y_keywords_match_ies_plural(keyword, text):
    """-y→-ies 가지를 빼면(`(?:es|s)?` 만 붙던 match-v2) 복수형 줄이 실패한다 — 10/20 robot policy 상향이 이 때문에 근거 없음으로 거절됐다(§269)."""
    assert ps.keyword_pattern(keyword).search(text)


@pytest.mark.parametrize("keyword, text, expected", [
    ("key point", "key points", True),                        # 모음 뒤 y 는 -s 복수 — 기존 가지 그대로
    ("robot policy", "robot policyholder", False),            # 낱말 경계는 그대로
    ("robot policy", "robot polic", False),
    ("logical anomaly", "logical anomalyes", False),          # -ies 키워드는 `es` 꼬리를 안 받는다
    ("memory", "memories", True),
])
def test_ies_branch_keeps_word_boundaries(keyword, text, expected):
    """-ies 가지를 넣으며 경계를 풀거나(오탐), 모음 뒤 -y 까지 -ies 로 바꾸면(keys 를 놓침) 실패한다."""
    assert bool(ps.keyword_pattern(keyword).search(text)) is expected


def test_weekly_evidence_check_uses_same_plural_rule():
    """주간 관리의 근거 확인이 채점기와 다른 매처를 쓰게 되면 실패한다 — §269 의 잘못된 거절이 이 자리였다."""
    assert am._in_text("robot policy", "Training robot policies with residual RL") is True


def test_rank_policy_version_records_match_v3():
    """매처 뜻이 바뀌었는데 정책 버전을 안 올리면 실패한다 — 관측·건강 점검이 match-v2 와 v3 를 한 기준선에 섞는다."""
    assert "+match-v3+" in rp.RANK_POLICY_VERSION
