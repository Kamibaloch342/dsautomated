# Image generation: code review guide

How the 5 listing images are made, where each piece lives, and what to change for what.
Sample output for G0507745 is in `docs/sample_output/`.

## Flow

```
row (DB / export / test_data)
  └─ hathoro/data.py        parse_listing(): size, wall type, ECT or burst rating, pack, origin
       └─ Listing.fields()   every {placeholder} the images can use ({L}, {pack}, {rating}, ...)
            └─ hathoro/images.py   render_listing(): runs the 5 templates, saves JPGs
                 ├─ content.json   all text per image (filled with the placeholders)
                 ├─ theme.json     colours, fonts, sizes, template photos + anchor points
                 └─ hathoro/design.py   Canvas: photos, text, header, tiles, seal, arrows
```

## Files to review (image code only)

| file | what it does |
|---|---|
| `hathoro/images.py` | **The 5 templates.** One function per image: `main_image`, `dimensions_image`, `corrugated_image`, `pack_image`, `origin_image`. Each lays out its blocks in design px on a 1500×1500 canvas. `render_listing()` runs them, picks fallback text (`content_key`) and reuses identical images (`cache`). |
| `hathoro/design.py` | **The drawing toolkit.** `Theme` loads theme.json, fonts and template photos. `Canvas` draws at 3× resolution and downsamples (sharp edges): `photo()` (multiply / alpha cut-out / rounded card), `text()` with baseline/anchor control, `header()` (eyebrow + orange badge + subline), `tile()`/`tiles()`/`tile_column()` (feature tiles), `seal()` (round badge with curved text), `dim_arrow()` (dashed dimension arrow), `logo()`. Layout maths: `fit_photo()` (largest scale that fits a region, labels included), `edge_callout()` (arrow parallel to a box edge + label position), `trim_white()`. |
| `hathoro/icons.py` | Tile icons drawn from lines and polygons (recycle, box, flat, flag, shield, truck, weight, ruler, crush, check, star). |
| `tools/prepare_photos.py` | One-time prep of the fixed template photos: crop the Hathoro box photo and level its background to white, key the purple background out of the flat-pack stack photo, crop the close-up. |
| `tools/check_anchors.py` | Draws the box-corner anchor points on the open-box photo so you can see the arrows will sit on the edges. |
| `hathoro/api.py`, `make_images.py` | Make the images for any size from Python or the command line, with text overrides. |
| `hathoro/zoro_images.py` | Finds or downloads the supplier photo used by image 1. |

## Each image

| # | function | photo | what changes per listing |
|---|---|---|---|
| 1 | `main_image` | the product's own Zoro photo: white border trimmed (`trim_white`), centred, multiplied onto white; logo top-right | the photo (falls back to the Hathoro box photo) |
| 2 | `dimensions_image` | Hathoro open box (`photos.open_box`) | header text; width/length/height arrows come from `photos.open_box.anchors` scaled by `fit_photo`, offset outwards by `edge_callout` |
| 3 | `corrugated_image` | close-up in a rounded card (`cover()` crop) + seal + 3 spec tiles | ECT **or** burst wording, max weight, size limit (`corrugated_generic` if no rating) |
| 4 | `pack_image` | flat-pack stack cut-out, bleeding off the right edge, soft shadow | pack count, wall type |
| 5 | `origin_image` | Hathoro open box + seal + 3 tiles | Made in USA only when the data says USA, otherwise `origin_not_usa` |

## Where to change what

| want to change | edit |
|---|---|
| any word on an image, file names | `content.json` |
| colours, fonts, text sizes, margins, tile size, logo | `theme.json` (`colors`, `fonts`, `text`, `layout`) |
| arrow look (thickness, dashes, heads, distance from the box) | `theme.json` → `arrows` |
| a template photo | put it in `assets/photos/source/`, run `tools/prepare_photos.py`, update anchors, run `tools/check_anchors.py` |
| a layout (positions of blocks) | the image's function in `hathoro/images.py` |
| how text, tiles or the seal are drawn | `hathoro/design.py` |
| strength wording rules (ECT vs burst) | `Listing.rating_fields()` in `hathoro/data.py` |

## Try it

```bash
pip3 install -r requirements.txt
python3 make_images.py --size 22x18x8 --pack 25 --ect 32 --made-in-usa       # -> output/manual/
python3 make_images.py --size 12x9x6 --pack 25 --set "pack.badge={pack} BOXES"
python3 tools/check_anchors.py                                                 # anchor overlay
python3 tests/test_pipeline.py && python3 tests/test_tunnel.py                 # tests
```

## Archive

`archive/v1_3d_renderer/` is the first version (a box drawn in 3D with numpy instead of real
photos). It is not used any more; it is included only for reference.
