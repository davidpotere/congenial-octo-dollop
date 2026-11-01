# Sky camera: a skyward GoPro and a 360 camera

The house already knows when the sky is about to be worth filming. `naturalist/skycam.py` turns those moments into time-lapse triggers. It's switched off until you set `skycam.enabled: true` in `config.yaml`.

## Triggers already wired

| Condition | How it's detected | Default action |
|---|---|---|
| `storm_approaching` | Tempest pressure falling plus gusts over 20 mph, or a 70%+ thunderstorm chance in the next 2 hours | 45-minute time-lapse |
| `sunset_with_clouds` | Sunset is under 45 minutes away and the forecast says partly or mostly cloudy (the best sunsets) | 40-minute time-lapse |
| `lightning_close` | A Tempest strike within 10 miles in the last 15 minutes | 30-minute night-lapse |

Still to add: a `clear_moonless_night` trigger (star trails or Milky Way) and `aurora` (Kp ≥ 6 and clear).

## Suggested hardware (to confirm together)

- **Skyward camera:** a GoPro HERO (HERO9 or newer supports the Open GoPro API) in a weatherproof housing that lets you run power to it, with a heat or anti-fog strip for New England winters. Run a **USB-C cable for power and control together**. Open GoPro over wired USB answers at `172.2X.1YZ.51:8080`, where the digits come from the camera's serial number. This is steadier than Wi-Fi for an always-on install.
- **360 camera:** a GoPro MAX or an Insta360. If it's a GoPro MAX, the same Open GoPro control applies. If it's an Insta360, it needs its own small adapter (Insta360 cameras speak their own Wi-Fi/OSC protocol). This is the next module to write once you pick the camera.
- **Power:** an outdoor-rated PoE or USB-C power run from the house, so nothing depends on batteries. A PoE splitter to USB-C at the mast works well.
- **Mount:** next to (but not shading) the Tempest, with a clear view of the western sky for sunsets.

## Finishing the workflow

1. Clips are pulled off the camera (Open GoPro `/gopro/media/list`) and saved to `site/media/sky/`.
2. The weekly roundup gets a "Sky of the week" chapter with the best time-lapse.
3. Optionally, a strike-by-strike lightning map is overlaid on the storm time-lapse.
