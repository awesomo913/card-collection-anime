"""Deterministic OpenCV measurements of a card's foil behaviour.

This is the CV half of the hybrid rarity engine. It turns a tilt-burst of frames
into a handful of robust scalars (``schemas.RarityMeasurements``) that map onto
the PHYSICAL discriminators between Rarity Collection rarities:

- foil-line ANGLE  → Quarter Century Secret = horizontal/vertical see-through
  lines (orthogonal); Platinum Secret = diagonal see-through lines.
- sparkle density/MOTION across the burst → holo/prismatic foils sparkle and
  shift under tilt; plain rares barely move.
- name-region COLOUR → Quarter Century's card name is champagne-gold.
- frame vs art VARNISH energy → Prismatic Ultimate has raised varnish texture
  on the colored frame.
- black-border DAZZLE → Prismatic Collector's has a dazzle pattern on the
  border.

``rarity_service`` feeds these numbers + the frames to DeepSeek for the final
call. The numbers are also independently useful as a rule-based fallback when
DeepSeek is unavailable.

Robustness contract: ``measure()`` NEVER raises. OpenCV/numpy missing (dev box
without the wheel) → all-None measurements. Any individual measure throwing →
that field is None, the rest still compute. The request path is never broken by
a CV failure.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import schemas

logger = logging.getLogger(__name__)

# Rectified card canvas (≈ Yu-Gi-Oh 59×86mm aspect). Fixed so relative crops
# (title band, border ring, art) land in the same place every time.
_RECT_W = 590
_RECT_H = 860

Frame = Tuple[bytes, str]


def _imports():
    """Return (cv2, np) or (None, None) when the CV stack isn't installed."""
    try:
        import cv2
        import numpy as np
        return cv2, np
    except Exception as exc:  # noqa: BLE001
        logger.info("rarity_cv: OpenCV/numpy unavailable (%s) — vision-only mode", exc)
        return None, None


def measure(frames: List[Frame]) -> schemas.RarityMeasurements:
    """Extract rarity measurements from a tilt burst. Never raises."""
    cv2, np = _imports()
    if cv2 is None or not frames:
        return schemas.RarityMeasurements()

    bgr_frames = _decode(cv2, np, frames)
    if not bgr_frames:
        return schemas.RarityMeasurements()

    # Rectify every frame we can; fall back to centre-crops when no card quad
    # is found so sparkle/colour still have something sane to chew on.
    rects = [r for r in (_rectify(cv2, np, f) for f in bgr_frames) if r is not None]
    rectified = bool(rects)
    work = rects if rects else [_center_crop(np, f) for f in bgr_frames]
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in work]

    m = schemas.RarityMeasurements(rectified=rectified)

    sharp = _sharpest_index(cv2, np, grays)

    angle = _safe(lambda: _foil_angle(np, grays[sharp]))
    if angle is not None:
        m.foil_line_angle_deg, m.foil_line_strength = angle

    m.sparkle_density = _safe(lambda: _sparkle_density(np, grays))
    m.sparkle_motion = _safe(lambda: _sparkle_motion(np, grays))

    if rectified:
        name = _safe(lambda: _name_region(cv2, np, work[sharp]))
        if name is not None:
            m.name_region_hue, m.name_is_champagne_gold = name
        m.frame_varnish_energy = _safe(lambda: _varnish_energy(cv2, np, grays[sharp]))
        m.border_dazzle_score = _safe(lambda: _border_dazzle(np, grays[sharp]))

    logger.info(
        "rarity_cv frames=%s rectified=%s angle=%s strength=%s sparkle=%s motion=%s gold=%s",
        len(frames), rectified, m.foil_line_angle_deg, m.foil_line_strength,
        m.sparkle_density, m.sparkle_motion, m.name_is_champagne_gold,
    )
    return m


def _safe(fn):
    """Run a measure; swallow + log any failure → None (never sink the rest)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("rarity_cv measure failed: %s", exc)
        return None


# ----- decode / geometry ---------------------------------------------------

def _decode(cv2, np, frames: List[Frame]):
    out = []
    for data, _mime in frames:
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            out.append(img)
    return out


def _center_crop(np, img):
    h, w = img.shape[:2]
    # Centre 70% — drops most of the background when there's no clean quad.
    y0, y1 = int(0.15 * h), int(0.85 * h)
    x0, x1 = int(0.15 * w), int(0.85 * w)
    return img[y0:y1, x0:x1]


def _rectify(cv2, np, img):
    """Perspective-warp the largest 4-corner contour to the fixed card canvas.

    Returns the warped BGR image, or None when no plausible card quad is found.
    """
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 40, 140)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    img_area = float(h * w)
    best = None
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        area = cv2.contourArea(c)
        if area < 0.20 * img_area:
            break  # contours only get smaller — none big enough left
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            best = approx.reshape(4, 2).astype("float32")
            break
    if best is None:
        return None
    src = _order_corners(np, best)
    dst = np.array([[0, 0], [_RECT_W - 1, 0],
                    [_RECT_W - 1, _RECT_H - 1], [0, _RECT_H - 1]], dtype="float32")
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, matrix, (_RECT_W, _RECT_H))


def _order_corners(np, pts):
    """Return corners ordered TL, TR, BR, BL (handles either card orientation)."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([
        pts[np.argmin(s)],  # top-left  = smallest x+y
        pts[np.argmin(d)],  # top-right = smallest y-x
        pts[np.argmax(s)],  # bot-right = largest x+y
        pts[np.argmax(d)],  # bot-left  = largest y-x
    ], dtype="float32")


