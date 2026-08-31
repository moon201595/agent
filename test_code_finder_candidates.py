"""github_search 후보 검증 — 전부 DB에 실제로 남아 있는 시도 기록으로 만든 픽스처다.

2026-08-31 실측에서 GRAFT 논문(본문에 URL 이 하나도 없다)에 대해 이름만 같은
무관한 저장소 세 개가 연달아 클론되고 Docker 까지 돌았다. 검색 결과에 "이
논문의 저장소라는 독립적 근거"를 요구하기로 했는데, 이 규칙이 **과거에 성공한
재현 두 건을 거부하면 안 된다**는 게 제일 중요한 제약이다.

저장소 이름·설명은 실제 GitHub API 응답을 그대로 옮긴 것이다.
"""

import json

import pytest

from code_finder import corroborates_paper, is_non_code_repo


def _cand(full_name, description=None):
    return {"url": f"https://github.com/{full_name}", "full_name": full_name,
            "description": description, "source": "github_search"}


# ------------------------------------------------------ 반드시 채택 (과거 성공 사례)

def test_accepts_swe_agent_the_known_success():
    """설명에 "software engineering"이 없어서 제목 낱말로는 못 잡는다 —
    조직명 자체가 SWE-agent 인 것이 근거다. 이 건을 거부하면 이 프로젝트에서
    유일하게 완전 성공한 재현이 사라진다."""
    assert corroborates_paper(
        _cand("SWE-agent/SWE-agent",
              "SWE-agent takes a GitHub issue and tries to automatically fix it, "
              "using your LM of choice. It can also be employed for offensive "
              "cybersecurity or competitive coding challenges. [NeurIPS 2024]"),
        "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering",
        "SWE-agent") is True


def test_accepts_lf_yolo_the_other_known_success():
    assert corroborates_paper(
        _cand("lmomoy/LF-YOLO",
              "LF-YOLO (Lighter and Faster YOLO) is used to detect defect of X-ray weld image."),
        "LF-YOLO: A Lighter and Faster YOLO for Weld Defect Detection of X-ray Image",
        "LF-YOLO") is True


def test_accepts_official_repo_whose_description_repeats_the_title():
    assert corroborates_paper(
        _cand("mit-han-lab/llm-awq",
              "[MLSys 2024 Best Paper Award] AWQ: Activation-aware Weight Quantization "
              "for LLM Compression and Acceleration"),
        "AWQ: Activation-aware Weight Quantization for LLM Compression and Acceleration",
        "AWQ") is True


def test_accepts_community_reimplementation_of_the_same_paper():
    """github_search 후보는 원래부터 "커뮤니티 재구현체 가능성"으로 라벨된다 —
    같은 논문의 재구현이면 ⑦ 이 답하려는 질문에는 유효한 대상이다."""
    assert corroborates_paper(
        _cand("milesial/Pytorch-UNet",
              "PyTorch implementation of the U-Net for image semantic segmentation "
              "with high quality images"),
        "U-Net: Convolutional Networks for Biomedical Image Segmentation",
        "U-Net") is True


# ------------------------------------------------------ 반드시 거부 (이름 충돌)

@pytest.mark.parametrize("full_name,description", [
    ("trailhq/Graft", "Turbocharge Claude Code, Cursor, Codex, Gemini & every coding "
                      "agent: faster, cheaper, with contextual understanding specific "
                      "to your codebase."),
    ("hmgle/graftcp", "A flexible tool for redirecting a program's TCP, UDP, and DNS "
                      "traffic to SOCKS5 or HTTP proxies."),
    ("orbitinghail/graft", "Graft is an open-source transactional storage engine "
                           "optimized for lazy, partial, and strongly consistent "
                           "replication—perfect for edge, offline-first, and "
                           "distributed applications."),
])
def test_rejects_graft_name_collisions(full_name, description):
    """실측: 이 세 개를 연달아 클론하고 Docker 까지 돌렸다. 논문 본문에는
    URL 이 하나도 없어서 검색 결과가 유일한 후보였다."""
    assert corroborates_paper(
        _cand(full_name, description),
        "GRAFT: Grounded and Efficient Online Reinforcement Adaptation for "
        "Fine-Grained Robot Manipulation",
        "GRAFT") is False


