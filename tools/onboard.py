#!/usr/bin/env python3
"""Onboard photos from raw-images/ into the bluekoru site.

  tools/onboard.py add raw-images/foo-20200101.jpg   process + propose a look (draft)
  tools/onboard.py tune foo-20200101 --y 20 ...      adjust the look and re-preview
  tools/onboard.py preview foo-20200101              re-render the preview only
  tools/onboard.py approve foo-20200101              make it live in the rotation
  tools/onboard.py list                              show everything in images.json

`add` resizes to WebP and drops all metadata (EXIF, GPS, ICC), proposes a
palette and vertical position, then renders previews/<id>.png (desktop,
tablet and phone side by side) using the real page in headless Chromium.
Draft entries are only shown via index.html?img=<id> until approved.
"""
import argparse
import colorsys
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageOps

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "images.json"
IMG = ROOT / "img"
PREVIEWS = ROOT / "previews"
WIDTHS = [1280, 1920, 2560]
VIEWPORTS = [("desktop", 1920, 1080), ("tablet", 820, 1180), ("phone", 390, 844)]
GLOWS = ("light", "dark", "none")


# ---------- manifest ----------

def load():
    return json.loads(MANIFEST.read_text())


def save(data):
    MANIFEST.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def find(data, id_):
    for entry in data["images"]:
        if entry["id"] == id_:
            return entry
    sys.exit(f"no image with id '{id_}' (try: tools/onboard.py list)")


def slugify(name):
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


# ---------- colour helpers ----------

