"""User images ("start from an image"): a concept sheet, a sketch, a product photo or a reference the user already has.

Every upload is re-encoded here before anything else sees it: the real type is sniffed from the bytes (never the file
name), a decompression bomb is refused before decoding, the EXIF orientation is applied, colour goes to sRGB, and EXIF
(with GPS), XMP, ICC and every other metadata block are dropped. What is stored is a JPEG (quality 85, long edge at
most 2048 px) plus a 384 px thumbnail, under projects/<pid>/uploads/ in the project's store (local or GCS).
Routes: app/routes/uploads.py. The agent sees the stored JPEGs as image parts (app/agent/history.py)."""
from __future__ import annotations

import io
import re
import threading
import unicodedata
from functools import lru_cache

from .util import new_id, now_iso

MAX_BYTES = 15 * 1024 * 1024          # raw upload
MAX_PIXELS = 50_000_000               # decoded pixels (a 50 MP camera photo is fine; a bomb is not)
MAX_PIXELS_FLAT = 30_000_000          # PNG and WebP: a tiny flat file can decode to 30 MP (the browser shrinks)
MAX_PIXELS_JPEG = 120_000_000         # JPEGs decode at 1/2-1/8 scale (draft), so a 108 MP phone photo is cheap
DECODES = threading.BoundedSemaphore(1)   # one image decoded at a time per instance (1 GiB of memory, 40 streams)
LONG_EDGE = 2048
THUMB_EDGE = 384
QUALITY = 85
THUMB_QUALITY = 80
PER_MESSAGE = 4
PER_PROJECT = 12
ACCEPTED = ("image/jpeg", "image/png", "image/webp", "image/heic")
ID_RE = re.compile(r"u_[a-z0-9]{10}")
# HEIF major or compatible brands (iPhone photos are heic/mif1); AVIF shares mif1, so it is checked first
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
AVIF_BRANDS = {b"avif", b"avis"}
PIL_FORMATS = {"image/jpeg": {"JPEG", "MPO"}, "image/png": {"PNG"}, "image/webp": {"WEBP"},
               "image/heic": {"HEIF", "HEIC"}}

MSG_TYPE = "Please attach a JPEG, PNG or WebP image (a photo, a sketch or a screenshot)."
MSG_HEIC = "HEIC is not supported yet, please export as JPEG."
MSG_AVIF = "AVIF is not supported yet, please export as JPEG."
MSG_BIG = "That image is over 15 MB. Please attach a smaller one (a screenshot or an exported JPEG works well)."
MSG_PIXELS = "That image is too large to open safely. Please attach a smaller one (at most about 50 megapixels)."
MSG_PIXELS_FLAT = ("That image is too large to open safely. Please attach a smaller one (PNG and WebP: at most about "
                   "30 megapixels; a JPEG may be larger).")
MSG_BROKEN = "That file could not be read as an image. Please export it again as JPEG or PNG."
MSG_PROJECT = f"This project already has {PER_PROJECT} images, the most it can hold. Start a new project for more."
MSG_MESSAGE = f"Attach at most {PER_MESSAGE} images to one message."


class UploadError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def upload_key(pid: str, uid: str, thumb: bool = False) -> str:
    return f"projects/{pid}/uploads/{uid}{'.thumb' if thumb else ''}.jpg"


def upload_url(pid: str, uid: str, thumb: bool = False) -> str:
    return f"/api/projects/{pid}/uploads/{uid}" + ("?thumb=1" if thumb else "")


@lru_cache(maxsize=1)
def heif_available() -> bool:
    """True when pillow-heif is installed (its opener is registered with Pillow once)."""
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        return True
    except Exception:  # noqa: BLE001 - not installed or a broken native library: HEIC is then refused with a 415
        return False


