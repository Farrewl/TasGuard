/* HP A (pemantau): dipegang pengguna, menerima status dari HP penjaga (HP B) dan
   menampilkan peringatan berupa tulisan, getaran, dan notifikasi.
   HP ini tidak mengirim sensor dan tidak menjalankan AI. */

const HISTORY_KEY = "tasguard_riwayat";
const ALARM_VIBRATION = [500, 250, 500, 250, 500, 250, 500];
const WARN_VIBRATION = [200, 100, 200];

const DISMISS_GRACE_MS = 3000;   // snapshot lama yang masih di jalan diabaikan setelah peringatan ditutup
const REKAM_KEY = "tasguard_rekam";

const monitor = {
    guard: null,          // snapshot terakhir HP B dari server
    dangerShown: false,   // getaran/notifikasi untuk bahaya yang sedang berlangsung sudah dibunyikan
    dismissedAt: 0,       // waktu (ms) peringatan terakhir ditutup
    label: "",            // label rekaman data yang sedang aktif di server
    labelSince: 0,        // waktu (ms) label aktif mulai
    labelButtons: {},     // nilai label -> tombolnya
};

function monitorBind() {
    el["btn-dismiss-alarm"].addEventListener("click", monitorDismiss);
    el["btn-label-clear"].addEventListener("click", () => sendLabel(""));
    el["label-card"].classList.toggle("hidden", !recordingEnabled());
}

/* Bagian "Rekam data" hanya alat uji, disembunyikan dari pengguna biasa. Buka halaman dengan
   ?rekam=1 untuk menampilkannya (diingat di HP ini) dan ?rekam=0 untuk menyembunyikannya lagi. */
function recordingEnabled() {
    try {
        const param = new URLSearchParams(window.location.search).get("rekam");
        if (param === "1") localStorage.setItem(REKAM_KEY, "1");
        if (param === "0") localStorage.removeItem(REKAM_KEY);
        return localStorage.getItem(REKAM_KEY) === "1";
    } catch (err) {
        return false;
    }
}

async function requestNotificationPermission() {
    if (!("Notification" in window) || Notification.permission !== "default") return;
    try {
        await Notification.requestPermission();
    } catch (err) {
        console.error(err);
    }
}

/* Pesan dari server */

function monitorOnAuth(data) {
    monitor.guard = data.peer;
    monitor.dangerShown = false;
    buildLabelButtons(data.labels || []);
    applyLabel(data.label || "");
    loadHistory();
    requestWakeLock();
    initBattery();
    applyGuard(data.peer);
}

function monitorOnPeer(data) {
    applyLabel(data.label || "");
    applyGuard(data.peer);
}

function applyGuard(guardSnapshot) {
    if (guardSnapshot.status === "danger" && Date.now() - monitor.dismissedAt < DISMISS_GRACE_MS) {
        guardSnapshot = { ...guardSnapshot, status: "safe" };   // balasan server belum menyusul dismiss
    }
    monitor.guard = guardSnapshot;

    if (guardSnapshot.status === "danger") {
        // Bahaya yang belum dibunyikan (mis. alarm terlewat saat HP A baru tersambung).
        if (!monitor.dangerShown) {
            const reason = (guardSnapshot.last_alarm && guardSnapshot.last_alarm.reason) || "Aktivitas mencurigakan";
            signalDanger(reason);
        }
    } else if (monitor.dangerShown) {
        monitor.dangerShown = false;
        stopSignals();
    }

    renderGuardCard();
    renderStatus();
}

function monitorOnAlarm(data) {
    const time = new Date().toLocaleTimeString("id-ID");
    const isWarn = data.level === "WARN";

    if (isWarn) {
        vibrate(WARN_VIBRATION);
        notifyServiceWorker({
            type: "SHOW_WARN_NOTIFICATION",
            title: "Peringatan: HP penjaga",
            body: `${time} - ${data.reason}`,
        });
    } else {
        if (monitor.guard) monitor.guard.status = "danger";
        signalDanger(data.reason);
    }

    let latency = null;
    if (data.server_ts) {
        latency = Math.max(0, Math.round((Date.now() / 1000 + state.clockOffset - Number(data.server_ts)) * 1000));
    }
    addHistory({ time, reason: data.reason, level: data.level, latency });
    renderGuardCard();
    renderStatus();
}

