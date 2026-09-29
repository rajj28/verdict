"""Render docs/assets/verdict-system.gif: how a request flows through VERDICT.

    python docs/assets/make_system_gif.py

Pillow only. Each scene lights the real path through the system (see ARCHITECTURE.md).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).with_name("verdict-system.gif")
S = 2                      # draw at 2x, downsample for smooth edges
W, H = 1280, 700
PAPER, INK, MUTED, LINE = "#f6f4ef", "#1a1a1a", "#5b5750", "#d9d4c9"
ACCENT, TINT, VISITED = "#b3361e", "#fbe9e4", "#fdf4f1"
STOP, STOP_TINT, OK = "#c0392b", "#fde2e0", "#2e7d4f"
FONTS = Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (FONTS / name, Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size * S)
    return ImageFont.load_default()


TITLE, BOLD, BODY, SMALL, CAPTION = (font("seguisb.ttf", 24), font("seguisb.ttf", 17),
                                     font("segoeui.ttf", 13), font("segoeui.ttf", 14),
                                     font("seguisb.ttf", 21))

COL = {1: (40, 220), 2: (320, 300), 3: (680, 270), 4: (1010, 230)}
ROW = {0: (110, 110), 1: (250, 110), 2: (390, 110)}
BOXES = {
    "browser": (1, 0, "Browser", "server-rendered pages, no JavaScript build step"),
    "cli": (1, 1, "curl · run.py · scripts", "the same API, with bearer tokens"),
    "embed": (1, 2, "Embed widget", "the public gallery on any website"),
    "api": (2, 0, "Pages + REST API", "Django 5.2 + DRF · 137 documented operations"),
    "policy": (2, 1, "Policy layer", "role, track and conflict checks in the backend"),
    "services": (2, 2, "Services", "deadlines on server time, row locks, previews, audit"),
    "engine": (3, 0, "Results engine", "judge offsets · ridge λ by cross-validation · 200 re-runs"),
    "pubs": (3, 1, "Publications", "immutable snapshots · Verify recomputes them"),
    "worker": (3, 2, "webhook-worker", "HMAC-signed, retried, SSRF-guarded deliveries"),
    "db": (4, 0, "PostgreSQL 16", "all state · hash-chained audit log · no SQLite anywhere"),
    "keys": (4, 2, "Ed25519 keys", "signed judge records, verifiable by anyone"),
}


def rect(key: str) -> tuple[int, int, int, int]:
    col, row = BOXES[key][0], BOXES[key][1]
    x, w = COL[col]
    y, h = ROW[row]
    if key == "db":
        h = ROW[1][0] + ROW[1][1] - y          # spans the first two rows
    return x, y, w, h


# Orthogonal routes between boxes, through the gaps between columns and rows.
EDGES = {
    ("browser", "api"): [(260, 165), (320, 165)],
    ("cli", "api"): [(260, 305), (290, 305), (290, 165), (320, 165)],
    ("embed", "api"): [(260, 445), (290, 445), (290, 165), (320, 165)],
    ("api", "policy"): [(470, 220), (470, 250)],
    ("policy", "services"): [(470, 360), (470, 390)],
    ("services", "db"): [(620, 445), (650, 445), (650, 235), (1010, 235)],
    ("services", "engine"): [(620, 445), (650, 445), (650, 165), (680, 165)],
    ("services", "pubs"): [(620, 445), (650, 445), (650, 305), (680, 305)],
    ("pubs", "db"): [(950, 305), (980, 305), (980, 235), (1010, 235)],
    ("pubs", "engine"): [(815, 250), (815, 220)],
    ("services", "worker"): [(620, 445), (680, 445)],
    ("services", "keys"): [(620, 445), (650, 445), (650, 375), (990, 375), (990, 445), (1010, 445)],
}

SCENES = [
    ("A judge scores a project", ["browser", "api", "policy", "services", "db"], "ok"),
    ("Judge B asks for judge A's scores: 403 from the backend, not a hidden button",
     ["cli", "api", "policy"], "stop"),
    ("The organizer previews results: judge-bias correction and rank certainty",
     ["browser", "api", "policy", "services", "engine"], "ok"),
    ("Publish: an immutable, versioned snapshot, recorded in the audit chain",
     ["browser", "api", "policy", "services", "pubs", "db"], "ok"),
    ("Anyone re-verifies a publication: recomputed from its stored inputs, bit for bit",
     ["embed", "api", "policy", "services", "pubs", "engine"], "ok"),
    ("Every audited change goes out as a signed webhook", ["browser", "api", "policy", "services", "worker"], "ok"),
    ("Judges receive Ed25519-signed participation records", ["browser", "api", "policy", "services", "keys"], "ok"),
]
TRAVEL, HOLD = 18, 10      # frames per scene: dot travelling, then the lit path held


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=fnt) <= width * S or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + [line]


def edges(path: list[str]) -> list[list[tuple[float, float]]]:
    return [EDGES[(a, b)] for a, b in zip(path, path[1:])]


def length(points) -> float:
    return sum(abs(x2 - x1) + abs(y2 - y1) for (x1, y1), (x2, y2) in zip(points, points[1:]))


def cut(routes, fraction: float):
    """The drawn part of each route at this fraction of the whole path, and the dot position."""
    target = sum(length(r) for r in routes) * fraction
    drawn, tip = [], routes[0][0]
    for route in routes:
        if target <= 0:
            break
        part = [route[0]]
        for p, q in zip(route, route[1:]):
            seg = abs(q[0] - p[0]) + abs(q[1] - p[1])
            if target >= seg:
                part.append(q)
                target -= seg
                continue
            t = target / seg if seg else 0
            part.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
            target = 0
            break
        drawn.append(part)
        tip = part[-1]
    return drawn, tip


def reached(path: list[str], drawn) -> set[str]:
    """Boxes whose incoming arrow has been drawn completely."""
    done = {path[0]}
    for key, part, route in zip(path[1:], drawn, edges(path)):
        if len(part) == len(route) and part[-1] == route[-1]:
            done.add(key)
    return done


def frame(scene: int, fraction: float) -> Image.Image:
    caption, path, kind = SCENES[scene]
    img = Image.new("RGB", (W * S, H * S), PAPER)
    d = ImageDraw.Draw(img)
    d.text((40 * S, 26 * S), "VERDICT · how a request flows", font=TITLE, fill=INK)
    d.text((40 * S, 62 * S), "Every rule is enforced on the server; every result can be recomputed.",
           font=SMALL, fill=MUTED)
    drawn, tip = cut(edges(path), fraction)
    lit = reached(path, drawn)
    for key, (_, _, name, sub) in BOXES.items():
        x, y, w, h = rect(key)
        fill, outline, width = "#ffffff", LINE, 1
        if key in lit:
            fill, outline, width = (TINT, ACCENT, 3) if key == path[-1] or key == max(lit, key=path.index) else (VISITED, ACCENT, 2)
            if kind == "stop" and key == path[-1] and fraction >= 1:
                fill, outline, width = STOP_TINT, STOP, 3
        d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], radius=12 * S, fill=fill,
                            outline=outline, width=width * S)
        d.text(((x + 14) * S, (y + 12) * S), name, font=BOLD, fill=INK)
        for i, line in enumerate(wrap(d, sub, BODY, w - 28)):
            d.text(((x + 14) * S, (y + 42 + i * 18) * S), line, font=BODY, fill=MUTED)
    for part in drawn:
        if len(part) > 1:
            d.line([(px * S, py * S) for px, py in part], fill=ACCENT, width=4 * S, joint="curve")
    r = 7
    d.ellipse([(tip[0] - r) * S, (tip[1] - r) * S, (tip[0] + r) * S, (tip[1] + r) * S], fill=ACCENT)
    if kind == "stop" and fraction >= 1:
        x, y, w, h = rect("policy")
        label = "403 refused"
        tw = d.textlength(label, font=BOLD) / S
        bx, by = x + w - tw - 34, y + h - 34
        d.rounded_rectangle([bx * S, by * S, (bx + tw + 20) * S, (by + 26) * S], radius=8 * S, fill=STOP)
        d.text(((bx + 10) * S, (by + 2) * S), label, font=BOLD, fill="#ffffff")
    # Footer: the one command, and the scene caption.
    d.rounded_rectangle([40 * S, 530 * S, 1240 * S, 578 * S], radius=10 * S, fill="#ece8df")
    d.text((58 * S, 542 * S), "docker compose up  ·  one command, network off, seeded with the organizers' fixtures.json",
           font=SMALL, fill=INK)
    d.text((40 * S, 610 * S), f"{scene + 1}/{len(SCENES)}  ", font=CAPTION, fill=MUTED)
    d.text((100 * S, 610 * S), caption, font=CAPTION, fill=STOP if kind == "stop" and fraction >= 1 else ACCENT)
    return img.resize((W, H), Image.LANCZOS)


def main() -> None:
    frames, durations = [], []
    for scene in range(len(SCENES)):
        for f in range(TRAVEL):
            frames.append(frame(scene, (f + 1) / TRAVEL))
            durations.append(70)
        frames.append(frame(scene, 1.0))
        durations.append(1500)
    palette = frames[0].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    quantized = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    quantized[0].save(OUT, save_all=True, append_images=quantized[1:], duration=durations, loop=0,
                      optimize=True, disposal=1)
    print(f"{OUT} {OUT.stat().st_size / 1e6:.2f} MB, {len(frames)} frames")


if __name__ == "__main__":
    main()
