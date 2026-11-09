#!/usr/bin/env python3
# Collection Art v1.0 for muOS - created by Caleb Hinton
import os, sys, time, json, struct, select, glob, math, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artlib
from artlib import read_png, paste, fill, contain, cover, load_bin, save_bin

VERSION = "1.1"
AUTHOR = "Caleb Hinton"
W, H = 640, 480
BG = (9, 11, 18); PANEL = (15, 21, 40); PANEL2 = (24, 32, 60)
ACC = (84, 104, 227); WHITE = (255, 255, 255); GREY = (150, 160, 190); DIM = (95, 104, 135)
GREEN = (80, 200, 120); RED = (236, 64, 52)
TILE_COLS = [(236, 64, 52), (66, 103, 178), (43, 170, 160), (244, 184, 40)]
LOG = os.path.join(HERE, "last_run.log")
COVER_CACHE = os.path.join(HERE, "cache", "covers.bin")
INIT_DIRS = ["/mnt/mmc/MUOS/init"]
INIT_NAME = "collectionart_watch.sh"

def log(*a):
    try:
        with open(LOG, "a") as f: f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
    except OSError: pass

# ======================= display backends =======================
class SDLDisplay:
    def __init__(self, buf):
        import ctypes
        from ctypes import c_void_p, c_int, c_uint32, c_char_p
        lib = None
        for n in ("libSDL2-2.0.so.0", "libSDL2.so", "/usr/lib/libSDL2-2.0.so.0"):
            try: lib = ctypes.CDLL(n); break
            except OSError: pass
        if not lib: raise RuntimeError("no SDL2")
        self.l = l = lib
        l.SDL_Init.argtypes = [c_uint32]
        l.SDL_CreateWindow.restype = c_void_p
        l.SDL_CreateWindow.argtypes = [c_char_p, c_int, c_int, c_int, c_int, c_uint32]
        l.SDL_CreateRenderer.restype = c_void_p; l.SDL_CreateRenderer.argtypes = [c_void_p, c_int, c_uint32]
        l.SDL_CreateTexture.restype = c_void_p; l.SDL_CreateTexture.argtypes = [c_void_p, c_uint32, c_int, c_int, c_int]
        l.SDL_UpdateTexture.argtypes = [c_void_p, c_void_p, c_void_p, c_int]
        l.SDL_RenderClear.argtypes = [c_void_p]; l.SDL_RenderPresent.argtypes = [c_void_p]
        l.SDL_RenderCopy.argtypes = [c_void_p, c_void_p, c_void_p, c_void_p]
        l.SDL_GetError.restype = c_char_p
        if l.SDL_Init(0x20) != 0: raise RuntimeError("SDL_Init: %s" % l.SDL_GetError())
        l.SDL_ShowCursor(0)
        self.win = l.SDL_CreateWindow(b"Collection Art", 0x2FFF0000, 0x2FFF0000, W, H, 0x1001 | 0x4)
        if not self.win: raise RuntimeError("window: %s" % l.SDL_GetError())
        self.ren = l.SDL_CreateRenderer(self.win, -1, 0)
        if not self.ren: raise RuntimeError("renderer: %s" % l.SDL_GetError())
        self.tex = l.SDL_CreateTexture(self.ren, 0x17101803, 1, W, H)   # RGB24, streaming
        if not self.tex: raise RuntimeError("texture: %s" % l.SDL_GetError())
        self.ptr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
    def present(self):
        l = self.l
        l.SDL_PumpEvents()
        l.SDL_UpdateTexture(self.tex, None, self.ptr, W * 3)
        l.SDL_RenderClear(self.ren); l.SDL_RenderCopy(self.ren, self.tex, None, None); l.SDL_RenderPresent(self.ren)
    def idle(self): self.l.SDL_PumpEvents()
    def close(self):
        try: self.l.SDL_Quit()
        except Exception: pass

