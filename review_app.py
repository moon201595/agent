"""review_app.py — ⑥ 사람 판단 UI (Streamlit).

키워드로 논문을 검색해 요약을 생성하고, 생성된 요약을 사람이 보고 승인·반려하는
화면. server.py 는 판단하지 않는다는 원칙을 그대로 지킨다 — 승인/반려 버튼을
누르는 게 "판단"이고, 이 파일은 그 결과를 server.set_review_status() 로 저장만
시킨다.

실행:
    streamlit run review_app.py

검증 실패(수치 불일치)는 "오류 확정"이 아니라 "사람이 확인" 신호라는 게 이
하네스의 원칙이다 (docs/PROGRESS.md §6). 그래서 반려 사유를 강제하지 않고,
불일치 항목을 원문 대조하기 쉽게 문맥과 함께 보여주는 데 집중했다.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import streamlit as st

import server
import summarize_engine as engine
import verify

ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = ROOT / "prompts" / "summary_template.md"

st.set_page_config(page_title="논문 검색·분석 에이전트", layout="wide")


def run_async(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 공용 조회


def _fetch_review_rows(show_all: bool) -> list[dict]:
    with server._db() as con:
        query = """
            SELECT s.arxiv_id, p.title, s.path, s.numbers_total, s.numbers_matched,
                   s.review_status, s.review_note, s.created_at
            FROM summaries s JOIN papers p ON s.arxiv_id = p.arxiv_id
        """
        if not show_all:
            query += " WHERE s.review_status = 'pending' OR s.review_status IS NULL"
        query += " ORDER BY s.created_at DESC"
        rows = con.execute(query).fetchall()
    return [dict(r) for r in rows]


def _verify_detail(arxiv_id: str, summary_text: str) -> verify.VerificationReport:
    with server._db() as con:
        row = con.execute(
            "SELECT text_path FROM papers WHERE arxiv_id=?", (arxiv_id,)
        ).fetchone()
    source = Path(row["text_path"]).read_text(encoding="utf-8")
    return verify.verify_numbers(summary_text, source)


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _render_image_gallery(arxiv_id: str) -> None:
    """표·그림 원본을 갤러리로 보여준다. HTML 출처는 실제 캡션이 라벨로 붙고,
    PDF 출처는 순서대로 '그림 N'만 붙는다 — PyMuPDF(AGPL)를 배제한 채로는
    pypdf 만으로 PDF 안에서 어떤 이미지가 정확히 몇 번 Figure인지 매칭할 수
    없어서다. 표(Table)는 PDF 안에서 대개 이미지가 아니라 벡터·텍스트로
    그려져 있어 이 방식으로는 거의 뽑히지 않는다.
    """
    img_dir = run_async(server.ensure_images_extracted(arxiv_id))
    files = sorted(p for p in img_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS)
    if not files:
        st.caption("추출된 이미지 없음 (원문에 임베드된 이미지가 없거나 추출 실패)")
        return

    labels: dict[str, str] = {}
    labels_path = img_dir / "_labels.json"
    if labels_path.exists():
        labels = json.loads(labels_path.read_text(encoding="utf-8"))

    cols = st.columns(3)
    for i, f in enumerate(files):
        caption = labels.get(f.name) or f"그림 {i + 1}"
        with cols[i % 3]:
            st.image(str(f), caption=caption, use_container_width=True)


# ---------------------------------------------------------------- 탭 ①: 검색·요약


async def _run_search_and_summarize(mode: str, value: str, top_n: int, status_box) -> list[str]:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    if mode == "id":
        targets = [t.strip() for t in value.replace(",", " ").split() if t.strip()]
    elif mode == "title":
        status_box.write(f"'{value}' 제목으로 arXiv 검색 중...")
        result = json.loads(
            await server.arxiv_search_papers(server.ArxivSearchInput(query=value, max_results=1))
        )
        papers = result.get("papers", [])
        if not papers:
            status_box.write("검색 결과 없음")
            return []
        targets = [papers[0]["arxiv_id"]]
        status_box.write(f"찾음: {papers[0]['title']}")
    else:  # keyword
        status_box.write(f"① '{value}' 키워드로 arXiv + Semantic Scholar 검색 중...")
        arxiv_res = json.loads(
            await server.arxiv_search_papers(
                server.ArxivSearchInput(query=value, max_results=top_n * 3)
            )
        )
        s2_res = json.loads(
            await server.s2_search_papers(server.S2SearchInput(query=value, limit=top_n * 3))
        )
        combined = arxiv_res.get("papers", []) + s2_res.get("papers", [])
        status_box.write(f"arXiv {len(arxiv_res.get('papers', []))}건, S2 {len(s2_res.get('papers', []))}건 발견")
        if not combined:
            status_box.write(f"검색 결과 없음 — 외부 API 한도 초과(429)일 수 있음: {arxiv_res} / {s2_res}")
            return []
        status_box.write("② 중복 제거·선별 중...")
        ranked = json.loads(
            await server.dedupe_and_rank_papers(
                server.SelectPapersInput(papers=combined, top_k=top_n)
            )
        )
        targets = [p["arxiv_id"] for p in ranked.get("papers", []) if p.get("arxiv_id")]
        status_box.write(f"선별됨: {targets}")

    if not targets:
        return []

    done = []
    async with httpx.AsyncClient() as client:
        for arxiv_id in targets:
            status_box.write(f"③ [{arxiv_id}] 원문 수집 중...")
            fetch_result = json.loads(
                await server.fetch_paper(server.FetchPaperInput(arxiv_id=arxiv_id))
            )
            if "error" in fetch_result:
                status_box.write(f"❌ [{arxiv_id}] 수집 실패: {fetch_result}")
                continue
            status_box.write(
                f"③ [{arxiv_id}] 완료 — {fetch_result.get('extract_method')}, "
                f"{fetch_result.get('text_chars')}자"
            )
            if await _summarize_target(arxiv_id, template, client, status_box):
                done.append(arxiv_id)

    return done


async def _summarize_target(arxiv_id: str, template: str, client: httpx.AsyncClient, status_box) -> bool:
    """③(원문 수집)까지 끝난 논문 하나에 ④⑤(요약·검증+저장)만 돌린다.
    키워드/ID/제목 검색과 PDF 업로드·오픈액세스 수집이 여기서부터 합류한다.
    """
    text_result = json.loads(
        await server.get_paper_text(
            server.GetTextInput(arxiv_id=arxiv_id, offset=0, max_chars=engine.MAX_PAPER_CHARS)
        )
    )
    status_box.write(f"④ [{arxiv_id}] 요약 생성 중...")
    summary, used_engine = await engine.summarize(client, text_result["text"], template)
    status_box.write(f"④ [{arxiv_id}] 완료 — {used_engine} 사용")

    status_box.write(f"⑤ [{arxiv_id}] 검증 + 저장 중...")
    save_result = json.loads(
        await server.save_summary(server.SaveSummaryInput(arxiv_id=arxiv_id, markdown=summary))
    )
    v = save_result.get("verification", {})
    status_box.write(
        f"✅ [{arxiv_id}] 완료 — pass_ratio={v.get('pass_ratio')} "
        f"({v.get('matched')}/{v.get('total_numbers')})"
    )
    return True


async def _run_pdf_upload_and_summarize(pdf_bytes: bytes, title: str, status_box) -> list[str]:
    """arXiv 밖 저널 PDF를 직접 업로드해 ③(수동)→④⑤ 를 돈다. 이미 합법적으로
    접근 가능한 파일(기관 구독 등)을 사용자가 올리는 경로 — 페이월 우회 아님.
    """
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    status_box.write("③ PDF 텍스트 추출 중...")
    try:
        result = server.ingest_local_pdf(pdf_bytes, title, source_note="manual-pdf: streamlit-upload")
    except ValueError as e:
        status_box.write(f"❌ 추출 실패: {e}")
        return []
    arxiv_id = result["arxiv_id"]
    status_box.write(f"③ [{arxiv_id}] 완료 — {result['text_chars']}자 ({result.get('note', '신규 저장')})")

    async with httpx.AsyncClient() as client:
        ok = await _summarize_target(arxiv_id, template, client, status_box)
    return [arxiv_id] if ok else []


async def _run_open_access_and_summarize(doi_or_url: str, title: str, status_box) -> list[str]:
    """DOI 또는 PDF 직접 링크로 오픈액세스 논문을 받아 ③(자동)→④⑤ 를 돈다.
    DOI 형태(슬래시 포함, .pdf로 안 끝남)면 Unpaywall 로 먼저 합법적 PDF
    위치를 찾고, 이미 PDF 링크면 바로 받는다. 오픈액세스가 아니면 실패를
    정직하게 보고한다 — 페이월을 다른 방법으로 우회하지 않는다.
    """
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    pdf_url = doi_or_url
    if not doi_or_url.lower().endswith(".pdf") and "/" in doi_or_url:
        status_box.write(f"DOI '{doi_or_url}' 로 오픈액세스 PDF 위치 조회 중 (Unpaywall)...")
        resolved = await server.resolve_unpaywall_pdf(doi_or_url)
        if not resolved:
            status_box.write("❌ 오픈액세스 버전을 찾지 못함 — 이 논문은 PDF 업로드로 들여와야 함")
            return []
        pdf_url = resolved
        status_box.write(f"오픈액세스 PDF 발견: {pdf_url}")

    status_box.write("③ PDF 다운로드·텍스트 추출 중...")
    try:
        result = await server.fetch_pdf_from_url(pdf_url, title=title, source_note=f"open-access: {doi_or_url}")
    except (ValueError, httpx.HTTPError) as e:
        status_box.write(f"❌ 수집 실패: {type(e).__name__}: {e}")
        return []
    arxiv_id = result["arxiv_id"]
    status_box.write(f"③ [{arxiv_id}] 완료 — {result['text_chars']}자 ({result.get('note', '신규 저장')})")

    async with httpx.AsyncClient() as client:
        ok = await _summarize_target(arxiv_id, template, client, status_box)
    return [arxiv_id] if ok else []


def render_search_tab():
    st.subheader("논문 검색 → 요약 생성")
    mode_label = st.radio(
        "입력 방식",
        ["키워드 검색", "논문 ID 직접 지정", "제목으로 검색", "PDF 업로드", "DOI/URL (오픈액세스)"],
        horizontal=True,
    )
    mode = {
        "키워드 검색": "keyword", "논문 ID 직접 지정": "id", "제목으로 검색": "title",
        "PDF 업로드": "pdf", "DOI/URL (오픈액세스)": "oa",
    }[mode_label]

    top_n = 3
    uploaded_file = None
    pdf_title = ""
    if mode == "keyword":
        value = st.text_input("검색 키워드", placeholder="예: LoRA fine-tuning summarization")
        top_n = st.number_input("선별할 편수", min_value=1, max_value=10, value=3)
    elif mode == "id":
        value = st.text_input("arXiv ID (공백/쉼표로 여러 개 가능)", placeholder="예: 2505.13033 2405.15793")
    elif mode == "title":
        value = st.text_input("논문 제목", placeholder="예: TSPulse")
    elif mode == "pdf":
        st.caption("arXiv 밖 논문(저널·컨퍼런스) — 이미 기관 구독 등으로 합법적으로 접근 가능한 PDF만 올릴 것")
        uploaded_file = st.file_uploader("PDF 파일", type="pdf")
        pdf_title = st.text_input("제목", placeholder="논문 제목 (필수 — PDF에서 자동 추출 안 함)")
        value = "ok" if (uploaded_file and pdf_title) else ""
    else:  # oa
        st.caption("DOI를 넣으면 Unpaywall로 오픈액세스 PDF를 자동으로 찾는다. PDF 직접 링크도 가능.")
        value = st.text_input("DOI 또는 PDF 직접 링크", placeholder="예: 10.1038/s41467-023-xxxxx-x")
        pdf_title = st.text_input("제목 (선택 — 비우면 '(제목 미입력)'으로 저장)")

    if st.button("🚀 시작", type="primary", disabled=not value):
        status_box = st.status("진행 중...", expanded=True)
        if mode == "pdf":
            done = run_async(
                _run_pdf_upload_and_summarize(uploaded_file.getvalue(), pdf_title, status_box)
            )
        elif mode == "oa":
            done = run_async(_run_open_access_and_summarize(value, pdf_title, status_box))
        else:
            done = run_async(_run_search_and_summarize(mode, value, top_n, status_box))
        if done:
            status_box.update(label=f"완료 — {len(done)}편 처리됨", state="complete")
            st.success(f"{len(done)}편 저장 완료. '요약 검토' 탭에서 확인하세요: {done}")
        else:
            status_box.update(label="처리된 논문 없음", state="error")


# ---------------------------------------------------------------- 탭 ②: 요약 검토


def render_review_tab():
    st.subheader("요약 검토 (⑥ 사람 판단)")
    show_all = st.checkbox("전체 보기 (승인·반려 포함)", value=False)
    rows = _fetch_review_rows(show_all)

    if not rows:
        st.info("검토할 요약이 없습니다." if not show_all else "저장된 요약이 없습니다.")
        return

    status_emoji = {"pending": "🟡", "approved": "✅", "rejected": "❌", None: "🟡"}

    for row in rows:
        arxiv_id = row["arxiv_id"]
        status = row["review_status"] or "pending"
        emoji = status_emoji.get(status, "🟡")
        header = f"{emoji} {row['title']} ({arxiv_id}) — {row['numbers_matched']}/{row['numbers_total']}"

        with st.expander(header):
            summary_path = Path(row["path"])
            if not summary_path.exists():
                st.error(f"요약 파일을 찾을 수 없음: {summary_path}")
                continue
            summary_text = summary_path.read_text(encoding="utf-8")

            report = _verify_detail(arxiv_id, summary_text)
            ratio = report.matched / report.total if report.total else 1.0
            if ratio == 1.0:
                st.success(f"수치 검증: {report.matched}/{report.total} 전부 일치")
            else:
                st.warning(f"수치 검증: {report.matched}/{report.total} 일치 — 아래 불일치 항목 확인")
                for c in report.unmatched:
                    st.markdown(f"- **`{c.token}`** — 문맥: _{c.context}_")

            if row["review_note"]:
                st.caption(f"이전 검토 메모: {row['review_note']}")

            if st.toggle("🖼️ 그림·표 이미지 보기", key=f"imgtoggle_{arxiv_id}"):
                _render_image_gallery(arxiv_id)

            st.markdown("---")
            # 화면 폭(wide layout)에 텍스트를 그대로 채우면 줄이 끝까지 늘어져서
            # 읽기 힘들다 — 가운데 컬럼으로 폭을 제한해 적당한 지점에서 줄바꿈되게 한다.
            # "1~7절"처럼 범위 표기에 쓴 물결표가 markdown 취소선(~text~)으로
            # 오인식되는 걸 실측으로 확인 — 렌더링 직전에 이스케이프한다.
            _, mid, _ = st.columns([1, 4, 1])
            with mid:
                st.markdown(summary_text.replace("~", "\\~"))
            st.markdown("---")

            col1, col2, col3 = st.columns([1, 2, 1])
            with col1:
                if st.button("✅ 승인", key=f"approve_{arxiv_id}"):
                    server.set_review_status(arxiv_id, "approved")
                    st.rerun()
            with col2:
                reason = st.text_input("반려 사유 (선택)", key=f"reason_{arxiv_id}")
                if st.button("❌ 반려", key=f"reject_{arxiv_id}"):
                    server.set_review_status(arxiv_id, "rejected", note=reason)
                    st.rerun()
            with col3:
                if st.button("🔄 다시 생성", key=f"regen_{arxiv_id}"):
                    with st.spinner("재생성 중..."):
                        template = TEMPLATE_PATH.read_text(encoding="utf-8")
                        text_result = json.loads(
                            run_async(
                                server.get_paper_text(
                                    server.GetTextInput(
                                        arxiv_id=arxiv_id, offset=0, max_chars=engine.MAX_PAPER_CHARS
                                    )
                                )
                            )
                        )

                        async def _regen():
                            async with httpx.AsyncClient() as client:
                                return await engine.summarize(client, text_result["text"], template)

                        new_summary, used_engine = run_async(_regen())
                        run_async(
                            server.save_summary(
                                server.SaveSummaryInput(arxiv_id=arxiv_id, markdown=new_summary)
                            )
                        )
                    st.rerun()


# ---------------------------------------------------------------- 메인


st.title("📄 논문 검색·분석 에이전트")

tab1, tab2 = st.tabs(["🔍 검색·요약 생성", "✅ 요약 검토"])
with tab1:
    render_search_tab()
with tab2:
    render_review_tab()
