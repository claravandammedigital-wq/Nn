#!/usr/bin/env python3
"""
Assemble the rendered frames into the final MP4 and draw all on-screen texts with ffmpeg.

    python3 compose.py                       # -> <video.output> (H.264, 1080x1920, 30 fps, no audio)
    python3 compose.py --still 1 build/preview/scene1_door_close.png out.png
                                             # text overlay on a preview still (scene 1 timing)

Texts, clock times and durations come from config.json.
"""
import argparse
import json
import os
import subprocess

from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json")))
V = CFG["video"]
TX = CFG["texts"]
FPS = V["fps"]
W, H = V["width"], V["height"]
FONTS = os.path.join(HERE, "fonts")
F_TITLE = os.path.join(FONTS, "PlayfairDisplay-Italic.ttf")
F_FINAL = os.path.join(FONTS, "PlayfairDisplay-Italic.ttf")
F_CLOCK = os.path.join(FONTS, "Montserrat-Bold.ttf")
F_SMALL = os.path.join(FONTS, "Montserrat-SemiBold.ttf")
BUILD = os.path.join(HERE, "build")
TXT = os.path.join(BUILD, "text")

# layout (pixels, 1080x1920 canvas). TikTok UI covers roughly the bottom ~380 px and right ~140 px.
TITLE_Y, TITLE_SIZE, TITLE_LEAD = 120, 60, 78
CLOCK_X, CLOCK_Y, CLOCK_SIZE = 72, 300, 150
SUB_Y, SUB_SIZE, SUB_TRACK = 475, 32, 0.32      # tracking in em
FINAL_Y, FINAL_SIZE, FINAL_LEAD = 1190, 64, 92


def scene_times():
    t, out = 0.0, []
    for s in CFG["scenes"]:
        n = round(s["duration"] * FPS)
        out.append((s, t, t + n / FPS, n))
        t += n / FPS
    return out, t


def textfile(name, text):
    os.makedirs(TXT, exist_ok=True)
    p = os.path.join(TXT, name + ".txt")
    with open(p, "w") as f:
        f.write(text)
    return p


def esc(p):
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def q(expr):
    """expression for a filter option: escape commas at filtergraph level (no quotes)"""
    return str(expr).replace(",", "\\,")


def drawtext(name, text, font, size, x, y, color="white", alpha="1", shadow=True, enable=None, border=0):
    o = [f"fontfile={esc(font)}", f"textfile={esc(textfile(name, text))}", "expansion=none",
         f"fontsize={size}", f"fontcolor={color}", f"x={q(x)}", f"y={q(y)}", f"alpha={q(alpha)}"]
    if shadow:
        o += ["shadowcolor=black@0.45", "shadowx=0", "shadowy=3"]
    if border:
        o += [f"borderw={border}", "bordercolor=black@0.18"]
    if enable:
        o.append(f"enable={q(enable)}")
    return "drawtext=" + ":".join(o)


def tracked_letters(text, font, size, x0, track):
    """Per-letter x positions for letter-spaced small caps (drawtext has no tracking option)."""
    f = TTFont(font)
    cmap, hmtx, upm = f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm
    xs, x = [], x0
    for ch in text:
        xs.append((ch, x))
        adv = hmtx[cmap[ord(ch)]][0] * size / upm
        x += adv + track * size
    return xs


