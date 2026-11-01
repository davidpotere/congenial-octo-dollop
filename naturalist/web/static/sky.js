// The living backdrop: the real sky over the house, drawn from the station's data.
// Sun altitude sets the light; Tempest conditions set clouds, rain, snow; wind
// drives the clouds; lightning strikes flash; at night, stars and the true moon phase.
(function () {
  const cv = document.getElementById("sky");
  const ctx = cv.getContext("2d");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let W = 0, H = 0, dpr = 1;
  const S = { sunAlt: 20, sunAz: 180, cloud: 0.15, rain: 0, snow: 0, wind: 4, moonIllum: 50, moonAlt: -10, waxing: true, flash: 0 };
  let stars = [], clouds = [], drops = [];

  function resize() {
    dpr = Math.min(devicePixelRatio || 1, 2);
    W = innerWidth; H = innerHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    stars = Array.from({ length: Math.round(W * H / 2600) }, () => ({
      x: Math.random() * W, y: Math.random() * H * 0.85, r: Math.random() * 1.2 + 0.2, p: Math.random() * 6.28, s: 0.5 + Math.random() * 1.5 }));
    seedClouds();
  }

  function seedClouds() {
    const n = Math.round(4 + S.cloud * 18);
    clouds = Array.from({ length: n }, (_, i) => ({
      x: Math.random() * W * 1.4 - W * 0.2, y: H * (0.04 + Math.random() * 0.5),
      w: W * (0.18 + Math.random() * 0.35), h: 40 + Math.random() * 90, d: 0.4 + Math.random() * 0.8, i }));
  }

  // Sky colors keyed on sun altitude (degrees). Each stop: [alt, zenith, horizon]
  const STOPS = [
    [-90, "#04060f", "#0b1026"], [-18, "#060a1c", "#121a3d"], [-10, "#0e1438", "#2b2c5e"],
    [-4, "#1f2a63", "#c0617a"], [0, "#2f4a8c", "#f09a6a"], [4, "#3f6db0", "#f7c08a"],
    [12, "#3d7fd6", "#a9cdf0"], [35, "#2f74d0", "#9cc7f0"], [90, "#2a6cc9", "#93c1ee"],
  ];
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const rgb = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  function skyColors(alt) {
    for (let i = 1; i < STOPS.length; i++) {
      if (alt <= STOPS[i][0]) {
        const [a0, z0, h0] = STOPS[i - 1], [a1, z1, h1] = STOPS[i];
        const t = (alt - a0) / (a1 - a0);
        return [mix(hex(z0), hex(z1), t), mix(hex(h0), hex(h1), t)];
      }
    }
    return [hex(STOPS.at(-1)[1]), hex(STOPS.at(-1)[2])];
  }

  function drawMoon(x, y, r, illum, waxing) {
    ctx.save();
    const glow = ctx.createRadialGradient(x, y, r, x, y, r * 5);
    glow.addColorStop(0, `rgba(230,230,210,${0.18 * illum / 100})`);
    glow.addColorStop(1, "rgba(230,230,210,0)");
    ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(x, y, r * 5, 0, 7); ctx.fill();
    ctx.fillStyle = "rgba(30,34,60,.9)"; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
    // lit portion: half disc + terminator ellipse
    const k = illum / 100, side = waxing ? 1 : -1;
    ctx.fillStyle = "#efeadb";
    ctx.beginPath();
    ctx.arc(x, y, r, -Math.PI / 2, Math.PI / 2, side < 0);
    ctx.ellipse(x, y, Math.abs(1 - 2 * k) * r, r, 0, Math.PI / 2, -Math.PI / 2, (k > 0.5) === (side > 0));
    ctx.fill();
    ctx.restore();
  }

  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    const [zen, hor] = skyColors(S.sunAlt);
    const gray = Math.min(0.85, S.cloud * 0.75 + S.rain * 0.25);
    const g = ctx.createLinearGradient(0, 0, 0, H);
    const grayc = S.sunAlt > -6 ? [120, 128, 140] : [22, 24, 34];
    g.addColorStop(0, rgb(mix(zen, grayc, gray)));
    g.addColorStop(0.75, rgb(mix(hor, grayc, gray * 0.9)));
    g.addColorStop(1, rgb(mix(hor, grayc, gray)));
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

    const night = Math.max(0, Math.min(1, (-S.sunAlt - 4) / 10));
    if (night > 0) {
      const vis = night * (1 - S.cloud * 0.9);
      for (const s of stars) {
        const tw = reduce ? 1 : 0.6 + 0.4 * Math.sin(now / 1000 * s.s + s.p);
        ctx.fillStyle = `rgba(255,255,245,${vis * tw * (1 - s.y / H)})`;
        ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, 7); ctx.fill();
      }
    }
    // Sun glow low on the horizon / by day
    if (S.sunAlt > -8) {
      const sx = W * (0.15 + 0.7 * ((S.sunAz - 90) / 180));
      const sy = H * (0.78 - Math.max(-0.1, Math.min(1, S.sunAlt / 50)) * 0.6);
      const sg = ctx.createRadialGradient(sx, sy, 0, sx, sy, W * 0.6);
      const a = (S.sunAlt < 8 ? 0.55 : 0.25) * (1 - S.cloud * 0.8);
      sg.addColorStop(0, `rgba(255,214,150,${a})`); sg.addColorStop(1, "rgba(255,214,150,0)");
      ctx.fillStyle = sg; ctx.fillRect(0, 0, W, H);
    }
    if (S.moonAlt > 0 && S.sunAlt < 2) {
      drawMoon(W * 0.78, H * (0.32 - Math.min(1, S.moonAlt / 60) * 0.18), Math.max(16, Math.min(W, H) * 0.035), S.moonIllum, S.waxing);
    }
    // Clouds: soft layered ellipses, lit by the sky
    const lit = S.sunAlt > 0 ? [250, 248, 244] : S.sunAlt > -8 ? [235, 170, 150] : [70, 76, 104];
    const speed = (6 + S.wind * 3);
    for (const c of clouds) {
      if (!reduce) c.x += speed * c.d * dt;
      if (c.x - c.w > W) { c.x = -c.w * 1.2; c.y = H * (0.04 + Math.random() * 0.5); }
      const a = (0.10 + S.cloud * 0.30) * c.d;
      for (let k = 0; k < 4; k++) {
        const cx = c.x + (k - 1.5) * c.w * 0.22, cy = c.y + Math.sin(k * 1.7 + c.i) * c.h * 0.18;
        const r = c.w * (0.28 + 0.06 * Math.sin(k + c.i));
        const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
        cg.addColorStop(0, rgb(lit, a)); cg.addColorStop(1, rgb(lit, 0));
        ctx.fillStyle = cg; ctx.beginPath(); ctx.ellipse(cx, cy, r, r * 0.45, 0, 0, 7); ctx.fill();
      }
    }
    // Precipitation
    const want = Math.round((S.rain + S.snow) * W / 3);
    while (drops.length < want) drops.push({ x: Math.random() * W, y: Math.random() * H, v: 0.7 + Math.random() * 0.6 });
    drops.length = Math.min(drops.length, want);
    if (drops.length) {
      const snow = S.snow > S.rain;
      ctx.strokeStyle = "rgba(200,220,255,.35)"; ctx.fillStyle = "rgba(255,255,255,.8)"; ctx.lineWidth = 1;
      ctx.beginPath();
      for (const d of drops) {
        if (!reduce) {
          d.y += (snow ? 40 : 700) * d.v * dt; d.x += (S.wind * (snow ? 6 : 10)) * dt + (snow ? Math.sin(now / 700 + d.v * 9) * 0.4 : 0);
        }
        if (d.y > H) { d.y = -10; d.x = Math.random() * W; }
        if (d.x > W) d.x -= W;
        if (snow) { ctx.moveTo(d.x + 1.6, d.y); ctx.arc(d.x, d.y, 1.6 * d.v, 0, 7); }
        else { ctx.moveTo(d.x, d.y); ctx.lineTo(d.x - S.wind * 0.6, d.y - 14 * d.v); }
      }
      snow ? ctx.fill() : ctx.stroke();
    }
    if (S.flash > 0) {
      ctx.fillStyle = `rgba(235,240,255,${S.flash})`; ctx.fillRect(0, 0, W, H);
      S.flash = Math.max(0, S.flash - dt * 2.6);
    }
    requestAnimationFrame(frame);
  }

  window.Sky = {
    set(next) {
      const before = S.cloud;
      Object.assign(S, next);
      if (Math.abs(before - S.cloud) > 0.15) seedClouds();
      document.querySelector('meta[name="theme-color"]').content = rgb(skyColors(S.sunAlt)[0]);
    },
    flash() {
      S.flash = 0.75;
      setTimeout(() => (S.flash = 0.5), 140);
    },
    state: S,
  };
  addEventListener("resize", resize);
  resize();
  requestAnimationFrame(frame);
})();
