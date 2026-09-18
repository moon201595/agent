"""다이어그램 PNG(2x) 를 내용 경계로 잘라 crops/ 에 두고 크기를 sizes.json 에 적는다 — build_deck.js 가 읽는다.
캔버스(1280×720) 여백이 슬라이드 제목 아래 빈 띠로 겹쳐 보여서(2026-09-17 PowerPoint 렌더 확인) 자른다."""
from PIL import Image, ImageChops
import glob, json, os
HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(f"{HERE}/crops", exist_ok=True)
out = {}
for f in sorted(glob.glob(f"{HERE}/../0*.png")):
    im = Image.open(f).convert("RGB")
    bbox = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
    pad = 28
    x0, y0, x1, y1 = max(0, bbox[0]-pad), max(0, bbox[1]-pad), min(im.width, bbox[2]+pad), min(im.height, bbox[3]+pad)
    c = im.crop((x0, y0, x1, y1)); name = os.path.basename(f); c.save(f"{HERE}/crops/{name}"); out[name] = [c.width, c.height]
json.dump(out, open(f"{HERE}/crops/sizes.json", "w"))
print(out)
