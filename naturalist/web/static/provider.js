// Direct-from-browser provider: lets the dashboard run with no home box.
// Talks straight to WeatherFlow Tempest (REST + live websocket), NWS Boston,
// NOAA SWPC and iNaturalist, and computes the sky with SunCalc.
// The token comes from window.TEMPEST_TOKEN, the page's token box, or
// localStorage (it is only ever sent to WeatherFlow).
(function () {
  const REST = "https://swd.weatherflow.com/swd/rest";
  const WS = "wss://ws.weatherflow.com/swd/data";
  const UA = { Accept: "application/geo+json" };
  const status = {};
  let station = null, deviceId = null, latest = null;

  const store = {
    get() { try { return localStorage.getItem("tempest_token") || ""; } catch { return ""; } },
    set(v) { try { localStorage.setItem("tempest_token", v); } catch { /* private mode */ } },
  };
  const token = () => (window.TEMPEST_TOKEN || store.get() || "").trim();

  const cToF = c => c == null ? null : Math.round((c * 9 / 5 + 32) * 10) / 10;
  const msToMph = v => v == null ? null : Math.round(v * 2.23694 * 10) / 10;
  const kmToMi = v => v == null ? null : Math.round(v * 0.621371 * 10) / 10;

  async function getJSON(url, headers) {
    const r = await fetch(url, { headers });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  }
  const tw = (path, params = {}) => getJSON(`${REST}${path}?` + new URLSearchParams({ token: token(), ...params }));

  // ---- Tempest -----------------------------------------------------------
  async function tempest() {
    if (!station) {
      const st = (await tw("/stations")).stations?.[0];
      if (!st) throw new Error("This token has no stations");
      station = st;
      deviceId = (st.devices || []).find(d => d.device_type === "ST")?.device_id;
    }
    const fc = await tw("/better_forecast", { station_id: station.station_id, units_temp: "f", units_wind: "mph",
      units_pressure: "inhg", units_precip: "in", units_distance: "mi" });
    const cc = fc.current_conditions || {};
    const weather = {
      ts: cc.time, station: station.name, temp_f: cc.air_temperature, feels_f: cc.feels_like, dew_f: cc.dew_point,
      humidity: cc.relative_humidity, wind_mph: cc.wind_avg, gust_mph: cc.wind_gust, wind_dir: cc.wind_direction,
      pressure_inhg: cc.sea_level_pressure, pressure_trend: cc.pressure_trend, uv: cc.uv, solar: cc.solar_radiation,
      rain_today_in: cc.precip_accum_local_day, rain_rate_in: 0,
      lightning_1h: cc.lightning_strike_count_last_1hr, lightning_3h: cc.lightning_strike_count_last_3hr,
      lightning_last_mi: cc.lightning_strike_last_distance, lightning_last_ts: cc.lightning_strike_last_epoch,
      conditions: cc.conditions, icon: cc.icon,
    };
    const f = fc.forecast || {};
    const forecast = {
      hourly: (f.hourly || []).slice(0, 48).map(h => ({ ts: h.time, temp_f: h.air_temperature, feels_f: h.feels_like,
        pop: h.precip_probability, icon: h.icon, conditions: h.conditions, wind_mph: h.wind_avg, gust_mph: h.wind_gust })),
      daily: (f.daily || []).slice(0, 10).map(d => ({ ts: d.day_start_local, high_f: d.air_temp_high, low_f: d.air_temp_low,
        pop: d.precip_probability, icon: d.icon, conditions: d.conditions, sunrise: d.sunrise, sunset: d.sunset })),
    };
    status.tempest = { ok: true, note: station.name };
    return { weather, forecast };
  }

  // ---- Sky (SunCalc) ------------------------------------------------------
  const SHOWERS = [["Quadrantids", 0, 3, "radiant in Boötes, best before dawn"], ["Lyrids", 3, 22, "radiant near Vega, before dawn"],
    ["Eta Aquariids", 4, 6, "low from New England, pre-dawn"], ["Perseids", 7, 12, "radiant in Perseus, after midnight"],
    ["Orionids", 9, 21, "near Orion's club, after midnight"], ["Leonids", 10, 17, "radiant in Leo, after midnight"],
    ["Geminids", 11, 14, "near Castor, good all night"]];
  const PHASES = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"];
  const sec = d => d && !isNaN(d) ? Math.round(d.getTime() / 1000) : null;

  function sky(lat, lon) {
    const SC = window.SunCalc, now = new Date();
    const sun = SC.getPosition(now, lat, lon);
    const t = SC.getTimes(now, lat, lon), t2 = SC.getTimes(new Date(+now + 86400e3), lat, lon);
    const mi = SC.getMoonIllumination(now), mp = SC.getMoonPosition(now, lat, lon), mt = SC.getMoonTimes(now, lat, lon);
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const showers = SHOWERS.map(([name, m, d, note]) => {
      let peak = new Date(today.getFullYear(), m, d);
      if (peak < today - 2 * 86400e3) peak = new Date(today.getFullYear() + 1, m, d);
      return { name, note, days: Math.round((peak - today) / 86400e3) };
    }).filter(s => s.days <= 21);
    const deg = r => Math.round(r * 180 / Math.PI * 10) / 10;
    return {
      sun_alt: deg(sun.altitude), sun_az: (deg(sun.azimuth) + 180) % 360,
      sunrise: sec(now < t.sunrise ? t.sunrise : t2.sunrise), sunset: sec(t.sunset),
      dark_start: sec(t.night), dark_end: sec(t2.nightEnd),
      moon: { phase: PHASES[Math.round(mi.phase * 8) % 8], illum: Math.round(mi.fraction * 100), alt: Math.round(deg(mp.altitude)),
        rise: sec(mt.rise), set: sec(mt.set) },
      planets: [], showers,
    };
  }

  // ---- Public sources -------------------------------------------------------
  async function nws(lat, lon) {
    const out = { alerts: [], afd: null };
    const a = await getJSON(`https://api.weather.gov/alerts/active?point=${lat.toFixed(4)},${lon.toFixed(4)}`, UA);
    out.alerts = (a.features || []).map(f => f.properties);
    const list = await getJSON("https://api.weather.gov/products/types/AFD/locations/BOX", UA);
    const id = list["@graph"]?.[0]?.["@id"];
    if (id) out.afd = await getJSON(id, UA);
    status.nws = { ok: true, note: `${out.alerts.length} active alerts` };
    return out;
  }

  async function kp() {
    const rows = await getJSON("https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json");
    const [h, ...data] = rows, i = Object.fromEntries(h.map((k, n) => [k, n]));
    const up = data.filter(d => d[i.observed] !== "observed").map(d => ({ time: d[i.time_tag], kp: +d[i.kp] }));
    status.swpc = { ok: true, note: "aurora outlook" };
    return { peak: up.reduce((m, x) => (!m || x.kp > m.kp ? x : m), null) };
  }

  async function inat(lat, lon) {
    const d1 = new Date(Date.now() - 4 * 86400e3).toISOString().slice(0, 10);
    const r = await getJSON("https://api.inaturalist.org/v1/observations?" + new URLSearchParams({
      lat, lng: lon, radius: 5, d1, order_by: "observed_on", per_page: 30, photos: "true" }));
    status.inat = { ok: true, note: `${r.results.length} nearby` };
    return r.results.map(o => {
      const tx = o.taxon || {}, name = tx.preferred_common_name || tx.name || "Something alive";
      const photo = o.photos?.[0]?.url?.replace("square", "medium");
      const ts = Math.round(new Date(o.time_observed_at || o.observed_on).getTime() / 1000);
      const kind = { Aves: "bird", Mammalia: "mammal" }[tx.iconic_taxon_name];
      return { id: `inat-${o.id}`, source: "inat", kind: kind || "community", ts, species: name,
        title: `${name}${kind ? "" : " spotted nearby"}`, subtitle: `iNaturalist · ${o.place_guess || "nearby"}`, media_url: photo, link: o.uri };
    });
  }

  // Forecasters' Key Messages make a lovely "brief" when there's no home box.
  function afdBrief(afd, alerts) {
    if (!afd) return null;
    const text = (afd.productText || "").replace(/\r/g, "");
    const block = (name) => { const m = text.match(new RegExp(`\\.${name}[^\\n]*\\n([\\s\\S]*?)(?=\\n\\.[A-Z]|\\n&&|$)`)); return m ? m[1].trim() : ""; };
    const clean = s => s.replace(/\n(?!\n)/g, " ").replace(/\s+/g, " ").trim();
    const key = block("KEY MESSAGES") || block("SYNOPSIS") || block("WHAT HAS CHANGED");
    const bullets = key.split(/\n\s*(?:\*|-|\d\))\s+/).map(b => clean(b).replace(/^(?:\*|-|\d\))\s*/, "")).filter(Boolean);
    const sections = [
      ...alerts.slice(0, 2).map(a => ({ icon: "alert", title: a.event, body: a.headline || "" })),
      ...bullets.slice(0, 4).map((b, n) => ({ icon: "weather", title: `Key message ${n + 1}`, body: b.length > 420 ? b.slice(0, 417) + "…" : b })),
    ];
    const near = clean(block("NEAR TERM") || block("SHORT TERM") || "");
    return {
      ts: Math.round(new Date(afd.issuanceTime).getTime() / 1000), page: null,
      content: {
        kicker: "From the forecasters at NWS Boston", headline: "What the pros are thinking",
        greeting: near ? near.slice(0, 340) + (near.length > 340 ? "…" : "") : (bullets[0] || "The latest Area Forecast Discussion."),
        sections, narration: [near, ...bullets].join(". ").slice(0, 2400),
        links: [{ label: "Read the full discussion", href: "https://forecast.weather.gov/product.php?site=BOX&issuedby=BOX&product=AFD" }],
      },
    };
  }

  async function settle(name, p) {
    try { return await p; } catch (e) { status[name] = { ok: false, note: String(e.message || e) }; return null; }
  }

  async function load() {
    if (!token()) { askToken(); }
    const t = token() ? await settle("tempest", tempest()) : null;
    const lat = station?.latitude ?? 42.4154, lon = station?.longitude ?? -71.1565;
    const [n, k, nat] = await Promise.all([settle("nws", nws(lat, lon)), settle("swpc", kp()), settle("inat", inat(lat, lon))]);
    status.sky = { ok: true, note: "computed in your browser" };
    const skyNow = sky(lat, lon);
    const feed = [];
    if (t?.weather.lightning_3h) feed.push({ id: "lt", source: "tempest", kind: "lightning", ts: t.weather.lightning_last_ts,
      title: `${t.weather.lightning_3h} lightning strikes in the last 3 hours`, subtitle: `nearest recent ${t.weather.lightning_last_mi} mi` });
    for (const a of n?.alerts || []) feed.push({ id: a.id, source: "nws", kind: "alert", ts: Math.round(new Date(a.sent) / 1000), title: a.event, subtitle: a.headline });
    for (const e of nat || []) if (e.kind !== "community") feed.push(e);
    feed.push(...live_events);
    feed.sort((a, b) => b.ts - a.ts);
    latest = {
      demo: false,
      home: { name: station?.name || "Pine Ridge", town: "Arlington, MA" },
      now: { ...(t || {}), sky: skyNow, kp: k, nws_alerts: n?.alerts },
      status: { ...status }, feed, series: [],
      community: (nat || []).filter(e => e.kind === "community"),
      morning: afdBrief(n?.afd, n?.alerts || []), weekly: null,
    };
    return latest;
  }

  // ---- Live websocket ---------------------------------------------------------
  const live_events = [];
  function live(handle) {
    if (!token() || !deviceId) return;
    let backoff = 3000;
    const connect = () => {
      const ws = new WebSocket(`${WS}?token=${encodeURIComponent(token())}`);
      ws.onopen = () => {
        backoff = 3000;
        for (const type of ["listen_start", "listen_rapid_start"]) ws.send(JSON.stringify({ type, device_id: deviceId, id: type }));
        status.live = { ok: true, note: "live websocket" };
      };
      ws.onmessage = m => {
        const msg = JSON.parse(m.data);
        if (msg.type === "rapid_wind" && msg.ob) {
          handle({ type: "wind", mph: msgToMph(msg.ob[1]), dir: msg.ob[2] });
        } else if (msg.type === "obs_st" && msg.obs?.[0] && latest?.now.weather) {
          const o = msg.obs[0], w = { ...latest.now.weather };
          Object.assign(w, { ts: o[0], wind_mph: msToMph(o[2]), gust_mph: msToMph(o[3]), wind_dir: o[4], temp_f: cToF(o[7]),
            humidity: o[8], uv: o[10], solar: o[11], rain_rate_in: Math.round((o[12] || 0) / 25.4 * 60 * 100) / 100 });
          latest.now.weather = w;
          handle({ type: "now", key: "weather", value: w });
        } else if (msg.type === "evt_strike" && msg.evt) {
          const mi = kmToMi(msg.evt[1]);
          handle({ type: "lightning", distance_mi: mi });
          const ev = { id: `strike-${msg.evt[0]}`, source: "tempest", kind: "lightning", ts: msg.evt[0], title: `Lightning ~${mi} mi away` };
          live_events.push(ev); handle({ type: "event", event: ev });
        } else if (msg.type === "evt_precip" && msg.evt) {
          const ev = { id: `rain-${msg.evt[0]}`, source: "tempest", kind: "rain", ts: msg.evt[0], title: "Rain just started at the station" };
          live_events.push(ev); handle({ type: "event", event: ev });
        }
      };
      ws.onclose = () => { setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 120000); };
    };
    const msgToMph = msToMph;
    connect();
  }

  function askToken() {
    const box = document.getElementById("token-box");
    if (!box) return;
    box.hidden = false;
    box.querySelector("form").onsubmit = ev => {
      ev.preventDefault();
      store.set(box.querySelector("input").value.trim());
      location.reload();
    };
  }

  window.NATURALIST_PROVIDER = { load, live };
})();
