# Pine Ridge Naturalist

A home weather, wildlife and sky station for Arlington, MA. It runs on a small always-on box at home and pulls everything the house notices into one place:

- **WeatherFlow Tempest**: live conditions, the station's own forecast, and each lightning strike or first raindrop as it happens. It uses the cloud websocket and the LAN broadcast, so it still works when the internet is down.
- **Haikubox (birds and bats)**: who sang, when, and the bats overnight.
- **Bird Buddy**: photo postcards from the feeder.
- **Arlo**: animals on the yard cameras, named by Claude from the thumbnail.
- **The neighborhood**:
  - eBird notable sightings nearby and iNaturalist observations within a few km
  - NWS Boston alerts, plus the forecasters' own Area Forecast Discussion
  - NOAA aurora (Kp) outlook
  - any forum or Reddit feeds you list
  - a daily Claude web scan of local weather, birding and astronomy forums (AmericanWx New England, MASSBIRD, Mass Audubon, ATMoB, Cloudy Nights, and others)
- **The sky**: sun, moon phase, darkness window, the planets at 9pm and meteor showers, all computed locally.

What you get from it:

| | |
|---|---|
| **Live dashboard** (`http://<box>:8787`) | Behind everything is an animated sky drawn from the real sky: sun angle, clouds, rain and snow, stars, the actual moon phase, and lightning flashes. On top: a forecast sentence, a 24-hour strip and 10 days, tonight's sky, the yard timeline, and neighborhood chatter. **Listen to outside** plays a soundscape built from the station's data: wind at its speed and gusts, rain at its rate, thunder delayed by each strike's real distance, crickets chirping at the rate Dolbear's law gives for the temperature, and today's actual Haikubox recordings. |
| **Morning wake-up** | Written by Claude from the last 24 hours, narrated, emailed, and pushed to your phone at a time you pick (or relative to sunrise). |
| **End-of-week roundup** | A shareable story page with photos, song clips, stats, a temperature chart, species of the week, the sky ahead, and a "try this" activity for a kid or a class. Emailed, pushed, and optionally published to GitHub Pages. |
| **Live phone alerts** (ntfy) | Lightning nearby, rain starting, NWS warnings, a notable bird reported nearby, a new yard species for the year, the first bat of the night, an animal on camera, and aurora watch. Quiet hours are respected except for severe weather. |

## Try the design right now (no accounts needed)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m naturalist demo        # open http://localhost:8787
```

The demo uses clearly labeled sample data. The sky, moon and planets are real.

## Set it up for real

1. `cp .env.example .env` and `cp config.example.yaml config.yaml`
2. Fill in `.env`. Every source is optional; anything left blank is simply skipped.

   | Key | Where to get it |
   |---|---|
   | `TEMPEST_TOKEN` | tempestwx.com → Settings → Data Authorizations. Your station's coordinates are read from it, so your address never goes in a file. |
   | `ANTHROPIC_API_KEY` | console.anthropic.com. Writes the briefs, scans the forums, and identifies Arlo critters. |
   | `HAIKUBOX_SERIAL` | The serial printed on the box, or shown in the Haikubox app. |
   | `BIRDBUDDY_EMAIL` / `_PASSWORD` | Your Bird Buddy login. If you signed up with Google or Apple, set a password in the Bird Buddy app first. |
   | `ARLO_USERNAME` / `_PASSWORD` + `ARLO_TFA_IMAP_*` | Arlo sends a 2FA code by email, and the box reads it over IMAP. For Gmail, create an App Password. Consider a dedicated Arlo "friend" account with view-only access. |
   | `EBIRD_API_KEY` | ebird.org/api/keygen (free) |
   | `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | myaccount.google.com/apppasswords |
   | `NTFY_TOPIC` | Install the ntfy app on your phone and subscribe to a long, random topic name. |
3. `python -m naturalist check` polls each source once and prints ✓ / ✗ / · for each.
4. `python -m naturalist brief morning` writes one now so you can read it (add `--send` to email and push it).
5. Run it for good:
   - Raspberry Pi 5: `deploy/install-pi.sh`
   - Mac: `deploy/com.pineridge.naturalist.plist`

Narration uses the macOS voices (`say`, so pick a Premium voice) or Piper on Linux/Pi, and is converted to mp3 with ffmpeg. With no voice engine installed, the page reads the brief aloud in the browser.

## Notes and honest caveats

- **Bird Buddy and Arlo have no official public APIs.** This uses the community libraries `pybirdbuddy` and `pyaarlo`, which log in as you. They work today but can break when those companies change their apps.
- **Haikubox field names vary by box generation.** The parser is forgiving, but run `python -m naturalist probe haikubox` on day one and check the output. Bats are recognized by species name against the list of Massachusetts bats.
- **Shared pages** copy their photos and audio locally, so they keep working after Bird Buddy's signed links expire. Yard-camera stills stay out of shared pages unless you set `share_arlo: true`.
- **Costs:** a morning brief plus a weekly roundup, with web search, runs a few dollars a month on the Claude API at the default model and effort. Lower `claude.effort` in `config.yaml` to spend less.

## Coming next

- `docs/skycam.md`: skyward GoPro + 360 camera, triggered for storms, cloudy sunsets and nearby lightning (the trigger engine is already in `naturalist/skycam.py`)
- `docs/school-rig.md`: a matching station for a nearby science class

## Layout

```
naturalist/
  sources/   tempest, haikubox, birdbuddy, arlo, community (eBird/iNat/NWS/SWPC/RSS), sky
  hub.py     shared state + event timeline + live fan-out
  briefs.py  morning + weekly: gather → forum scan → Claude → page → narration → delivery
  alerts.py  live phone pushes        skycam.py  camera triggers
  web/static dashboard (sky.js canvas, sound.js soundscape, app.js)
  templates/ shareable brief and roundup pages
```
