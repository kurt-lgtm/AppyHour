---
name: sprite-sheet-generator
description: >
  Generate animated sprite sheets and frame-by-frame pixel art animations as PNG
  sprite sheets and animated GIFs. Use this skill when the user wants to create
  animation frames, sprite sheets, walk cycles, idle animations, attack animations,
  or any multi-frame pixel art sequence. Also trigger for requests like "animate
  this sprite", "make a walk cycle", "create a sprite sheet", "animated GIF pixel
  art", "frame-by-frame animation", or any mention of game animation assets.
  Works with Python PIL — no Aseprite or external editors needed.
---

# Sprite Sheet Generator

Create multi-frame pixel art animations — walk cycles, idle bobs, attack swings,
explosions — and export them as both sprite sheets (single PNG with all frames
in a row) and animated GIFs.

## How Sprite Animation Works

A sprite animation is a sequence of small images (frames) played in order, like
a flipbook. The key to good pixel animation:

- **Minimal changes between frames** — move only 1-2 pixels per frame. Pixel art
  animation is about subtlety, not dramatic redraws.
- **Consistent volume** — the character shouldn't grow or shrink between frames
  unless that's intentional (like a squash-and-stretch jump).
- **Key poses first** — design the extreme positions (left foot forward, right
  foot forward) then create the in-betweens.
- **Looping** — most game animations loop seamlessly. The last frame should
  transition smoothly back to the first.

## Frame Design Patterns

### Walk Cycle (4 frames)
The classic side-view walk cycle:
1. **Contact** — front foot touches ground, back foot lifts
2. **Down** — body drops slightly, weight shifts forward
3. **Pass** — legs cross, body at neutral height
4. **Up** — push off back foot, body rises slightly

For a front-facing walk, alternate leg positions and add a 1px body bob:
- Frame 1: left leg forward, body at y+0
- Frame 2: legs neutral, body at y-1 (slight bob up)
- Frame 3: right leg forward, body at y+0
- Frame 4: legs neutral, body at y-1

### Idle Animation (2-4 frames)
Keep it subtle — the character should feel alive without being distracting:
- Frame 1-2: normal pose
- Frame 3: 1px body shift down (breathing)
- Frame 4: back to normal
Or just alternate between two frames with a subtle 1px change (blink, sway).

### Attack Swing (3-5 frames)
1. **Windup** — arm pulled back, body tilted
2. **Swing** — arm extended, motion blur pixels (1-2 bright pixels trailing)
3. **Impact** — full extension, small impact flash (2-3 bright pixels)
4. **Recovery** — arm returning, body straightening
5. **Rest** — back to idle pose

### Explosion / Effect (4-6 frames)
1. Small bright core (2x2 yellow/white pixels)
2. Core expands (4x4), outer ring appears
3. Full size, core fades to orange, ring expands
4. Core gone, ring fragments into scattered pixels
5. Scattered pixels fade (reduce count by half)
6. Empty / transparent

## Rendering Code

```python
from PIL import Image
import struct
import zlib

def render_sprite(grid, palette, scale=1):
    """Render a single frame from a character grid."""
    h = len(grid)
    w = len(grid[0])
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            color = palette.get(ch)
            if color is not None:
                img.putpixel((x, y), (*color, 255))
    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    return img


def make_sprite_sheet(frames, palette, scale=8):
    """Combine animation frames into a horizontal sprite sheet.

    Args:
        frames: list of grids (list of strings), one per frame.
        palette: color mapping dict.
        scale: pixel multiplier for final output.

    Returns:
        PIL.Image — all frames side by side in a single row.
    """
    rendered = [render_sprite(f, palette, scale) for f in frames]
    fw, fh = rendered[0].size
    sheet = Image.new('RGBA', (fw * len(rendered), fh), (0, 0, 0, 0))
    for i, frame in enumerate(rendered):
        sheet.paste(frame, (i * fw, 0))
    return sheet


def make_animated_gif(frames, palette, scale=8, frame_duration=150,
                      output_path='animation.gif'):
    """Create an animated GIF from pixel art frames.

    Args:
        frames: list of grids.
        palette: color mapping.
        scale: pixel size multiplier.
        frame_duration: milliseconds per frame (150ms ≈ 6.7 FPS, good default).
        output_path: where to save the GIF.
    """
    rendered = [render_sprite(f, palette, scale) for f in frames]

    # GIF doesn't support RGBA well — composite onto a solid background
    # or use transparency for the first color
    gif_frames = []
    for img in rendered:
        # Convert RGBA to P mode with transparency
        bg = Image.new('RGBA', img.size, (0, 0, 0, 0))
        bg.paste(img, (0, 0))
        gif_frames.append(bg)

    gif_frames[0].save(
        output_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=frame_duration,
        loop=0,  # 0 = loop forever
        disposal=2,  # clear frame before drawing next
        transparency=0,
    )
    return output_path
```

## Timing Guide

Frame duration controls how fast the animation plays:

| Duration (ms) | FPS  | Use for |
|---------------|------|---------|
| 80-100        | 10-12| Fast actions (running, attacks, explosions) |
| 120-150       | 6-8  | Standard (walking, idle) |
| 200-250       | 4-5  | Slow, deliberate (breathing, floating) |
| 400-500       | 2    | Very slow (blinking, ambient) |

## Sprite Sheet Format

Sprite sheets are exported as a single horizontal strip:

```
[Frame1][Frame2][Frame3][Frame4]
```

Include metadata so game engines can slice it:

```python
metadata = {
    "image": "hero_walk.png",
    "frame_width": 16 * scale,
    "frame_height": 16 * scale,
    "frame_count": 4,
    "fps": 8,
    "loop": True,
    "animations": {
        "walk": {"start": 0, "end": 3},
    }
}
```

Save metadata as a JSON sidecar: `hero_walk.json` alongside `hero_walk.png`.

## Output Checklist

For every animation request, deliver:

1. **Sprite sheet PNG** — all frames in a horizontal strip, scaled up (8x default)
2. **Animated GIF** — looping preview the user can immediately see
3. **Metadata JSON** — frame dimensions, count, FPS, animation tags
4. **1x sprite sheet** — unscaled version for game engine import

Name files consistently: `{name}_{animation}.png`, `{name}_{animation}.gif`,
`{name}_{animation}.json`.

## Working with the pixel-art-creator Skill

If the user has already created a static sprite using the pixel-art-creator skill,
you can animate it. Take the existing grid as frame 1, then create variations
for subsequent frames. Keep the palette and overall proportions identical —
only change the parts that move (legs for walking, arms for attacking, etc.).
