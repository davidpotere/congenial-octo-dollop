// "Listen to outside": a soundscape synthesized from what the station is measuring.
//  - Wind: brown noise through a moving low-pass, loudness from wind speed and gusts.
//  - Rain: filtered noise plus droplet ticks, from rain rate.
//  - Thunder: on each real lightning strike, delayed by the strike's distance
//    (sound travels about a mile every 4.7 seconds), deeper and softer when far.
//  - Crickets on warm nights, chirping at the rate Dolbear's law predicts for
//    the station's temperature (snowy tree crickets really do this).
//  - The real recorded calls from today's Haikubox detections, played now and then.
(function () {
  let ac = null, master, wind, windGain, windFilter, rain, rainGain, timers = [];
  const S = { wind: 0, gust: 0, rain: 0, temp: 50, night: false, clips: [] };

  function noise(kind) {
    const len = ac.sampleRate * 4, buf = ac.createBuffer(1, len, ac.sampleRate), d = buf.getChannelData(0);
    let last = 0;
    for (let i = 0; i < len; i++) {
      const w = Math.random() * 2 - 1;
      if (kind === "brown") { last = (last + 0.02 * w) / 1.02; d[i] = last * 3.5; } else d[i] = w;
    }
    const src = ac.createBufferSource(); src.buffer = buf; src.loop = true; src.start();
    return src;
  }

  function start() {
    ac = new (window.AudioContext || window.webkitAudioContext)();
    master = ac.createGain(); master.gain.value = 0; master.connect(ac.destination);
    master.gain.linearRampToValueAtTime(0.9, ac.currentTime + 2);
    wind = noise("brown"); windFilter = ac.createBiquadFilter(); windFilter.type = "lowpass";
    windGain = ac.createGain(); wind.connect(windFilter).connect(windGain).connect(master);
    rain = noise("white"); const bp = ac.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 2400; bp.Q.value = 0.6;
    rainGain = ac.createGain(); rainGain.gain.value = 0; rain.connect(bp).connect(rainGain).connect(master);
    apply(); loopGusts(); loopDrops(); loopCrickets(); loopBirds();
  }

  function stop() {
    timers.forEach(clearTimeout); timers = [];
    if (ac) { master.gain.linearRampToValueAtTime(0, ac.currentTime + 0.8); const a = ac; setTimeout(() => a.close(), 1000); }
    ac = null;
  }

  function apply() {
    if (!ac) return;
    const t = ac.currentTime;
    const w = Math.min(1, S.wind / 25);
    windGain.gain.setTargetAtTime(0.05 + w * 0.5, t, 1.5);
    windFilter.frequency.setTargetAtTime(220 + w * 900, t, 1.5);
    rainGain.gain.setTargetAtTime(Math.min(0.35, S.rain * 1.2), t, 2);
  }

  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }

  function loopGusts() {
    if (!ac) return;
    const t = ac.currentTime, g = Math.min(1, (S.gust || S.wind) / 30);
    windFilter.frequency.setTargetAtTime(300 + g * 1400, t, 0.8);
    windGain.gain.setTargetAtTime(0.08 + g * 0.6, t, 0.9);
    later(() => { apply(); later(loopGusts, 2000 + Math.random() * 6000); }, 1500 + Math.random() * 2000);
  }

  function loopDrops() {
    if (!ac) return;
    if (S.rain > 0.01) {
      const o = ac.createOscillator(), g = ac.createGain(), t = ac.currentTime;
      o.frequency.value = 1800 + Math.random() * 2600; g.gain.setValueAtTime(0.03, t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
      o.connect(g).connect(master); o.start(t); o.stop(t + 0.06);
    }
    later(loopDrops, S.rain > 0.01 ? 30 + Math.random() * 120 / Math.min(4, S.rain * 10) : 1000);
  }

  // Dolbear (1897): chirps per minute ≈ 4·(T°F − 50) + 40, for the snowy tree cricket.
  function loopCrickets() {
    if (!ac) return;
    const warm = S.night && S.temp >= 55;
    const perMin = Math.max(20, 4 * (S.temp - 50) + 40);
    if (warm) {
      const t = ac.currentTime;
      for (let p = 0; p < 3; p++) {
        const o = ac.createOscillator(), g = ac.createGain(), st = t + p * 0.028;
        o.frequency.value = 4400; g.gain.setValueAtTime(0, st);
        g.gain.linearRampToValueAtTime(0.025, st + 0.006); g.gain.linearRampToValueAtTime(0, st + 0.022);
        o.connect(g).connect(master); o.start(st); o.stop(st + 0.03);
      }
    }
    later(loopCrickets, warm ? 60000 / perMin : 5000);
  }

  function loopBirds() {
    if (!ac) return;
    const clip = S.clips[Math.floor(Math.random() * S.clips.length)];
    if (clip && !S.night) {
      const a = new Audio(clip); a.crossOrigin = "anonymous"; a.volume = 0.5;
      a.play().catch(() => {});
    }
    later(loopBirds, 25000 + Math.random() * 50000);
  }

  function thunder(distanceMi) {
    if (!ac) return;
    const d = distanceMi ?? 5, delay = Math.min(60, d * 4.7) * 1000;
    later(() => {
      if (!ac) return;
      const src = noise("brown"), f = ac.createBiquadFilter(), g = ac.createGain(), t = ac.currentTime;
      f.type = "lowpass"; f.frequency.value = Math.max(90, 500 - d * 30);
      const peak = Math.max(0.15, 1.2 - d * 0.08);
      g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(peak, t + 0.15 + d * 0.03);
      g.gain.exponentialRampToValueAtTime(0.001, t + 3 + d * 0.5);
      src.connect(f).connect(g).connect(master); src.stop(t + 4 + d * 0.5);
    }, delay);
  }

  window.Sound = {
    toggle() { ac ? stop() : start(); return !!ac; },
    get on() { return !!ac; },
    set(next) { Object.assign(S, next); apply(); },
    thunder,
  };
})();
