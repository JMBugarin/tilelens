"""Record real frames of a game window, with the facts needed to replay them later.

    uv run python tools/capture_real.py --out benchmarks/real/raw/session-1 --count 30 --every 2
    uv run python tools/capture_real.py --out benchmarks/real/raw/session-1 --on-change --count 60

Frames are captured at the display's native resolution without any TileLens window in them, and
saved as lossless PNG next to a `session.json` with the window rectangle, display scale and the
time of every frame. Review each frame before sharing it: a Discord window can also show
conversations. --crop x,y,w,h (frame pixels) keeps only the game canvas.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import cv2
import numpy as np

from tilelens.platforms import get_adapter
from tilelens.platforms.base import Region


def find_window(owner: str) -> Region | None:
    """Largest on-screen window of an app, in screen points (macOS)."""
    import Quartz

    info = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    )
    best, area = None, 0
    for w in info or ():
        if w.get("kCGWindowOwnerName") != owner or int(w.get("kCGWindowLayer", 0)) != 0:
            continue
        b = w["kCGWindowBounds"]
        a = float(b["Width"]) * float(b["Height"])
        if a > area:
            best, area = b, a
    if best is None:
        return None
    return Region(int(best["X"]), int(best["Y"]), int(best["Width"]), int(best["Height"]))


def changed(a: np.ndarray, b: np.ndarray, threshold: float) -> bool:
    small = lambda im: cv2.resize(im, (160, 90), interpolation=cv2.INTER_AREA)  # noqa: E731
    return float(np.abs(small(a).astype(np.int16) - small(b).astype(np.int16)).mean()) > threshold


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--app", default="Discord", help="owner name of the window to record")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--every", type=float, default=2.0, help="seconds between looks")
    ap.add_argument("--on-change", action="store_true", help="save only when the picture changed")
    ap.add_argument("--threshold", type=float, default=1.5, help="mean difference that counts")
    ap.add_argument("--crop", help="x,y,w,h in frame pixels")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    adapter = get_adapter()
    if adapter.screen_permission() is False:
        raise SystemExit(adapter.permission_help())
    args.out.mkdir(parents=True, exist_ok=True)
    crop = tuple(int(v) for v in args.crop.split(",")) if args.crop else None
    frames, last, started = [], None, time.time()
    t0 = time.perf_counter()
    while len(frames) < args.count:
        region = find_window(args.app)
        if region is None:
            print(f"no {args.app} window on screen; waiting")
            time.sleep(args.every)
            continue
        t_grab = time.perf_counter()
        img = adapter.capture(region)
        grab_ms = (time.perf_counter() - t_grab) * 1000
        if crop:
            x, y, w, h = crop
            img = img[y : y + h, x : x + w]
        if not args.on_change or last is None or changed(last, img, args.threshold):
            n = len(frames) + 1
            name = f"frame-{n:04d}.png"
            cv2.imwrite(str(args.out / name), img)
            frames.append(
                {
                    "file": name,
                    "t": round(time.perf_counter() - t0, 3),
                    "grab_ms": round(grab_ms, 1),
                    "region_points": region.to_dict(),
                    "size_px": [int(img.shape[1]), int(img.shape[0])],
                    "scale": round(
                        (img.shape[1] if not crop else crop[2]) / max(1, region.width), 3
                    ),
                }
            )
            print(f"saved {name}  {img.shape[1]}x{img.shape[0]}  scale {frames[-1]['scale']}")
        last = img
        time.sleep(args.every)
    meta = {
        "app": args.app,
        "note": args.note,
        "recorded": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "machine": platform.platform(),
        "crop": crop,
        "frames": frames,
    }
    (args.out / "session.json").write_text(json.dumps(meta, indent=2))
    print(f"{len(frames)} frames in {args.out}")


if __name__ == "__main__":
    main()
