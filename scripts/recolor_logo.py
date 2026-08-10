"""Recolor Brave logo: neon yellow-green/black -> #5B8DEF / #0B0E14."""
from pathlib import Path

import numpy as np
from PIL import Image

OUT_DIR = Path(r"c:\Users\ECSZMLPT0067\Downloads\Projects\Brave\brave-app\assets")
SRC = OUT_DIR / "_src_logo.png"

NAVY = np.array([0x0B, 0x0E, 0x14], dtype=np.float32)
BLUE = np.array([0x5B, 0x8D, 0xEF], dtype=np.float32)


def recolor(img: Image.Image) -> Image.Image:
    arr = np.array(img.convert("RGBA"))
    rgb = arr[:, :, :3].astype(np.float32)
    a = arr[:, :, 3].astype(np.float32)

    lum = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
    logo_w = np.clip((lum - 20.0) / 60.0, 0.0, 1.0)

    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    chroma_yg = np.clip((g - b) / 40.0, 0.0, 1.0) * np.clip(g / 80.0, 0.0, 1.0)
    logo_w = np.clip(np.maximum(logo_w, chroma_yg * 0.85), 0.0, 1.0)

    out = np.empty_like(rgb)
    for c in range(3):
        out[:, :, c] = NAVY[c] * (1.0 - logo_w) + BLUE[c] * logo_w

    result = np.dstack([out.astype(np.uint8), a.astype(np.uint8)])
    return Image.fromarray(result, "RGBA")


def fit_square(im: Image.Image, size: int, pad_ratio: float = 0.14) -> Image.Image:
    """Center logo on navy square with padding; return opaque RGB."""
    aarr = np.array(im)
    # Blue logo pixels for bounding box
    mask = (aarr[:, :, 2].astype(np.int16) > 100) & (aarr[:, :, 0].astype(np.int16) < 200)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        content = im
    else:
        pad = 8
        x0 = max(0, int(xs.min()) - pad)
        y0 = max(0, int(ys.min()) - pad)
        x1 = min(im.width, int(xs.max()) + pad + 1)
        y1 = min(im.height, int(ys.max()) + pad + 1)
        content = im.crop((x0, y0, x1, y1))

    max_side = int(size * (1.0 - 2 * pad_ratio))
    content = content.copy()
    content.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0x0B, 0x0E, 0x14, 255))
    x = (size - content.width) // 2
    y = (size - content.height) // 2
    canvas.paste(content, (x, y), content)

    bg = Image.new("RGB", (size, size), (0x0B, 0x0E, 0x14))
    bg.paste(canvas, mask=canvas.split()[-1])
    return bg


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"missing source: {SRC}")
    img = Image.open(SRC)
    print("source:", SRC, "size:", img.size, "mode:", img.mode)
    recolored = recolor(img)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    paths = {
        "icon.png": fit_square(recolored, 1024, pad_ratio=0.14),
        "adaptive-icon.png": fit_square(recolored, 1024, pad_ratio=0.18),
        "splash-icon.png": fit_square(recolored, 1024, pad_ratio=0.22),
        "favicon.png": fit_square(recolored, 48, pad_ratio=0.12),
    }
    for name, im in paths.items():
        p = OUT_DIR / name
        im.save(p, "PNG")
        print("wrote", p, im.size, p.stat().st_size)

    preview = OUT_DIR / "logo-recolor-preview.png"
    flat = Image.new("RGB", recolored.size, (0x0B, 0x0E, 0x14))
    flat.paste(recolored, mask=recolored.split()[-1])
    flat.save(preview, "PNG")
    print("preview", preview)

    sample = np.array(paths["icon.png"])
    print("corner (expect navy):", sample[0, 0])
    blue_mask = (sample[:, :, 2] > 180) & (sample[:, :, 0] < 120)
    ys, xs = np.where(blue_mask)
    if len(xs):
        mid = len(ys) // 2
        print("logo sample (expect blue):", sample[ys[mid], xs[mid]])
    else:
        c = sample[400:600, 400:600].reshape(-1, 3)
        print("no strong blue — center mean:", c.mean(axis=0))


if __name__ == "__main__":
    main()
