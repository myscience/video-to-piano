"""Screenshots and a GIF of the practice viewer for the README.

Needs the viewer running (`pianoscribe serve`) and Google Chrome installed:

    uv run --extra docs python scripts/readme_images.py --song gymnopedie-1

Writes docs/images/{viewer,line,editing,add-song}.png and line.gif.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

from PIL import Image
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--song", default="gymnopedie-1")
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--at", type=float, default=40.0, help="Recording time (s) to show playing")
    ap.add_argument("--search", default="satie gymnopedie piano")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
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
