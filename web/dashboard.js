/* Dashboard laptop TasGuard.

   Alur satu arah: HP B (penjaga di dalam tas) -> laptop (AI) -> HP A (pemantau).
   Dashboard menampilkan keduanya, plus perbandingan AI dengan aturan lama. */

document.addEventListener("DOMContentLoaded", () => {
    setInterval(fetchStatus, 1000);
    fetchStatus();
    initLabelBar();
});

const byId = (id) => document.getElementById(id);

// Label diatur dari dashboard ini atau dari HP A; nilainya selalu dibaca dari server tiap detik.
let labelsBuilt = false;

function initLabelBar() {
    const select = byId("label-select");
    select.addEventListener("change", async () => {
        try {
            await fetch(`/api/label?name=${encodeURIComponent(select.value)}`, { method: "POST" });
        } catch (err) {
            byId("label-current").textContent = "Gagal mengatur label";
        }
    });
}

function syncLabels(labels, label) {
    const select = byId("label-select");
    if (!labelsBuilt && labels && labels.length) {
        labels.forEach((item) => {
            const option = document.createElement("option");
            option.value = item.value;
            option.textContent = `${item.value} - ${item.text}`;
            select.appendChild(option);
        });
        labelsBuilt = true;
    }
    if (document.activeElement !== select) select.value = label || "";
    byId("label-current").textContent = `Label aktif: ${label || "-"}`;
}

async function fetchStatus() {
    try {
        const resp = await fetch("/api/status");
        if (!resp.ok) {
            byId("dash-time").textContent = "Akses ditolak / error server";
            return;
        }
        const data = await resp.json();
        renderDashboard(data);
    } catch (err) {
        byId("dash-time").textContent = "Gagal memuat status";
    }
}

function renderDashboard(data) {
    const dateStr = new Date(data.server_ts * 1000).toLocaleTimeString("id-ID");
    byId("dash-time").textContent = `Terakhir diperbarui: ${dateStr}`;
    byId("dash-total-alarms").textContent = `Total peringatan: ${data.total_alarms}`;

    renderModelInfo(data.ai_model);
    syncLabels(data.labels, data.label);
    renderMonitor(data.devices.A, data.devices.B);
    renderGuard(data.devices.B);
}

/* HP A: pemantau */

function renderMonitor(monitor, guard) {
    const dot = byId("status-dot-A");
    const card = byId("card-A");

    if (!monitor.connected) {
        dot.className = "badge-dot dot-red";
        dot.textContent = "Offline";
        card.className = "dash-card";
        byId("stat-status-A").textContent = "Belum terhubung";
        byId("stat-seen-A").textContent = "-";
    } else {
        dot.className = "badge-dot dot-green";
        dot.textContent = "Online";
        card.className = "dash-card safe";
        byId("stat-status-A").textContent = "Terhubung (menerima peringatan)";
        byId("stat-seen-A").textContent = `${monitor.last_seen_s}s lalu`;
    }

    const last = guard.last_alarm;
    byId("stat-lastalarm-A").textContent = last
        ? `${new Date(last.ts * 1000).toLocaleTimeString("id-ID")} - ${last.reason}`
        : "belum ada";
}

/* HP B: penjaga */

function renderGuard(dev) {
    renderLocation(dev);
    renderAI(dev);

    const dot = byId("status-dot-B");
    const card = byId("card-B");
    const statStatus = byId("stat-status-B");

    byId("stat-alarms-B").textContent = dev.alarms || 0;

    if (!dev.connected) {
        dot.className = "badge-dot dot-red";
        dot.textContent = dev.link === "lost" ? "Terputus" : "Offline";
        card.className = "dash-card";
        statStatus.textContent = dev.status === "danger" ? "TIDAK AMAN (HP terputus)" : "Belum terhubung";
        byId("stat-guard-B").textContent = dev.link === "lost" ? "Terputus saat menjaga" : "-";
        byId("stat-light-B").textContent = "-";
        byId("stat-accel-B").textContent = "-";
        byId("stat-battery-B").textContent = "-";
        byId("stat-seen-B").textContent = "-";
        return;
    }

    if (dev.status === "danger") {
        dot.className = "badge-dot dot-red";
        dot.textContent = "Bahaya!";
        card.className = "dash-card danger";
    } else {
        dot.className = "badge-dot dot-green";
        dot.textContent = "Aman";
        card.className = "dash-card safe";
    }

    statStatus.textContent = dev.status === "danger" ? "TIDAK AMAN (peringatan dikirim ke HP A)" : "Aman";
    byId("stat-guard-B").textContent = guardText(dev);

    if (dev.guarding) {
        const terang = dev.bright ? "Terang" : "Gelap";
        byId("stat-light-B").textContent = `${terang} (${Math.round(dev.light.value || 0)} - ${dev.light.source})`;
        const gerak = dev.shaking ? "Bergerak" : "Diam";
        byId("stat-accel-B").textContent = `${gerak} (dev: ${(dev.deviation || 0).toFixed(2)})`;
    } else {
        byId("stat-light-B").textContent = "-";
        byId("stat-accel-B").textContent = "-";
    }
    byId("stat-battery-B").textContent = dev.battery !== null && dev.battery !== undefined ? `${dev.battery}%` : "-";
    byId("stat-seen-B").textContent = dev.last_seen_s !== null ? `${dev.last_seen_s}s lalu` : "-";
}

