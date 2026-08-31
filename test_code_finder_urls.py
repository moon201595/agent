"""code_finder 의 URL 추출 방어 — 전부 2026-08-31 실전 실행에서 관측된 사례다.

그날 ⑦ 재현 4건이 모두 실패했는데, 원인이 "저자 코드가 안 돈다"가 아니라
"엉뚱한 주소를 클론하려 했다"였다. 다이제스트의 [재현 ✗] 라벨이 두 다른
원인을 구분하지 못하고 있었다는 게 문제의 핵심이라, 원인 쪽을 막는다.
"""

import code_finder as cf


# ------------------------------------------------- 본문 조각이 URL 뒤에 붙는 문제

def test_sentence_glued_after_url_is_cut():
    """실측: 원문 줄바꿈을 지우자 "...DSSG." + "Index terms" 가 이어붙어
    github.com/mrmenand/DSSG.Index 가 됐고 clone 단계에서 죽었다."""
    assert cf._clean_url("https://github.com/mrmenand/DSSG.Index") == \
        "https://github.com/mrmenand/DSSG"


def test_all_caps_heading_glued_after_url_is_cut():
    """실측: "...COM_MATD3." + "TABLE I" → COM_MATD3.TABLE."""
    assert cf._clean_url("https://github.com/yanfeisu/COM_MATD3.TABLE") == \
        "https://github.com/yanfeisu/COM_MATD3"


def test_repo_name_with_lowercase_dot_is_preserved():
    """socket.io / next.js 같은 진짜 저장소 이름을 자르면 안 된다 —
    절단은 마침표 뒤가 대문자일 때로 한정한다."""
    for url in ("https://github.com/socketio/socket.io",
                "https://github.com/vercel/next.js"):
        assert cf._clean_url(url) == url


def test_sibling_repo_of_same_owner_is_not_touched():
    """_drop_prefix_duplicates 의 기존 회귀(ogx vs ogx-k8s-operator)를
    _clean_url 변경이 되살리지 않는지 본다."""
    url = "https://github.com/ogx/ogx-k8s-operator"
    assert cf._clean_url(url) == url


def test_only_the_real_repo_survives_in_full_text_flow():
    text = ("Our code is available at https://github.com/mrmenand/DSSG.\n"
            "Index terms—defect detection, PCB inspection.\n")
    assert [c.url for c in cf.find_links_in_text(text)] == \
        ["https://github.com/mrmenand/DSSG"]


# ------------------------------------------------- 도구로 인용한 저장소 문제

def test_tool_repo_cited_in_paper_is_not_a_candidate():
    """실측: CSymPlan 본문이 시뮬레이터로 언급한 isaac-sim/IsaacSim 이
    저자 저장소로 잡혀 재현이 stage=no_target 으로 실패했다."""
    assert cf._is_tool_repo("https://github.com/isaac-sim/IsaacSim") is True
    text = "We reproduce results using the simulator code at https://github.com/isaac-sim/IsaacSim ."
    assert cf.find_links_in_text(text) == []


def test_framework_repos_are_excluded():
    for url in ("https://github.com/huggingface/transformers",
                "https://github.com/pytorch/pytorch",
                "https://github.com/ultralytics/ultralytics"):
        assert cf._is_tool_repo(url) is True


def test_ordinary_author_repo_is_not_excluded():
    """제외 목록이 넓어져 진짜 저자 저장소를 삼키면 지금 문제보다 나쁘다."""
    for url in ("https://github.com/mrmenand/DSSG",
                "https://github.com/facebookresearch/some-paper-code",
                "https://github.com/huggingface-user/my-paper"):
        assert cf._is_tool_repo(url) is False
