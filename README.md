# bluekoru

The landing page for [bluekoru.eu](https://bluekoru.eu), served by GitHub Pages.

The page is one full-screen photograph with the **blue*koru*** wordmark over it, and an optional place caption under the wordmark. Each time the page loads it shows a different photo. Each photo has its own wordmark colours and its own wordmark position, so the text stays readable on that image.

There is no build step. The site is a static `index.html`, an `images.json` manifest and a folder of WebP images. A Python script, `tools/onboard.py`, prepares new photos and adds them to the manifest.

## Repository layout

| Path | Published? | What it is |
|---|---|---|
| `index.html` | yes | The whole site: markup, CSS and a small inline script. |
| `images.json` | yes | The manifest: one entry per photo, with its look. |
| `img/` | yes | Resized, metadata-free WebP files, `<id>-<width>.webp`. |
| `CNAME` | yes | Custom domain for GitHub Pages (`bluekoru.eu`). |
| `tools/onboard.py` | yes (not used by the page) | The script that onboards photos. |
| `raw-images/` | no (gitignored) | Original photos. They can carry EXIF/GPS data, so they are never committed. |
| `previews/` | no (gitignored) | Preview sheets rendered by the script. |

## How the page works

1. `index.html` fetches `images.json`.
2. It chooses an image (see *Random order without repeats* below).
3. It applies that image's look by setting CSS custom properties:
   - `--ink`: colour of "blue"
   - `--fire`: colour of "koru" and of the place caption
   - `--y`: vertical position of the wordmark, in `vh`
   - `--glow`: a soft light or dark text shadow, or none
4. It builds an `<img>` with a `srcset` covering every available width, so the browser downloads the right size. `sizes` uses the image's aspect ratio so that `object-fit: cover` stays sharp on tall screens. `object-position` comes from the entry's `focus`.
5. The photo and the wordmark stay hidden (`opacity: 0`) until the image loads, then fade in together. This means you never see the wrong colours over a half-loaded photo.

### Wordmark position

The wordmark is always centred horizontally. Its vertical position comes from `y`. By convention every image uses one of two values:

- `3.5`: near the **top** of the screen
- `76`: near the **bottom** of the screen

You choose whichever puts the text over a calm part of the photo. The script proposes one of the two automatically, and you can switch it with `tune --y`.

### Random order without repeats (the shuffle bag)

Each visitor gets a random sequence in which no photo repeats until they have seen all of them.

- On the first visit, the script shuffles the IDs of all live (non-draft) images with Fisher–Yates. The result is the **bag**.
- Each page load takes one ID from the bag. The rest of the bag is saved in `localStorage` (key `bag`).
- When the bag is empty, a new shuffle starts. Photos shown in the second half of the previous round are moved to the end of the new order. This prevents the same photo appearing twice in a row across the boundary between rounds.
- The saved bag expires after 24 hours, after which a new round starts. IDs that are no longer in the manifest are dropped, so adding or removing photos never breaks the bag.
- If `localStorage` is unavailable (for example in some private browsing modes), each load is a fresh random pick.

### URL parameters

- `?img=<id>`: always show that image. This works for drafts too, which is how you review an image before it goes live.
- `?still`: turn off the fade-in. The script uses this when taking screenshots.

If JavaScript is off, a `<noscript>` fallback shows the Karikari harakeke photo.

## The manifest (`images.json`)

```json
{
  "id": "karikari-harakeke-20201128",
  "alt": "Orange harakeke flowers in front of the sea at Karikari",
  "base": "img/karikari-harakeke-20201128",
  "widths": [1280, 1920, 2560],
  "aspect": 1.3333,
  "ink": "#285a78",
  "fire": "#cc3e0b",
  "y": 3.5,
  "focus": "62% 50%",
  "glow": "light",
  "place": "Karikari, New Zealand"
}
```

| Field | Meaning |
|---|---|
| `id` | Slug made from the raw file name, usually `place-subject-YYYYMMDD`. |
| `alt` | Alt text for the photo. |
| `base` | Path prefix. The files are `<base>-<width>.webp`. |
| `widths` | Widths that were generated: 1280, 1920 and 2560, or fewer if the original is smaller. |
| `aspect` | Width ÷ height of the processed image. |
| `ink` / `fire` | Colours of "blue" and "koru". |
| `y` | Wordmark top offset in `vh`: `3.5` (top) or `76` (bottom). |
| `focus` | CSS `object-position`, which controls what stays visible when the photo is cropped. |
| `glow` | `light`, `dark` or `none`. |
| `place` | Optional caption, for example `"Nikko, Japan"`. |
| `draft` | If `true`, the image is left out of the rotation and can only be seen with `?img=`. |
| `crop_right` | Optional fraction trimmed off the right edge at processing time. It is kept when you re-run `add`. |

## The onboarding tool (`tools/onboard.py`)

### Requirements

- Python 3 with **Pillow** and **numpy** (`pip install pillow numpy`)
- **Chromium** or Google Chrome on your `PATH`, for previews. This is optional: if no browser is found, the script skips the preview.

Run all commands from the repository root.

### Workflow

```sh
# 1. Put the original in raw-images/ and add it (it is created as a draft)
tools/onboard.py add raw-images/nikko-shrine-20190415.jpg --alt "A red shrine gate among cedars in Nikko"

# 2. Look at previews/nikko-shrine-20190415.png, then adjust the look
tools/onboard.py tune nikko-shrine-20190415 --y 76 --glow dark --place "Nikko, Japan"
tools/onboard.py tune nikko-shrine-20190415 --ink "#1d3b52" --fire "#d8452a" --focus "40% 60%"

# 3. Put it into the rotation
tools/onboard.py approve nikko-shrine-20190415

# 4. Commit img/ and images.json, then push. GitHub Pages deploys it.
```

### Commands

| Command | What it does |
|---|---|
| `add <raw> [--alt TEXT] [--crop-right F]` | Processes the photo, proposes a look and adds a **draft** entry, then renders a preview. If the ID already exists, it only regenerates the image files and keeps the tuned look. |
| `tune <id> [--ink] [--fire] [--y] [--focus] [--glow] [--alt] [--place]` | Changes any part of the look, then renders a new preview. `--place ""` removes the caption. |
| `preview <id>` | Renders the preview again without changing anything. |
| `approve <id>` | Removes `draft`, so the image joins the rotation. |
| `list` | Lists every entry and whether it is live or a draft. |

### What `add` does

1. **Cleans and resizes the image.** It applies the EXIF orientation, converts embedded colour profiles to sRGB, optionally trims the right edge, and writes WebP files (quality 82) at each width up to the original's width. Each file is rebuilt from raw pixels so that no EXIF, XMP, ICC or GPS data can carry over. The script then checks every output file and refuses to keep any that still contains metadata. It also tells you if the original had GPS data.
2. **Proposes a look.**
   - **Position:** it centre-crops the photo to how a 16:9 desktop would show it. It then scores a wordmark-sized band at the top (`3.5`) and bottom (`76`) positions, on edge detail and tonal variation, and picks the calmer one. This is only a starting point, so check the preview.
   - **Colours:** it reduces the photo to 12 dominant colours and lightens or darkens each one until it reaches 3.5:1 contrast against the band behind the text. It chooses a vivid, prominent colour, then the colour most distinct from it. The darker of the two becomes `ink` ("blue") and the other becomes `fire` ("koru"). For monochrome photos it takes the strongest-contrast tone. The colours always come from the photo itself.
   - **Glow:** `light` on dark backgrounds, `dark` on light backgrounds.
3. **Renders a preview.** It opens the real `index.html?still&img=<id>` in headless Chromium at desktop (1920×1080), tablet (820×1180) and phone (390×844) sizes. It retries if it catches a blank page, and saves the three screenshots side by side as `previews/<id>.png`. Because the preview uses the real page, it shows exactly what visitors will see.

At the end it reminds you if the alt text is still a filename placeholder, or if the photo is portrait. Portrait photos get cropped heavily on landscape screens.

## Previewing the site locally

The page uses `fetch()` to load `images.json`, so open it through a local server rather than as a `file://` URL:

```sh
python3 -m http.server 8000
# http://localhost:8000/                       random, using the shuffle bag
# http://localhost:8000/?img=<id>              a specific image, including drafts
```

To reset your own shuffle bag, run `localStorage.removeItem('bag')` in the browser console.

## Deployment

GitHub Pages serves the repository root of `main`, and the `CNAME` file points it at `bluekoru.eu`. To deploy, push to `main`.
