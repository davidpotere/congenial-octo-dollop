"""`python -m naturalist demo`: the dashboard with clearly labeled SAMPLE data.

Lets you see the design (and tweak it) before any account is connected. The
sky, moon and planets are computed for real; everything else is invented
and the page shows a "Sample data" banner.
"""
from __future__ import annotations

import math
import random
import tempfile
import time
from pathlib import Path

from .app import Naturalist, create_app
from .config import load_config
from .sources.sky import sky_report

YARD = [("Carolina Wren", 14), ("Blue Jay", 11), ("Dark-eyed Junco", 9), ("White-throated Sparrow", 8),
        ("Black-capped Chickadee", 8), ("Northern Cardinal", 6), ("Tufted Titmouse", 5),
        ("Red-bellied Woodpecker", 3), ("Golden-crowned Kinglet", 2), ("Big Brown Bat", 4)]


def demo_app():
    cfg = load_config()
    cfg.data_dir = Path(tempfile.mkdtemp(prefix="naturalist-demo-"))
    nat = Naturalist(cfg)
    seed(nat)
    return create_app(cfg, nat=nat, live=False)


def seed(nat: Naturalist) -> None:
    hub, now = nat.hub, int(time.time())
    rnd = random.Random(7)
    sky = sky_report(nat.cfg.lat, nat.cfg.lon, nat.cfg.tz)
    hub.now["sky"] = sky
    hour0 = now - now % 3600
    hourly = []
    for i in range(48):
        t = hour0 + i * 3600
        lh = (time.localtime(t).tm_hour)
        temp = 47 + 8 * math.sin((lh - 9) / 24 * 2 * math.pi) + (-(i - 20) * 0.2 if i > 20 else 0)
        pop = max(0, min(90, int((i - 14) * 9))) if 14 <= i <= 30 else (40 if 30 < i < 34 else 0)
        icon = ("rainy" if pop >= 60 else "possibly-rainy-" if pop >= 30 else "partly-cloudy-" if i > 10 else "clear-")
        if not icon.endswith("y"):
            icon += "night" if lh < 6 or lh >= 17 else "day"
        hourly.append({"ts": t, "temp_f": round(temp), "pop": pop, "icon": icon, "wind_mph": 6 + i % 5,
                       "conditions": "Rain Likely" if pop >= 60 else "Partly Cloudy" if i > 10 else "Clear"})
    daily = []
    day0 = now - (now - time.timezone) % 86400
    for d in range(10):
        hi, lo = 54 - d * 0.8 + rnd.randint(-4, 4), 37 - d * 0.6 + rnd.randint(-3, 3)
        pop = [5, 80, 40, 10, 0, 20, 60, 15, 5, 30][d]
        icon = "rainy" if pop >= 60 else "possibly-rainy-day" if pop >= 30 else "partly-cloudy-day" if pop >= 10 else "clear-day"
        daily.append({"ts": day0 + d * 86400, "high_f": hi, "low_f": lo, "pop": pop, "icon": icon})
    night = sky["sun_alt"] < -6
    hub.now["weather"] = {"ts": now, "temp_f": hourly[0]["temp_f"], "feels_f": hourly[0]["temp_f"] - 3, "humidity": 64,
                          "wind_mph": 7, "gust_mph": 15, "wind_dir": 305, "pressure_inhg": 30.12,
                          "pressure_trend": "falling", "uv": 2, "rain_today_in": 0.0, "lightning_1h": 0,
                          "conditions": "Clear" if night else "Partly Cloudy",
                          "icon": "clear-night" if night else "partly-cloudy-day"}
    hub.now["forecast"] = {"hourly": hourly, "daily": daily}
    hub.now["kp"] = {"peak": {"time": "", "kp": 4.3}}
    hub.now["haikubox_today"] = [{"name": n, "count": c, "bat": "Bat" in n} for n, c in YARD]
    ev = []
    for i, (name, c) in enumerate(YARD):
        for k in range(min(c, 3)):
            ev.append(dict(id=f"demo-hb-{i}-{k}", source="haikubox", kind="bat" if "Bat" in name else "bird",
                           ts=now - rnd.randint(300, 20 * 3600), species=name, title=name,
                           data={"first_this_year": name == "Golden-crowned Kinglet" and k == 0}))
    ev += [
        dict(id="demo-bb-1", source="birdbuddy", kind="bird", ts=now - 2400, species="Dark-eyed Junco",
             title="Dark-eyed Junco", subtitle="at the feeder · first of the season"),
        dict(id="demo-arlo-1", source="arlo", kind="mammal", ts=now - 6 * 3600, species="Red fox",
             title="Red fox on the Backyard cam", subtitle="Trotting the fence line at 3:12am, nose down."),
        dict(id="demo-arlo-2", source="arlo", kind="mammal", ts=now - 9 * 3600, species="Eastern cottontail",
             title="Eastern cottontail on the Side yard cam", subtitle="Browsing clover by the shed."),
        dict(id="demo-c1", source="ebird", kind="community", ts=now - 5 * 3600, title="Snow Bunting reported at Great Meadows",
             subtitle="eBird notable · 3 seen", data={"topic": "birding"}),
        dict(id="demo-c2", source="inat", kind="community", ts=now - 8 * 3600, title="Eastern Gray Squirrel spotted nearby",
             subtitle="iNaturalist · Menotomy Rocks Park"),
        dict(id="demo-c3", source="rss", kind="community", ts=now - 3 * 3600,
             title="Nor'easter talk for next week? Models split", subtitle="r/boston weather talk"),
        dict(id="demo-c4", source="rss", kind="community", ts=now - 20 * 3600,
             title="Leonids prep: dark sites within an hour of Boston", subtitle="r/astrophotography (Boston)"),
    ]
    for e in ev:
        hub.store.add_event(**e)
    for t in range(now - 86400, now, 900):
        lh = time.localtime(t).tm_hour
        hub.store.add_weather(t, {"temp_f": round(45 + 7 * math.sin((lh - 9) / 24 * 2 * math.pi), 1)})
    for k in ("tempest", "haikubox", "birdbuddy", "arlo", "ebird", "inat", "nws", "rss"):
        hub.status[k] = {"ok": None, "ts": now, "note": "sample data"}
    hub.status["sky"] = {"ok": True, "ts": now, "note": "computed live"}
    hub.store.add_brief("morning-demo", "morning", now - 3600, {
        "headline": "Sample brief: a junco morning before the rain",
        "greeting": "This is sample text so you can see the layout. Your real brief is written each morning "
                    "from your Tempest, Haikubox, Bird Buddy and Arlo, plus local birders and forecasters.",
        "sections": [
            {"icon": "weather", "title": "Dry until mid-afternoon", "body": "Example section: the hour the rain arrives, the high, the wind."},
            {"icon": "birds", "title": "Overnight and at dawn", "body": "Example section: who sang first, who visited the feeder, any first-of-year."},
            {"icon": "bats", "title": "Bats", "body": "Example section: bat passes after dusk and the species."},
            {"icon": "critters", "title": "On camera", "body": "Example section: the fox at 3am, the rabbit by the shed."},
            {"icon": "sky", "title": "Tonight", "body": "Example section: moon, planets, meteor showers, aurora odds."},
        ],
        "narration": "This is a sample narration. Each morning, the house reads you its notes.",
        "push": "Sample", "hero_event_id": ""}, None, None)