function guardText(dev) {
    if (!dev.guarding) return "Belum dimulai";
    if (dev.link === "silent") return "Aktif, tetapi data berhenti";
    return "Aktif";
}

/* Panel AI (hanya untuk HP B) */

const AI_PHASE = {
    idle: "Menunggu data",
    calibrating: "Kalibrasi baseline...",
    ready: "Memantau",
    unsupported_light: "Sumber cahaya tidak didukung (pakai kamera)",
    unavailable: "Model tidak tersedia",
};

function renderModelInfo(info) {
    const el = byId("ai-model-info");
    if (!info) return;
    if (!info.enabled) el.textContent = "Mode AI nonaktif (aturan threshold lama menjadi penentu alarm)";
    else if (!info.available) el.textContent = `Model AI tidak tersedia: ${info.error}`;
    else el.textContent = `Model AI: Random Forest (dilatih pada data sintetis), ambang ${info.threshold}, ${info.consecutive} window berurutan`;
}

function renderAI(dev) {
    const set = (id, text) => { byId(`${id}-B`).textContent = text; };
    const fill = byId("ai-bar-fill-B");
    const thrLine = byId("ai-bar-thr-B");
    const ai = dev.ai;
    if (!dev.connected || !dev.guarding || !ai) {
        set("ai-phase", dev.connected ? "Menunggu penjagaan dimulai di HP B" : "-");
        set("ai-score", "-"); set("ai-thr", "-"); set("ai-consec", "-"); set("ai-base", "-"); set("ai-open", "-"); set("rule-shadow", "-");
        fill.style.width = "0%";
        return;
    }
    let phase = AI_PHASE[ai.phase] || ai.phase;
    if (ai.phase === "calibrating" && ai.calibration_left_s !== null) {
        phase += ` (HP B harus diam ${ai.calibration_left_s} dtk lagi; hitungan mengulang bila bergerak)`;
    }
    if (ai.calibration_fallback) phase += " - kalibrasi darurat (HP B tidak pernah diam)";
    if (ai.phase === "ready" && ai.window_valid === false) phase += " - window tidak valid (data tidak teratur)";
    set("ai-phase", phase);
    const score = ai.score;
    set("ai-score", score === null ? "-" : `${score.toFixed(3)} (${ai.prediction})`);
    set("ai-thr", ai.threshold);
    set("ai-consec", `${ai.consecutive} / ${ai.required}`);
    set("ai-open", ai.light_evidence === null || ai.light_evidence === undefined ? "-"
        : ai.light_evidence ? "ya"
        : (ai.require_light && score !== null && score >= ai.threshold
            ? "tidak (skor tinggi diabaikan: tas belum dibuka)" : "tidak"));
    set("ai-base", ai.baseline_luma === null || ai.baseline_luma === undefined ? "-"
        : `${ai.baseline_luma} luma` + (ai.rebaselines ? ` (diturunkan ${ai.rebaselines}x)` : ""));
    fill.style.width = score === null ? "0%" : `${Math.round(score * 100)}%`;
    fill.classList.toggle("hot", score !== null && score >= ai.threshold);
    thrLine.style.left = `${Math.round(ai.threshold * 100)}%`;
    const rs = dev.rule_shadow || { count: 0, last_ago_s: null };
    set("rule-shadow", rs.count === 0 ? "belum pernah memicu"
        : `akan memicu ${rs.count}x (terakhir ${rs.last_ago_s}s lalu)`);
}

/* Peta lokasi HP B: hanya tampil saat tas berstatus tidak aman */

let guardMap = null;

function renderLocation(dev) {
    const wrap = byId("map-wrap-B");
    const location = dev.location;

    if (dev.status !== "danger" || !location) {
        wrap.classList.add("hidden");
        return;
    }

    wrap.classList.remove("hidden");
    if (!guardMap) guardMap = new MiniMap(byId("map-B"));

    guardMap.setPosition(location.lat, location.lng, location.accuracy);

    byId("map-text-B").textContent =
        `${location.lat.toFixed(5)}, ${location.lng.toFixed(5)} ` +
        `(±${Math.round(location.accuracy || 0)} m, ${location.age_s}s lalu)`;

    const link = byId("map-link-B");
    link.href = `https://www.google.com/maps?q=${location.lat},${location.lng}`;
}
