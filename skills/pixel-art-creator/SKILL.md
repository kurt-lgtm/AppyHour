---
name: pixel-art-creator
description: >
  Create pixel art sprites, icons, characters, items, and tilesets as PNG images
  using Python (Pillow). Use this skill whenever the user asks to make pixel art,
  create a sprite, design a retro game character, draw an 8-bit or 16-bit style icon,
  generate pixel art assets, or create any low-resolution intentionally-pixelated
  image. Also trigger when the user mentions "sprite", "pixel", "retro art",
  "game asset", "tileset", "NES style", "SNES style", "Game Boy palette", or
  similar retro/pixel art terminology — even if they don't say "pixel art" explicitly.
  Does NOT require Aseprite or any external tools — works entirely with Python PIL.
---

# Pixel Art Creator

Create pixel art sprites, characters, icons, items, and tiles as crisp PNG images
using Python and Pillow. No external pixel art editors needed.

## Philosophy

Pixel art is a deliberate art form where every single pixel is an intentional
design choice. Unlike high-resolution digital art where you paint broad strokes,
pixel art means thinking at the individual pixel level — each dot of color matters.

The best pixel art has these qualities:
- **Clarity at tiny sizes** — a 16x16 character should read instantly
- **Limited palettes** — constraints breed creativity; 4-16 colors is the sweet spot
- **No anti-aliasing** — hard pixel edges, never blurred or smoothed
- **Readable silhouettes** — the shape alone should tell you what it is
- **Consistent pixel density** — every "pixel" in the art should be the same size

## How It Works

You draw pixel art by placing colored pixels on a small grid (typically 8x8 to
64x64), then scale the result up for display. The workflow:

1. Define a color palette (or pick a classic retro palette)
2. Build the sprite as a 2D array of palette indices
3. Render to a small PIL Image
4. Scale up with nearest-neighbor resampling (keeps pixels crisp)
5. Save as PNG

## Palettes

Use these classic palettes or create custom ones. Each palette is a dict mapping
single-character keys to RGB tuples for easy grid definition.

```python
# Game Boy (4 colors)
GAMEBOY = {
    '0': (15, 56, 15),     # darkest green
    '1': (48, 98, 48),     # dark green
    '2': (139, 172, 15),   # light green
    '3': (155, 188, 15),   # lightest green
    '.': None,              # transparent
}

# NES-inspired (common subset)
NES = {
    'K': (0, 0, 0),        # black
    'W': (255, 255, 255),  # white
    'R': (188, 32, 38),    # red
    'B': (0, 88, 168),     # blue
    'G': (0, 136, 56),     # green
    'Y': (248, 216, 0),    # yellow
    'O': (228, 108, 16),   # orange
    'P': (148, 64, 164),   # purple
    'S': (188, 148, 108),  # skin
    'D': (116, 76, 36),    # dark brown
    'L': (168, 168, 168),  # light gray
    'M': (88, 88, 88),     # medium gray
    '.': None,              # transparent
}

# PICO-8 (16 colors)
PICO8 = {
    '0': (0, 0, 0),        # black
    '1': (29, 43, 83),     # dark blue
    '2': (126, 37, 83),    # dark purple
    '3': (0, 135, 81),     # dark green
    '4': (171, 82, 54),    # brown
    '5': (95, 87, 79),     # dark gray
    '6': (194, 195, 199),  # light gray
    '7': (255, 241, 232),  # white
    '8': (255, 0, 77),     # red
    '9': (255, 163, 0),    # orange
    'A': (255, 236, 39),   # yellow
    'B': (0, 228, 54),     # green
    'C': (41, 173, 255),   # blue
    'D': (131, 118, 156),  # lavender
    'E': (255, 119, 168),  # pink
    'F': (255, 204, 170),  # peach
    '.': None,              # transparent
}
```

## Rendering Code

Use this pattern for every sprite:

```python
from PIL import Image

def render_sprite(grid, palette, scale=8):
    """Render a pixel art grid to a scaled PNG image.

    Args:
        grid: list of strings, each char maps to a palette color.
              '.' is always transparent.
        palette: dict mapping single chars to (R, G, B) tuples or None.
        scale: integer multiplier (8 = each pixel becomes 8x8 on screen).

    Returns:
        PIL.Image in RGBA mode, scaled up with crisp nearest-neighbor.
    """
    h = len(grid)
    w = len(grid[0])
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))

    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            color = palette.get(ch)
            if color is not None:
                img.putpixel((x, y), (*color, 255))

    # Scale up with NEAREST to keep pixels sharp — never use BILINEAR/LANCZOS
    return img.resize((w * scale, h * scale), Image.NEAREST)
```

## Grid Design Tips

When designing the grid, think in terms of the silhouette first:

- **Characters**: Start with the head (2-3px wide), body (3-5px), legs (1px each).
  Arms extend 1px from the body. Eyes are single pixels — their placement defines
  the expression. A 1px mouth reads as neutral; offset it for emotion.

- **Items/icons**: Fill 80% of the canvas. Leave 1px padding on the edges.
  Use 1-2 highlight pixels (brighter shade of base color) on the upper-left
  to suggest light direction. Add 1px dark outline for pop.

- **Tiles**: Must tile seamlessly — left edge matches right, top matches bottom.
  Test by placing 4 copies in a 2x2 grid.

- **Outlines**: Use a 1px dark outline (usually black or darkest palette color)
  around the entire sprite. Interior outlines between color regions help
  readability at small sizes.

## Example: 16x16 Character

```python
grid = [
    '....KKKK....',  # top of head
    '...KSSSSK...',  # forehead
    '..KSWKWKSK..',  # eyes (W=white, K=pupil)
    '..KSSSSSSK..',  # cheeks
    '...KSOSK....',  # mouth (O=open)
    '....KKKK....',  # chin
    '...KBBBBK...',  # shirt
    '..KBBYBBK...',  # shirt with belt (Y)
    '..KBBBBBBK..',  # shirt
    '..KSKKKKSK..',  # hands + belt
    '...KBBBBK...',  # pants
    '...KBBBBK...',  # pants
    '..KBK..KBK..',  # legs
    '..KBK..KBK..',  # legs
    '.KDDK..KDDK.',  # boots
    '.KKKK..KKKK.',  # boot soles
]
```

## Output Requirements

- Always save as PNG with transparency support (RGBA mode)
- Default scale: 8x (a 16x16 sprite becomes 128x128 on screen)
- For display thumbnails or previews, also save a 1x version
- Use `Image.NEAREST` for all scaling — never smooth or blur pixel art
- Name files descriptively: `hero_idle.png`, `sword_icon.png`, `grass_tile.png`

## Iteration Approach

Pixel art benefits from rapid iteration:

1. **Block out** — fill the silhouette with base colors, ignore details
2. **Refine** — add shading (1 darker + 1 lighter shade per color)
3. **Detail** — eyes, small features, highlights
4. **Polish** — check outline consistency, fix stray pixels

Show the user each stage when working on complex pieces so they can
give feedback before you get too deep into details.

## Common Requests and How to Handle Them

| Request | Grid Size | Palette | Notes |
|---------|-----------|---------|-------|
| Game character / hero | 16x16 or 32x32 | NES or PICO-8 | Front-facing, clear silhouette |
| Weapon / item | 16x16 | NES | 45-degree angle looks best |
| Enemy / monster | 16x16 to 32x32 | PICO-8 | Menacing silhouette, bright eyes |
| UI icon | 8x8 or 16x16 | Custom 4-6 colors | Maximum clarity, thick lines |
| Tileset piece | 16x16 | Custom earth tones | Must tile seamlessly |
| Portrait / face | 32x32 or 48x48 | Custom | More detail for expressions |
| Logo / text | varies | Custom 2-3 colors | Block letters, 1px stroke |