class FBDisplay:
    def __init__(self, buf):
        self.buf = buf
        rd = lambda n: open("/sys/class/graphics/fb0/" + n).read().strip()
        self.bpp = int(rd("bits_per_pixel"))
        if self.bpp != 32: raise RuntimeError("fb bpp %d unsupported" % self.bpp)
        vw, vh = [int(v) for v in rd("virtual_size").split(",")]
        try: self.stride = int(rd("stride"))
        except Exception: self.stride = vw * 4
        try: self.pany = int(rd("pan").split(",")[1])
        except Exception: self.pany = 0
        self.vw = vw
        self.fd = os.open("/dev/fb0", os.O_RDWR)
        self.out = bytearray(b"\xff" * (W * H * 4))
    def present(self):
        o, b = self.out, self.buf
        o[0::4] = b[2::3]; o[1::4] = b[1::3]; o[2::4] = b[0::3]
        rowb = W * 4
        if self.stride == rowb:
            os.pwrite(self.fd, bytes(o), self.pany * self.stride)
        else:
            for y in range(H):
                os.pwrite(self.fd, bytes(o[y*rowb:(y+1)*rowb]), (self.pany + y) * self.stride)
    def idle(self): pass
    def close(self):
        try: os.close(self.fd)
        except OSError: pass

class DumpDisplay:          # used for testing on a PC
    def __init__(self, buf): self.buf = buf; self.n = 0
    def present(self): self.n += 1
    def idle(self): pass
    def close(self): pass

# ======================= input =======================
class Input:
    UP, DOWN, LEFT, RIGHT, OK, BACK = "up", "down", "left", "right", "ok", "back"
    def __init__(self):
        self.fds = []
        self.size = struct.calcsize("llHHi")
        for p in sorted(glob.glob("/dev/input/event*")):
            try: self.fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
            except OSError: pass
        self.script = []           # testing: queued actions
    def poll(self):
        acts = []
        if self.script: acts.append(self.script.pop(0))
        if not self.fds: return acts
        try: r, _, _ = select.select(self.fds, [], [], 0)
        except Exception: return acts
        for fd in r:
            try: data = os.read(fd, self.size * 64)
            except OSError: continue
            for i in range(0, len(data) - self.size + 1, self.size):
                _, _, typ, code, val = struct.unpack("llHHi", data[i:i+self.size])
                if typ == 1 and val in (1, 2):
                    if code in (103, 544): acts.append(self.UP)
                    elif code in (108, 545): acts.append(self.DOWN)
                    elif code in (105, 546): acts.append(self.LEFT)
                    elif code in (106, 547): acts.append(self.RIGHT)
                    elif code in (304, 305, 307, 308, 315, 28, 57): acts.append(self.OK)
                    elif code in (314, 316, 1, 158, 139): acts.append(self.BACK)
                elif typ == 3 and code in (16, 17) and val != 0:
                    if code == 17: acts.append(self.UP if val < 0 else self.DOWN)
                    else: acts.append(self.LEFT if val < 0 else self.RIGHT)
        return acts

