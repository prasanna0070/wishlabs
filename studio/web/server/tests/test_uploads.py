"""User image uploads ("start from an image", app/uploads.py + app/routes/uploads.py): the real type comes from the
bytes, size and pixel limits hold, EXIF/GPS are stripped with the orientation applied, HEIC works only with
pillow-heif, 12 images per project, login required, and share links see the images only after share_uploads.
Every image here is synthetic (drawn with Pillow)."""
from __future__ import annotations

import io
import struct
import zlib

import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from app import uploads as up  # noqa: E402
from conftest import KEY  # noqa: E402

MULTIPART = pytest.mark.skipif(
    __import__("importlib.util").util.find_spec("python_multipart") is None,
    reason="python-multipart is not installed (pip install -r requirements.txt)")


def sketch(w=320, h=240, fmt="PNG", mode="RGB", exif=None, **kw) -> bytes:
    """A small drawn 'sketch': a lamp outline and a label."""
    im = Image.new(mode, (w, h), (0, 0, 0, 0) if mode == "RGBA" else (250, 248, 240))   # transparent BLACK
    d = ImageDraw.Draw(im)
    d.ellipse((w * 0.3, h * 0.15, w * 0.7, h * 0.55), outline=(20, 20, 20), width=4)
    d.rectangle((w * 0.45, h * 0.55, w * 0.55, h * 0.85), outline=(20, 20, 20), width=4)
    d.text((10, 10), "desk lamp v0", fill=(20, 20, 20))
    buf = io.BytesIO()
    if exif is not None:
        kw["exif"] = exif
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def exif_with_gps(orientation: int = 6):
    ex = Image.Exif()
    ex[0x0112] = orientation                     # Orientation: 6 = rotate 90 degrees clockwise to display
    ex[0x010F] = "TestCamMaker"                  # Make
    ex[0x8825] = {1: "N", 2: (12.0, 58.0, 30.0), 3: "E", 4: (77.0, 35.0, 10.0)}   # GPS IFD
    return ex


def png_bomb(w=40000, h=40000) -> bytes:
    """A tiny PNG whose header claims w x h pixels (Pillow refuses it when opening, before any decoding)."""
    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    raw = zlib.compress(b"\x00" + b"\x00" * 8)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + \
        chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def post(c, pid, data: bytes, name="sketch.png", ctype="application/octet-stream"):
    return c.post(f"/api/projects/{pid}/uploads", files={"file": (name, data, ctype)})


