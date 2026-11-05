/* TasGuard (halaman HP): koneksi, autentikasi, pemilihan peran, dan bagian bersama.

   Konsep satu arah: HP B (penjaga, di dalam tas) mengirim sensor ke laptop; AI di
   laptop memutuskan; peringatan dikirim ke HP A (pemantau, dipegang pengguna).
   Logika HP A ada di monitor.js dan logika HP B di guard.js. */

const STORAGE_KEY = "tas_guard_key";
const DEVICE_ID_KEY = "tas_guard_device_id";
const ROLE_KEY = "tas_guard_role";
const ROLE_MONITOR = "A";
const ROLE_GUARD = "B";

const AUTH_ERRORS = {
    key: "Kode akses salah. Periksa kembali.",
    blocked: "Terlalu banyak percobaan gagal. Tunggu 60 detik.",
    timeout: "Server tidak merespons saat autentikasi. Coba lagi.",
    peran: "Pilih peran HP ini terlebih dahulu.",
    peran_terpakai: "Peran ini sedang dipakai HP lain. Putuskan HP tersebut atau pilih peran yang lain.",
};

// Kode penutupan WebSocket dari server -> alasan autentikasi gagal.
const CLOSE_CODE_REASONS = { 4001: "key", 4002: "peran", 4003: "blocked", 4004: "peran_terpakai" };

const state = {
    ws: null,
    key: "",
    deviceId: "",
    chosenRole: "",     // peran yang dipilih pengguna di layar sambung
    role: "",           // peran yang diterima dari server setelah auth_ok
    reconnectAttempt: 0,
    manualClose: false,
    pingTimer: null,
    offlineTimer: null,
    clockOffset: 0,
    wakeLock: null,
    battery: null,
    batteryTimer: null,
};

const el = {};

document.addEventListener("DOMContentLoaded", init);

function init() {
    const ids = [
        "connect-screen", "main-screen", "access-key", "toggle-key", "remember-key",
        "btn-connect", "btn-disconnect", "error-msg", "role-badge", "insecure-warning",
        "role-option-A", "role-option-B",
        "status-card", "status-title", "status-detail", "monitor-view", "guard-view",
        "btn-dismiss-alarm", "alarm-history-list", "guard-badge", "guard-state",
        "label-card", "label-status", "label-buttons", "btn-label-clear",
        "guard-battery", "guard-age", "guard-alarms",
        "map-panel", "map-title", "map-status", "map-canvas", "map-coords", "map-link",
        "btn-toggle-monitor", "btn-bag-mode", "bag-mode-overlay", "bag-overlay-status",
        "val-light", "val-light-src", "val-accel", "val-accel-sub", "btn-retry-camera",
        "monitor-badge", "hidden-video", "hidden-canvas", "bar-laptop", "bar-battery",
    ];
    ids.forEach((id) => {
        el[id] = document.getElementById(id);
    });

    el.canvasCtx = el["hidden-canvas"].getContext("2d");

    getOrCreateDeviceId();
    registerServiceWorker();
    readKeyFromUrlOrStorage();
    restoreRole();
    showInsecureWarning();
    bindEvents();
    monitorBind();
    guardBind();
}

function showInsecureWarning() {
    const host = window.location.hostname;
    const localHost = host === "localhost" || host === "127.0.0.1";
    if (window.isSecureContext || localHost) return;
    el["insecure-warning"].classList.remove("hidden");
}

function getOrCreateDeviceId() {
    let id = localStorage.getItem(DEVICE_ID_KEY);
    if (!id) {
        id = "hp-" + Math.random().toString(36).substring(2, 10);
        localStorage.setItem(DEVICE_ID_KEY, id);
    }
    state.deviceId = id;
}

/* Service worker */

function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;

    navigator.serviceWorker.register("./sw.js").catch((err) => {
        console.error("Service worker gagal:", err);
    });

    navigator.serviceWorker.addEventListener("message", (event) => {
        if (event.data && event.data.type === "ACTION_DISMISS_ALARM" && state.role === ROLE_MONITOR) {
            monitorDismiss();
        }
    });
}

async function notifyServiceWorker(payload) {
    if (!("serviceWorker" in navigator)) return;
    try {
        const registration = await navigator.serviceWorker.ready;
        const worker = registration.active || registration.waiting || registration.installing;
        if (worker) worker.postMessage(payload);
    } catch (err) {
        console.error("Gagal mengirim pesan ke service worker:", err);
    }
}

/* Kode akses dan peran */

function readKeyFromUrlOrStorage() {
    // Cek hash (#key=...)
    const hash = window.location.hash;
    if (hash.startsWith("#key=")) {
        const key = decodeURIComponent(hash.slice(5));
        if (key) {
            el["access-key"].value = key;
            el["access-key"].type = "text";
            localStorage.setItem(STORAGE_KEY, key);
            history.replaceState(null, "", window.location.pathname + window.location.search);
            return;
        }
    }
    // Cadangan: query parameter (?key=...)
    const params = new URLSearchParams(window.location.search);
    const queryKey = params.get("key");
    if (queryKey) {
        el["access-key"].value = queryKey;
        el["access-key"].type = "text";
        localStorage.setItem(STORAGE_KEY, queryKey);
        history.replaceState(null, "", window.location.pathname);
        return;
    }
    // Cadangan: localStorage
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) el["access-key"].value = saved;
}