# ======================= drawing =======================
class Gfx:
    def __init__(self):
        self.buf = bytearray(bytes(BG) * (W * H))
        self.scr = (W, H, self.buf)
        self.bgfull = bytes(self.buf)
        meta = json.load(open(os.path.join(HERE, "assets", "font.json")))
        self.atlas = self.asset("font", gray=True)
        self.fonts = meta; self.cache = {}; self.luts = {}
        self.logo = self.asset("logo")
        self.logo_s = self.asset("logo_small")
        self.icon = self.asset("icon_big")
    @staticmethod
    def asset(name, gray=False):
        b = os.path.join(HERE, "assets", name + ".bin")
        if os.path.exists(b):
            try: return load_bin(b)
            except Exception: pass
        return read_png(os.path.join(HERE, "assets", name + ".png"), keep_alpha=gray)
    def clear(self, col=BG):
        if col == BG: self.buf[:] = self.bgfull
        else: fill(self.scr, 0, 0, W, H, col)
    def rect(self, x, y, w, h, col): 
        x0, y0 = max(0, x), max(0, y); x1, y1 = min(W, x + w), min(H, y + h)
        if x1 > x0 and y1 > y0: fill(self.scr, x0, y0, x1 - x0, y1 - y0, col)
    def rrect(self, x, y, w, h, r, col):
        r = min(r, h // 2, w // 2)
        for yy in range(h):
            if yy < r: dy = r - yy - 0.5
            elif yy >= h - r: dy = yy - (h - r) + 0.5
            else: dy = 0
            ins = int(round(r - math.sqrt(max(0, r * r - dy * dy)))) if dy else 0
            self.rect(x + ins, y + yy, w - 2 * ins, 1, col)
    def blit(self, img, x, y, sw=None):
        if sw is not None and sw < img[0]:
            iw, ih, d = img; sw = max(0, sw)
            img = (sw, ih, b"".join(bytes(d[r*iw*3:(r*iw+sw)*3]) for r in range(ih)))
            if sw == 0: return
        paste(self.scr, img, x, y)
    def text_img(self, s, size="m", fg=WHITE, bg=BG):
        key = (s, size, fg, bg)
        if key in self.cache: return self.cache[key]
        f = self.fonts[size]; ch = f["chars"]; lh = f["h"]; ay = f["y"]
        aw, _, ad = self.atlas
        glyphs = [ch.get(str(ord(c)), ch["63"]) for c in s]
        tw = max(1, sum(g[1] for g in glyphs))
        out = bytearray(bytes(bg) * (tw * lh))
        lut = self.luts.get((fg, bg))
        if lut is None:
            lut = self.luts[(fg, bg)] = [bytes((bg[0] + (fg[0]-bg[0]) * a // 255, bg[1] + (fg[1]-bg[1]) * a // 255, bg[2] + (fg[2]-bg[2]) * a // 255)) for a in range(256)]
        x = 0
        for gx, gw in glyphs:
            for yy in range(lh):
                row = ad[(ay + yy) * aw + gx:(ay + yy) * aw + gx + gw]
                if any(row):
                    o = (yy * tw + x) * 3
                    out[o:o + gw * 3] = b"".join(lut[a] for a in row)
            x += gw
        img = (tw, lh, out)
        if len(self.cache) > 400: self.cache.clear()
        self.cache[key] = img
        return img
    def width(self, s, size="m"):
        ch = self.fonts[size]["chars"]; return sum(ch.get(str(ord(c)), ch["63"])[1] for c in s)
    def fit(self, s, size, maxw):
        if self.width(s, size) <= maxw: return s
        while s and self.width(s + "…", size) > maxw: s = s[:-1]
        return s + "…"
    def text(self, s, x, y, size="m", fg=WHITE, bg=BG, align="left", maxw=None):
        if maxw: s = self.fit(s, size, maxw)
        img = self.text_img(s, size, fg, bg)
        if align == "center": x -= img[0] // 2
        elif align == "right": x -= img[0]
        self.blit(img, x, y); return img[0]

def ease(t): t = max(0.0, min(1.0, t)); return 1 - (1 - t) ** 3

# ======================= app =======================
class App:
    def __init__(self, display_cls=None, paths=None):
        open(LOG, "w").close()
        self.g = Gfx()
        self.inp = Input()
        self.disp = None
        for cls in ([display_cls] if display_cls else [SDLDisplay, FBDisplay]):
            try: self.disp = cls(self.g.buf); log("display:", cls.__name__); break
            except Exception as e: log("display", cls.__name__, "failed:", e)
        if not self.disp: self.disp = DumpDisplay(self.g.buf)
        cats, col, out = artlib.detect(paths)
        log("catalogues", cats, "collections", col, "output", out)
        self.ok = bool(cats and col and out)
        self.lib = artlib.Library(cats, col, out) if self.ok else None
        self.sel = 0; self.dirty = True; self.need_covers = False; self.job = None
        self.covers = self.load_covers()
        self.refresh_stats()

    # ---------- helpers ----------
    def load_covers(self):
        """instant: cached covers from last time, else coloured tiles (cache is built while idle)"""
        try:
            w, h, d = load_bin(COVER_CACHE)
            if w == 96 and h == 384:
                return [(96, 96, d[i*96*96*3:(i+1)*96*96*3]) for i in range(4)]
        except Exception: pass
        self.need_covers = True
        return [(96, 96, bytearray(bytes(c) * 9216)) for c in TILE_COLS]
    def cover_job(self):
        """generator: picks 4 random covers, one decode per step, then saves the cache"""
        out = []
        try:
            for p in self.lib.random_covers(10):
                yield
                try:
                    im = read_png(p)
                    if im: out.append(cover(im, 96, 96))
                except Exception: pass
                if len(out) == 4: break
            if len(out) == 4:
                os.makedirs(os.path.dirname(COVER_CACHE), exist_ok=True)
                save_bin(COVER_CACHE, (96, 384, b"".join(bytes(o[2]) for o in out)))
                self.covers = out; self.dirty = True
        except Exception as e:
            log("cover cache failed", e)
    def refresh_stats(self):
        self.ncol = self.nmiss = 0
        if self.lib:
            cols = self.lib.collections(); self.ncol = len(cols)
            self.nmiss = sum(1 for n, f in cols if not self.lib.has_art(n))
    def auto_on(self):
        return any(os.path.exists(os.path.join(d, INIT_NAME)) for d in INIT_DIRS)
    def set_auto(self, on):
        for d in INIT_DIRS:
            p = os.path.join(d, INIT_NAME)
            try:
                if on:
                    os.makedirs(d, exist_ok=True)
                    with open(p, "w") as f:
                        f.write("#!/bin/sh\n# Collection Art auto-update watcher (created by the Collection Art app)\n"
                                "( for i in $(seq 1 60); do\n"
                                "    for a in /run/muos/storage/application/CollectionArt /mnt/sdcard/MUOS/application/CollectionArt /mnt/mmc/MUOS/application/CollectionArt; do\n"
                                "      [ -f \"$a/watch.sh\" ] && exec sh \"$a/watch.sh\"\n"
                                "    done; sleep 5\n"
                                "  done ) >/dev/null 2>&1 &\n")
                    os.chmod(p, 0o755)
                    os.system("setsid sh '%s' >/dev/null 2>&1 < /dev/null &" % os.path.join(HERE, "watch.sh"))
                elif os.path.exists(p):
                    os.remove(p)
                    os.system("pkill -f CollectionArt/watch.sh >/dev/null 2>&1")
            except OSError as e:
                log("auto toggle failed", e)
    def frame(self):
        self.disp.present()
    def wait(self, dt):
        end = time.time() + dt
        while time.time() < end:
            self.disp.idle(); time.sleep(0.01)

    # ---------- screens ----------
    def draw_intro(self, t):
        t *= 1.8          # v1.1: quicker intro
        g = self.g; g.clear()
        cx, cy = W // 2, 168; s = 96; gap = 6
        starts = [(-120, -120), (W + 20, -120), (-120, H + 20), (W + 20, H + 20)]
        for i, img in enumerate(self.covers):
            k = ease((t - i * 0.12) / 0.7)
            fx = cx - s - gap // 2 + (i % 2) * (s + gap); fy = cy - s - gap // 2 + (i // 2) * (s + gap)
            sx, sy = starts[i]
            g.blit(img, int(sx + (fx - sx) * k), int(sy + (fy - sy) * k))
        k = ease((t - 1.0) / 0.5)
        if k > 0:
            title = g.text_img("Collection Art", "x")
            ty = int(290 + 30 * (1 - k))
            g.rect(0, 280, W, 90, BG)
            g.blit(title, cx - title[0] // 2, ty, sw=int(title[0] * min(1, k * 1.4)))
        k = ease((t - 1.4) / 0.4)
        if k > 0:
            bw = int(260 * k)
            for i, c in enumerate(TILE_COLS):
                g.rect(cx - bw // 2 + i * bw // 4, 350, bw // 4, 4, c)
        k = ease((t - 1.8) / 0.4)
        if k > 0:
            sub = g.text_img("created by " + AUTHOR, "m", GREY)
            g.blit(sub, cx - sub[0] // 2, 370, sw=int(sub[0] * k))
        if t > 2.4:
            g.text("Press any button", cx, 440, "s", DIM, align="center")

    MENU = ["missing", "all", "auto", "credits", "exit"]
    def menu_label(self, k):
        return {"missing": "Create missing art", "all": "Recreate all art",
                "auto": "Auto-update: " + ("On" if self.auto_on() else "Off"),
                "credits": "Credits", "exit": "Exit"}[k]
    DESC = {"missing": "Only builds art for collections that don't have any yet.",
            "all": "Rebuilds art for every collection, replacing current images.",
            "auto": "Rebuilds art by itself whenever a collection changes.",
            "credits": "Who made Collection Art.", "exit": "Go back to muOS."}
    def draw_header(self, right=None):
        g = self.g
        g.blit(g.logo_s, 24, 20)
        if right: g.text(right, W - 24, 32, "s", GREY, align="right")
        g.rect(24, 72, W - 48, 1, PANEL2)
    def draw_menu(self):
        g = self.g; g.clear()
        if not self.ok:
            self.draw_header()
            g.text("Couldn't find your muOS collections folder.", W // 2, 200, "m", WHITE, align="center")
            g.text("Details are in last_run.log in the app folder.", W // 2, 232, "s", GREY, align="center")
            g.text("Press any button to exit", W // 2, 440, "s", DIM, align="center"); return
        self.draw_header("%d collections · %d without art" % (self.ncol, self.nmiss))
        # cover stack on the left
        g.rrect(24, 96, 222, 222, 16, PANEL)
        for i, img in enumerate(self.covers):
            g.blit(img, 36 + (i % 2) * 102, 108 + (i // 2) * 102)
        g.text("Your collections,", 135, 334, "s", GREY, align="center")
        g.text("now with covers.", 135, 354, "s", GREY, align="center")
        # buttons
        x, y0, bw, bh, sp = 266, 96, 350, 46, 10
        for i, k in enumerate(self.MENU):
            y = y0 + i * (bh + sp); on = (i == self.sel)
            col = ACC if on else PANEL
            g.rrect(x, y, bw, bh, 14, col)
            dot = TILE_COLS[i % 4]
            g.rrect(x + 16, y + bh // 2 - 5, 10, 10, 3, WHITE if on else dot)
            g.text(self.menu_label(k), x + 38, y + (bh - 22) // 2, "m", WHITE, bg=col)
        g.text(self.DESC[self.MENU[self.sel]], W // 2, 390, "s", GREY, align="center")
        g.rect(24, 424, W - 48, 1, PANEL2)
        g.text("D-Pad  Move     A  Select     Menu  Quit", W // 2, 440, "s", DIM, align="center")

    def draw_credits(self, t):
        g = self.g; g.clear()
        bob = int(4 * math.sin(t * 2.2))
        g.blit(g.icon, W // 2 - 60, 34 + bob)
        g.text("Collection Art", W // 2, 170, "x", WHITE, align="center")
        g.text("Version " + VERSION, W // 2, 222, "s", ACC, align="center")
        g.text("Created by " + AUTHOR, W // 2, 258, "l", WHITE, align="center")
        lines = ["Made for muOS on the Anbernic RG35XX Pro",
                 "Builds collection art from your own game covers",
                 "Built with Claude"]
        for i, l in enumerate(lines):
            g.text(l, W // 2, 304 + i * 24, "s", GREY, align="center")
        # moving colour bar
        seg = 80; off = int(t * 60) % (seg * 4)
        for x in range(-seg * 4, W + seg, seg):
            c = TILE_COLS[((x + off) // seg) % 4]
            g.rect(x + off % seg, 400, seg, 4, c)
        g.text("Press any button to go back", W // 2, 440, "s", DIM, align="center")

    def draw_progress(self, st, t):
        g = self.g; g.clear()
        self.draw_header(st["title"])
        total = max(1, st["total"])
        frac = (st["index"] + (st["gdone"] / max(1, st["gtotal"]) if st["working"] else 0)) / total
        if st["finished"]: frac = 1.0
        g.rrect(24, 92, W - 48, 18, 9, PANEL)
        fw = int((W - 48) * frac)
        if fw >= 18: g.rrect(24, 92, fw, 18, 9, ACC)
        left = ("Collection %d of %d" % (min(st["index"] + 1, st["total"]), st["total"])) if st["total"] else "Nothing to do"
        if st["finished"]: left = "%d of %d done" % (st["total"], st["total"])
        g.text(left, 24, 118, "s", GREY)
        g.text("%d%%" % int(frac * 100), W - 24, 118, "s", GREY, align="right")
        # preview panel
        g.rrect(24, 150, 182, 196, 16, PANEL)
        g.text("Latest", 115, 158, "s", DIM, bg=PANEL, align="center")
        if st["preview"]:
            p = st["preview"]
            g.blit(p, 115 - p[0] // 2, 186 + (150 - p[1]) // 2)
        else:
            for i, c in enumerate(TILE_COLS):
                g.rrect(52 + (i % 2) * 64, 196 + (i // 2) * 64, 58, 58, 10, PANEL2)
        x = 226
        if st["finished"]:
            g.text("All done!", x, 152, "x", WHITE)
            g.text("Made art for %d collection%s" % (st["made"], "" if st["made"] == 1 else "s"), x, 208, "m", WHITE)
            if st["noart"]: g.text("%d had no cover art to use" % st["noart"], x, 236, "s", GREY)
            if st.get("cancelled"): g.text("Stopped early", x, 258, "s", RED)
        else:
            g.text(st["name"] or "Getting ready…", x, 152, "l", WHITE, maxw=W - x - 24)
            g.text(st["status"], x, 190, "s", GREY, maxw=W - x - 24)
            for i in range(4):   # bouncing dots
                k = (math.sin(t * 6 - i * 0.8) + 1) / 2
                g.rrect(x + i * 22, 224 - int(6 * k), 12, 12, 6, TILE_COLS[i])
        y = 262 if not st["finished"] else 286
        for name, ok in st["recent"][-(4 if st["finished"] else 4):][::-1]:
            mark, col = ("✓", GREEN) if ok else ("•", DIM)
            g.text(mark, x, y, "m", col)
            g.text(name if ok else name + " · no covers", x + 24, y + 2, "s", WHITE if ok else DIM, maxw=W - x - 48)
            y += 22
        g.rect(24, 424, W - 48, 1, PANEL2)
        g.text("A  Continue" if st["finished"] else "Menu  Stop", W // 2, 440, "s", DIM, align="center")

    # ---------- flows ----------
    def intro(self):
        t0 = time.time()
        while True:
            t = time.time() - t0
            self.draw_intro(t); self.frame()
            if self.inp.poll() and t > 0.15: return
            if t > 1.9: return
            time.sleep(1 / 40)

    def credits(self):
        t0 = time.time(); time.sleep(0.15); self.inp.poll()
        while True:
            t = time.time() - t0
            self.draw_credits(t); self.frame()
            if self.inp.poll(): return
            time.sleep(1 / 30)

    def run(self, mode):
        st = {"title": "Creating missing art" if mode == "missing" else "Recreating all art",
              "total": 0, "index": 0, "gdone": 0, "gtotal": 1, "name": "", "status": "Scanning collections…",
              "preview": None, "recent": [], "finished": False, "working": False, "made": 0, "noart": 0}
        t0 = time.time(); last = [0.0]
        class Stop(Exception): pass
        def draw(force=False):
            now = time.time()
            if force or now - last[0] > 0.06:
                last[0] = now; self.draw_progress(st, now - t0); self.frame()
                if Input.BACK in self.inp.poll(): raise Stop()
        def ev(kind, **k):
            if kind == "start": st["total"] = k["total"]
            elif kind == "collection":
                st.update(index=k["index"], name=k["name"], working=True, gdone=0, gtotal=1, status="Finding covers…")
            elif kind == "game":
                st.update(gdone=k["done"], gtotal=k["total"], status="Placing cover %d of %d" % (k["done"], k["total"]))
            elif kind == "built":
                ok = k["status"] in ("single", "collage")
                st["recent"].append((k["name"], ok))
                if k["preview"]: st["preview"] = k["preview"]
                st.update(working=False, index=k["index"] + 1)
            elif kind == "finish":
                st.update(made=k["made"], noart=k["noart"])
            draw(kind in ("start", "built", "finish"))
        try:
            self.lib = artlib.Library(self.lib.cats, self.lib.col, os.path.dirname(self.lib.out))
            self.lib.run(mode, ev)
        except Stop:
            st["cancelled"] = True
            st["made"] = sum(1 for _, ok in st["recent"] if ok); st["noart"] = sum(1 for _, ok in st["recent"] if not ok)
        st.update(finished=True)
        self.inp.poll()
        while True:
            self.draw_progress(st, time.time() - t0); self.frame()
            if self.inp.poll(): break
            time.sleep(1 / 30)
        self.refresh_stats()

    def main(self):
        self.intro()
        if not self.ok:
            self.draw_menu(); self.frame()
            while not self.inp.poll(): self.wait(0.05)
            return
        if self.need_covers: self.job = self.cover_job()
        while True:
            if self.dirty: self.draw_menu(); self.frame(); self.dirty = False
            acts = self.inp.poll()
            if not acts:
                if self.job:
                    try: next(self.job)
                    except StopIteration: self.job = None
                else: self.wait(0.03)
                continue
            for a in acts:
                self.dirty = True
                if a == Input.UP: self.sel = (self.sel - 1) % len(self.MENU)
                elif a == Input.DOWN: self.sel = (self.sel + 1) % len(self.MENU)
                elif a == Input.BACK: return
                elif a == Input.OK:
                    k = self.MENU[self.sel]
                    if k == "exit": return
                    if k == "credits": self.credits()
                    elif k == "auto": self.set_auto(not self.auto_on())
                    else:
                        self.run(k)
                        self.job = self.cover_job()   # fresh covers for next launch
                    self.inp.poll()

def headless_auto():
    lock = "/tmp/collectionart.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - os.path.getmtime(lock) < 600: return
        os.remove(lock); fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        cats, col, out = artlib.detect()
        if cats and col and out:
            made, noart, n = artlib.Library(cats, col, out).run("auto")
            if n: log("auto: updated %d of %d collections" % (made, n))
    finally:
        os.close(fd); os.remove(lock)

if __name__ == "__main__":
    if "--auto" in sys.argv:
        headless_auto(); sys.exit(0)
    app = None
    try:
        app = App(); app.main()
    except Exception:
        log(traceback.format_exc())
    finally:
        if app and app.disp: app.disp.close()
