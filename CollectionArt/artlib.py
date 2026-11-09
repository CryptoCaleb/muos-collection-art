# Collection Art engine - pure Python (zlib only).
import os, re, zlib, struct, json, math, hashlib, shutil

BG = (15, 21, 40)
EMPTY = (26, 34, 62)

# ---------------- PNG ----------------
def read_png(path, keep_alpha=False):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8; idat = []; plte = None; trns = None; w = h = bd = ct = il = None
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos+8]); body = data[pos+8:pos+8+ln]; pos += 12 + ln
        if typ == b"IHDR": w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE": plte = body
        elif typ == b"tRNS": trns = body
        elif typ == b"IDAT": idat.append(body)
        elif typ == b"IEND": break
    if bd != 8 or il != 0 or ct not in (0, 2, 3, 4, 6):
        return None
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    raw = zlib.decompress(b"".join(idat)); stride = w * ch
    out = bytearray(h * stride); prev = bytearray(stride); p = 0
    for y in range(h):
        ft = raw[p]; line = bytearray(raw[p+1:p+1+stride]); p += 1 + stride
        if ft == 1:
            for i in range(ch, stride): line[i] = (line[i] + line[i-ch]) & 255
        elif ft == 2:
            for i in range(stride): line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                a = line[i-ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i-ch] if i >= ch else 0; b = prev[i]; c = prev[i-ch] if i >= ch else 0
                pa = abs(b - c); pb = abs(a - c); pc = abs(a + b - 2*c)
                line[i] = (line[i] + (a if (pa <= pb and pa <= pc) else (b if pb <= pc else c))) & 255
        out[y*stride:(y+1)*stride] = line; prev = line
    n = w * h
    if ct == 2:
        return w, h, out
    if ct == 0 and keep_alpha:
        return w, h, out          # grayscale mask (font atlas)
    rgb = bytearray(n * 3)
    if ct == 0:
        rgb[0::3] = out; rgb[1::3] = out; rgb[2::3] = out
        return w, h, rgb
    if ct == 3:
        pal = [tuple(plte[i:i+3]) for i in range(0, len(plte), 3)]
        tr = list(trns) if trns else []
        for i in range(n):
            k = out[i]; r, g, b = pal[k]; a = tr[k] if k < len(tr) else 255
            if a < 255:
                r = (r*a + BG[0]*(255-a)) // 255; g = (g*a + BG[1]*(255-a)) // 255; b = (b*a + BG[2]*(255-a)) // 255
            rgb[i*3] = r; rgb[i*3+1] = g; rgb[i*3+2] = b
        return w, h, rgb
    step = 4 if ct == 6 else 2
    for i in range(n):
        o = i * step
        if ct == 6: r, g, b, a = out[o], out[o+1], out[o+2], out[o+3]
        else: r = g = b = out[o]; a = out[o+1]
        if a < 255:
            r = (r*a + BG[0]*(255-a)) // 255; g = (g*a + BG[1]*(255-a)) // 255; b = (b*a + BG[2]*(255-a)) // 255
        rgb[i*3] = r; rgb[i*3+1] = g; rgb[i*3+2] = b
    return w, h, rgb

def write_png(path, img):
    w, h, rgb = img
    raw = b"".join(b"\x00" + bytes(rgb[y*w*3:(y+1)*w*3]) for y in range(h))
    def chunk(t, b): return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f: f.write(png)
    os.replace(tmp, path)

def save_bin(path, img, channels=3):
    w, h, d = img
    data = struct.pack(">4sHHB", b"CAB1", w, h, channels) + zlib.compress(bytes(d), 6)
    with open(path + ".tmp", "wb") as f: f.write(data)
    os.replace(path + ".tmp", path)

def load_bin(path):
    with open(path, "rb") as f: data = f.read()
    magic, w, h, ch = struct.unpack(">4sHHB", data[:9])
    if magic != b"CAB1": return None
    return w, h, bytearray(zlib.decompress(data[9:]))

# ---------------- scaling ----------------
def resample(img, tw, th, crop=None):
    """box-average scale of img (optionally cropped region sx,sy,sw,sh) to tw x th"""
    w, h, src = img
    sx, sy, sw, sh = crop if crop else (0, 0, w, h)
    out = bytearray(tw * th * 3)
    fx = sw / tw; fy = sh / th
    xs = []
    for tx in range(tw):
        a = int(sx + tx * fx); b = max(a + 1, int(sx + (tx + 1) * fx)); xs.append((a, min(b, w)))
    for ty in range(th):
        y0 = int(sy + ty * fy); y1 = min(max(y0 + 1, int(sy + (ty + 1) * fy)), h)
        o = ty * tw * 3
        for tx in range(tw):
            x0, x1 = xs[tx]
            if x1 - x0 == 1 and y1 - y0 == 1:
                i = (y0 * w + x0) * 3; out[o] = src[i]; out[o+1] = src[i+1]; out[o+2] = src[i+2]
            else:
                r = g = b = n = 0
                for yy in range(y0, y1):
                    base = yy * w
                    for xx in range(x0, x1):
                        i = (base + xx) * 3; r += src[i]; g += src[i+1]; b += src[i+2]; n += 1
                out[o] = r // n; out[o+1] = g // n; out[o+2] = b // n
            o += 3
    return tw, th, out