function selectRole(role) {
    state.chosenRole = role;
    [ROLE_MONITOR, ROLE_GUARD].forEach((r) => {
        el[`role-option-${r}`].classList.toggle("selected", r === role);
    });
    try {
        localStorage.setItem(ROLE_KEY, role);
    } catch (err) {}
}

function restoreRole() {
    let saved = "";
    try {
        saved = localStorage.getItem(ROLE_KEY) || "";
    } catch (err) {}
    if (saved === ROLE_MONITOR || saved === ROLE_GUARD) {
        document.querySelectorAll('input[name="role"]').forEach((input) => {
            input.checked = input.value === saved;
        });
        selectRole(saved);
    }
}

/* Event */

function bindEvents() {
    el["toggle-key"].addEventListener("click", () => {
        const input = el["access-key"];
        const hidden = input.type === "password";
        input.type = hidden ? "text" : "password";
        el["toggle-key"].textContent = hidden ? "Sembunyi" : "Lihat";
    });

    document.querySelectorAll('input[name="role"]').forEach((input) => {
        input.addEventListener("change", () => selectRole(input.value));
    });

    el["btn-connect"].addEventListener("click", () => {
        const key = el["access-key"].value.trim();
        if (!key) {
            showError("Kode akses belum diisi.");
            return;
        }
        if (!state.chosenRole) {
            showError(AUTH_ERRORS.peran);
            return;
        }
        if (el["remember-key"].checked) localStorage.setItem(STORAGE_KEY, key);
        else localStorage.removeItem(STORAGE_KEY);
        state.key = key;
        state.manualClose = false;
        // Izin notifikasi diminta saat klik pengguna; hanya HP pemantau yang menampilkan notifikasi.
        if (state.chosenRole === ROLE_MONITOR) requestNotificationPermission();
        connect();
    });

    el["btn-disconnect"].addEventListener("click", () => {
        state.manualClose = true;
        if (state.role === ROLE_GUARD) guardStop();   // memberi tahu server sebelum koneksi ditutup
        if (state.role === ROLE_MONITOR) monitorLeave();
        releaseWakeLock();
        clearOfflineTimer();
        if (state.ws) state.ws.close();
        state.role = "";
        showConnectScreen();
    });

    document.addEventListener("visibilitychange", async () => {
        if (document.visibilityState === "visible" && wantsWakeLock()) {
            await requestWakeLock();
        }
    });
}

function showError(message) {
    el["error-msg"].textContent = message;
    el["error-msg"].classList.remove("hidden");
}

function hideError() {
    el["error-msg"].textContent = "";
    el["error-msg"].classList.add("hidden");
}

/* WebSocket */

function connect() {
    if (state.ws && state.ws.readyState <= 1) return;

    hideError();
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    let socket;
    try {
        socket = new WebSocket(`${scheme}//${location.host}/ws`);
    } catch (err) {
        showError("Gagal membuka koneksi.");
        return;
    }
    state.ws = socket;

    socket.addEventListener("open", () => {
        socket.send(
            JSON.stringify({
                type: "auth",
                key: state.key,
                device_id: state.deviceId,
                role: state.chosenRole,
            })
        );
    });

    socket.addEventListener("message", (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (err) {
            return;
        }
        handleServerMessage(data);
    });

    socket.addEventListener("close", (event) => {
        clearInterval(state.pingTimer);
        state.pingTimer = null;
        setLaptopConnected(false);

        const reason = CLOSE_CODE_REASONS[event.code];
        if (reason && !state.manualClose) {
            onAuthFail({ reason });
            return;
        }
        if (state.manualClose) return;
        scheduleReconnect();
    });

    socket.addEventListener("error", () => {
        setLaptopConnected(false);
    });
}

function handleServerMessage(data) {
    switch (data.type) {
        case "auth_ok":
            onAuthOk(data);
            break;

        case "auth_fail":
            onAuthFail(data);
            break;

        case "pong":
            {
                const t1 = Date.now() / 1000;
                const rtt = t1 - Number(data.ping_ts);
                const serverTime = Number(data.server_ts);
                state.clockOffset = serverTime - (Number(data.ping_ts) + rtt / 2);
            }
            break;

        case "peer":
            if (state.role === ROLE_MONITOR) monitorOnPeer(data);
            else if (state.role === ROLE_GUARD) guardOnPeer(data);
            break;

        case "alarm":
            // Peringatan hanya dikirim server ke HP pemantau.
            if (state.role === ROLE_MONITOR) monitorOnAlarm(data);
            break;
    }
}

function onAuthOk(data) {
    state.role = data.role;
    el["role-badge"].textContent = `HP ${state.role}`;
    state.reconnectAttempt = 0;
    setLaptopConnected(true);
    showMainScreen();
    startHeartbeat();
    clearOfflineTimer();
    if (state.role === ROLE_MONITOR) monitorOnAuth(data);
    else guardOnAuth(data);
}

