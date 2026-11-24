import io
import os
import json
import logging
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, Response, jsonify, request
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

INFO_API = "INFO-API"

BANNER_APIS = [
    "https://raw.githubusercontent.com/pankaj07-ux/ff-assets/main/banners/{}.png",
]

ICON_APIS = [
    "https://www.dl.cdn.freefireofficial.com/icons/{}.png",
    "https://ff-item.netlify.app/icons/{}.png",
    "https://iconapi.wasmer.app/{}",
]

CONNECT_TIMEOUT = 5
READ_TIMEOUT    = 25

DEFAULT_BANNER_ID = "900000014"
DEFAULT_AVATAR_ID = "900000013"

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
FRAME_PATH = os.path.join(BASE_DIR, "frame.png")

# ═══════════════════════════════════════════════════════════════
#  BOX COORDINATES — updated 1536×647 frame.png
#  Avatar window: left panel; Banner window: right panel.
#  Coordinates are aligned to the transparent openings in frame.png.
# ═══════════════════════════════════════════════════════════════

AVATAR_BOX = (61, 99, 495, 566)
BANNER_BOX = (542, 99, 1470, 566)

UA = {"User-Agent": "BannerAPI/7.0"}

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger("BannerAPI")


# ═══════════════════════════════════════════════════════════════
#  JSON HELPERS
# ═══════════════════════════════════════════════════════════════

def _norm(k):
    return str(k).lower().replace("_", "").replace("-", "").strip()


def _scalar_id(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (str, int, float)):
        s = str(v).strip()
        if s and s != "0" and s.lower() not in {"none", "null", "false", ""}:
            return s
    if isinstance(v, dict):
        for k, val in v.items():
            if _norm(k) in {"id", "value", "itemid", "resourceid"}:
                f = _scalar_id(val)
                if f:
                    return f
    if isinstance(v, list) and v:
        return _scalar_id(v[0])
    return None


def find_value(data, names):
    if isinstance(data, str):
        s = data.strip()
        if s.startswith(("{", "[")):
            try:
                return find_value(json.loads(s), names)
            except (ValueError, TypeError):
                return None
        return None
    if isinstance(data, dict):
        for k, v in data.items():
            if _norm(k) in names:
                f = _scalar_id(v)
                if f is not None:
                    return f
        for k, v in data.items():
            nk = _norm(k)
            if any(nk.endswith(n) or nk.startswith(n) for n in names if len(n) >= 6):
                f = _scalar_id(v)
                if f is not None:
                    return f
        for v in data.values():
            f = find_value(v, names)
            if f is not None:
                return f
    elif isinstance(data, list):
        for item in data:
            f = find_value(item, names)
            if f is not None:
                return f
    return None


BANNER_KEYS = {
    "bannerid", "accountbannerid", "currentbannerid", "equippedbannerid",
    "banner", "accountbanner", "currentbanner", "equippedbanner",
    "banneridvalue", "bannerresourceid", "profilebannerid", "selectedbannerid",
}
AVATAR_KEYS = {
    "avatarid", "accountavatarid", "currentavatarid", "equippedavatarid",
    "headpic", "headpicid", "avatar", "accountavatar", "currentavatar",
    "equippedavatar", "profilepic", "profilepicid", "avatarresourceid",
    "selectedavatarid", "characteravatarid", "portraitid",
}
NAME_KEYS  = {"nickname", "accountname", "playername", "name", "username"}
LEVEL_KEYS = {"level", "accountlevel", "playerlevel", "lvl"}


def extract_info(data):
    banner = find_value(data, BANNER_KEYS)
    avatar = find_value(data, AVATAR_KEYS)
    return {
        "banner_id":      banner or DEFAULT_BANNER_ID,
        "avatar_id":      avatar or DEFAULT_AVATAR_ID,
        "name":           find_value(data, NAME_KEYS) or "",
        "level":          find_value(data, LEVEL_KEYS) or "",
        "banner_default": banner is None,
        "avatar_default": avatar is None,
    }


# ═══════════════════════════════════════════════════════════════
#  MULTI-CDN ICON FETCHING
# ═══════════════════════════════════════════════════════════════

