"""Uniform player portraits for Lineup Lab.

The nba-headshots cutouts (players/headshots/face/, transparent PNG, 256px)
are framed differently by era: recent photos fill the square, older ones show
the player smaller with more chest. This finds the face in each cutout and
crops every one the same way (face the same size and in the same place), so
active and retired players look alike in the cards' circles.

Output: docs/faces/<nba_id>.webp, 160px, transparent background (the page
draws the light grey circle). Only files whose source changed are redone
(data/faces_state.json keeps a hash per source file).

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
FACE_FRAC = 0.40      # detected face box width as a share of the output width
FACE_Y = 0.44         # face box centre, from the top, as a share of the height
MAX_FACE_FRAC = 0.56  # zoom in no further than this to avoid an empty band at the bottom
MAX_FACE_FRAC_OUTLINE = 0.42  # outline-only crops: keep heads the same size, allow some grey under the chin

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


def crop(rgba, box, max_frac=MAX_FACE_FRAC):
    alpha = rgba[:, :, 3]
    x, y, w, h = box
    fw = max(w, h)
    cx, cy = x + w / 2, y + h / 2
    side = fw / FACE_FRAC
    # Keep the crop's bottom inside the cutout (no empty band under the
    # shoulders): if it would run past the last opaque row, zoom in, up to
    # MAX_FACE_FRAC.
    rows = np.where(alpha.max(axis=1) > 32)[0]
    bottom = rows[-1] if len(rows) else rgba.shape[0]
    top = cy - side * FACE_Y
    if top + side > bottom + 1:
        need = (bottom + 1 - cy) / (1 - FACE_Y)
        side = max(min(side, need), fw / max_frac)
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
        if state.get(pid, {}).get('hash') == h and os.path.exists(out):
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
        crop(rgba, box, MAX_FACE_FRAC if how == 'face' else MAX_FACE_FRAC_OUTLINE).save(out, 'WEBP', quality=82, method=6)
        state[pid] = {'hash': h, 'stem': stem, 'how': how}
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
