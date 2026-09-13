"""Convert the approved character artwork into app icon formats (requires Pillow)."""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "aindiff" / "assets"


def main() -> None:
    with Image.open(ASSETS / "aindiff-master.png") as source:
        if source.width != source.height:
            raise ValueError("Icon master must be square")
        if "A" not in source.getbands() or source.getchannel("A").getextrema()[0] != 0:
            raise ValueError("Icon master must contain a genuinely transparent background")
        image = source.convert("RGBA").resize((512, 512), Image.Resampling.LANCZOS)
    image.save(ASSETS / "aindiff.png")
    image.save(ASSETS / "aindiff.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    print("Generated window PNG and multi-resolution ICO")


if __name__ == "__main__":
    main()