/* Bunyi peringatan */

function vibrate(pattern) {
    if ("vibrate" in navigator) navigator.vibrate(pattern);
}

function signalDanger(reason) {
    monitor.dangerShown = true;
    vibrate(ALARM_VIBRATION);
    notifyServiceWorker({
        type: "SHOW_ALARM_NOTIFICATION",
        title: "TAS TIDAK AMAN!",
        body: `${new Date().toLocaleTimeString("id-ID")} - ${reason}`,
    });
}

function stopSignals() {
    vibrate(0);
    notifyServiceWorker({ type: "CLOSE_ALARM_NOTIFICATION" });
}

/* Menutup peringatan: hanya dari HP A */

function monitorDismiss() {
    if (monitor.guard) monitor.guard.status = "safe";
    monitor.dangerShown = false;
    monitor.dismissedAt = Date.now();
    stopSignals();
    send({ type: "dismiss" });
    renderGuardCard();
    renderStatus();
}

function monitorLeave() {
    stopSignals();
    hideMapPanel();
    monitor.guard = null;
    monitor.dangerShown = false;
    monitor.label = "";
}

/* Rekam data: label kondisi untuk menguji AI di HP nyata */

function buildLabelButtons(labels) {
    const box = el["label-buttons"];
    box.innerHTML = "";
    monitor.labelButtons = {};
    labels.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "label-btn";
        button.textContent = item.text;
        button.addEventListener("click", () => sendLabel(item.value));
        box.appendChild(button);
        monitor.labelButtons[item.value] = button;
    });
}

function sendLabel(name) {
    send({ type: "label", name });
    applyLabel(name);   // tampilkan langsung; server mengonfirmasi lewat pesan peer berikutnya
}

function applyLabel(name) {
    if (name !== monitor.label) {
        monitor.label = name;
        monitor.labelSince = Date.now();
    }
    Object.keys(monitor.labelButtons).forEach((value) => {
        monitor.labelButtons[value].classList.toggle("active", value === name);
    });
    if (!name) {
        el["label-status"].textContent = "Label aktif: - (tanpa label)";
        return;
    }
    const seconds = Math.max(0, Math.round((Date.now() - monitor.labelSince) / 1000));
    el["label-status"].textContent = `Label aktif: ${name} (${seconds} dtk)`;
}

/* Tampilan */

function guardPhase(guardSnapshot) {
    const ai = guardSnapshot.ai || {};
    return ai.phase || "idle";
}

function renderGuardCard() {
    const g = monitor.guard || {};
    const badge = el["guard-badge"];

    if (!g.connected) {
        badge.className = "badge-dot dot-red";
        badge.textContent = g.link === "lost" ? "Terputus" : "Offline";
    } else if (g.status === "danger") {
        badge.className = "badge-dot dot-red";
        badge.textContent = "Bahaya!";
    } else {
        badge.className = "badge-dot dot-green";
        badge.textContent = "Online";
    }

    let stateText = "Belum dimulai";
    if (g.guarding) {
        const phase = guardPhase(g);
        stateText = {
            calibrating: "Kalibrasi",
            ready: "Aktif",
            unavailable: "Aktif (AI tidak tersedia)",
            unsupported_light: "Aktif (cahaya tidak didukung)",
        }[phase] || "Memulai";
        if (g.link === "silent") stateText = "Data berhenti";
        if (g.link === "lost") stateText = "Terputus";
    } else if (!g.connected) {
        stateText = "-";
    }
    el["guard-state"].textContent = stateText;
    el["guard-battery"].textContent = g.battery !== null && g.battery !== undefined ? `${g.battery}%` : "-";
    el["guard-age"].textContent = g.last_seen_s !== null && g.last_seen_s !== undefined ? `${g.last_seen_s} dtk lalu` : "-";
    el["guard-alarms"].textContent = g.alarms || 0;
}