@lru_cache(maxsize=2048)
def _cached_icon_bytes(item_id):
    for api in ICON_APIS:
        url = api.format(item_id)
        try:
            r = requests.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), headers=UA)
            if not r.ok:
                continue
            ct = r.headers.get("Content-Type", "").lower()
            raw = r.content
            is_img = "image" in ct or raw.startswith(
                (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"WEBP")
            )
            if not is_img or len(raw) < 200:
                continue
            with Image.open(io.BytesIO(raw)) as im:
                im.verify()
            logger.info(f"✅ Icon {item_id} from {url.split('/')[2]}")
            return raw
        except (requests.RequestException, OSError, ValueError):
            continue
    logger.warning(f"❌ All CDNs failed for {item_id}")
    return None


def fetch_icon_bytes(item_id):
    result = _cached_icon_bytes(str(item_id))
    if result is None:
        _cached_icon_bytes.cache_clear()
    return result


def get_icon(item_id, fallback_id):
    for iid in dict.fromkeys((str(item_id), str(fallback_id))):
        raw = fetch_icon_bytes(iid)
        if raw:
            try:
                with Image.open(io.BytesIO(raw)) as im:
                    return im.convert("RGBA"), iid
            except (OSError, ValueError):
                continue
    return None, str(item_id)


@lru_cache(maxsize=2048)
def _cached_banner_bytes(item_id):
    """Fetch the actual wide banner artwork by its banner ID."""
    for api in BANNER_APIS:
        url = api.format(item_id)
        try:
            r = requests.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), headers=UA)
            if not r.ok:
                continue
            ct = r.headers.get("Content-Type", "").lower()
            raw = r.content
            is_img = "image" in ct or raw.startswith(
                (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"WEBP")
            )
            if not is_img or len(raw) < 200:
                continue
            with Image.open(io.BytesIO(raw)) as im:
                im.verify()
            logger.info(f"✅ Full banner {item_id} from {url.split('/')[2]}")
            return raw
        except (requests.RequestException, OSError, ValueError):
            continue
    return None


def get_banner(item_id, fallback_id):
    """Prefer the actual banner artwork; use an icon only as a last resort."""
    for iid in dict.fromkeys((str(item_id), str(fallback_id))):
        raw = _cached_banner_bytes(iid)
        if raw:
            try:
                with Image.open(io.BytesIO(raw)) as im:
                    return im.convert("RGBA"), iid
            except (OSError, ValueError):
                pass
    logger.warning("Full banner artwork unavailable; falling back to item icon")
    return get_icon(item_id, fallback_id)


# ═══════════════════════════════════════════════════════════════
#  FRAME
# ═══════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def load_frame():
    with Image.open(FRAME_PATH) as im:
        return im.convert("RGBA")


# ═══════════════════════════════════════════════════════════════
#  COVER — বক্সের পুরো জায়গা ভরাট করবে (crop, no padding)
# ═══════════════════════════════════════════════════════════════

def cover(img, target_w, target_h):
    """
    ছবিকে বক্সের PERFECT SIZE এ resize + center-crop করি।
    Aspect ratio ঠিক থাকবে, এবং পুরো বক্স ভরাট হবে।
    """
    img_ratio    = img.width / img.height
    target_ratio = target_w / target_h

    if img_ratio > target_ratio:
        # ছবি বেশি চওড়া → height fit
        new_h = target_h
        new_w = int(target_h * img_ratio)
    else:
        # ছবি বেশি লম্বা → width fit
        new_w = target_w
        new_h = int(target_w / img_ratio)

    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Center crop to EXACT target size
    left = (new_w - target_w) // 2
    top  = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def fit_banner(img, target_w, target_h):
    """Show the complete banner as a straight, square-cornered rectangle.

    Full banner artwork is requested by banner ID first, rather than using the
    tilted thumbnail from an item-icon library. Resize the actual artwork to
    the window dimensions so none of the artwork is center-cropped.
    """
    img = img.convert("RGBA")
    return img.resize((target_w, target_h), Image.Resampling.LANCZOS)


# ═══════════════════════════════════════════════════════════════
#  WATERMARK
# ═══════════════════════════════════════════════════════════════

def add_watermark(canvas):
    try:
        draw = ImageDraw.Draw(canvas, "RGBA")
        text = "t.me/HYPER_APEX_PRIME"
        font = ImageFont.load_default()
        for fp in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]:
            if os.path.exists(fp):
                try:
                    font = ImageFont.truetype(fp, 22)
                    break
                except OSError:
                    pass
        bbox = draw.textbbox((0, 0), text, font=font)
        x = canvas.width  - (bbox[2] - bbox[0]) - 30
        y = canvas.height - (bbox[3] - bbox[1]) - 25
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y),         text, font=font, fill=(255, 255, 255, 230))
    except Exception as e:
        logger.warning(f"Watermark failed: {e}")
    return canvas


