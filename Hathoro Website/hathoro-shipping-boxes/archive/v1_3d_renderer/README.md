# Box Image Generator

Generates 1500×1500 px, white-background product images of an open corrugated shipping box
with dimension callouts (width / length / height arrows), a title, a size badge, a tagline
and the brand logo.

The box is not a photo. It is a 3D model (walls, flaps, rounded creases and corrugated
cut edges, all with real board thickness) rendered with exact perspective math. The box
proportions follow the real dimensions, and every arrow is computed from the projected box
edges, so the callouts always sit exactly on the box.

![12x9x3 example](output/box_12x9x3.png)

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# default image from config.json (12x9x3)
python box_image.py

# another size
python box_image.py --size 12x9x6 --title "SMALL SHIPPING BOX"

# custom tagline (\n = line break) and output path
python box_image.py --size 24x6x6 --tagline "Great for Long Items,\nPosters & Tools" --out output/long.png

# many images at once: one row per image
python box_image.py --batch sizes.csv
```

Images are saved in `output/` by default.

### Batch CSV columns

| column | required | notes |
|---|---|---|
| `length`, `width`, `height` | yes | numbers, e.g. `12`, `9`, `3.5` |
| `title` | no | defaults to `config.json` |
| `tagline` | no | use `\n` for a line break |
| `badge` | no | defaults to `{L}X{W}X{H}"` |
| `length_label`, `width_label`, `height_label` | no | e.g. `{L} IN LENGTH` |
| `filename` | no | defaults to `box_LxWxH.png` |

### Blank image + layout JSON

If you want to draw the text yourself:

```bash
python box_image.py --blank --layout --out output/blank.png
```

This writes the box on its own (no text, arrows or logo) plus `output/blank.json` with the
exact pixel positions of every arrow (`arrow: [[x0, y0], [x1, y1]]`), label centre, badge
rectangle, title, tagline and logo. The blank box is in exactly the same place as in the
full image.

## Customising (`config.json`)

| key | what it controls |
|---|---|
| `box` | dimensions and unit symbol (`"` by default; use `" cm"` for metric) |
| `shape` | set to `{"length": 12, "width": 9, "height": 6}` to keep one fixed box shape while `box` only changes the text |
| `title`, `badge`, `tagline`, `labels` | text templates; `{L}`, `{W}`, `{H}`, `{u}` are replaced with the dimensions and unit |
| `colors` | brand navy, badge orange, text colors, cardboard (kraft) and corrugated edge colors |
| `fonts` | font files (Poppins is bundled in `assets/fonts`, OFL licence) |
| `logo` | logo PNG with transparency (`assets/logo.png`); a higher-resolution original logo file will look sharper |
| `camera` | `yaw` (rotation), `elevation` (how far above), `perspective` (0 = flat/isometric) |
| `flaps.angles` | how far each flap is opened, in degrees from vertical |
| `layout.height_arrow` | `"corner"` (on the front face, like the reference) or `"right"` (outside the right edge). Very flat boxes switch to `"right"` automatically |
| `layout` | margins, font sizes, arrow thickness, dash length, etc. All sizes are in px for a 1500 px canvas and scale with `canvas` |
| `supersample` | anti-aliasing quality (3 = render at 4500 px and downscale) |