def cover(img, tw, th):
    w, h, _ = img
    s = max(tw / w, th / h); cw = tw / s; chh = th / s
    return resample(img, tw, th, (int((w - cw) / 2), int((h - chh) / 2), max(1, int(cw)), max(1, int(chh))))

def contain(img, tw, th):
    w, h, _ = img
    s = min(tw / w, th / h)
    return resample(img, max(1, int(w * s)), max(1, int(h * s)))

def canvas(w, h, col=BG):
    return w, h, bytearray(bytes(col) * (w * h))

def paste(dst, src, x, y):
    dw, dh, d = dst; sw, sh, s = src
    for yy in range(sh):
        ty = y + yy
        if 0 <= ty < dh:
            x0 = max(0, x); x1 = min(dw, x + sw)
            if x1 > x0:
                d[(ty*dw + x0)*3:(ty*dw + x1)*3] = s[(yy*sw + x0 - x)*3:(yy*sw + x1 - x)*3]

def fill(dst, x, y, w, h, col):
    dw, dh, d = dst; row = bytes(col) * w
    for yy in range(y, y + h):
        d[(yy*dw + x)*3:(yy*dw + x + w)*3] = row

# ---------------- library ----------------
SKIP_CATS = {"Collection", "Folder", "Application", "Archive", "Task", "Theme", "Root"}
HASH = re.compile(r"-[0-9A-Fa-f]{6,}$")

