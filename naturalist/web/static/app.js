// Pine Ridge dashboard: renders /api/state and stays live over Server-Sent Events.
(() => {
  const $ = s => document.querySelector(s);
  const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let state = null, filter = "all";

  // ---- time helpers --------------------------------------------------------
  const clock = ts => ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).replace(" ", "").toLowerCase() : "";
  const hour = ts => new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric" }).replace(" ", "").toLowerCase();
  const ago = ts => {
    const s = Date.now() / 1000 - ts;
    if (s < 90) return "now"; if (s < 3600) return `${Math.round(s / 60)}m`;
    if (s < 86400) return clock(ts); return new Date(ts * 1000).toLocaleDateString([], { weekday: "short" });
  };
  const compass = d => d == null ? "" : ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][Math.round(d / 22.5) % 16];

  // ---- icons (hand-drawn SVG, stroke-based) --------------------------------------
  const I = {
    sun: '<circle cx="12" cy="12" r="4.2" fill="#f5c96b" stroke="none"/><g stroke="#f5c96b">' + [0, 45, 90, 135, 180, 225, 270, 315].map(a => `<path d="M12 2.6v2.2" transform="rotate(${a} 12 12)"/>`).join("") + "</g>",
    moon: '<path d="M15.5 3.5a8.5 8.5 0 1 0 5 13.6A7 7 0 0 1 15.5 3.5z" fill="#e9e4d4" stroke="none"/>',
    cloud: '<path d="M7 18h10.5a4 4 0 0 0 .4-8 5.5 5.5 0 0 0-10.6 1.2A3.4 3.4 0 0 0 7 18z" fill="rgba(240,240,248,.9)" stroke="none"/>',
    rain: '<g stroke="#6fb4ff"><path d="M8 19.5l-1 2.2M12 19.5l-1 2.2M16 19.5l-1 2.2"/></g>',
    snow: '<g fill="#fff" stroke="none"><circle cx="8" cy="20.5" r="1"/><circle cx="12" cy="21.5" r="1"/><circle cx="16" cy="20.5" r="1"/></g>',
    bolt: '<path d="M12.5 15l-2 4h2.5l-1.5 4 4-5.5h-2.6l1.6-2.5z" fill="#f5c96b" stroke="none"/>',
    wind: '<g stroke="rgba(240,240,248,.9)"><path d="M3 9h11a2.5 2.5 0 1 0-2.5-2.5M3 13h15a2.5 2.5 0 1 1-2.5 2.5M3 17h8"/></g>',
    fog: '<g stroke="rgba(240,240,248,.85)"><path d="M4 9h16M3 13h18M5 17h14"/></g>',
    bird: '<path d="M4 14c3 0 5-1.5 6.5-4 1-1.7 2.8-3 5-3 1.6 0 2.7.8 3.5 2l2 .5-2 1.2c0 4.5-3.7 8.3-9 8.3-2.5 0-4.6-1-6-2.5z" fill="#9fe0a6" stroke="none"/><circle cx="16.3" cy="9.3" r=".8" fill="#0b1026" stroke="none"/>',
    bat: '<path d="M12 9.5l1-2 .6 1.6 .4-1.6 1 2c2-1 4.5-1.2 7-.5-1.5 1-2 2.6-2 4.3-1.4-.9-3-1-4.3-.2-.8.6-1.6 1.6-2 2.9-.6-1.3-1.2-2.3-2-2.9-1.3-.8-2.9-.7-4.3.2 0-1.7-.5-3.3-2-4.3 2.5-.7 5-.5 7 .5z" fill="#b69cff" stroke="none"/>',
    paw: '<g fill="#ffb58a" stroke="none"><ellipse cx="12" cy="16" rx="4" ry="3.4"/><circle cx="6.8" cy="11" r="1.7"/><circle cx="10" cy="8" r="1.7"/><circle cx="14" cy="8" r="1.7"/><circle cx="17.2" cy="11" r="1.7"/></g>',
    star: '<path d="M12 3l2.3 5.8 6.2.4-4.8 4 1.6 6-5.3-3.4-5.3 3.4 1.6-6-4.8-4 6.2-.4z" fill="#f5c96b" stroke="none"/>',
    alert: '<path d="M12 3l9.5 17h-19z" fill="#ff7a6b" stroke="none"/><path d="M12 10v4.5M12 17v.4" stroke="#0b1026" stroke-width="2"/>',
    talk: '<path d="M4 5h16v10H9l-5 4z" fill="rgba(240,240,248,.85)" stroke="none"/>',
  };
  const svg = (inner, cls = "icon") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round">${inner}</svg>`;
  function wxIcon(name = "") {
    const n = name || "";
    const night = n.includes("night");
    const base = n.startsWith("clear") ? (night ? I.moon : I.sun) : "";
    const partly = n.includes("partly") ? `<g transform="translate(-3 -3) scale(.75)">${night ? I.moon : I.sun}</g>` : "";
    const cloud = /(cloudy|rain|snow|sleet|thunder)/.test(n) ? I.cloud : "";
    const extra = n.includes("thunder") ? I.bolt : /(rain|sleet)/.test(n) ? I.rain : n.includes("snow") ? I.snow : "";
    if (n.includes("wind")) return svg(I.wind);
    if (n.includes("fog")) return svg(I.fog);
    return svg((base || partly) + cloud + extra || I.cloud);
  }
  const kindIcon = e => svg(({ bird: I.bird, bat: I.bat, mammal: I.paw, lightning: I.bolt, rain: I.cloud + I.rain, alert: I.alert, sky: I.star })[e.kind] || I.talk, "glyph");

  // ---- now ---------------------------------------------------------------------------
  function renderNow() {
    const w = state.now.weather || {}, fc = state.now.forecast || {}, sky = state.now.sky || {};
    $("#place-name").textContent = state.home.name; $("#place-town").textContent = state.home.town;
    document.title = `${w.temp_f != null ? Math.round(w.temp_f) + "° · " : ""}${state.home.name}`;
    $("#temp").textContent = w.temp_f != null ? Math.round(w.temp_f) : "--";
    $("#sentence").textContent = sentence(w, fc.hourly || [], sky);
    const facts = [
      ["Feels", w.feels_f != null ? `${Math.round(w.feels_f)}°` : null],
      ["Wind", w.wind_mph != null ? `<span class="wind-arrow" style="transform:rotate(${(w.wind_dir || 0) + 180}deg)">↑</span> ${Math.round(w.wind_mph)}${w.gust_mph ? `–${Math.round(w.gust_mph)}` : ""} mph ${compass(w.wind_dir)}` : null],
      ["Humidity", w.humidity != null ? `${Math.round(w.humidity)}%` : null],
      ["Pressure", w.pressure_inhg ? `${w.pressure_inhg.toFixed(2)}″ ${({ rising: "↗", falling: "↘", steady: "→" })[w.pressure_trend] || ""}` : null],
      ["Rain today", w.rain_today_in != null ? `${w.rain_today_in.toFixed(2)}″` : null],
      ["UV", w.uv != null && sky.sun_alt > 0 ? Math.round(w.uv) : null],
      ["Lightning 1h", w.lightning_1h ? `${w.lightning_1h} · ${w.lightning_last_mi} mi` : null],
    ].filter(f => f[1] != null);
    $("#facts").innerHTML = facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
    const icon = w.icon || (fc.hourly?.[0]?.icon) || "";
    const cloud = /clear/.test(icon) ? 0.05 : /partly/.test(icon) ? 0.4 : /(cloudy|fog)/.test(icon) ? 0.85 : /(rain|snow|thunder|sleet)/.test(icon) ? 0.95 : 0.3;
    const raining = (w.rain_rate_in || 0) > 0 || /rainy|thunder/.test(icon) && !/possibly/.test(icon);
    window.Sky.set({
      sunAlt: sky.sun_alt ?? 20, sunAz: sky.sun_az ?? 180, cloud, wind: w.wind_mph || 0,
      rain: raining ? Math.max(0.25, Math.min(1, (w.rain_rate_in || 0.1) * 4)) : 0,
      snow: /snow|sleet/.test(icon) && !/possibly/.test(icon) ? 0.6 : 0,
      moonIllum: sky.moon?.illum ?? 50, moonAlt: sky.moon?.alt ?? -1, waxing: /Waxing|First|New/.test(sky.moon?.phase || ""),
    });
    window.Sound.set({ wind: w.wind_mph || 0, gust: w.gust_mph || 0, rain: raining ? (w.rain_rate_in || 0.08) : 0,
      temp: w.temp_f ?? 50, night: (sky.sun_alt ?? 10) < -6,
      clips: (state.feed || []).filter(e => e.audio_url && e.kind === "bird").map(e => e.audio_url).slice(0, 12) });
  }

  // A forecast sentence in the spirit of "Light rain starting in 20 min."
  function sentence(w, hourly, sky) {
    const cond = (w.conditions || hourly[0]?.conditions || "").replace(/\.$/, "");
    const wet = hourly.findIndex(h => (h.pop || 0) >= 50);
    let tail = "";
    if (wet === 0 && hourly[1] && (hourly.slice(0, 6).findIndex(h => (h.pop || 0) < 30) > 0)) {
      tail = ` Drying out around ${hour(hourly[hourly.slice(0, 6).findIndex(h => (h.pop || 0) < 30)].ts)}.`;
    } else if (wet > 0 && wet < 18) {
      tail = ` ${/snow/.test(hourly[wet].icon || "") ? "Snow" : "Rain"} likely by ${hour(hourly[wet].ts)}.`;
    } else if (hourly.length) {
      const hi = Math.max(...hourly.slice(0, 14).map(h => h.temp_f ?? -99)), lo = Math.min(...hourly.slice(0, 14).map(h => h.temp_f ?? 199));
      tail = (sky.sun_alt ?? 0) > 0 ? ` Topping out near ${Math.round(hi)}°.` : ` Down to ${Math.round(lo)}° overnight.`;
    }
    if ((w.gust_mph || 0) >= 25) tail += ` Gusty, to ${Math.round(w.gust_mph)} mph.`;
    return (cond ? cond + "." : "") + tail || "Listening to the sky…";
  }

  // ---- hourly ---------------------------------------------------------------------
  function renderHourly() {
    const hs = (state.now.forecast?.hourly || []).slice(0, 25);
    if (!hs.length) { $("#hourly").innerHTML = '<div class="empty">Forecast appears once the Tempest is connected.</div>'; return; }
    const step = 58, W = step * hs.length, H = 170, top = 46, bot = 112;
    const temps = hs.map(h => h.temp_f);
    const lo = Math.min(...temps) - 2, hi = Math.max(...temps) + 2;
    const y = t => bot - (t - lo) / (hi - lo || 1) * (bot - top);
    const pts = hs.map((h, i) => [i * step + step / 2, y(h.temp_f)]);
    const path = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const area = path + ` L${pts.at(-1)[0]} ${H - 30} L${pts[0][0]} ${H - 30} Z`;
    const sky = state.now.sky || {};
    const marks = [["sunrise", I.sun], ["sunset", I.sun]].map(([k]) => sky[k]).filter(Boolean);
    let out = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
      <defs><linearGradient id="tg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#f5c96b" stop-opacity=".35"/><stop offset="1" stop-color="#f5c96b" stop-opacity="0"/></linearGradient></defs>
      <path d="${area}" fill="url(#tg)"/><path d="${path}" fill="none" stroke="#f5c96b" stroke-width="2" stroke-linejoin="round"/>`;
    hs.forEach((h, i) => {
      const x = i * step + step / 2, p = h.pop || 0;
      if (p > 0) out += `<rect x="${x - 9}" y="${H - 30 - p * 0.32}" width="18" height="${p * 0.32}" rx="3" fill="#6fb4ff" opacity="${0.25 + p / 160}"/>`;
      if (i % 2 === 0 || hs.length < 14) out += `<text class="t" x="${x}" y="${y(h.temp_f) - 10}" text-anchor="middle">${Math.round(h.temp_f)}°</text>`;
      out += `<g transform="translate(${x - 11} 4) scale(.92)">${wxIcon(h.icon).replace('class="icon"', 'width="24" height="24"')}</g>`;
      out += `<text x="${x}" y="${H - 10}" text-anchor="middle">${i === 0 ? "Now" : hour(h.ts)}</text>`;
      if (p >= 20) out += `<text x="${x}" y="${H - 36 - p * 0.32}" text-anchor="middle" style="fill:#6fb4ff;font-size:11px">${p}%</text>`;
    });
    for (const t of marks) {
      const i = (t - hs[0].ts) / 3600;
      if (i > 0 && i < hs.length - 1) {
        const x = i * step + step / 2;
        out += `<line x1="${x}" x2="${x}" y1="${top - 6}" y2="${H - 26}" stroke="rgba(245,201,107,.4)" stroke-dasharray="2 4"/>`;
      }
    }
    $("#hourly").innerHTML = out + "</svg>";
  }

  // ---- daily ---------------------------------------------------------------------
  function renderDaily() {
    const ds = state.now.forecast?.daily || [];
    if (!ds.length) { $("#daily").innerHTML = '<li class="empty">—</li>'; return; }
    const lo = Math.min(...ds.map(d => d.low_f)), hi = Math.max(...ds.map(d => d.high_f));
    const pct = t => ((t - lo) / (hi - lo || 1)) * 100;
    const nowT = state.now.weather?.temp_f;
    const tc = t => t < 32 ? "#8fc1ff" : t < 50 ? "#9be0d6" : t < 65 ? "#c9e08a" : t < 80 ? "#f5c96b" : "#ff8f6b";
    $("#daily").innerHTML = ds.map((d, i) => {
      const name = i === 0 ? "Today" : new Date(d.ts * 1000).toLocaleDateString([], { weekday: "short" });
      return `<li><span>${name}</span>${wxIcon(d.icon)}<span class="pop">${d.pop >= 20 ? d.pop + "%" : ""}</span>
        <span class="lo">${Math.round(d.low_f)}°</span>
        <span class="bar"><i style="left:${pct(d.low_f)}%;right:${100 - pct(d.high_f)}%;background:linear-gradient(90deg,${tc(d.low_f)},${tc(d.high_f)})"></i>${i === 0 && nowT != null ? `<b style="left:${pct(Math.max(d.low_f, Math.min(d.high_f, nowT)))}%"></b>` : ""}</span>
        <span>${Math.round(d.high_f)}°</span></li>`;
    }).join("");
  }

  // ---- sky ---------------------------------------------------------------------
  function renderSky() {
    const s = state.now.sky; if (!s) return;
    const m = s.moon;
    $("#moon-phase").textContent = m.phase;
    $("#moon-sub").textContent = `${m.illum}% lit · rises ${clock(m.rise)} · sets ${clock(m.set)}`;
    $("#dark-window").textContent = s.dark_start ? `Truly dark ${clock(s.dark_start)} – ${clock(s.dark_end)} · sunset ${clock(s.sunset)}` : "";
    drawMoon($("#moon"), m.illum / 100, /Waxing|First/.test(m.phase));
    $("#planets").innerHTML = s.planets.map(p => `<li class="${p.visible_evening ? "up" : ""}"><b>${p.name}</b>
      ${p.visible_evening ? `${p.alt_9pm}° up at 9pm, ${compass(p.az_9pm)}` : p.rise ? `rises ${clock(p.rise)}` : "below horizon"}</li>`).join("");
    const kp = state.now.kp?.peak;
    $("#kp").innerHTML = kp ? `Aurora outlook · peak Kp <b>${kp.kp.toFixed(1)}</b>${kp.kp >= 6 ? " — look north!" : kp.kp >= 5 ? " — possible on camera" : " — quiet"}
      <div class="meter"><b style="left:${Math.min(100, kp.kp / 9 * 100)}%"></b></div>` : "";
    $("#showers").innerHTML = (s.showers || []).map(x => `☄ ${x.name} peak ${x.days <= 0 ? "now" : `in ${x.days} days`} (${x.note})`).join("<br>");
  }

  function drawMoon(cv, k, waxing) {
    const c = cv.getContext("2d"), r = 52, x = 60, y = 60;
    c.clearRect(0, 0, 120, 120);
    const g = c.createRadialGradient(x, y, r * 0.8, x, y, r * 1.15);
    g.addColorStop(0, "rgba(240,235,215,.25)"); g.addColorStop(1, "rgba(240,235,215,0)");
    c.fillStyle = g; c.beginPath(); c.arc(x, y, 60, 0, 7); c.fill();
    c.fillStyle = "#1d2240"; c.beginPath(); c.arc(x, y, r, 0, 7); c.fill();
    c.fillStyle = "#efe9d6"; c.beginPath();
    c.arc(x, y, r, -Math.PI / 2, Math.PI / 2, !waxing);
    c.ellipse(x, y, Math.abs(1 - 2 * k) * r, r, 0, Math.PI / 2, -Math.PI / 2, (k > 0.5) === waxing);
    c.fill();
    c.globalAlpha = 0.08; c.fillStyle = "#000";
    [[-14, -10, 9], [12, 8, 12], [-4, 20, 6], [18, -18, 5]].forEach(([dx, dy, rr]) => { c.beginPath(); c.arc(x + dx, y + dy, rr, 0, 7); c.fill(); });
    c.globalAlpha = 1;
  }

  // ---- feed ---------------------------------------------------------------------
  function renderHeard() {
    const today = state.now.haikubox_today || [];
    $("#heard").innerHTML = today.slice(0, 24).map(s => `<span class="${s.bat ? "bat" : ""}">${esc(s.name)}<em>${s.count}</em></span>`).join("");
  }

  function eventNode(e) {
    const li = $("#tpl-event").content.firstElementChild.cloneNode(true);
    li.classList.add(e.kind);
    const media = li.querySelector(".ev-media");
    media.innerHTML = kindIcon(e) + (e.media_url ? `<img loading="lazy" alt="" src="${esc(e.media_url)}" onerror="this.remove()">` : "");
    if (e.audio_url) {
      const b = el("button", "", '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>');
      b.title = "Play recording"; b.onclick = () => new Audio(e.audio_url).play();
      media.append(b);
    }
    const first = e.data && e.data.first_this_year;
    li.querySelector(".ev-title").innerHTML = esc(e.title) + (first ? '<span class="badge">first of year</span>' : "");
    li.querySelector(".ev-sub").textContent = e.subtitle || ({ haikubox: "heard by the Haikubox", birdbuddy: "at the feeder", arlo: "yard camera", tempest: "Tempest" })[e.source] || "";
    const t = li.querySelector(".ev-time"); t.textContent = ago(e.ts); t.dateTime = new Date(e.ts * 1000).toISOString();
    if (e.link) li.querySelector(".ev-title").innerHTML = `<a href="${esc(e.link)}" target="_blank" rel="noopener">${li.querySelector(".ev-title").innerHTML}</a>`;
    return li;
  }

  function collapse(events) {
    // Collapse repeated Haikubox songs of one species into "Carolina Wren ×14 · since 6:02am"
    const out = [], seen = new Map();
    for (const e of events) {
      if (e.source === "haikubox") {
        const k = e.kind + e.species;
        if (seen.has(k)) { const x = seen.get(k); x._n++; x._first = e.ts; if (!x.audio_url && e.audio_url) x.audio_url = e.audio_url; continue; }
        const c = { ...e, _n: 1, _first: e.ts }; seen.set(k, c); out.push(c);
      } else out.push(e);
    }
    out.forEach(e => { if (e._n > 1) e.subtitle = `${e.kind === "bat" ? "passes" : "songs"} ×${e._n} since ${clock(e._first)}`; });
    return out;
  }

  function renderFeed() {
    const want = e => filter === "all" || (filter === "weather" ? ["lightning", "rain", "alert"].includes(e.kind) : e.kind === filter);
    const items = collapse((state.feed || []).filter(want)).slice(0, 40);
    const ol = $("#feed"); ol.innerHTML = "";
    if (!items.length) ol.innerHTML = '<li class="empty">Quiet so far. The house is listening.</li>';
    items.forEach(e => ol.append(eventNode(e)));
  }

  function renderCommunity() {
    const items = state.community || [];
    $("#community").innerHTML = items.length ? items.slice(0, 14).map(e => `<li><div>
        <div class="src">${esc(({ ebird: "eBird notable", inat: "iNaturalist", rss: e.subtitle, nws: "NWS Boston" })[e.source] || e.source)}</div>
        ${e.link ? `<a href="${esc(e.link)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : esc(e.title)}
        ${e.source !== "rss" && e.subtitle ? `<div class="muted small">${esc(e.subtitle)}</div>` : ""}</div>
        <time class="ev-time">${ago(e.ts)}</time></li>`).join("") : '<li class="empty">Add an eBird key and feeds to hear from the neighborhood.</li>';
  }

  // ---- brief ---------------------------------------------------------------------
  let briefAudio = null;
  function renderBrief() {
    const b = state.morning; if (!b) return;
    const c = b.content;
    $("#brief-card").hidden = false;
    $("#brief-kicker").textContent = (c.kicker || "This morning") + " · " + new Date(b.ts * 1000).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
    $("#brief-headline").textContent = c.headline;
    $("#brief-greeting").textContent = c.greeting;
    const tint = { birds: "var(--bird)", bats: "var(--bat)", critters: "var(--critter)", weather: "var(--rain)", sky: "var(--warm)", alert: "var(--alert)" };
    $("#brief-sections").innerHTML = c.sections.map(s => `<article><h3><i class="tag-dot" style="background:${tint[s.icon] || "var(--ink-3)"}"></i>${esc(s.title)}</h3><p>${esc(s.body)}</p></article>`).join("");
    const links = b.page ? [`<a href="/site/${esc(b.page)}">Open the full brief</a>`] : (c.links || []).map(l => `<a href="${esc(l.href)}" target="_blank" rel="noopener">${esc(l.label)}</a>`);
    if (state.weekly) links.push(`<a href="/site/${esc(state.weekly.page)}">This week's roundup: ${esc(state.weekly.content.title)}</a>`);
    $("#brief-links").innerHTML = links.join("");
    $("#btn-brief").onclick = () => playBrief(b);
  }

  function playBrief(b) {
    const btn = $("#btn-brief");
    const playing = btn.classList.toggle("playing");
    btn.innerHTML = playing ? '<svg viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>' : '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
    const done = () => { btn.classList.remove("playing"); btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>'; };
    if (!playing) { briefAudio?.pause(); speechSynthesis.cancel(); return; }
    if (b.audio && !P) { briefAudio = new Audio("/site/" + b.audio); briefAudio.onended = done; briefAudio.play(); return; }
    const u = new SpeechSynthesisUtterance(b.content.narration);
    const v = speechSynthesis.getVoices().find(v => /en-US/.test(v.lang) && /(Premium|Enhanced|Natural|Samantha|Ava)/.test(v.name));
    if (v) u.voice = v; u.rate = 0.98; u.onend = done; speechSynthesis.speak(u);
  }

  function renderStatus() {
    const names = { tempest: "Tempest", haikubox: "Haikubox", birdbuddy: "Bird Buddy", arlo: "Arlo", ebird: "eBird", inat: "iNaturalist", nws: "NWS", swpc: "Space weather", rss: "Forums", sky: "Sky", skycam: "Sky cam" };
    $("#status").innerHTML = Object.entries(state.status || {}).map(([k, v]) =>
      `<span class="${v.ok ? "ok" : v.ok === false ? "bad" : ""}" title="${esc(v.note)}">${names[k] || k}</span>`).join("");
  }

  function renderAll() {
    $("#demo-banner").hidden = !state.demo;
    renderNow(); renderHourly(); renderDaily(); renderSky(); renderHeard(); renderFeed(); renderCommunity(); renderBrief(); renderStatus();
  }

  // ---- wiring ---------------------------------------------------------------------
  $("#filters").onclick = ev => {
    const b = ev.target.closest(".chip"); if (!b) return;
    filter = b.dataset.k; document.querySelectorAll(".chip").forEach(c => c.classList.toggle("on", c === b)); renderFeed();
  };
  $("#btn-sound").onclick = () => { const on = window.Sound.toggle(); $("#btn-sound").setAttribute("aria-pressed", on); };

  // A provider lets the same dashboard run without the home box (see live.html).
  const P = window.NATURALIST_PROVIDER;

  async function load() {
    state = P ? await P.load() : await (await fetch("/api/state")).json();
    renderAll();
  }

  function live() {
    if (P) return P.live(handle);
    const es = new EventSource("/api/stream");
    es.onmessage = m => handle(JSON.parse(m.data));
  }

  function handle(msg) {
      if (msg.type === "now") { state.now[msg.key] = msg.value; if (msg.key === "weather" || msg.key === "forecast" || msg.key === "sky") { renderNow(); renderHourly(); renderDaily(); renderSky(); } if (msg.key === "haikubox_today") renderHeard(); }
      else if (msg.type === "event") {
        const e = msg.event;
        if (e.kind === "community") { state.community.unshift(e); renderCommunity(); } else { state.feed.unshift(e); renderFeed(); }
      } else if (msg.type === "lightning") { window.Sky.flash(); window.Sound.thunder(msg.distance_mi); }
      else if (msg.type === "wind") { window.Sound.set({ wind: msg.mph }); window.Sky.set({ wind: msg.mph }); }
      else if (msg.type === "brief") load();
  }

  load().then(live);
  setInterval(load, 10 * 60 * 1000);
})();