def build_filters(total):
    times, _ = scene_times()
    f = []
    # soft gradients for legibility (top, and bottom for the final line)
    f.append(f"[0:v]format=rgba[base]")
    f.append(f"color=black@1:s={W}x620:d={total},format=rgba,geq=r=0:g=0:b=0:a=150*pow(1-Y/H\\,1.7)[gtop]")
    f.append(f"[base][gtop]overlay=0:0:format=auto[v0]")
    fin_scene = times[TX["final_starts_at_scene"] - 1]
    fin_t = fin_scene[1] + TX["final_delay"]
    f.append(f"color=black@1:s={W}x760:d={total},format=rgba,"
             f"geq=r=0:g=0:b=0:a=170*pow(Y/H\\,1.4)[gbot]")
    f.append(f"[v0][gbot]overlay=0:{H - 760 - 240}:enable=gte(t\\,{fin_t:.3f})[v1]")
    chain = []
    # title
    op = TX["title_opacity"]
    for i, line in enumerate(TX["title"]):
        chain.append(drawtext(f"title{i}", line, F_TITLE, TITLE_SIZE, "(w-text_w)/2", TITLE_Y + i * TITLE_LEAD,
                              alpha=f"{op}"))
    # clock per scene (slides down + fades in at each cut)
    for s, a, b, _ in times:
        al = f"min(1,(t-{a:.3f})/0.18)"
        yy = f"{CLOCK_Y}-24*max(0,1-(t-{a:.3f})/0.22)"
        chain.append(drawtext(f"clock{s['n']}", s["clock"], F_CLOCK, CLOCK_SIZE, CLOCK_X, yy, alpha=al,
                              enable=f"between(t,{a:.3f},{b - 0.001:.3f})"))
    # HOME ALONE, letter-spaced small capitals under the clock
    for i, (ch, x) in enumerate(tracked_letters(TX["subtitle"], F_SMALL, SUB_SIZE, CLOCK_X + 6, SUB_TRACK)):
        if ch.strip():
            chain.append(drawtext(f"sub{i}", ch, F_SMALL, SUB_SIZE, f"{x:.1f}", SUB_Y, alpha="0.92"))
    # thin accent rule left of the subtitle
    chain.append(f"drawbox=x={CLOCK_X + 6}:y={SUB_Y + SUB_SIZE + 16}:w=70:h=4:color=0xF29A3A@0.95:t=fill")
    # final text
    for i, line in enumerate(TX["final"]):
        a = fin_t + i * 0.7
        size = FINAL_SIZE if i == 0 else int(FINAL_SIZE * 1.25)
        font = F_FINAL if i == 0 else F_CLOCK
        color = "white" if i == 0 else "0xFFB45A"
        chain.append(drawtext(f"final{i}", line, font, size, "(w-text_w)/2",
                              f"{FINAL_Y + i * FINAL_LEAD}+16*max(0,1-(t-{a:.3f})/0.35)",
                              color=color, alpha=f"if(lt(t,{a:.3f}),0,min(1,(t-{a:.3f})/0.35))",
                              enable=f"gte(t,{a:.3f})"))
    f.append("[v1]" + ",".join(chain) + ",format=yuv420p[out]")
    return ";\n".join(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--still", nargs=3, metavar=("SCENE", "IN", "OUT"),
                    help="overlay texts on a still image as it would appear in that scene")
    ap.add_argument("--at", type=float, default=0.8, help="seconds into the scene for --still")
    a = ap.parse_args()
    times, total = scene_times()
    script = os.path.join(BUILD, "filters.txt")
    os.makedirs(BUILD, exist_ok=True)
    if a.still:
        n, src, dst = int(a.still[0]), a.still[1], a.still[2]
        t0 = times[n - 1][1] + a.at
        with open(script, "w") as fh:
            fh.write(build_filters(total))
        cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-i", src,
               "-filter_complex_script", script, "-map", "[out]", "-ss", f"{t0:.3f}", "-frames:v", "1",
               "-update", "1", dst]
        # need the overlay clock to run from 0: render up to t0 then keep only that frame
        subprocess.run(cmd, check=True)
        return
    # gather all frames into one numbered sequence (symlinks)
    seq = os.path.join(BUILD, "sequence")
    os.makedirs(seq, exist_ok=True)
    for x in os.listdir(seq):
        os.remove(os.path.join(seq, x))
    k = 0
    for s, _, _, n in times:
        d = os.path.join(BUILD, "frames", f"s{s['n']}")
        frames = sorted(x for x in os.listdir(d) if x.endswith(".png"))
        if len(frames) < n:
            raise SystemExit(f"scene {s['n']}: {len(frames)} frames rendered, {n} expected")
        for x in frames[:n]:
            k += 1
            os.symlink(os.path.join(d, x), os.path.join(seq, f"{k:05d}.png"))
    with open(script, "w") as fh:
        fh.write(build_filters(total))
    out = os.path.join(HERE, V["output"])
    cmd = ["ffmpeg", "-y", "-v", "error", "-stats", "-framerate", str(FPS), "-i", os.path.join(seq, "%05d.png"),
           "-filter_complex_script", script, "-map", "[out]", "-an",
           "-c:v", "libx264", "-profile:v", "high", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
           "-r", str(FPS), "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    print("wrote", out, f"({k} frames, {total:.2f}s)")


if __name__ == "__main__":
    main()
