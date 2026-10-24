"""Uniform player portraits for Lineup Lab.

The nba-headshots cutouts (players/headshots/face/, transparent PNG, 256px)
are framed differently by era: recent photos show shoulders and jersey, most
older ones (about 6 in 10 files) stop at the chin or neck. This finds the face
in each cutout, crops every one the same way (face the same size and in the
same place) and ends every portrait at the same line under the chin with a soft
edge, so active and retired players look alike in the cards' circles: a head
on the grey circle, never a jersey for some and a floating head for others.
The few source photos that still have their studio background (no cutout) keep
it, filling the circle, since a head cut out of a rectangle looks worse.

Output: docs/faces/<nba_id>.webp, 160px, transparent background (the page
draws the light grey circle). Only files whose source changed are redone
(data/faces_state.json keeps a hash per source file and the CROP_V used).

Usage:
  python build/lineup_lab_faces.py <folder with the face/*.png cutouts>
  python build/lineup_lab_faces.py --fetch
      (nightly) downloads only the cutouts of players in data/headshots.json
      that have no portrait yet, from the public nba-headshots repo, and crops
      them. No key or token: plain public file downloads.
"""
import glob
import hashlib
import json
import os
import sys
import tempfile
import urllib.request

import cv2
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'docs', 'faces')
STATE = os.path.join(ROOT, 'data', 'faces_state.json')
SIZE = 160
FACE_FRAC = 0.62  # detected face box width as a share of the output width
FACE_Y = 0.52     # face box centre, from the top, as a share of the height
# Where every portrait ends: this many face-box widths below the face centre,
# fading out over FADE widths. Measured on the 1,884 cutouts: the ones that
# stop at the neck end 0.45 to 0.69 widths below the centre (middle 0.59), so
# 0.64 keeps chins and beards and hides any jersey.
CUT = 0.64
FADE = 0.10
# Bump when the framing above changes: every portrait is then redone.
CROP_V = 2

CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
# Second opinion for photos the first detector misses (headbands, tight
# crops): OpenCV's YuNet model (MIT licence, build/models/). Its box is mapped
# onto the first detector's scale so every portrait is framed alike; measured
# on 249 photos where both found the face: first-detector width = 1.214 x
# YuNet width (middle half 1.17 to 1.26), same centre, 0.035 widths higher.
YUNET = cv2.FaceDetectorYN.create(os.path.join(ROOT, 'build', 'models', 'face_detection_yunet_2023mar.onnx'),
                                  '', (256, 256), 0.6)
YN_SCALE, YN_DY = 1.214, -0.035


def detect(rgba):
    """Largest frontal face in the top part of the image, as (x, y, w, h)."""
    rgb = rgba[:, :, :3].copy()
    a = rgba[:, :, 3:4].astype(np.float32) / 255.0
    # composite on mid grey so transparent pixels don't read as black hair
    comp = (rgb.astype(np.float32) * a + 128 * (1 - a)).astype(np.uint8)
    gray = cv2.cvtColor(comp, cv2.COLOR_RGB2GRAY)
    h = gray.shape[0]
    faces = CASCADE.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=5, minSize=(24, 24))
    faces = [f for f in faces if f[1] + f[3] / 2 < h * 0.7]
    if len(faces):
        return max(faces, key=lambda f: f[2] * f[3])
    YUNET.setInputSize((comp.shape[1], comp.shape[0]))
    _, det = YUNET.detect(cv2.cvtColor(comp, cv2.COLOR_RGB2BGR))
    if det is None or not len(det):
        return None
    x, y, w, hh = max(det, key=lambda q: q[2] * q[3])[:4]
    cx, cy = x + w / 2, y + hh / 2 + YN_DY * w
    W = w * YN_SCALE
    return (int(cx - W / 2), int(cy - W / 2), int(W), int(W))


def head_box(alpha):
    """(x, top, width) of the head: the widest opaque span in the top quarter
    of the cutout (head and hair)."""
    rows = np.where(alpha.max(axis=1) > 32)[0]
    if not len(rows):
        return None
    top, bottom = rows[0], rows[-1]
    best = None
    for y in range(top, top + max(2, int((bottom - top) * 0.25))):
        cols = np.where(alpha[y] > 32)[0]
        if len(cols) and (best is None or cols[-1] - cols[0] > best[2]):
            best = (cols[0], top, cols[-1] - cols[0])
    return best


def fallback_box(alpha):
    """No face found: frame it from the outline. Measured on detected photos
    (Kobe Bryant: head 84px wide, face box 92px; LeBron James: 121 and 156),
    the detector's face box is about 1.15 times the head-and-hair width and
    centred about 0.75 of that width below the top of the head; crop() then
    frames it exactly like a detected face."""
    hb = head_box(alpha)
    if hb is None:
        return None
    hx, top, hw = hb
    w = int(hw * 1.15)
    cx = hx + hw / 2
    cy = top + hw * 0.75
    return (int(cx - w / 2), int(cy - w / 2), w, w)


