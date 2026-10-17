import argparse
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np

from fetch import fetch_cameras

DATA = Path(__file__).parents[1] / "data"
FRAMES = DATA / "camera_frames"  # the analysed frame per camera, for the UI to draw boxes on
LOG = DATA / "camera_log.jsonl"  # every reading, appended, so the simulator can learn typical congestion by time of day
SF_TZ = ZoneInfo("America/Los_Angeles")  # readings are bucketed by San Francisco hour wherever this runs
TRACKER = Path(__file__).with_name("trackers.yaml")
VEHICLES = [2, 3, 5, 7]  # COCO car, motorcycle, bus, truck
CLIP_S = 3
SAMPLE_FPS = 15  # at 10fps small fast cars stop overlapping frame to frame and lose their tracks
MIN_TRACK_FRAMES = 5
MOVING_BOX_H_PER_S = 0.3  # speed threshold in box heights per second, so near and far cars are judged alike
JAM_VEHICLES = 20
K = 3.0
IMGSZ = 960  # Caltrans streams are 320x240 to 720x480; upscaling finds the small far-off cars
CONF = 0.2
POLL_S = 30
BLANK_STD = 15  # live frames measured 34 to 64; the 'No video' card 11.7, black frames about 1
BLANK_SHARE = 0.4  # share of pixels on one grey level: live frames 0.02 or less, placeholders 0.45 to 0.92
N_CAMERAS = 10


def load_cameras():
    path = DATA / "cameras.json"
    if not path.exists():
        DATA.mkdir(exist_ok=True)
        path.write_text(json.dumps(fetch_cameras(), indent=1))
    return json.loads(path.read_text())


def busiest(cams, n):
    calls = DATA / "calls.parquet"
    if not calls.exists():
        return cams[:n]
    import pandas as pd

    df = pd.read_parquet(calls, columns=["lat", "lon"])

    def nearby(c):
        dy = (df.lat - c["lat"]) * 111_000
        dx = (df.lon - c["lon"]) * 111_000 * math.cos(math.radians(c["lat"]))
        return int(((dx ** 2 + dy ** 2) < 1000 ** 2).sum())

    return sorted(cams, key=nearby, reverse=True)[:n]


