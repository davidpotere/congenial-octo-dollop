"""Bundle the dashboard into one self-contained HTML file that runs in any browser
with no home box: it talks to Tempest, NWS, NOAA SWPC and iNaturalist directly.

  python -m naturalist.web.build_live out.html            # asks for the token on first open
  python -m naturalist.web.build_live out.html --token T  # token baked in: keep that file private
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

STATIC = Path(__file__).parent / "static"
SUNCALC = "https://cdnjs.cloudflare.com/ajax/libs/suncalc/1.9.0/suncalc.min.js"

TOKEN_BOX = """
  <div id="token-box" class="card token-box" hidden>
    <form>
      <label for="tempest-token">Tempest access token</label>
      <p class="muted small">Create one at tempestwx.com → Settings → Data Authorizations. It stays in this browser and is only sent to WeatherFlow.</p>
      <div class="token-row"><input id="tempest-token" autocomplete="off" spellcheck="false" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"><button class="pill" type="submit">Connect</button></div>
    </form>
  </div>
"""

EXTRA_CSS = """
.token-box { margin: 0 auto 16px; max-width: 1080px; position: relative; z-index: 2; }
.token-box label { font-weight: 600; }
.token-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.token-row input { flex: 1 1 240px; min-width: 0; padding: 10px 14px; border-radius: 12px; border: 1px solid var(--line);
  background: rgba(0,0,0,.25); color: var(--ink); font: 14px ui-monospace, monospace; }
"""


def build(out: Path, token: str = "") -> None:
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "style.css").read_text() + EXTRA_CSS
    html = html.replace('<link rel="stylesheet" href="/static/style.css">', f"<style>\n{css}\n</style>")
    html = html.replace("<main>", "<main>" + TOKEN_BOX, 1)
    html = html.replace('<a href="/site/">Archive of briefs and weekly roundups</a>',
                        'Live from your Tempest, NWS Boston, NOAA SWPC and iNaturalist')
    scripts = "".join(f"<script>\n{(STATIC / n).read_text()}\n</script>\n" for n in ("sky.js", "sound.js"))
    scripts += f'<script src="{SUNCALC}"></script>\n'
    scripts += f"<script>window.TEMPEST_TOKEN = {json.dumps(token)};</script>\n"
    scripts += f"<script>\n{(STATIC / 'provider.js').read_text()}\n</script>\n"
    scripts += f"<script>\n{(STATIC / 'app.js').read_text()}\n</script>\n"
    html = re.sub(r'\s*<script src="/static/sky.js"></script>\s*<script src="/static/sound.js"></script>\s*'
                  r'<script src="/static/app.js"></script>', lambda _: "\n" + scripts, html)
    out.write_text(html)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("out", type=Path)
    p.add_argument("--token", default="")
    a = p.parse_args()
    build(a.out, a.token)
    print(f"wrote {a.out}")
