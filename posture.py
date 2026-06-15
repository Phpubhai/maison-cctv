# Keypoint geometry: posture classification and posture-imbalance estimates.
# Ported from behavior_monitor_v2 (ceiling-camera heuristics).
import math

# COCO keypoint indices
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 5, 6, 11, 12
L_WRIST, R_WRIST = 9, 10
L_KNEE, R_KNEE = 13, 14
KP_CONF = 0.3


def kpt(pts, kconf, j):
    """Keypoint j as (x, y), or None if low confidence."""
    if float(kconf[j]) > KP_CONF:
        return float(pts[j][0]), float(pts[j][1])
    return None


def midpoint(a, b):
    if a and b:
        return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    return a or b


def line_tilt_deg(a, b):
    """Tilt of the line a-b from horizontal, signed. Positive = b is lower."""
    if not a or not b or abs(b[0] - a[0]) < 1:
        return None
    return math.degrees(math.atan2(b[1] - a[1], abs(b[0] - a[0])))


def classify_posture(pts, kconf, box):
    """Best-effort posture from keypoints. Returns (label, head_down) with
    label in standing / sitting / bending / reclined / lying / upright / unknown."""
    w, h = box[2] - box[0], box[3] - box[1]
    aspect = w / h if h > 0 else 0

    nose = kpt(pts, kconf, NOSE)
    sh = midpoint(kpt(pts, kconf, L_SHOULDER), kpt(pts, kconf, R_SHOULDER))
    hip = midpoint(kpt(pts, kconf, L_HIP), kpt(pts, kconf, R_HIP))
    knee = midpoint(kpt(pts, kconf, L_KNEE), kpt(pts, kconf, R_KNEE))

    # head clearly below the shoulder line (8% of body height) = head down
    head_down = bool(nose and sh and nose[1] > sh[1] + 0.08 * h)
    # head resting AT shoulder level also counts (napping on a table: the ear
    # or nose sits at/below the shoulders; an upright head is well above them)
    head_pts = [p for p in (kpt(pts, kconf, j) for j in (NOSE, L_EYE, R_EYE, L_EAR, R_EAR)) if p]
    if head_pts and sh:
        head_down = head_down or min(p[1] for p in head_pts) > sh[1] - 0.05 * h

    if not (sh and hip):
        return ("lying" if aspect > 1.5 else "unknown"), head_down or aspect > 1.6

    dx, dy = hip[0] - sh[0], hip[1] - sh[1]
    torso_len = math.hypot(dx, dy)
    # angle of the torso from vertical: 0 = upright, 90 = horizontal
    theta = 90.0 if dy <= 0 else math.degrees(math.atan2(abs(dx), dy))

    if aspect > 1.5 or theta > 70:
        return "lying", True
    if theta > 40:
        # tilted torso: head above shoulders = leaning back, else bent forward
        if nose and nose[1] < sh[1]:
            return "reclined", head_down
        return "bending", True
    # upright torso: knees close to hip height = sitting
    if knee and torso_len > 0:
        if abs(knee[1] - hip[1]) < 0.5 * torso_len:
            return "sitting", head_down
        return "standing", head_down
    return "upright", head_down


def face_hidden(kconf):
    """True when ALL head keypoints are missing (face buried in arms, or
    simply facing away -- callers must combine with posture)."""
    return not any(float(kconf[j]) > KP_CONF for j in range(5))


def imbalance_metrics(pts, kconf):
    """Tilt angles (deg) for the posture-imbalance estimate. None = not visible."""
    sh_l, sh_r = kpt(pts, kconf, L_SHOULDER), kpt(pts, kconf, R_SHOULDER)
    hip_l, hip_r = kpt(pts, kconf, L_HIP), kpt(pts, kconf, R_HIP)
    eye_l, eye_r = kpt(pts, kconf, L_EYE), kpt(pts, kconf, R_EYE)
    ear_l, ear_r = kpt(pts, kconf, L_EAR), kpt(pts, kconf, R_EAR)
    head_a, head_b = (eye_l, eye_r) if (eye_l and eye_r) else (ear_l, ear_r)

    lean = None
    sh_m, hip_m = midpoint(sh_l, sh_r), midpoint(hip_l, hip_r)
    if sh_l and sh_r and hip_l and hip_r:
        dy = hip_m[1] - sh_m[1]
        if dy > 1:
            lean = math.degrees(math.atan2(hip_m[0] - sh_m[0], dy))

    # anything beyond 20 deg is a camera-perspective artifact, not anatomy
    def plausible(v):
        return v if v is not None and abs(v) <= 20 else None

    return {
        "shoulder": plausible(line_tilt_deg(sh_l, sh_r)),
        "hip": plausible(line_tilt_deg(hip_l, hip_r)),
        "head": plausible(line_tilt_deg(head_a, head_b)),
        "lean": plausible(lean),
    }


def describe_causes(flagged):
    """Turn persistent tilt measurements into possible muscle causes.
    Returns (short on-screen line, full event text). Spa conversation
    starter only -- worded as possibilities, never a diagnosis."""
    # sign conventions: shoulder/hip median > 0 means the person's LEFT side
    # sits higher; head/lean > 0 means tilted/leaning toward their RIGHT
    sh, hip = flagged.get("shoulder"), flagged.get("hip")
    head, lean = flagged.get("head"), flagged.get("lean")
    high = lambda v: "left" if v > 0 else "right"
    toward = lambda v: "right" if v > 0 else "left"

    causes, shorts = [], []
    if sh is not None and hip is not None and high(sh) == high(hip):
        s = high(sh)
        causes.append(f"whole {s}-side muscle chain may be tight (neck down to "
                      f"lower back) - full-back massage focus on the {s} side")
        shorts.append(f"{s[0].upper()} side chain tight?")
    else:
        if sh is not None:
            s = high(sh)
            causes.append(f"{s} shoulder rides higher - possibly tight {s} upper "
                          f"trapezius/neck, or a habit of carrying weight on the {s} side")
            shorts.append(f"{s[0].upper()} neck/shoulder tight?")
        if hip is not None:
            s = high(hip)
            causes.append(f"{s} hip rides higher - possibly tight {s} lower back "
                          f"(QL), uneven standing habit, or slight leg-length difference")
            shorts.append(f"{s[0].upper()} lower back tight?")
    if head is not None:
        s = toward(head)
        causes.append(f"head tilts {s} - possible neck muscle tension on the {s} side")
        shorts.append("neck tension?")
    if lean is not None and sh is None and hip is None:
        s = toward(lean)
        causes.append(f"body leans {s} - possible core/hip imbalance")
        shorts.append(f"leans {s[0].upper()}")

    measured = ", ".join(f"{k} {abs(v):.0f}deg" for k, v in flagged.items())
    full = (f"possible causes: {'; '.join(causes)}. "
            f"[measured: {measured}] (estimate only, not a diagnosis)")
    return "possible: " + ", ".join(shorts[:2]), full
