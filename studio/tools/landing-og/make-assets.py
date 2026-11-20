"""Turn render.cjs output into the deployable files under studio/site/assets/img/ (VM venv has Pillow).
   python make-assets.py <renderOutDir> <siteImgDir>"""
import os, sys
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
og = Image.open(os.path.join(src, "og-1200x630.png")).convert("RGB")
assert og.size == (1200, 630), og.size
og.save(os.path.join(dst, "og-wish-labs-1200x630.jpg"), "JPEG", quality=86, optimize=True, progressive=True)
Image.open(os.path.join(src, "icon-32.png")).save(os.path.join(dst, "icon-32.png"), optimize=True)
Image.open(os.path.join(src, "apple-touch-icon-180.png")).convert("RGB").save(os.path.join(dst, "apple-touch-icon-180.png"), optimize=True)
Image.open(os.path.join(src, "icon-256.png")).save(os.path.join(dst, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])
for f in ("og-wish-labs-1200x630.jpg", "icon-32.png", "apple-touch-icon-180.png", "favicon.ico"):
    p = os.path.join(dst, f)
    print(f, os.path.getsize(p), Image.open(p).size)