class Library:
    def __init__(self, catalogues, collection_root, out_catalogue):
        self.cats = catalogues; self.col = collection_root
        self.out = os.path.join(out_catalogue, "Collection")
        self._index = None
        self.manifest_path = os.path.join(self.out, ".collectionart.json")
        try: self.manifest = json.load(open(self.manifest_path))
        except Exception: self.manifest = {}
        if isinstance(self.manifest, list): self.manifest = {k: "" for k in self.manifest}

    @property
    def index(self):
        if self._index is None:
            idx = {}
            for root in self.cats:
                try: systems = os.listdir(root)
                except OSError: continue
                for sysname in systems:
                    if sysname in SKIP_CATS: continue
                    for kind in ("grid", "box"):
                        d = os.path.join(root, sysname, kind)
                        try: files = os.listdir(d)
                        except OSError: continue
                        for f in files:
                            lf = f.lower()
                            if lf.endswith(".png") and lf != "default.png":
                                idx.setdefault((kind, lf[:-4]), []).append((sysname, os.path.join(d, f)))
            self._index = idx
        return self._index

    def random_covers(self, n):
        import random
        keys = [k for k in self.index if k[0] == "grid"]
        random.shuffle(keys)
        return [self.index[k][0][1] for k in keys[:n]]

    def collections(self):
        out = []
        for root, dirs, files in os.walk(self.col):
            for d in sorted(dirs):
                out.append((d, os.path.join(root, d)))
        return out

    def games(self, folder):
        res = []
        try: files = sorted(os.listdir(folder))
        except OSError: return res
        for f in files:
            p = os.path.join(folder, f)
            if not (f.lower().endswith(".cfg") and os.path.isfile(p)): continue
            stems = [HASH.sub("", f[:-4])]; hints = set()
            try:
                for line in open(p, encoding="utf-8", errors="ignore"):
                    line = line.strip()
                    if not line: continue
                    hints.add(line)
                    base = os.path.basename(line)
                    if "." in base: stems.append(os.path.splitext(base)[0])
                    stems.append(base)
            except OSError: pass
            res.append((stems, hints))
        return res

    def signature(self, folder):
        try: names = sorted(f for f in os.listdir(folder) if f.lower().endswith(".cfg"))
        except OSError: names = []
        return hashlib.md5("\n".join(names).encode()).hexdigest()

    def find(self, kind, stems, hints):
        for s in stems:
            hits = self.index.get((kind, s.lower()))
            if hits:
                for c, p in hits:
                    if c in hints: return p
                return hits[0][1]
        return None

    def target(self, kind, name):
        return os.path.join(self.out, kind, name + ".png")

    def has_art(self, name):
        return os.path.exists(self.target("grid", name))

    def needs(self, name, folder, mode):
        if mode == "all": return True
        if not self.has_art(name): return True
        if mode == "auto" and name in self.manifest:
            return self.manifest[name] != self.signature(folder)
        return False

    def save_manifest(self):
        try:
            with open(self.manifest_path + ".tmp", "w") as f: json.dump(self.manifest, f)
            os.replace(self.manifest_path + ".tmp", self.manifest_path)
        except OSError: pass

    # ---------- build one collection ----------
    def build(self, name, folder, progress=None):
        """returns (status, grid_preview_img or None)"""
        for k in ("grid", "box"): os.makedirs(os.path.join(self.out, k), exist_ok=True)
        games = self.games(folder)
        found = []
        for stems, hints in games:
            g = self.find("grid", stems, hints); b = self.find("box", stems, hints)
            if g or b: found.append((g or b, b or g))
        if not found:
            return "noart", None
        n = len(found)
        cache = {}
        def load(p):
            if p not in cache:
                try: cache[p] = read_png(p)
                except Exception: cache[p] = None
            return cache[p]
        if n == 1:
            g, b = found[0]
            # single game: the collection simply uses that game's own art
            shutil.copyfile(g, self.target("grid", name) + ".tmp"); os.replace(self.target("grid", name) + ".tmp", self.target("grid", name))
            shutil.copyfile(b, self.target("box", name) + ".tmp"); os.replace(self.target("box", name) + ".tmp", self.target("box", name))
            if progress: progress(1, 1)
            prev = load(g)
            if prev and (prev[0] > 150 or prev[1] > 150): prev = contain(prev, 150, 150)
            return "single", prev
        results = {}
        for kind, size, gap in (("grid", 150, 3), ("box", 300, 6)):
            img = canvas(size, size)
            if n == 2:
                cw = (size - gap) // 2
                for i, (g, b) in enumerate(found[:2]):
                    src = load(b if (kind == "box") else g) or load(g) or load(b)
                    if progress and kind == "grid": progress(i + 1, 2)
                    if not src: continue
                    t = contain(src, cw, size)
                    paste(img, t, i * (cw + gap) + (cw - t[0]) // 2, (size - t[1]) // 2)
            else:
                N = 2 if n < 4 else min(4, int(math.isqrt(n)))   # largest grid the covers fill
                cell = (size - gap * (N - 1)) // N
                off = (size - (cell * N + gap * (N - 1))) // 2
                use = found[:N * N]
                for i in range(N * N):
                    x = off + (i % N) * (cell + gap); y = off + (i // N) * (cell + gap)
                    if i < len(use):
                        g, b = use[i]
                        src = load(b if cell > 140 else g) or load(g) or load(b)
                        if progress and kind == "grid": progress(i + 1, len(use))
                        if src:
                            paste(img, cover(src, cell, cell), x, y); continue
                    fill(img, x, y, cell, cell, EMPTY)
            write_png(self.target(kind, name), img)
            results[kind] = img
        return "collage", results["grid"]

    def run(self, mode, events=None):
        """mode: missing | all | auto. events(kind, **info) for UI."""
        cols = self.collections()
        todo = [(n, f) for n, f in cols if self.needs(n, f, mode)]
        made = skipped = noart = 0
        if events: events("start", total=len(todo), collections=len(cols))
        for i, (name, folder) in enumerate(todo):
            if events: events("collection", index=i, name=name)
            def prog(j, t, _n=name): events and events("game", index=i, name=_n, done=j, total=t)
            try:
                status, prev = self.build(name, folder, prog)
            except Exception as e:
                status, prev = "error", None
                if events: events("error", name=name, error=str(e))
            if status in ("single", "collage"):
                made += 1; self.manifest[name] = self.signature(folder); self.save_manifest()
            elif status == "noart": noart += 1
            if events: events("built", index=i, name=name, status=status, preview=prev)
        if events: events("finish", made=made, noart=noart, total=len(todo))
        return made, noart, len(todo)

# ---------------- muOS paths ----------------
def detect(extra=None):
    bases = (extra or []) + ["/run/muos/storage/info", "/mnt/sdcard/MUOS/info", "/mnt/mmc/MUOS/info"]
    cats = []; col = None; out = None
    for b in bases:
        c = os.path.join(b, "catalogue")
        if os.path.isdir(c):
            if os.path.realpath(c) not in [os.path.realpath(x) for x in cats]: cats.append(c)
            if out is None: out = c
        k = os.path.join(b, "collection")
        if col is None and os.path.isdir(k): col = k
    return cats, col, out
