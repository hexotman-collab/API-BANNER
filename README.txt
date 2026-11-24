# Free Fire Profile Banner API

Flask API that returns a profile banner as PNG or player details as JSON.
The included `frame.png` is a redesigned 1536×647 neon floral frame with transparent avatar and banner windows.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

The service starts on port `8000`.

## Endpoints

- `GET /` — API status and output dimensions
- `GET /api?uid=PLAYER_UID` — rendered PNG banner
- `GET /banner?uid=PLAYER_UID` — alias for the PNG banner
- `GET /api?uid=PLAYER_UID&format=json` — extracted player information as JSON

## Frame layout

- Avatar window: `(61, 99, 495, 566)`
- Banner window: `(542, 99, 1470, 566)`
- Output: `1536×647` PNG

Keep `frame.png` alongside `app.py`. The frame's inner windows are transparent so fetched images can show through.

## Notes

Player information and icon images are retrieved from the endpoints configured in `app.py`; their availability depends on those external services.


Banner artwork fix: the API now requests full banner artwork from the banners/{banner_id}.png asset path first. Avatar fetching remains on the item-icon sources. The banner is resized to the straight rectangular frame window without center-cropping. If a full-art asset is missing, the API falls back to the existing icon source.


Vercel deployment note: this project uses Vercel's native Flask detection. The `vercel.json` intentionally does not rewrite all requests to `/app.py`; Flask's `/`, `/api`, and `/banner` routes should be handled by the app itself.
