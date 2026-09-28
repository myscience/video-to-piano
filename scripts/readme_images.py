"""Screenshots and a GIF of the practice viewer for the README.

Needs the viewer running (`pianoscribe serve`) and Google Chrome installed:

    uv run --extra docs python scripts/readme_images.py --song gymnopedie-1

Writes docs/images/{viewer,line,editing,add-song}.png, line.gif and shelf.gif. The shelf shows
every score in the library, so for public images serve a library of public-domain pieces only
(`pianoscribe serve` reads ./library: run it from a folder whose library/ links just those).
"""

from __future__ import annotations

import argparse
import base64
import io
import time
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import Page, sync_playwright

OUT = Path("docs/images")


def save(png: bytes, name: str, width: int | None = None) -> None:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if width and img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    img.save(OUT / name, optimize=True)
    print(f"✓ {OUT / name} ({img.width}x{img.height})")


def ready(page: Page) -> None:
    page.wait_for_function("document.querySelectorAll('g.note').length > 50 && document.getElementById('audio').readyState >= 1")
    page.wait_for_timeout(500)


def seek(page: Page, seconds: float) -> None:
    page.evaluate(f"document.getElementById('audio').currentTime = {seconds}")
    page.wait_for_timeout(900)  # a few animation frames: highlight + follow