# ═══════════════════════════════════════════════════════════════
#  BANNER COMPOSITION
# ═══════════════════════════════════════════════════════════════

def make_banner(info):
    # ─── Fetch icons in parallel ───
    with ThreadPoolExecutor(max_workers=2) as pool:
        fb = pool.submit(get_banner, info["banner_id"], DEFAULT_BANNER_ID)
        fa = pool.submit(get_icon, info["avatar_id"], DEFAULT_AVATAR_ID)
        banner_img, banner_used = fb.result()
        avatar_img, avatar_used = fa.result()

    # ─── Load frame ───
    frame = load_frame()
    W, H = frame.size

    # ─── Canvas ───
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # ─── Avatar — পুরো বক্স ভরাট করবে ───
    if avatar_img is not None:
        x0, y0, x1, y1 = AVATAR_BOX
        bw, bh = x1 - x0, y1 - y0
        fitted = cover(avatar_img, bw, bh)
        canvas.alpha_composite(fitted, (x0, y0))
        logger.info(f"   ✅ Avatar pasted at ({x0},{y0}) size {bw}×{bh}")

    # ─── Banner — পুরো বক্স ভরাট করবে ───
    if banner_img is not None:
        x0, y0, x1, y1 = BANNER_BOX
        bw, bh = x1 - x0, y1 - y0
        fitted = fit_banner(banner_img, bw, bh)
        canvas.alpha_composite(fitted, (x0, y0))
        logger.info(f"   ✅ Banner pasted at ({x0},{y0}) size {bw}×{bh}")

    # ─── Frame overlay ───
    canvas.alpha_composite(frame)

    # ─── Watermark ───
    canvas = add_watermark(canvas)

    # ─── Save ───
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True, compress_level=6)
    return out.getvalue(), banner_used, avatar_used, (W, H)


# ═══════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════

@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return resp


@app.get("/")
def root():
    frame = load_frame()
    return jsonify({
        "status":      "online",
        "api":         "Free Fire Banner API",
        "owner":       "@RG_RIFAT_07",
        "output_size": f"{frame.width}x{frame.height}",
        "avatar_box":  AVATAR_BOX,
        "banner_box":  BANNER_BOX,
        "usage":       "/api?uid=PLAYER_UID",
    })


@app.get("/api")
@app.get("/banner")
def banner():
    uid = (request.args.get("uid") or "").strip()

    if not uid:
        return jsonify({"error": "Missing ?uid= parameter"}), 400
    if not uid.isdigit() or len(uid) > 24:
        return jsonify({"error": "UID must be 1-24 digits"}), 400

    # ─── Fetch player info ───
    try:
        r = requests.get(
            INFO_API.format(uid=uid),
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            headers=UA,
        )
        r.raise_for_status()
        info = extract_info(r.json())
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"Info API failed for {uid}: {e}")
        info = {
            "banner_id":      DEFAULT_BANNER_ID,
            "avatar_id":      DEFAULT_AVATAR_ID,
            "name":           "",
            "level":          "",
            "banner_default": True,
            "avatar_default": True,
        }

    # ─── JSON mode ───
    if request.args.get("format") == "json":
        return jsonify({**info, "uid": uid})

    # ─── PNG mode ───
    try:
        img_bytes, b_used, a_used, size = make_banner(info)
    except Exception as e:
        logger.exception(f"Banner failed for {uid}")
        return jsonify({"error": "Banner generation failed", "detail": str(e)}), 500

    return Response(
        img_bytes,
        mimetype="image/png",
        headers={
            "Cache-Control":       "no-store, max-age=0",
            "X-Banner-ID":         b_used,
            "X-Avatar-ID":         a_used,
            "X-Output-Size":       f"{size[0]}x{size[1]}",
            "Content-Disposition": f'inline; filename="banner-{uid}.png"',
        },
    )


if __name__ == "__main__":
    frame = load_frame()
    logger.info(f"🎨 Banner API — Frame size: {frame.width}x{frame.height}")
    logger.info(f"   Avatar box: {AVATAR_BOX}  ({AVATAR_BOX[2]-AVATAR_BOX[0]}×{AVATAR_BOX[3]-AVATAR_BOX[1]})")
    logger.info(f"   Banner box: {BANNER_BOX}  ({BANNER_BOX[2]-BANNER_BOX[0]}×{BANNER_BOX[3]-BANNER_BOX[1]})")
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