# ---------------------------------------------------------------------- pure functions
def test_sniff_reads_the_bytes_not_the_name():
    assert up.sniff(sketch(fmt="PNG")) == "image/png"
    assert up.sniff(sketch(fmt="JPEG")) == "image/jpeg"
    assert up.sniff(sketch(fmt="WEBP")) == "image/webp"
    assert up.sniff(sketch(fmt="GIF")) == "image/gif"
    assert up.sniff(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic") == "image/heic"
    assert up.sniff(b"\x00\x00\x00\x1cftypmif1\x00\x00\x00\x00mif1heic....") == "image/heic"
    assert up.sniff(b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1miaf") == "image/avif"
    assert up.sniff(b"%PDF-1.7 ...") is None
    assert up.sniff(b"hello, this is text") is None


def test_clean_name_strips_paths_controls_and_markup():
    assert up.clean_name("C:\\fakepath\\My Sketch.PNG") == "My Sketch.PNG"
    assert up.clean_name("../../etc/passwd") == "passwd"
    assert up.clean_name("evil\u202egnp.exe<script>.png") == "evilgnp.exe_script_.png"
    assert up.clean_name("  \x00\x07 ") == "image"
    assert up.clean_name(None) == "image"
    long = up.clean_name("a" * 200 + ".jpeg")
    assert len(long) <= 80 and long.endswith(".jpeg")
    assert up.clean_name("Konzept Skizze \u00fc\u00e9 \u0939\u093f\u0902\u0926\u0940.png").endswith(".png")


def test_normalise_applies_orientation_and_strips_exif_and_gps():
    raw = sketch(w=64, h=32, fmt="JPEG", exif=exif_with_gps(6))
    src = Image.open(io.BytesIO(raw))
    assert src.getexif().get(0x0112) == 6 and src.getexif().get_ifd(0x8825), "fixture must carry orientation + GPS"
    assert b"TestCamMaker" in raw
    jpg, thumb, w, h, mime = up.normalise(raw)
    assert mime == "image/jpeg" and (w, h) == (32, 64)            # rotated upright
    out = Image.open(io.BytesIO(jpg))
    assert out.format == "JPEG" and out.size == (32, 64) and out.mode == "RGB"
    assert not out.getexif() and "exif" not in out.info and "icc_profile" not in out.info
    assert b"TestCamMaker" not in jpg and b"Exif\x00\x00" not in jpg and b"Exif\x00\x00" not in thumb


def test_normalise_resizes_flattens_alpha_and_makes_a_thumbnail():
    jpg, thumb, w, h, mime = up.normalise(sketch(w=3000, h=1500, fmt="PNG", mode="RGBA"))
    assert mime == "image/png" and (w, h) == (2048, 1024)
    out = Image.open(io.BytesIO(jpg))
    assert out.size == (2048, 1024)
    r, g, b = out.getpixel((2, 1020))                               # transparent corner -> white, not black
    assert min(r, g, b) > 240
    t = Image.open(io.BytesIO(thumb))
    assert max(t.size) == 384 and t.format == "JPEG"


def test_normalise_keeps_small_images_and_handles_big_jpegs():
    _, _, w, h, _ = up.normalise(sketch(w=300, h=200, fmt="WEBP"))
    assert (w, h) == (300, 200)
    _, _, w, h, _ = up.normalise(sketch(w=6000, h=4000, fmt="JPEG", quality=60))   # DCT draft path
    assert (w, h) == (2048, 1365) or (w, h) == (2048, 1366)


@pytest.mark.parametrize("data,status,code", [
    (b"hello world, not an image" * 10, 415, "unsupported_type"),
    (b"%PDF-1.4\n" + b"0" * 100, 415, "unsupported_type"),
    (b"GIF89a" + b"\x00" * 100, 415, "unsupported_type"),
    (b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1miaf" + b"\x00" * 64, 415, "unsupported_type"),
    (b"\xff\xd8\xff\xe0" + b"\x00" * 200, 415, "unsupported_type"),       # JPEG magic, no image behind it
    (b"", 422, "invalid"),
])
def test_normalise_refuses_non_images(data, status, code):
    with pytest.raises(up.UploadError) as e:
        up.normalise(data)
    assert (e.value.status, e.value.code) == (status, code)


def test_normalise_refuses_decompression_bombs():
    with pytest.raises(up.UploadError) as e:
        up.normalise(png_bomb(40000, 40000))                       # 1.6 gigapixels in a 100-byte file
    assert (e.value.status, e.value.code) == (413, "too_large") and "too large" in e.value.message
    with pytest.raises(up.UploadError) as e:
        up.normalise(png_bomb(8000, 7000))                         # 56 MP: over our 50 MP cap, under Pillow's
    assert e.value.status == 413


def test_heic_needs_pillow_heif(monkeypatch):
    fake = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
    monkeypatch.setattr(up, "heif_available", lambda: False)
    with pytest.raises(up.UploadError) as e:
        up.normalise(fake)
    assert e.value.status == 415 and e.value.message == "HEIC is not supported yet, please export as JPEG."


def test_heic_is_read_when_pillow_heif_is_installed():
    if not up.heif_available():
        pytest.skip("pillow-heif is not installed")
    im = Image.open(io.BytesIO(sketch(w=200, h=100, fmt="PNG")))
    buf = io.BytesIO()
    try:
        im.save(buf, format="HEIF", quality=80)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"this libheif build cannot encode HEIC: {exc}")
    raw = buf.getvalue()
    assert up.sniff(raw) == "image/heic"
    jpg, _, w, h, mime = up.normalise(raw)
    assert mime == "image/heic" and (w, h) == (200, 100) and Image.open(io.BytesIO(jpg)).format == "JPEG"


def test_attachment_ids_are_checked_against_the_project():
    p = {"uploads": [{"id": "u_aaaaaaaaaa", "name": "a.png", "mime": "image/jpeg", "width": 10, "height": 20}]}
    assert up.message_attachments(p, [{"id": "u_aaaaaaaaaa"}, "u_aaaaaaaaaa"]) == [
        {"id": "u_aaaaaaaaaa", "name": "a.png", "mime": "image/jpeg", "width": 10, "height": 20}]
    assert up.message_attachments(p, None) == []
    for bad, status in (([{"id": "u_bbbbbbbbbb"}], 422), ([{"id": "../x"}], 422), ("u_aaaaaaaaaa", 422),
                        ([{"id": f"u_{c * 10}"} for c in "abcde"], 413)):
        with pytest.raises(up.UploadError) as e:
            up.message_attachments(p, bad)
        assert e.value.status == status, bad


def test_hide_uploads_drops_records_and_names():
    q = {"uploads": [{"id": "u_x"}], "image_brief": {"title": "Lamp"},
         "chat": [{"id": "m1", "role": "user", "text": "hi", "attachments": [{"id": "u_x", "name": "secret.png"}]},
                  {"id": "m2", "role": "assistant", "text": "ok"}]}
    h = up.hide_uploads(q)
    assert "uploads" not in h and h["image_brief"] == {"title": "Lamp"}
    assert "attachments" not in h["chat"][0] and h["chat"][0]["attachments_hidden"] == 1
    assert "secret" not in str(h) and q["chat"][0]["attachments"]          # the stored project is untouched


# ---------------------------------------------------------------------- routes
@MULTIPART
def test_upload_roundtrip_owner_only(authed, client):
    pid = authed.post("/api/projects", json={"mode": "fast"}).json()["id"]
    raw = sketch(w=900, h=600, fmt="JPEG", exif=exif_with_gps(6))
    r = post(authed, pid, raw, name="C:\\fakepath\\lamp sketch.png", ctype="image/png")   # name and type lie
    assert r.status_code == 201, r.text
    u = r.json()
    assert set(u) == {"id", "name", "mime", "bytes", "width", "height", "url", "thumb_url"}
    assert up.ID_RE.fullmatch(u["id"]) and u["name"] == "lamp sketch.png" and u["mime"] == "image/jpeg"
    assert (u["width"], u["height"]) == (600, 900)
    assert u["url"] == f"/api/projects/{pid}/uploads/{u['id']}" and u["thumb_url"] == u["url"] + "?thumb=1"
    g = authed.get(u["url"])
    assert g.status_code == 200 and g.headers["content-type"] == "image/jpeg" and len(g.content) == u["bytes"]
    assert b"TestCamMaker" not in g.content and b"Exif\x00\x00" not in g.content
    t = authed.get(u["thumb_url"])
    assert t.status_code == 200 and max(Image.open(io.BytesIO(t.content)).size) == 384
    p = authed.get(f"/api/projects/{pid}").json()
    assert [x["id"] for x in p["uploads"]] == [u["id"]] and p["uploads"][0]["source_mime"] == "image/jpeg"
    row = next(x for x in authed.get("/api/projects").json()["projects"] if x["id"] == pid)
    assert row["thumb_url"] == u["thumb_url"]                      # the project list shows the user's image
    store = authed.app.state.svc.store
    assert store.exists(f"projects/{pid}/uploads/{u['id']}.jpg")
    assert store.exists(f"projects/{pid}/uploads/{u['id']}.thumb.jpg")
    # signed out: nothing
    authed.post("/api/logout")
    authed.cookies.clear()
    assert authed.get(u["url"]).status_code == 401
    assert post(authed, pid, raw).status_code == 401
    assert authed.post("/api/login", json={"key": KEY}).status_code == 200
    assert authed.get(f"/api/projects/{pid}/uploads/u_zzzzzzzzzz").status_code == 404
    assert authed.get(f"/api/projects/{pid}/uploads/..%2Fproject.json").status_code == 404


@MULTIPART
def test_upload_serves_from_a_store_without_local_files(authed, monkeypatch):
    """GCS has no local path: the bytes are read from the store instead."""
    pid = authed.post("/api/projects", json={}).json()["id"]
    u = post(authed, pid, sketch(fmt="PNG")).json()
    store = authed.app.state.svc.store
    monkeypatch.setattr(store, "local_path", lambda key: None)
    g = authed.get(u["url"])
    assert g.status_code == 200 and g.content == store.read_bytes(f"projects/{pid}/uploads/{u['id']}.jpg")


@MULTIPART
@pytest.mark.parametrize("data,name,status,code,needle", [
    (b"just some notes, not a picture" * 20, "notes.jpg", 415, "unsupported_type", "JPEG, PNG or WebP"),
    (b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1miaf" + b"\x00" * 64, "x.avif", 415, "unsupported_type",
     "AVIF"),
    (png_bomb(), "bomb.png", 413, "too_large", "too large"),
    (b"", "empty.png", 422, "invalid", "empty"),
])
def test_upload_refusals(authed, data, name, status, code, needle):
    pid = authed.post("/api/projects", json={}).json()["id"]
    r = post(authed, pid, data, name=name)
    assert r.status_code == status, r.text
    e = r.json()["error"]
    assert e["code"] == code and needle in e["message"]
    assert not authed.get(f"/api/projects/{pid}").json().get("uploads")
    assert authed.app.state.svc.store.list_keys(f"projects/{pid}/uploads") == []


@MULTIPART
def test_upload_heic_without_pillow_heif_is_a_friendly_415(authed, monkeypatch):
    monkeypatch.setattr(up, "heif_available", lambda: False)
    pid = authed.post("/api/projects", json={}).json()["id"]
    r = post(authed, pid, b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64, name="IMG_0001.HEIC")
    assert r.status_code == 415 and r.json()["error"]["message"] == "HEIC is not supported yet, please export as JPEG."


@MULTIPART
def test_upload_size_limit_and_form_shape(authed):
    pid = authed.post("/api/projects", json={}).json()["id"]
    big = b"\xff\xd8\xff\xe0" + b"\x00" * (15 * 1024 * 1024)                 # just over 15 MB
    r = post(authed, pid, big, name="huge.jpg")
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    r = authed.post(f"/api/projects/{pid}/uploads", content=sketch(), headers={"content-type": "image/png"})
    assert r.status_code == 422 and "multipart" in r.json()["error"]["message"]
    r = authed.post(f"/api/projects/{pid}/uploads", files={"image": ("a.png", sketch(), "image/png")})
    assert r.status_code == 422 and '"file"' in r.json()["error"]["message"]
    assert authed.post("/api/projects/p_nope/uploads", files={"file": ("a.png", sketch(), "image/png")}
                       ).status_code == 404


@MULTIPART
def test_twelve_images_per_project(authed):
    pid = authed.post("/api/projects", json={}).json()["id"]
    tiny = sketch(w=40, h=30)
    for i in range(up.PER_PROJECT):
        assert post(authed, pid, tiny, name=f"s{i}.png").status_code == 201
    r = post(authed, pid, tiny, name="one-too-many.png")
    assert r.status_code == 409 and r.json()["error"]["code"] == "limit" and "12 images" in r.json()["error"]["message"]
    assert len(authed.get(f"/api/projects/{pid}").json()["uploads"]) == 12
    assert len(authed.app.state.svc.store.list_keys(f"projects/{pid}/uploads")) == 24
    # an image no message carries can be removed, which frees a slot
    first = authed.get(f"/api/projects/{pid}").json()["uploads"][0]["id"]
    assert authed.delete(f"/api/projects/{pid}/uploads/{first}").json() == {"ok": True}
    assert authed.get(f"/api/projects/{pid}/uploads/{first}").status_code == 404
    assert post(authed, pid, tiny).status_code == 201


@MULTIPART
def test_share_link_sees_images_only_when_shared(authed, client):
    pid = authed.post("/api/projects", json={}).json()["id"]
    u = post(authed, pid, sketch(), name="secret-client-sheet.png").json()
    p = authed.app.state.svc.repo.update(pid, lambda pp: pp["chat"].append(
        {"id": "m_x", "role": "user", "kind": "text", "text": "Here is what I have in mind.", "payload": None,
         "ts": "2026-11-20T10:00:00Z", "attachments": [{"id": u["id"], "name": u["name"], "mime": "image/jpeg",
                                                        "width": u["width"], "height": u["height"]}]}))
    assert p["chat"][-1]["attachments"]
    tok = authed.post(f"/api/projects/{pid}/share").json()["token"]
    base = f"/api/share/{tok}/uploads/{u['id']}"
    assert authed.get(base).status_code == 404 and authed.get(base + "?thumb=1").status_code == 404
    view = authed.get(f"/api/share/{tok}").json()
    assert "uploads" not in view and "secret-client-sheet" not in str(view)
    assert view["chat"][-1]["attachments_hidden"] == 1 and "attachments" not in view["chat"][-1]
    assert authed.patch(f"/api/projects/{pid}", json={"share_uploads": "yes"}).status_code == 422
    r = authed.patch(f"/api/projects/{pid}", json={"share_uploads": True})
    assert r.status_code == 200 and r.json()["share_uploads"] is True
    g = authed.get(base)
    assert g.status_code == 200 and g.headers["content-type"] == "image/jpeg"
    assert g.headers["cache-control"] == "no-store"
    assert authed.get(base + "?thumb=1").status_code == 200
    view = authed.get(f"/api/share/{tok}").json()
    assert view["share_uploads"] is True and "uploads" not in view       # the full list never goes out
    assert view["chat"][-1]["attachments"][0]["id"] == u["id"]
    # an image that no message carries (removed from the composer, never sent) stays private even when shared
    loose = post(authed, pid, sketch(), name="never-sent.png").json()
    own = authed.get(f"/api/projects/{pid}/uploads/{loose["id"]}")
    assert own.status_code == 200 and "immutable" not in own.headers["cache-control"]
    assert authed.get(f"/api/share/{tok}/uploads/{loose['id']}").status_code == 404
    assert authed.get(f"/api/share/{tok}/uploads/{loose['id']}?thumb=1").status_code == 404
    assert "never-sent" not in str(authed.get(f"/api/share/{tok}").json())
    # the share link works without a login, and only for this project's images
    authed.post("/api/logout")
    authed.cookies.clear()
    assert client.get(base).status_code == 200
    assert client.get(f"/api/share/{tok}/uploads/u_zzzzzzzzzz").status_code == 404
    assert client.get(f"/api/share/s_bogus/uploads/{u['id']}").status_code == 404
    assert authed.post("/api/login", json={"key": KEY}).status_code == 200
    assert authed.patch(f"/api/projects/{pid}", json={"share_uploads": False}).status_code == 200
    assert authed.get(base).status_code == 404
    # an image a message carries stays with the project
    assert authed.delete(f"/api/projects/{pid}/uploads/{u['id']}").status_code == 409