class Recorder:
    """Chrome's screencast (frames arrive only when something changes, time-stamped) plus the
    pointer's path, which headless screenshots don't show: resampled to a steady GIF."""

    def __init__(self, page: Page) -> None:
        self.page, self.frames, self.pointer, self.clicks = page, [], [], []
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._frame)

    def _frame(self, e: dict) -> None:
        self.frames.append((e["metadata"]["timestamp"], base64.b64decode(e["data"])))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": e["sessionId"]})

    def start(self, x: float, y: float) -> None:
        self.pointer.append((time.time(), x, y))
        self.page.mouse.move(x, y)
        self.cdp.send("Page.startScreencast", {"format": "png"})

    def glide(self, x: float, y: float, seconds: float = 0.6) -> None:
        t0, x0, y0 = self.pointer[-1][0], self.pointer[-1][1], self.pointer[-1][2]
        start, n = time.time(), max(2, round(seconds * 30))
        for i in range(1, n + 1):
            k = i / n
            k = k * k * (3 - 2 * k)  # ease in and out
            px, py = x0 + (x - x0) * k, y0 + (y - y0) * k
            self.page.mouse.move(px, py)
            self.pointer.append((time.time(), px, py))
            self.page.wait_for_timeout(max(0, round((start + seconds * i / n - time.time()) * 1000)))

    def click(self) -> None:
        self.clicks.append(time.time())
        self.page.mouse.down()
        self.page.mouse.up()

    def wait(self, seconds: float) -> None:
        self.page.wait_for_timeout(round(seconds * 1000))
        self.pointer.append((time.time(), *self.pointer[-1][1:]))

    def save_gif(self, name: str, width: int = 960, fps: int = 20) -> None:
        self.cdp.send("Page.stopScreencast")
        t_end = self.pointer[-1][0]
        t, out = self.frames[0][0], []
        while t <= t_end:
            png = max((f for f in self.frames if f[0] <= t), key=lambda f: f[0], default=self.frames[0])[1]
            img = Image.open(io.BytesIO(png)).convert("RGB")
            self._draw_pointer(img, t)
            out.append(img.resize((width, round(img.height * width / img.width)), Image.LANCZOS))
            t += 1 / fps
        samples = Image.new("RGB", (width, out[0].height * 3))
        for i, k in enumerate((0, len(out) // 2, len(out) - 1)):
            samples.paste(out[k], (0, i * out[0].height))
        palette = samples.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
        frames = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in out]
        frames[0].save(OUT / name, save_all=True, append_images=frames[1:], loop=0, duration=round(1000 / fps),
                       optimize=True)
        print(f"✓ {OUT / name} ({len(frames)} frames, {(OUT / name).stat().st_size / 1e6:.1f} MB)")

    def _draw_pointer(self, img: Image.Image, t: float) -> None:
        before = [p for p in self.pointer if p[0] <= t] or self.pointer[:1]
        _, x, y = before[-1]
        scale = img.width / self.page.viewport_size["width"]
        x, y, s = x * scale, y * scale, 1.25 * scale
        d = ImageDraw.Draw(img)
        for c in self.clicks:  # a ring spreading from each click
            if 0 <= t - c < 0.35:
                r = (8 + 60 * (t - c)) * scale
                d.ellipse((x - r, y - r, x + r, y + r), outline=(186, 85, 211), width=round(3 * scale))
        arrow = [(0, 0), (0, 17), (4.5, 13), (7.5, 20), (10, 19), (7, 12.5), (12.5, 12.5)]
        d.polygon([(x + ax * s, y + ay * s) for ax, ay in arrow], fill="white", outline="black", width=max(1, round(scale)))


def shelf_gif(browser, url: str, deck: str, also: str) -> None:
    """The landing page: hover a couple of decks (their pages fan out), open one (it grows into
    the score while the others fly into the library, which then steps aside)."""
    page = browser.new_page(viewport={"width": 1280, "height": 760}, device_scale_factor=1, color_scheme="dark")
    page.goto(f"{url}/")
    page.wait_for_function("[...document.querySelectorAll('.deck img')].every(i => i.classList.contains('ready'))")
    page.wait_for_timeout(600)
    center = lambda slug: (lambda b: (b["x"] + b["width"] / 2, b["y"] + b["height"] * 0.45))(
        page.locator(f'.deck[data-slug="{slug}"] .front').bounding_box())
    rec = Recorder(page)
    rec.start(640, 700)
    rec.wait(0.6)
    rec.glide(*center(also), 0.7)
    rec.wait(1.1)
    rec.glide(*center(deck), 0.6)
    rec.wait(1.1)
    rec.click()
    rec.wait(2.6)
    rec.save_gif("shelf.gif")
    page.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--song", default="gymnopedie-1")
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--at", type=float, default=40.0, help="Recording time (s) to show playing")
    ap.add_argument("--search", default="satie gymnopedie piano")
    ap.add_argument("--also", default="bach-prelude-c", help="A second deck to hover on the shelf")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        shelf_gif(browser, args.url, args.song, args.also)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2, color_scheme="light")
        page.goto(f"{args.url}/#{args.song}")
        page.evaluate("localStorage.setItem('pianoscribe.mode', 'pages')")
        page.reload()
        ready(page)

        # 1. Pages, following the recording, notes lit as they sound.
        seek(page, args.at)
        save(page.screenshot(), "viewer.png", 1800)

        # 2. The endless line under its playhead, and a short GIF of it gliding.
        page.click("#modeLine")
        page.wait_for_timeout(1200)
        seek(page, args.at)
        save(page.screenshot(), "line.png", 1800)
        box = page.locator(".stripwrap").bounding_box()
        page.evaluate("document.getElementById('audio').play()")
        frames = []
        for _ in range(60):  # ~6 s at 10 fps
            frames.append(Image.open(io.BytesIO(page.screenshot(clip=box))).convert("RGB"))
            page.wait_for_timeout(60)
        page.evaluate("document.getElementById('audio').pause()")
        small = [f.resize((960, round(f.height * 960 / f.width)), Image.LANCZOS) for f in frames]
        palette = small[0].quantize(colors=64)
        small[0].quantize(palette=palette).save(OUT / "line.gif", save_all=True, loop=0, duration=100, optimize=True,
                                                append_images=[f.quantize(palette=palette) for f in small[1:]])
        print(f"✓ {OUT / 'line.gif'} ({len(small)} frames)")

        # 3. Correcting the score: a selected note and the editing panel.
        page.click("#modePages")
        page.wait_for_timeout(1200)
        seek(page, args.at)
        page.keyboard.press("e")
        page.evaluate("[...document.querySelectorAll('.page g.note.playing')].slice(-1)[0]"
                      "?.dispatchEvent(new MouseEvent('click', {bubbles: true}))")
        page.wait_for_timeout(400)
        save(page.screenshot(), "editing.png", 1800)
        page.keyboard.press("e")

        # 4. Adding a song: YouTube search results and the credits form.
        page.evaluate("document.getElementById('addSong').click()")  # it lives in the (closed) library sidebar
        page.fill("#addQuery", args.search)
        page.click("#addGo")
        page.wait_for_selector("#addResults li[data-i]", timeout=30000)
        page.wait_for_timeout(2500)  # thumbnails
        page.click("#addResults li[data-i='0']")
        page.wait_for_timeout(300)
        save(page.screenshot(), "add-song.png", 1800)
        browser.close()


if __name__ == "__main__":
    main()