def test_rejects_unrelated_repo_matched_by_substring():
    assert corroborates_paper(
        _cand("nondanee/UnblockNeteaseMusic", "Revive unavailable songs for Netease Cloud Music"),
        "U-Net: Convolutional Networks for Biomedical Image Segmentation",
        "U-Net") is False


def test_rejects_repo_of_a_different_paper_with_similar_name():
    """U^2-Net 은 U-Net 과 다른 논문이다 — 이름이 닮았다고 채택하면 안 된다."""
    assert corroborates_paper(
        _cand("xuebinqin/U-2-Net",
              'The code for our newly accepted paper in Pattern Recognition 2020: '
              '"U^2-Net: Going Deeper with Nested U-Structure for Salient Object Detection."'),
        "U-Net: Convolutional Networks for Biomedical Image Segmentation",
        "U-Net",
        json.dumps(["Olaf Ronneberger", "Philipp Fischer", "Thomas Brox"])) is False


def test_rejects_tspulse_name_collision():
    assert corroborates_paper(
        _cand("ZentriaMC/tspulse", "Health-check sidecar for Tailscale services"),
        "TSPulse: Tiny Pre-Trained Models with Disentangled Representations",
        "TSPulse") is False


def test_rejects_unrelated_tool_for_eda_paper():
    assert corroborates_paper(
        _cand("virsi/logisim-mcp",
              "MCP server for Logisim Evolution 4.x. Lets an LLM design, edit and "
              "verify digital circuits in `.circ` format without ever opening the GUI."),
        "LLMs in Digital EDA: A perspective on shifting roles from Generation to Orchestration",
        "LLMs in Digital EDA") is False


def test_accepts_repo_named_after_the_paper_even_without_description():
    assert corroborates_paper(
        _cand("MattyCode101/LLMs_in_Digital_EDA_Perspective", None),
        "LLMs in Digital EDA: A perspective on shifting roles from Generation to Orchestration",
        "LLMs in Digital EDA") is True


def test_author_surname_in_owner_is_evidence():
    assert corroborates_paper(
        _cand("ronneberger/some-unrelated-name", None),
        "Totally Different Words Here",
        "Totally",
        json.dumps(["Olaf Ronneberger", "Philipp Fischer"])) is True


def test_short_surnames_are_not_used_as_evidence():
    """"Ye", "Xu", "Sun" 같은 두 글자 성은 아무 문자열에나 우연히 걸린다."""
    assert corroborates_paper(
        _cand("yexu/random-tool", None),
        "Totally Different Words Here",
        "Totally",
        json.dumps(["Haoliang Ye", "Ronald X Xu"])) is False


# ------------------------------------------------------ 소개 페이지 저장소

def test_website_repo_is_recognised():
    """실측: Riemann-1.0-Website. 클론은 되지만 "설치해서 도는가"에 답할 수 없다."""
    assert is_non_code_repo("https://github.com/Riemann-Dynamics/Riemann-1.0-Website") is True


@pytest.mark.parametrize("url", [
    "https://github.com/org/project-docs",
    "https://github.com/org/project.page",
    "https://github.com/org/docs",
    "https://github.com/org/org.github.io",
])
def test_other_non_code_repo_shapes(url):
    assert is_non_code_repo(url) is True


@pytest.mark.parametrize("url", [
    "https://github.com/SWE-agent/SWE-agent",
    "https://github.com/lmomoy/LF-YOLO",
    "https://github.com/michaelholm6/YOLOEZA",
    "https://github.com/org/homepage-detector",   # 이름 안에 들어있을 뿐 접미사가 아니다
])
def test_real_code_repos_are_not_flagged(url):
    assert is_non_code_repo(url) is False
