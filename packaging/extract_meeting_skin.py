"""Prepare the approved neutral glass sheet as runtime PNGs (developer only).

Usage: python packaging/extract_meeting_skin.py <1536x1024-sheet>
Pillow/numpy are not runtime dependencies. For RGB/JPEG inputs, transparency
is reconstructed approximately; the original white matte cannot be recovered.
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def extract(image, box, radius=48):
    crop = image.crop(box).convert('RGBA')
    if crop.getchannel('A').getextrema() != (255, 255):
        return crop  # Preserve actual supplied transparency when available.
    # This sheet arrived as JPEG on white. Neutralize chroma and reconstruct a
    # translucent coating, retaining its texture and brighter rim separately.
    gray = crop.convert('L').filter(ImageFilter.GaussianBlur(0.8))
    light = np.asarray(gray, dtype=float)
    baseline = np.asarray(gray.filter(ImageFilter.GaussianBlur(6)), dtype=float)
    mask = Image.new('L', (crop.width*4, crop.height*4))
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, mask.width-1, mask.height-1), radius*4, fill=255)
    coverage = np.asarray(mask.resize(crop.size, Image.Resampling.LANCZOS))/255
    inner = Image.new('L', mask.size)
    ImageDraw.Draw(inner).rounded_rectangle(
        (32, 32, mask.width-33, mask.height-33), (radius-8)*4, fill=255)
    rim = 1-np.asarray(inner.resize(crop.size, Image.Resampling.LANCZOS))/255
    # Amplify only the rim: JPEG compression noise must not become alpha grain.
    alpha = np.clip(86 + np.maximum(0, light-baseline)*9*rim, 0, 235)
    result = Image.merge('RGBA', (gray, gray, gray,
        Image.fromarray((alpha*coverage).astype('uint8'))))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=Path)
    args = parser.parse_args()
    image = Image.open(args.reference)
    if image.size != (1536, 1024):
        parser.error('Expected the approved 1536 x 1024 neutral sheet.')
    output = Path(__file__).resolve().parents[1] / 'assets' / 'meeting-glass'
    output.mkdir(parents=True, exist_ok=True)
    # Use the delivered contours, which differ from the requested mockup grid.
    extract(image, (68, 98, 1468, 210)).save(output/'toolbar.png')
    extract(image, (68, 258, 1468, 932)).save(output/'panel.png')


if __name__ == '__main__':
    main()