def sniff(data: bytes) -> str | None:
    """The image type from the first bytes: image/jpeg|png|webp|heic|avif|gif, or None."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[4:8] == b"ftyp" and len(data) >= 12:
        size = int.from_bytes(data[:4], "big")
        brands = {data[8:12]} | {data[i:i + 4] for i in range(16, min(max(size, 16), len(data), 256) - 3, 4)}
        if data[8:12] in AVIF_BRANDS:
            return "image/avif"
        if brands & HEIF_BRANDS:
            return "image/heic"
        if brands & AVIF_BRANDS:
            return "image/avif"
    return None


def clean_name(raw: str | None) -> str:
    """The original file name, safe to show and to quote to the model: no path, no control or bidi characters, no
    markup characters, at most 80 characters (the extension is kept)."""
    s = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    s = unicodedata.normalize("NFC", s)
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("C"))
    s = re.sub(r"[<>:\"/\\|?*`$\[\]{}]", "_", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > 80:
        stem, dot, ext = s.rpartition(".")
        s = (stem[:72].rstrip() + "." + ext[:6]) if dot and stem and len(ext) <= 6 else s[:80].rstrip()
    return s or "image"


def check_type(mime: str | None) -> None:
    if mime == "image/heic":
        if not heif_available():
            raise UploadError(415, "unsupported_type", MSG_HEIC)
        return
    if mime == "image/avif":
        raise UploadError(415, "unsupported_type", MSG_AVIF)
    if mime not in ACCEPTED:
        raise UploadError(415, "unsupported_type", MSG_TYPE)


def _flatten(im):
    """Any mode -> RGB; transparency is composited onto white (a transparent PNG must not turn black)."""
    from PIL import Image

    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    if im.mode.startswith("I"):                      # 16- or 32-bit greyscale (some PNGs)
        im = im.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    return im if im.mode == "RGB" else im.convert("RGB")


def _to_srgb(im):
    """Apply an embedded ICC profile (Display P3 phone photos, CMYK print files), so dropping it keeps the colours."""
    icc = im.info.get("icc_profile")
    if not icc or im.mode not in ("RGB", "CMYK"):
        return im
    try:
        from PIL import ImageCms

        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        return ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
    except Exception:  # noqa: BLE001 - a broken or unsupported profile: keep the pixels as they are
        return im


def _jpeg(im, quality: int) -> bytes:
    im.info = {}                                   # nothing from the source travels: no EXIF, GPS, XMP or ICC
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def normalise(data: bytes) -> tuple[bytes, bytes, int, int, str]:
    """Raw upload -> (jpeg, thumbnail jpeg, width, height, sniffed mime). Raises UploadError."""
    if not data:
        raise UploadError(422, "invalid", "The file is empty.")
    if len(data) > MAX_BYTES:
        raise UploadError(413, "too_large", MSG_BIG)
    mime = sniff(data)
    check_type(mime)
    from PIL import Image, ImageOps

    try:
        im = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        raise UploadError(413, "too_large", MSG_PIXELS) from None
    except Exception:  # noqa: BLE001 - UnidentifiedImageError, truncated headers, broken HEIF containers
        raise UploadError(415, "unsupported_type", MSG_BROKEN) from None
    try:
        if (im.format or "").upper() not in PIL_FORMATS[mime]:      # the bytes said one thing, the decoder another
            raise UploadError(415, "unsupported_type", MSG_BROKEN)
        w, h = im.size
        if w < 1 or h < 1:
            raise UploadError(415, "unsupported_type", MSG_BROKEN)
        cap = (MAX_PIXELS_JPEG if im.format in ("JPEG", "MPO") else MAX_PIXELS_FLAT if im.format in ("PNG", "WEBP")
               else MAX_PIXELS)
        if w * h > cap:
            raise UploadError(413, "too_large", MSG_PIXELS if cap >= MAX_PIXELS else MSG_PIXELS_FLAT)
        if im.format in ("JPEG", "MPO"):            # decode a huge photo at 1/2-1/8 scale straight from the DCT
            r = LONG_EDGE / max(w, h)
            if r < 0.5:
                im.draft("RGB", (max(1, int(w * r) + 1), max(1, int(h * r) + 1)))
        try:
            im.load()
        except Image.DecompressionBombError:
            raise UploadError(413, "too_large", MSG_PIXELS) from None
        except UploadError:
            raise
        except Exception:  # noqa: BLE001 - truncated or corrupt pixel data
            raise UploadError(415, "unsupported_type", MSG_BROKEN) from None
        if max(im.size) > LONG_EDGE:
            # shrink first, so orientation, colour and flattening work on at most 2048 px, not on the decoded original
            # (a 0.2 MB flat PNG can be 30 MP: four full-size copies were ~800 MB). info keeps orientation and ICC.
            if im.mode in ("P", "1"):              # these resize with nearest neighbour: thin sketch lines would vanish
                im = im.convert("RGBA" if "transparency" in im.info else "RGB" if im.mode == "P" else "L")
            try:
                im.thumbnail((LONG_EDGE, LONG_EDGE), Image.Resampling.LANCZOS, reducing_gap=3.0)
            except ValueError:                     # a mode the resampler does not take (16-bit greyscale): via 32-bit
                im = im.convert("I")
                im.thumbnail((LONG_EDGE, LONG_EDGE), Image.Resampling.LANCZOS, reducing_gap=3.0)
        try:
            out = ImageOps.exif_transpose(im) or im  # orientation applied (EXIF tag 274), then the tag is gone
        except Exception:  # noqa: BLE001 - a corrupt EXIF block: keep the pixels as stored (metadata is dropped anyway)
            out = im
        out = _to_srgb(out)
        out = _flatten(out)
        out.thumbnail((LONG_EDGE, LONG_EDGE), Image.Resampling.LANCZOS)
        thumb = out.copy()
        thumb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
        return _jpeg(out, QUALITY), _jpeg(thumb, THUMB_QUALITY), out.size[0], out.size[1], mime
    finally:
        im.close()


def public(rec: dict) -> dict:
    """The POST /uploads response (contract item 1)."""
    return {k: rec.get(k) for k in ("id", "name", "mime", "bytes", "width", "height", "url", "thumb_url")}


def ingest(store, pid: str, data: bytes, filename: str | None) -> dict:
    """Normalise and store one image; returns the upload record for project.uploads. Raises UploadError."""
    with DECODES:
        jpg, thumb, w, h, mime = normalise(data)
    uid = new_id("u", 10)
    store.write_bytes(upload_key(pid, uid), jpg, "image/jpeg")
    store.write_bytes(upload_key(pid, uid, thumb=True), thumb, "image/jpeg")
    return {"id": uid, "name": clean_name(filename), "mime": "image/jpeg", "bytes": len(jpg), "width": w, "height": h,
            "url": upload_url(pid, uid), "thumb_url": upload_url(pid, uid, thumb=True), "source_mime": mime,
            "created": now_iso()}


def discard(store, pid: str, uid: str) -> None:
    for thumb in (False, True):
        try:
            store.delete(upload_key(pid, uid, thumb))
        except Exception:  # noqa: BLE001 - best effort clean-up
            pass


def find_upload(p: dict, uid: str) -> dict | None:
    return next((u for u in p.get("uploads") or [] if u.get("id") == uid), None)


def attached_ids(p: dict) -> set[str]:
    """Upload ids some chat message carries."""
    return {a.get("id") for m in p.get("chat") or [] for a in m.get("attachments") or [] if isinstance(a, dict)}


def hide_uploads(q: dict) -> dict:
    """A share view while project.share_uploads is off: no upload records, and each chat message's attachments are
    replaced by their count (attachments_hidden), so no file name or image id is shown. The image brief (the reading
    the design is based on) stays, without text_seen (the words copied from the image, often most of a concept
    sheet) and upload_ids."""
    q = dict(q)
    q.pop("uploads", None)
    if isinstance(q.get("image_brief"), dict):
        q["image_brief"] = {k: v for k, v in q["image_brief"].items() if k not in ("text_seen", "upload_ids")}
    chat = []
    for m in q.get("chat") or []:
        if isinstance(m, dict) and m.get("attachments"):
            n = len(m["attachments"])
            m = {k: v for k, v in m.items() if k != "attachments"}
            m["attachments_hidden"] = n
        chat.append(m)
    q["chat"] = chat
    return q


def message_attachments(p: dict, raw) -> list[dict]:
    """Validate a chat body's "attachments" ([{"id": "u_..."}], ids also accepted as plain strings) against the
    project's uploads -> the stored message's attachments [{id, name, mime, width, height}]. Raises UploadError."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise UploadError(422, "invalid", 'attachments must be a list like [{"id": "u_..."}]')
    ids: list[str] = []
    for a in raw:
        uid = a.get("id") if isinstance(a, dict) else a
        if not isinstance(uid, str) or not ID_RE.fullmatch(uid):
            raise UploadError(422, "invalid", "Each attachment needs the id of an image uploaded to this project.")
        if uid not in ids:
            ids.append(uid)
    if len(ids) > PER_MESSAGE:
        raise UploadError(413, "too_many", MSG_MESSAGE)
    out = []
    for uid in ids:
        u = find_upload(p, uid)
        if u is None:
            raise UploadError(422, "invalid", "That image is not part of this project. Please attach it again.")
        out.append({k: u.get(k) for k in ("id", "name", "mime", "width", "height")})
    return out