def lum(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def hexc(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def unhex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def reach_contrast(rgb, bg, target):
    """Move lightness away from the background until contrast >= target."""
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    direction = -1 if lum(bg) > 0.35 else 1
    for _ in range(100):
        cur = tuple(c * 255 for c in colorsys.hls_to_rgb(h, l, s))
        if contrast(cur, bg) >= target or not 0 < l < 1:
            return cur
        l += direction * 0.01
    return cur


# ---------- proposal ----------

def view_16x9(im):
    """The image as a 16:9 desktop viewport would show it (centre crop), small."""
    w, h = im.size
    want = 16 / 9
    if w / h > want:
        nw = int(h * want)
        im = im.crop(((w - nw) // 2, 0, (w + nw) // 2, h))
    else:
        nh = int(w / want)
        im = im.crop((0, (h - nh) // 2, w, (h + nh) // 2))
    return im.resize((320, 180), Image.LANCZOS)


def propose(im):
    view = view_16x9(im)
    arr = np.asarray(view).astype(float)
    grey = arr @ [0.299, 0.587, 0.114]
    gy, gx = np.gradient(grey)
    busy = np.hypot(gx, gy)

    # text sits in roughly the central 60% of the width and ~16% of the height
    band_h, x0, x1 = 29, 64, 256
    best = None
    for top in range(3, 100):
        sl = (slice(top, top + band_h), slice(x0, x1))
        score = busy[sl].mean() + 0.4 * grey[sl].std() + 0.03 * top
        if best is None or score < best[0]:
            best = (score, top)
    top = best[1]
    bg = arr[top:top + band_h, x0:x1].reshape(-1, 3).mean(axis=0)

    # dominant colours of the whole view
    q = view.quantize(colors=12, method=Image.MEDIANCUT)
    pal = q.getpalette()[:36]
    idx_counts = np.bincount(np.asarray(q).ravel(), minlength=12)
    cols = []
    for i in range(12):
        rgb = tuple(pal[i * 3:i * 3 + 3])
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        cols.append((rgb, h * 360, s, v, idx_counts[i] / idx_counts.sum()))

    # both colours come from the photo's own palette, nudged only as far as needed to be legible
    adj = [(reach_contrast(c[0], bg, 3.5), c) for c in cols]
    if max(c[2] for c in cols) < 0.15:  # monochrome photo: first colour is just the strongest tone
        a_rgb = max(adj, key=lambda x: contrast(x[0], bg))[0]
    else:
        a_rgb = max(adj, key=lambda x: x[1][2] * (0.3 + x[1][4] ** 0.5) * (0.4 + x[1][3]))[0]

    def hsv(rgb):
        return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))

    ha, sa, _ = hsv(a_rgb)

    def distinct(rgb):
        h, s, _ = hsv(rgb)
        dh = min(abs(h - ha), 1 - abs(h - ha)) * 2  # 0..1
        return dh * min(s, sa) * 2 + abs(lum(rgb) - lum(a_rgb)) * 2

    b_rgb = max((x[0] for x in adj), key=distinct)
    # the darker of the two reads as the first word
    cool, warm = sorted([a_rgb, b_rgb], key=lum)

    ink, fire = cool, warm
    glow = "light" if lum(bg) > 0.35 else "dark"
    return {
        "ink": hexc(ink),
        "fire": hexc(fire),
        "y": round(top / 180 * 100, 1),
        "focus": "50% 50%",
        "glow": glow,
    }


# ---------- image processing ----------

def metadata_in(path: Path):
    """Names of any metadata blocks found in a file (empty list = clean)."""
    found = []
    data = path.read_bytes()
    for chunk in (b"EXIF", b"XMP ", b"ICCP"):
        if chunk in data[:65536] or chunk in data:
            found.append(chunk.decode().strip())
    with Image.open(path) as im:
        if im.getexif():
            found.append("exif-tags")
        extra = [k for k in im.info if k not in ("loop", "background", "duration")]
        found += [k for k in extra if k not in found]
    return sorted(set(found))


def process(raw: Path, id_, crop_right=0.0):
    im = Image.open(raw)
    had_gps = bool(im.getexif().get_ifd(0x8825))
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im.convert("RGB"), src,
                                           ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:
            pass
    im = im.convert("RGB")
    if crop_right:  # drop a strip from the right edge (e.g. to lose someone at the frame edge)
        im = im.crop((0, 0, round(im.width * (1 - crop_right)), im.height))
    widths = [w for w in WIDTHS if w <= im.width] or [im.width]
    IMG.mkdir(exist_ok=True)
    for w in widths:
        out = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
        # build a clean image from raw pixels only, so no EXIF/XMP/ICC/text chunks can carry over,
        # and pass exif/xmp/icc explicitly as empty
        clean = Image.frombytes("RGB", out.size, out.tobytes())
        dest = IMG / f"{id_}-{w}.webp"
        clean.save(dest, "WEBP", quality=82, method=6, exif=b"", xmp=b"", icc_profile=None)
        leftover = metadata_in(dest)
        if leftover:
            dest.unlink()
            sys.exit(f"metadata still present in {dest.name}: {leftover}; refusing to keep it")
    return im, widths, had_gps


# ---------- preview ----------

def render_preview(id_):
    browser = shutil.which("chromium-browser") or shutil.which("chromium") or shutil.which("google-chrome")
    if not browser:
        print("  (no Chromium found; skipping preview)")
        return None
    url = f"file://{ROOT}/index.html?still&img={id_}"
    shots = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, w, h in VIEWPORTS:
            path = Path(tmp) / f"{name}.png"
            for _attempt in range(4):
                path.unlink(missing_ok=True)
                subprocess.run(
                    [browser, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
                     f"--user-data-dir={tmp}/profile-{name}-{_attempt}",
                     f"--window-size={w},{h}", "--virtual-time-budget=15000",
                     f"--screenshot={path}", url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                if path.exists():
                    shot = Image.open(path).convert("RGB")
                    # a blank page is a single flat colour; retry if we caught it too early
                    if len(shot.getcolors(maxcolors=1_000_000) or []) > 50 or _attempt == 3:
                        shots.append(shot)
                        break
    if len(shots) != len(VIEWPORTS):
        print("  (screenshot failed; no preview)")
        return None
    target_h = 700
    shots = [s.resize((round(s.width * target_h / s.height), target_h), Image.LANCZOS) for s in shots]
    sheet = Image.new("RGB", (sum(s.width for s in shots) + 20 * (len(shots) + 1), target_h + 40), "#222")
    x = 20
    for s in shots:
        sheet.paste(s, (x, 20))
        x += s.width + 20
    PREVIEWS.mkdir(exist_ok=True)
    out = PREVIEWS / f"{id_}.png"
    sheet.save(out)
    return out


def report(entry):
    print(f"  id     {entry['id']}" + ("   [DRAFT]" if entry.get("draft") else "   [live]"))
    print(f"  ink    {entry['ink']}   fire {entry['fire']}   glow {entry['glow']}")
    print(f"  y      {entry['y']}vh   focus {entry['focus']}")
    print(f"  alt    {entry['alt']!r}   place {entry.get('place', '-')!r}")


# ---------- commands ----------

def cmd_add(a):
    raw = Path(a.raw)
    if not raw.exists():
        sys.exit(f"{raw} not found")
    id_ = slugify(raw.stem)
    data = load()
    existing = next((e for e in data["images"] if e["id"] == id_), None)
    crop = a.crop_right if a.crop_right is not None else (existing or {}).get("crop_right", 0.0)
    im, widths, had_gps = process(raw, id_, crop)
    if existing:
        existing.update(widths=widths, aspect=round(im.width / im.height, 4))
        if crop:
            existing["crop_right"] = crop
        else:
            existing.pop("crop_right", None)
        entry = existing
        print(f"Reprocessed images for existing entry; kept its look.")
    else:
        entry = {
            "id": id_,
            "alt": a.alt or id_.replace("-", " "),
            "base": f"img/{id_}",
            "widths": widths,
            "aspect": round(im.width / im.height, 4),
            **propose(im),
            "draft": True,
        }
        if crop:
            entry["crop_right"] = crop
        data["images"].append(entry)
    save(data)
    print(f"Wrote {len(widths)} WebP sizes to img/ (all metadata stripped and verified"
          + ("; original had GPS data, now removed" if had_gps else "") + ")")
    report(entry)
    out = render_preview(id_)
    if out:
        print(f"  preview {out}")
    if entry["aspect"] < 1:
        print("  note: portrait photo; expect heavy cropping on landscape screens")


def cmd_tune(a):
    data = load()
    entry = find(data, a.id)
    if a.ink:
        entry["ink"] = hexc(unhex(a.ink))
    if a.fire:
        entry["fire"] = hexc(unhex(a.fire))
    if a.y is not None:
        entry["y"] = a.y
    if a.focus:
        entry["focus"] = a.focus
    if a.glow:
        entry["glow"] = a.glow
    if a.alt:
        entry["alt"] = a.alt
    if a.place is not None:
        entry["place"] = a.place
    save(data)
    report(entry)
    out = render_preview(a.id)
    if out:
        print(f"  preview {out}")


def cmd_preview(a):
    entry = find(load(), a.id)
    report(entry)
    out = render_preview(a.id)
    if out:
        print(f"  preview {out}")


def cmd_approve(a):
    data = load()
    entry = find(data, a.id)
    entry.pop("draft", None)
    save(data)
    print(f"{a.id} is now live in the rotation.")


def cmd_list(_):
    for e in load()["images"]:
        print(f"{'draft' if e.get('draft') else 'live ':5}  {e['id']}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add"); s.add_argument("raw"); s.add_argument("--alt")
    s.add_argument("--crop-right", type=float, help="fraction to trim off the right edge, e.g. 0.16 (kept on re-runs)")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("tune"); s.add_argument("id")
    s.add_argument("--ink"); s.add_argument("--fire"); s.add_argument("--y", type=float)
    s.add_argument("--focus", help='CSS object-position, e.g. "40%% 60%%"')
    s.add_argument("--glow", choices=GLOWS); s.add_argument("--alt")
    s.add_argument("--place", help='caption under the wordmark, e.g. "Nikko, JP" ("" removes it)'); s.set_defaults(fn=cmd_tune)
    s = sub.add_parser("preview"); s.add_argument("id"); s.set_defaults(fn=cmd_preview)
    s = sub.add_parser("approve"); s.add_argument("id"); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("list"); s.set_defaults(fn=cmd_list)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
