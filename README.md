# FitPlus SEO queue

Articles written by the cloud SEO agent. The VPS publisher picks up `queue/<slug>/` (polls every 30 min) and posts it to fitplusapp.com: featured image, tags, Rank Math meta, then publish.

## How the SEO agent writes one article

1. `existing.json` lists every article already on the site (slug, title, focus keyword). Pick a NEW Persian fitness / nutrition topic with real search demand that none of them cover. Link 1–2 existing articles inside the body.
2. Write `article.json` (keys documented at the top of `tools/build_post.py`):
   - `slug` (English, short, hyphenated), `title`, `excerpt` (meta description, 140–160 chars), `category` (38 workout | 48 nutrition), `tags` (3 Persian), `rank_title`, `focus_keyword` ("main,sec1,sec2"), `mid_cta`, `body_html`.
   - `photo_topic`: one of `workout | home-workout | fat-loss | nutrition | cardio | recovery | app`, the closest to the article. Do NOT send the old `image` graphic block.
3. `python3 tools/build_post.py article.json` builds `queue/<slug>/post.json` + `<slug>.jpg`.
   - The hero image is a REAL photo from `photos/approved.json`, Digikala-Mag style, with the FitPlus logo badge. `photos/used.json` rotates them, so every article gets a different photo. Commit `photos/used.json` too.
   - Every post automatically gets the slim FitPlus banner at the top, plus 3 CTAs to https://web.fitplusapp.com/.
4. Add the article to `existing.json`, then commit and push.

## Photo rules (from the owner, strict)

Only modest photos: mostly men, fully clothed (no shirtless, no tight crop tops). Women only when wearing hijab and fully covered. Food and equipment photos are fine. Only files listed in `photos/approved.json` may be used; that list is reviewed by hand. Never add a photo to it without checking these rules.

## Photo library

`tools/photo_queries.json` + `tools/fetch_photos.py` (GitHub Action "FitPlus photo library") download CC0 stock photos into `photos/<topic>/`. New downloads are NOT approved automatically.

Fonts: Vazirmatn (SIL Open Font License) by Saber Rastikerdar.
