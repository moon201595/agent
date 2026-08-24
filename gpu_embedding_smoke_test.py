"""gpu_embedding_smoke_test.py — 공유 GPU 서버(본인 디렉토리·conda 가상환경)에서
직접 돌려볼 스캐폴드. 이 파일은 이 로컬 환경에서 실행하지 않는다 — Jupyter로
그 서버에 복사해서 본인 세션 안에서 돌리는 용도다.

확인하는 것 4가지:
1. GPU가 실제로 잡히는지(torch.cuda.is_available(), 이름·메모리)
2. 후보 임베딩 모델이 정상적으로 다운로드·로드되는지(그 서버가 huggingface
   hub에 나가는 방화벽 정책이 여기 없어서 이건 실제로 돌려봐야 안다)
3. 실제 텍스트 몇 개로 임베딩이 나오고 걸리는 시간이 감당할 만한지
4. 결과를 import_local_embeddings.py가 기대하는 JSON 형식으로 그대로
   내보내는지 — 이 형식 그대로 로컬로 가져오면 바로 paper_embeddings에
   들어간다(스키마: server.py의 hybrid_search 임베딩 캐시와 동일).

MODEL_NAME은 시작점일 뿐이다 — multilingual-e5 계열은 최근 다국어(한국어
포함) 검색 벤치마크에서 준수한 성능으로 많이 쓰이지만, 그 서버 환경(설치된
패키지, 다운로드 가능 여부, 다른 사람들이 이미 캐시해둔 모델이 있는지)에
맞게 바꿔도 된다 — 무엇을 쓰든 아래 파이프라인(로드→인코딩→JSON 저장)
모양만 유지하면 import 스크립트가 그대로 받는다.
"""

from __future__ import annotations

import json
import time

MODEL_NAME = "intfloat/multilingual-e5-base"  # 필요하면 바꿀 것 — 시작점일 뿐

# 실제로는 이 목록을 서버 안의 파일(예: 논문 title+abstract 목록 CSV/JSON)에서
# 읽어오면 된다. 여기서는 파이프라인 자체가 도는지만 확인하는 용도로 몇 개만.
SAMPLE_PAPERS = [
    {"arxiv_id": "1706.03762",
     "text": "Attention Is All You Need. We propose the Transformer, a model "
             "architecture eschewing recurrence and instead relying entirely "
             "on an attention mechanism to draw global dependencies."},
    {"arxiv_id": "2506.02153",
     "text": "Small Language Models are the Future of Agentic AI. We argue "
             "that small language models are sufficiently powerful, "
             "inherently more suitable, and necessarily more economical "
             "for many invocations in agentic systems."},
]

OUTPUT_PATH = "local_embeddings.json"  # 이 파일을 다운로드해서 로컬로 가져오면 됨


def main() -> None:
    import torch
    from sentence_transformers import SentenceTransformer

    print("=== 1. GPU 확인 ===")
    if torch.cuda.is_available():
        print(f"  GPU: {torch.cuda.get_device_name(0)}, "
              f"여유 메모리: {torch.cuda.mem_get_info()[0] / 1e9:.1f}GB / "
              f"{torch.cuda.mem_get_info()[1] / 1e9:.1f}GB")
    else:
        print("  GPU 안 잡힘 — CPU로 돎(속도만 느릴 뿐 아래 단계는 그대로 진행됨)")

    print(f"\n=== 2. 모델 로드: {MODEL_NAME} ===")
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, device="cuda" if torch.cuda.is_available() else "cpu")
    print(f"  로드 완료 ({time.time() - t0:.1f}초)")

    print(f"\n=== 3. 샘플 {len(SAMPLE_PAPERS)}건 인코딩 ===")
    t0 = time.time()
    # multilingual-e5 계열은 "passage: " 접두사를 붙이는 게 공식 권장 사용법이다
    # (query와 passage를 구분해 인코딩) — 다른 모델을 쓰면 이 줄은 지울 것.
    texts = [f"passage: {p['text']}" for p in SAMPLE_PAPERS]
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    elapsed = time.time() - t0
    print(f"  완료 ({elapsed:.2f}초, 건당 {elapsed / len(SAMPLE_PAPERS):.2f}초, "
          f"차원={embeddings.shape[1]})")

    print(f"\n=== 4. import_local_embeddings.py 형식으로 저장: {OUTPUT_PATH} ===")
    records = [
        {"arxiv_id": p["arxiv_id"], "model": f"local:{MODEL_NAME}",
         "embedding": emb.tolist()}
        for p, emb in zip(SAMPLE_PAPERS, embeddings)
    ]
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)
    print(f"  {len(records)}건 저장 — 이 파일을 다운로드해서 로컬로 가져오면 됨")
    print("\n  로컬에서: python import_local_embeddings.py local_embeddings.json")


if __name__ == "__main__":
    main()