function onAuthFail(data) {
    state.manualClose = true;
    state.role = "";
    showError(AUTH_ERRORS[data.reason] || "Autentikasi gagal.");
    showConnectScreen();
}

function send(payload) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify(payload));
    }
}

function startHeartbeat() {
    clearInterval(state.pingTimer);
    state.pingTimer = setInterval(() => {
        if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
        send({ type: "ping", ts: Date.now() / 1000 });
    }, 5000);
}

function scheduleReconnect() {
    if (state.manualClose) return;
    setLaptopConnected(false);
    if (state.role === ROLE_MONITOR) startOfflineTimer();

    const delays = [1000, 2000, 4000, 8000, 10000];
    const delay = delays[Math.min(state.reconnectAttempt, delays.length - 1)];
    state.reconnectAttempt += 1;

    if (el["main-screen"].classList.contains("hidden")) {
        showError(`Menghubungkan ulang dalam ${Math.round(delay / 1000)} dtk...`);
    }

    setTimeout(() => {
        if (!state.manualClose) connect();
    }, delay);
}

// HP pemantau: bila koneksi ke laptop putus lama, peringatan tidak akan diterima.
function startOfflineTimer() {
    if (state.offlineTimer) return;
    state.offlineTimer = setTimeout(() => {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) return;
        notifyServiceWorker({
            type: "SHOW_DISCONNECT_NOTIFICATION",
            body: "Koneksi ke laptop terputus. Peringatan dari HP penjaga tidak akan diterima.",
        });
    }, 15000);
}

function clearOfflineTimer() {
    if (state.offlineTimer) clearTimeout(state.offlineTimer);
    state.offlineTimer = null;
}

/* Layar */

function showMainScreen() {
    el["connect-screen"].classList.add("hidden");
    el["main-screen"].classList.remove("hidden");
    el["monitor-view"].classList.toggle("hidden", state.role !== ROLE_MONITOR);
    el["guard-view"].classList.toggle("hidden", state.role !== ROLE_GUARD);
    renderStatus();
}

function showConnectScreen() {
    el["main-screen"].classList.add("hidden");
    el["connect-screen"].classList.remove("hidden");
    el["role-badge"].textContent = "HP -";
}

function setLaptopConnected(connected) {
    el["bar-laptop"].className = connected ? "badge-dot dot-green" : "badge-dot dot-red";
    el["bar-laptop"].textContent = connected ? "Laptop terhubung" : "Laptop terputus";
    if (!connected) renderStatus();
}

function laptopConnected() {
    return Boolean(state.ws && state.ws.readyState === WebSocket.OPEN);
}

function setStatusCard(kind, title, detail) {
    el["status-card"].className = `status-card status-${kind}`;
    el["status-title"].textContent = title;
    el["status-detail"].textContent = detail || "";
}

function renderStatus() {
    if (state.role === ROLE_MONITOR) monitorRender();
    else if (state.role === ROLE_GUARD) guardRender();
}

/* Layar tetap menyala dan baterai */

function wantsWakeLock() {
    return state.role === ROLE_MONITOR || (state.role === ROLE_GUARD && guard.monitoring);
}

async function requestWakeLock() {
    if (!("wakeLock" in navigator)) return;
    if (state.wakeLock) return;

    try {
        state.wakeLock = await navigator.wakeLock.request("screen");
    } catch (err) {
        state.wakeLock = null;
    }
}

function releaseWakeLock() {
    if (state.wakeLock) {
        state.wakeLock.release().catch(() => {});
        state.wakeLock = null;
    }
}

function initBattery() {
    if (state.batteryTimer) return;
    if (!navigator.getBattery) {
        el["bar-battery"].textContent = "Baterai: tidak tersedia";
        return;
    }
    navigator.getBattery().then((battery) => {
        const update = () => {
            state.battery = Math.round(battery.level * 100);
            el["bar-battery"].textContent = `Baterai: ${state.battery}%`;
        };
        update();
        battery.addEventListener("levelchange", update);
        state.batteryTimer = setInterval(update, 30000);
    });
}

/* Peta lokasi (dipakai HP pemantau untuk menampilkan posisi HP penjaga) */

let miniMap = null;

function showMapPanel(title) {
    el["map-panel"].classList.remove("hidden");
    el["map-title"].textContent = title;
    if (!miniMap) miniMap = new MiniMap(el["map-canvas"]);
}

function applyMapPosition(location, statusText) {
    const accuracy = Math.round(location.accuracy ?? location.acc ?? 0);
    miniMap.setPosition(location.lat, location.lng, accuracy);

    el["map-status"].textContent = `${statusText} (±${accuracy} m)`;
    el["map-coords"].textContent = `${location.lat.toFixed(5)}, ${location.lng.toFixed(5)}`;

    const link = el["map-link"];
    link.href = `https://www.google.com/maps?q=${location.lat},${location.lng}`;
    link.classList.remove("hidden");
}

function hideMapPanel() {
    el["map-panel"].classList.add("hidden");
}