function monitorRender() {
    const g = monitor.guard || { connected: false };
    const dismissButton = el["btn-dismiss-alarm"];

    updateGuardMap(g);

    if (!laptopConnected()) {
        dismissButton.classList.add("hidden");
        setStatusCard("warning", "Tidak terhubung ke laptop",
            "Peringatan tidak akan diterima sampai tersambung kembali.");
        return;
    }

    if (g.status === "danger") {
        dismissButton.classList.remove("hidden");
        const alarmTime = g.last_alarm && g.last_alarm.ts
            ? new Date(g.last_alarm.ts * 1000).toLocaleTimeString("id-ID") : "";
        const reason = (g.last_alarm && g.last_alarm.reason) || "Aktivitas mencurigakan";
        setStatusCard("danger", "BAHAYA! Ada aktivitas mencurigakan di dalam tas",
            `${alarmTime ? alarmTime + " - " : ""}${reason}. Periksa tas Anda.`);
        return;
    }

    dismissButton.classList.add("hidden");

    if (!g.connected) {
        if (g.link === "lost") {
            setStatusCard("warning", "HP penjaga terputus",
                "Koneksi HP B hilang saat menjaga. Periksa tas Anda.");
        } else {
            setStatusCard("warning", "Menunggu HP penjaga (HP B)",
                "Sambungkan HP B, pilih peran Penjaga, lalu tekan Mulai Penjagaan.");
        }
        return;
    }

    if (!g.guarding) {
        setStatusCard("warning", "Penjagaan belum dimulai",
            "Tekan Mulai Penjagaan di HP B, lalu masukkan HP B ke dalam tas.");
        return;
    }

    if (g.link === "silent") {
        setStatusCard("warning", "HP penjaga tidak mengirim data", "Periksa HP B di dalam tas.");
        return;
    }

    const phase = guardPhase(g);
    if (phase === "calibrating") {
        const left = g.ai && g.ai.calibration_left_s !== null && g.ai.calibration_left_s !== undefined
            ? ` (${g.ai.calibration_left_s} dtk)` : "";
        setStatusCard("warning", "HP penjaga sedang kalibrasi",
            `Pastikan HP B sudah di dalam tas dan tetap diam${left}. Hitungan mengulang bila HP B bergerak.`);
    } else if (phase === "unavailable") {
        setStatusCard("warning", "Model AI tidak tersedia di laptop", "Penjagaan berjalan tetapi tidak ada deteksi.");
    } else if (phase === "unsupported_light") {
        setStatusCard("warning", "Cahaya HP penjaga tidak didukung AI",
            "HP B memakai sensor cahaya bawaan (lux); AI membutuhkan kamera.");
    } else if (phase === "ready") {
        setStatusCard("safe", "Tas aman", "HP B menjaga isi tas.");
    } else {
        setStatusCard("warning", "Penjagaan dimulai", "Menunggu data dari HP B.");
    }
}

/* Peta: posisi HP penjaga hanya ditampilkan saat tas dalam bahaya */

function updateGuardMap(g) {
    if (g.status !== "danger") {
        hideMapPanel();
        return;
    }

    showMapPanel("Lokasi HP penjaga (tas)");
    const location = g.location;
    if (location && Number.isFinite(location.lat)) {
        const age = location.age_s !== undefined ? `${location.age_s}s lalu` : "realtime";
        applyMapPosition(location, `Update ${age}`);
    } else {
        el["map-status"].textContent = "HP penjaga belum mengirim lokasi";
        el["map-coords"].textContent = "-";
        el["map-link"].classList.add("hidden");
    }
}

/* Riwayat peringatan */

function addHistory(entry) {
    let history = [];
    try {
        history = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    } catch (err) {
        history = [];
    }
    history.unshift(entry);
    try {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 20)));
    } catch (err) {}
    loadHistory();
}

function loadHistory() {
    let history = [];
    try {
        history = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    } catch (err) {
        history = [];
    }

    if (!history.length) {
        el["alarm-history-list"].textContent = "Belum ada riwayat peringatan.";
        return;
    }

    el["alarm-history-list"].innerHTML = "";
    history.forEach((item) => {
        const row = document.createElement("div");
        row.className = "history-row";
        const tag = item.level === "WARN" ? "PERINGATAN" : "BAHAYA";
        const latency = item.latency === null || item.latency === undefined ? "" : ` (${item.latency} ms)`;
        row.textContent = `${item.time} [${tag}] ${item.reason}${latency}`;
        el["alarm-history-list"].appendChild(row);
    });
}