def has_background(alpha):
    """True when the source is a plain photo, not a cutout (opaque corners)."""
    return float(np.mean([alpha[2, 2], alpha[2, -3], alpha[-3, 2], alpha[-3, -3]])) > 128


def crop(rgba, box):
    x, y, w, h = box
    fw = max(w, h)
    cx, cy = x + w / 2, y + h / 2
    if not has_background(rgba[:, :, 3]):
        # same soft line under the chin for every cutout
        a = rgba[:, :, 3].astype(np.float32)
        rows = np.arange(a.shape[0], dtype=np.float32)[:, None]
        a *= np.clip((cy + CUT * fw - rows) / (FADE * fw), 0, 1)
        rgba = rgba.copy()
        rgba[:, :, 3] = a.astype(np.uint8)
    side = fw / FACE_FRAC
    top = cy - side * FACE_Y
    left = cx - side / 2
    im = Image.fromarray(rgba, 'RGBA')
    # crop with transparent padding outside the source
    canvas = Image.new('RGBA', (int(round(side)), int(round(side))), (0, 0, 0, 0))
    canvas.paste(im, (int(round(-left)), int(round(-top))), im)
    return canvas.resize((SIZE, SIZE), Image.LANCZOS)


def main(src):
    os.makedirs(OUT, exist_ok=True)
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    # The NBA serves one identical "no photo" silhouette for players it has no
    # picture of; any file whose bytes match 3+ others is that placeholder and
    # gets no portrait, so the page shows the player's initials instead.
    counts = {}
    for f in glob.glob(os.path.join(src, '*.png')):
        with open(f, 'rb') as fh:
            hh = hashlib.sha256(fh.read()).hexdigest()
        counts[hh] = counts.get(hh, 0) + 1
    placeholder = {hh for hh, c in counts.items() if c >= 3}
    done = found = fell = skipped = 0
    for f in sorted(glob.glob(os.path.join(src, '*.png'))):
        stem = os.path.basename(f)[:-4]
        pid = stem.split('-', 1)[0]
        if not pid.isdigit():
            continue
        with open(f, 'rb') as fh:
            full = hashlib.sha256(fh.read()).hexdigest()
        h = full[:16]
        out = os.path.join(OUT, pid + '.webp')
        if full in placeholder:
            if os.path.exists(out):
                os.remove(out)
            state[pid] = {'hash': h, 'stem': stem, 'how': 'placeholder'}
            continue
        if state.get(pid, {}).get('hash') == h and state[pid].get('v') == CROP_V and os.path.exists(out):
            skipped += 1
            continue
        rgba = np.array(Image.open(f).convert('RGBA'))
        box = detect(rgba)
        how = 'face'
        if box is None:
            box = fallback_box(rgba[:, :, 3])
            how = 'outline'
        if box is None:
            continue
        crop(rgba, box).save(out, 'WEBP', quality=82, method=6)
        state[pid] = {'hash': h, 'stem': stem, 'how': how, 'v': CROP_V}
        done += 1
        found += how == 'face'
        fell += how == 'outline'
    with open(STATE, 'w') as fh:
        json.dump(state, fh, indent=0, sort_keys=True)
        fh.write('\n')
    print(f'faces: {done} written ({found} by face, {fell} by outline), {skipped} unchanged')


RAW = 'https://raw.githubusercontent.com/jsierrahoopshype/nba-headshots/main/players/headshots/face/'


def fetch_missing():
    hp = os.path.join(ROOT, 'data', 'headshots.json')
    if not os.path.exists(hp):
        print('faces: no data/headshots.json')
        return
    with open(hp) as f:
        stems = json.load(f)
    missing = [(pid, st) for pid, st in stems.items() if not os.path.exists(os.path.join(OUT, pid + '.webp'))]
    if not missing:
        print('faces: none missing')
        return
    with tempfile.TemporaryDirectory() as t:
        got = 0
        for pid, st in missing[:300]:
            try:
                with urllib.request.urlopen(RAW + st + '.png', timeout=60) as r, open(os.path.join(t, st + '.png'), 'wb') as f:
                    f.write(r.read())
                got += 1
            except Exception as e:  # noqa: BLE001 - a missing file just stays initials
                print('faces: could not fetch', st, e)
        print(f'faces: fetched {got} of {len(missing)} missing')
        main(t)


if __name__ == '__main__':
    if sys.argv[1:] == ['--fetch']:
        fetch_missing()
    else:
        main(sys.argv[1])