def read_clip(src, seconds=CLIP_S):
    for _ in range(3):  # Caltrans HLS opens fail intermittently
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
        if cap.isOpened():
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"cannot open {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    stride = max(1, round(fps / SAMPLE_FPS))
    frames = []
    for i in range(int(fps * seconds)):
        ok, frame = cap.read()
        if not ok:
            break
        if i % stride == 0:
            frames.append(frame)
    cap.release()
    if not frames:
        raise RuntimeError(f"no frames from {src}")
    if all(blank(f) for f in frames[:: max(1, len(frames) // 4)]):
        raise RuntimeError("stream shows Caltrans's 'No video' card")  # otherwise it reads as an empty, free-flowing road
    return frames, fps / stride


def blank(frame):
    """Caltrans serves a flat grey 'No video' card or a black frame when a camera is down."""
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return g.std() < BLANK_STD or np.bincount(g.ravel(), minlength=256).max() / g.size > BLANK_SHARE


def measure(frames, fps, model):
    """Vehicle count and share moving from stable tracks, plus the frame showing the most counted vehicles and
    their boxes (normalized x1, y1, x2, y2, moving) so the UI can show what was measured."""
    tracks = {}
    for i, frame in enumerate(frames):
        r = model.track(frame, persist=i > 0, tracker=str(TRACKER), classes=VEHICLES, imgsz=IMGSZ, conf=CONF, verbose=False)[0]
        if r.boxes.id is None:
            continue
        for tid, (x, y, w, h) in zip(r.boxes.id.int().tolist(), r.boxes.xywh.tolist()):
            tracks.setdefault(tid, []).append((i, x, y, w, h))
    moving = {}
    for tid, pts in tracks.items():
        if len(pts) < MIN_TRACK_FRAMES:
            continue
        (i0, x0, y0, _, _), (i1, x1, y1, _, _) = pts[0], pts[-1]
        box_h = float(np.median([p[4] for p in pts]))
        moving[tid] = math.hypot(x1 - x0, y1 - y0) / box_h / ((i1 - i0) / fps) > MOVING_BOX_H_PER_S
    # stable tracks over the clip, not per-frame boxes: YOLOv8n drops most cars in a dense queue on any one frame
    vehicles = len(moving)
    moving_frac = float(np.mean(list(moving.values()))) if moving else 1.0
    seen = [[(tid, p) for tid, pts in tracks.items() if tid in moving for p in pts if p[0] == i] for i in range(len(frames))]
    best = max(range(len(frames)), key=lambda i: len(seen[i]))
    fh, fw = frames[best].shape[:2]
    boxes = [[round(max(x - w / 2, 0) / fw, 4), round(max(y - h / 2, 0) / fh, 4), round(min(x + w / 2, fw) / fw, 4), round(min(y + h / 2, fh) / fh, 4), moving[tid]]
             for tid, (_, x, y, w, h) in seen[best]]
    return vehicles, moving_frac, best, boxes


def save_frame(camera_id, frame):
    FRAMES.mkdir(parents=True, exist_ok=True)
    tmp = FRAMES / f".{camera_id}.tmp.jpg"
    cv2.imwrite(str(tmp), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    os.replace(tmp, FRAMES / f"{camera_id}.jpg")


def slowdown(vehicles, moving_frac):
    return float(np.clip(1 + K * (1 - moving_frac) * min(vehicles / JAM_VEHICLES, 1), 1, 4))


def read_camera(cam, model, src=None):
    frames, fps = read_clip(src or cam["stream"])
    vehicles, moving_frac, best, boxes = measure(frames, fps, model)
    save_frame(cam["camera_id"], frames[best])
    fh, fw = frames[best].shape[:2]
    return {
        "camera_id": cam["camera_id"],
        "name": cam["name"],
        "lat": cam["lat"],
        "lon": cam["lon"],
        "route": cam.get("route", ""),
        "ts": datetime.now(SF_TZ).isoformat(timespec="seconds"),
        "vehicles": vehicles,
        "moving_frac": round(moving_frac, 2),
        "slowdown": round(slowdown(vehicles, moving_frac), 2),
        "frame": {"w": fw, "h": fh, "boxes": boxes},
    }


def poll(cams, model, clips):
    readings = []
    for cam in cams:
        try:
            r = read_camera(cam, model, clips.get(cam["camera_id"]))
        except Exception as e:
            print(f"{cam['camera_id']}: skipped ({e})")
            continue
        if cam["camera_id"] in clips:
            r["source"] = "clip"  # a saved video, not today's traffic
        readings.append(r)
        print(f"{r['camera_id']:6} {r['vehicles']:3d} veh  moving {r['moving_frac']:.2f}  slowdown {r['slowdown']:.2f}  {r['name']}")
    (DATA / "camera_readings.json").write_text(json.dumps(readings, indent=1))
    with LOG.open("a") as f:
        f.writelines(json.dumps({k: v for k, v in r.items() if k != "frame"}) + "\n" for r in readings if not r.get("source"))
    return readings


def main():
    ap = argparse.ArgumentParser(description="Caltrans cameras -> YOLO -> congestion readings")
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--camera", nargs="*", help="camera ids (default: the 10 nearest SF's busiest calls)")
    ap.add_argument("--clip", nargs=2, action="append", default=[], metavar=("CAMERA_ID", "FILE"),
                    help="replay a saved clip instead of the live stream for that camera")
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    clips = dict(args.clip)
    cams = [c for c in load_cameras() if c.get("stream") or c["camera_id"] in clips]  # some Caltrans cameras list no video link
    if args.camera:
        cams = [c for c in cams if c["camera_id"] in args.camera]
    else:
        cams = busiest(cams, N_CAMERAS)

    while True:
        start = time.time()
        poll(cams, model, clips)
        if args.once:
            break
        time.sleep(max(0, POLL_S - (time.time() - start)))


if __name__ == "__main__":
    main()