def _sharpest_index(cv2, np, grays) -> int:
    """Index of the least-blurry frame (max Laplacian variance)."""
    variances = [cv2.Laplacian(g, cv2.CV_64F).var() for g in grays]
    return int(np.argmax(variances)) if variances else 0


# ----- foil-line orientation (FFT) -----------------------------------------

def _foil_angle(np, gray) -> Tuple[float, float]:
    """Dominant foil-line angle (folded to [0,90]°) + line strength.

    See-through foils print a fine periodic line pattern. In the 2D Fourier
    magnitude that shows up as energy concentrated along one orientation. We
    histogram magnitude by angle in an annulus (skip the DC blob + the very
    high-freq noise), find the peak, and fold to [0,90] so 'orthogonal'
    (≈0/90) reads distinct from 'diagonal' (≈45).

    strength = peak_bin / mean_bin — how line-y the foil is (a plain matte
    card has flat angular energy → strength near 1).
    """
    g = gray.astype(np.float64)
    h, w = g.shape
    win = np.outer(np.hanning(h), np.hanning(w))  # kill edge ringing
    f = np.fft.fftshift(np.fft.fft2(g * win))
    mag = np.abs(f)
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    dy = yy - cy
    dx = xx - cx
    r = np.sqrt(dx * dx + dy * dy)
    rmax = 0.5 * min(h, w)
    band = (r > 0.05 * rmax) & (r < 0.9 * rmax)
    ang = (np.degrees(np.arctan2(dy, dx)) % 180.0)
    bins = ang[band].astype(int).clip(0, 179)
    hist = np.bincount(bins, weights=mag[band], minlength=180)
    hist = np.convolve(hist, np.ones(5) / 5.0, mode="same")
    peak = int(hist.argmax())
    strength = float(hist.max() / (hist.mean() + 1e-9))
    angle = float(peak if peak <= 90 else 180 - peak)
    return angle, strength


# ----- sparkle -------------------------------------------------------------

def _sparkle_mask(np, gray):
    thr = np.percentile(gray, 99.0)
    return gray >= thr


def _sparkle_density(np, grays) -> float:
    """Mean fraction of near-blown-out pixels across the burst (holo sparkle)."""
    fracs = [float(_sparkle_mask(np, g).mean()) for g in grays]
    return float(np.mean(fracs)) if fracs else 0.0


def _sparkle_motion(np, grays) -> float:
    """How much the sparkle pattern shifts frame-to-frame (tilt reveals foil).

    Per consecutive pair: |XOR| / |UNION| of the sparkle masks. Static glare
    → ~0; a living holo that dances under tilt → high.
    """
    if len(grays) < 2:
        return 0.0
    masks = [_sparkle_mask(np, g) for g in grays]
    scores = []
    for a, b in zip(masks, masks[1:]):
        union = np.count_nonzero(a | b)
        if union:
            scores.append(np.count_nonzero(a ^ b) / union)
    return float(np.mean(scores)) if scores else 0.0


# ----- name-region colour (champagne-gold) ---------------------------------

def _name_region(cv2, np, rect) -> Tuple[float, bool]:
    """Median hue of the card-name band + champagne-gold flag.

    Champagne gold = warm hue (~40-55°) at moderate saturation and high value.
    Used to separate Quarter Century Secret (gold name) from others.
    """
    y0, y1 = int(0.045 * _RECT_H), int(0.105 * _RECT_H)
    x0, x1 = int(0.07 * _RECT_W), int(0.80 * _RECT_W)
    band = rect[y0:y1, x0:x1]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    h_ch, s_ch, v_ch = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # Only consider reasonably bright pixels (the lettering/foil, not shadow).
    bright = v_ch > 120
    if np.count_nonzero(bright) < 50:
        bright = np.ones_like(v_ch, dtype=bool)
    hue_deg = float(np.median(h_ch[bright])) * 2.0  # OpenCV hue is 0-180
    med_s = float(np.median(s_ch[bright]))
    med_v = float(np.median(v_ch[bright]))
    is_gold = (35.0 <= hue_deg <= 60.0) and (30.0 <= med_s <= 200.0) and (med_v >= 140.0)
    return hue_deg, is_gold


# ----- varnish / dazzle texture --------------------------------------------

def _varnish_energy(cv2, np, gray) -> float:
    """High-frequency texture on the colored frame vs the art (raised varnish).

    Prismatic Ultimate has a physical varnish texture on the frame that the art
    lacks. Ratio of Laplacian variance (frame ring / art centre); >1 means the
    frame is texturier than the art.
    """
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    h, w = gray.shape
    # Art window: the central illustration box (rough but stable).
    art = lap[int(0.20 * h):int(0.58 * h), int(0.12 * w):int(0.88 * w)]
    # Frame ring: a band just inside the outer border, excluding the art box.
    ring = np.concatenate([
        lap[int(0.06 * h):int(0.12 * h), int(0.06 * w):int(0.94 * w)].ravel(),  # top
        lap[int(0.60 * h):int(0.66 * h), int(0.06 * w):int(0.94 * w)].ravel(),  # mid band
    ])
    art_var = float(art.var()) + 1e-6
    return float(ring.var() / art_var)


def _border_dazzle(np, gray) -> float:
    """Texture variance of the outer black border (Prismatic Collector's dazzle).

    A plain glossy black border is flat (low variance); a dazzle/diffraction
    pattern raises it. Normalised by mean so brightness doesn't dominate.
    """
    h, w = gray.shape
    t = max(2, int(0.03 * h))
    ring = np.concatenate([
        gray[:t, :].ravel(), gray[-t:, :].ravel(),
        gray[:, :t].ravel(), gray[:, -t:].ravel(),
    ]).astype(np.float64)
    return float(ring.var() / (ring.mean() + 1e-6))
