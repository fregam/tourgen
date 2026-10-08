"""
FILE: scripts/make_fixtures.py
ROLE: Generates the synthetic sample property photos in tests/fixtures/photos/.

HOW IT WORKS:
  - Draws simple one-point-perspective rooms/facades with Pillow (walls, floor,
    windows, doors, a few "furniture" blocks) with seeded texture noise, so each
    image has distinct content, realistic sharpness and a distinct phash.
  - Also writes one near-duplicate (to exercise dedupe) and one detail shot.

READS: nothing
WRITES: tests/fixtures/photos/*.jpg

CONNECTS TO:
  - `make fixtures`; consumed by tests and `make run-sample`.

INVARIANTS / GOTCHAS:
  - Filenames carry the room type; llm/fake_llm.py relies on that.
  - Synthetic on purpose: no third-party photo rights involved.
"""

from __future__ import annotations

import random
from pathlib import Path

import imagehash
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "photos"
W, H = 1280, 854

ROOMS = {
    "exterior_front": dict(
        wall=(196, 178, 150), floor=(90, 140, 80), sky=True, windows=4, door=True
    ),
    "entry": dict(wall=(225, 220, 210), floor=(120, 90, 60), windows=0, door=True),
    "living_room_1": dict(wall=(235, 228, 215), floor=(150, 110, 70), windows=2, sofa=True),
    "living_room_2": dict(wall=(210, 220, 225), floor=(140, 100, 65), windows=3, sofa=True),
    "dining_room": dict(wall=(230, 215, 200), floor=(130, 95, 60), windows=2, table=True),
    "kitchen": dict(wall=(240, 240, 235), floor=(170, 170, 165), windows=1, island=True),
    "bedroom": dict(wall=(215, 205, 225), floor=(160, 120, 85), windows=1, bed=True),
    "bathroom": dict(wall=(220, 235, 240), floor=(200, 200, 200), windows=1, tub=True),
    "garden": dict(wall=(180, 160, 130), floor=(70, 150, 70), sky=True, windows=2),
}


def _noise(img: Image.Image, seed: int, amount: int = 18) -> Image.Image:
    rng = random.Random(seed)
    noise = Image.effect_noise((W, H), 40).convert("RGB")
    blended = Image.blend(img, noise, 0.06)
    draw = ImageDraw.Draw(blended)
    for _ in range(400):
        x, y = rng.randrange(W), rng.randrange(H)
        shade = rng.randrange(-amount, amount)
        base = blended.getpixel((x, y))
        assert isinstance(base, tuple)
        color = tuple(max(0, min(255, c + shade)) for c in base[:3])
        draw.rectangle([x, y, x + rng.randrange(2, 9), y + rng.randrange(2, 9)], fill=color)
    return blended


def draw_room(name: str, spec: dict[str, object], seed: int) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H), spec["wall"])  # type: ignore[arg-type]
    d = ImageDraw.Draw(img)
    vx, vy = W // 2 + rng.randrange(-260, 260), H // 2 + rng.randrange(-90, 60)
    bw, bh = rng.randrange(220, 420), rng.randrange(140, 230)
    back = (vx - bw, vy - bh, vx + bw, vy + int(bh * 0.8))
    if spec.get("sky"):
        d.rectangle([0, 0, W, int(H * 0.45)], fill=(150, 190, 235))
        d.rectangle([0, int(H * 0.62), W, H], fill=spec["floor"])  # type: ignore[arg-type]
        d.polygon(
            [(220, int(H * 0.62)), (W - 220, int(H * 0.62)), (W - 260, 260), (260, 260)],
            fill=spec["wall"],
        )  # type: ignore[arg-type]
        d.polygon([(220, 260), (W // 2, 120), (W - 220, 260)], fill=(110, 60, 50))
    else:
        d.polygon([(0, H), (W, H), (back[2], back[3]), (back[0], back[3])], fill=spec["floor"])  # type: ignore[arg-type]
        d.polygon([(0, 0), (W, 0), (back[2], back[1]), (back[0], back[1])], fill=(245, 245, 240))
        d.rectangle(back, outline=(120, 120, 120), width=3)
        for x in (0, W):
            d.line(
                [(x, 0), (back[0] if x == 0 else back[2], back[1])], fill=(130, 130, 130), width=3
            )
            d.line(
                [(x, H), (back[0] if x == 0 else back[2], back[3])], fill=(130, 130, 130), width=3
            )
    windows = int(spec.get("windows", 0))  # type: ignore[call-overload]
    for i in range(windows):
        wx = back[0] + 30 + i * ((back[2] - back[0] - 60) // max(windows, 1))
        d.rectangle(
            [wx, back[1] + 40, wx + 110, back[1] + 180],
            fill=(170, 210, 240),
            outline=(255, 255, 255),
            width=6,
        )
        d.line([(wx + 55, back[1] + 50), (wx + 55, back[1] + 190)], fill=(255, 255, 255), width=4)
    if spec.get("door"):
        d.rectangle(
            [vx - 60, vy - 60, vx + 60, back[3]], fill=(100, 70, 45), outline=(60, 40, 30), width=4
        )
    furniture = [k for k in ("sofa", "table", "island", "bed", "tub") if spec.get(k)]
    for k in furniture:
        y0 = back[3] + rng.randrange(30, 140)
        color = {
            "sofa": (90, 100, 130),
            "table": (110, 75, 45),
            "island": (240, 240, 240),
            "bed": (250, 250, 250),
            "tub": (255, 255, 255),
        }[k]
        fx = vx + rng.randrange(-200, 200)
        d.rounded_rectangle(
            [fx - 260, y0, fx + 260, y0 + 170], radius=18, fill=color, outline=(60, 60, 60), width=3
        )
    img = _noise(img, seed)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=60))


MIN_PHASH_DISTANCE = 12  # well above the dedupe threshold (media/quality.py)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    hashes: list[imagehash.ImageHash] = []
    for index, (name, spec) in enumerate(ROOMS.items(), start=1):
        seed = index * 101
        while True:  # re-roll until clearly distinct from every earlier room
            img = draw_room(name, spec, seed)
            h = imagehash.phash(img)
            if all(h - other >= MIN_PHASH_DISTANCE for other in hashes):
                break
            seed += 1
        hashes.append(h)
        img.save(OUT / f"{name}.jpg", quality=80)
    kitchen = Image.open(OUT / "kitchen.jpg")
    kitchen.crop((380, 420, 900, 767)).resize((1280, 854)).save(
        OUT / "kitchen_detail.jpg", quality=80
    )
    living = Image.open(OUT / "living_room_1.jpg")
    living.resize((1200, 800)).save(OUT / "living_room_1_copy.jpg", quality=70)
    print(f"wrote fixtures to {OUT}")


if __name__ == "__main__":
    main()
