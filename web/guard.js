/* HP B (penjaga): ditaruh di dalam tas, membaca cahaya (kamera) dan akselerometer,
   lalu mengirimnya ke laptop. AI di laptop yang memutuskan; peringatan dikirim ke
   HP A. HP ini tetap senyap: tidak ada suara, getaran, atau notifikasi. */

const SENSOR_INTERVAL_MS = 200;     // ±5 Hz

const guard = {
    monitoring: false,       // pengguna sudah menekan Mulai Penjagaan
    sensorTimer: null,
    ambientSensor: null,
    lightSource: "none",
    lightValue: 0,
    accel: { x: 0, y: 0, z: 0, mag: 9.81 },
    maxDelta: 0,
    currentDelta: 0,
    lastMotionTime: 0,
    self: null,              // snapshot diri sendiri dari server (status tas, fase AI)
    monitorPeer: null,       // snapshot HP A (pemantau)
    notice: "",              // pesan galat izin sensor
    locationWatchId: null,
    lastLocationSent: 0,
};

function guardBind() {
    el["btn-toggle-monitor"].addEventListener("click", async () => {
        if (guard.monitoring) guardStop();
        else await guardStart();
    });

    el["btn-retry-camera"].addEventListener("click", async () => {
        await initLightSensor();
        if (guard.lightSource === "camera" || guard.lightSource === "ambient_sensor") {
            el["btn-retry-camera"].classList.add("hidden");
        }
        renderStatus();
    });

    el["btn-bag-mode"].addEventListener("click", () => {
        el["bag-mode-overlay"].classList.remove("hidden");
    });

    let holdTimer = null;
    const startHold = () => {
        holdTimer = setTimeout(() => {
            el["bag-mode-overlay"].classList.add("hidden");
        }, 3000);
    };
    const cancelHold = () => {
        if (holdTimer) clearTimeout(holdTimer);
    };
    ["touchstart", "mousedown"].forEach((name) => el["bag-mode-overlay"].addEventListener(name, startHold));
    ["touchend", "touchcancel", "mouseup", "mouseleave"].forEach((name) =>
        el["bag-mode-overlay"].addEventListener(name, cancelHold)
    );
}

/* Pesan dari server */

function guardOnAuth(data) {
    guard.self = data.self;
    guard.monitorPeer = data.peer;
    initBattery();
    // Sinkronkan status penjagaan dengan server (penting setelah sambung ulang).
    send({ type: "guard", active: guard.monitoring });
    renderStatus();
}

function guardOnPeer(data) {
    guard.self = data.self;
    guard.monitorPeer = data.peer;

    // Saat tas dalam bahaya, kirim lokasi agar HP A bisa melacak tas.
    if (guard.monitoring && data.self && data.self.status === "danger") startLocationTracking();
    else stopLocationTracking();

    renderStatus();
}

/* Memulai dan menghentikan penjagaan */

async function guardStart() {
    if (!laptopConnected()) {
        guard.notice = "Belum tersambung ke laptop.";
        renderStatus();
        return;
    }
    guard.notice = "";

    if (typeof DeviceMotionEvent !== "undefined" && typeof DeviceMotionEvent.requestPermission === "function") {
        try {
            const permission = await DeviceMotionEvent.requestPermission();
            if (permission !== "granted") {
                guard.notice = "Izin akselerometer ditolak.";
                renderStatus();
                return;
            }
        } catch (err) {
            console.error(err);
        }
    }

    if (!("DeviceMotionEvent" in window)) {
        guard.notice = "HP ini tidak menyediakan akselerometer.";
        renderStatus();
        return;
    }

    window.addEventListener("devicemotion", onDeviceMotion);

    // Izin kamera dan lokasi diminta selagi gesture klik masih berlaku.
    await initLightSensor();
    requestLocationPermission();

    await requestWakeLock();
    initBattery();

    guard.monitoring = true;
    el["btn-toggle-monitor"].textContent = "Hentikan Penjagaan";
    el["btn-toggle-monitor"].classList.remove("success");
    el["btn-toggle-monitor"].classList.add("danger");
    guard.sensorTimer = setInterval(sendSensor, SENSOR_INTERVAL_MS);
    send({ type: "guard", active: true });
    renderStatus();
}

function guardStop() {
    // Ubah tombol lebih dulu (sebelum pembersihan, agar pasti berubah)
    el["btn-toggle-monitor"].textContent = "Mulai Penjagaan";
    el["btn-toggle-monitor"].classList.remove("danger");
    el["btn-toggle-monitor"].classList.add("success");

    const wasMonitoring = guard.monitoring;
    guard.monitoring = false;
    window.removeEventListener("devicemotion", onDeviceMotion);
    clearInterval(guard.sensorTimer);
    guard.sensorTimer = null;

    try { stopCamera(); } catch (err) {}
    if (guard.ambientSensor) {
        try { guard.ambientSensor.stop(); } catch (err) {}
        guard.ambientSensor = null;
    }
    guard.lightSource = "none";
    stopLocationTracking();
    releaseWakeLock();
    el["bag-mode-overlay"].classList.add("hidden");

    // Beri tahu server agar HP A tahu penjagaan dihentikan sengaja (bukan terputus).
    if (wasMonitoring) send({ type: "guard", active: false });
    renderStatus();
}

/* Sensor */

function onDeviceMotion(event) {
    const acc = event.accelerationIncludingGravity;
    if (!acc) return;

    const x = acc.x || 0;
    const y = acc.y || 0;
    const z = acc.z || 0;
    const mag = Math.sqrt(x * x + y * y + z * z);
    const delta = Math.abs(mag - 9.81);

    guard.accel = { x, y, z, mag };
    guard.currentDelta = delta;
    guard.lastMotionTime = Date.now();
    if (delta > guard.maxDelta) guard.maxDelta = delta;
}

async function initLightSensor() {
    if ("AmbientLightSensor" in window) {
        try {
            const sensor = new AmbientLightSensor();
            let gotReading = false;
            sensor.addEventListener("reading", () => {
                gotReading = true;
                guard.lightValue = sensor.illuminance;
                guard.lightSource = "ambient_sensor";
                el["val-light-src"].textContent = "Sumber: sensor cahaya";
            });
            sensor.start();

            await new Promise((resolve) => setTimeout(resolve, 2000));
            if (gotReading) {
                guard.ambientSensor = sensor;
                el["btn-retry-camera"].classList.add("hidden");
                return;
            }
            sensor.stop();
        } catch (err) {}
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: "environment" }, width: { ideal: 32 }, height: { ideal: 32 } },
            audio: false,
        });
        el["hidden-video"].srcObject = stream;
        await el["hidden-video"].play();
        guard.lightSource = "camera";
        // Tampilkan kamera yang benar-benar dipakai agar penempatan HP di tas bisa dipastikan.
        let facing = "";
        try {
            facing = stream.getVideoTracks()[0].getSettings().facingMode || "";
        } catch (err) {}
        const cameraName = facing === "environment" ? " belakang" : facing === "user" ? " depan" : "";
        el["val-light-src"].textContent = `Sumber: kamera${cameraName}`;
        el["btn-retry-camera"].classList.add("hidden");
    } catch (err) {
        guard.lightSource = "unavailable";
        el["val-light-src"].textContent = `Sumber: tidak tersedia (${err.name || "gagal"})`;
        el["btn-retry-camera"].classList.remove("hidden");
    }
}

function stopCamera() {
    const video = el["hidden-video"];
    if (video.srcObject) {
        video.srcObject.getTracks().forEach((track) => track.stop());
        video.srcObject = null;
    }
}

function measureCameraLuma() {
    const video = el["hidden-video"];
    if (guard.lightSource !== "camera" || video.paused || video.ended) return 0;
    try {
        el.canvasCtx.drawImage(video, 0, 0, 16, 16);
        const data = el.canvasCtx.getImageData(0, 0, 16, 16).data;
        let total = 0;
        for (let i = 0; i < data.length; i += 4) {
            total += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        }
        return Math.round(total / (data.length / 4));
    } catch (err) {
        return 0;
    }
}

function sendSensor() {
    if (!guard.monitoring) return;

    if (guard.lightSource === "camera") {
        guard.lightValue = measureCameraLuma();
    }

    const moving = guard.maxDelta > 2.5;

    // Reset deviasi ke 0 jika tidak ada gerakan baru > 200 ms
    if (Date.now() - (guard.lastMotionTime || 0) > 200) {
        guard.maxDelta = 0;
        guard.currentDelta = 0;
    }

    el["val-light"].textContent = Math.round(guard.lightValue);
    el["val-accel"].textContent = moving ? "Bergerak" : "Diam";
    el["val-accel-sub"].textContent = `Deviasi: ${guard.maxDelta.toFixed(2)}`;

    send({
        type: "sensor",
        ts: Date.now() / 1000,
        light: { source: guard.lightSource, value: guard.lightValue },
        accel: { x: guard.accel.x, y: guard.accel.y, z: guard.accel.z },
        accel_mag: guard.accel.mag,
        battery: state.battery,
    });

    // Reset maxDelta setelah kirim (untuk jendela berikutnya)
    guard.maxDelta = 0;
}

/* Lokasi: hanya dikirim saat tas dalam bahaya */

function requestLocationPermission() {
    if (!navigator.geolocation) return;
    // Satu kali saat mulai: memicu popup izin agar nanti bisa melacak tanpa gangguan.
    navigator.geolocation.getCurrentPosition(() => {}, () => {}, {
        enableHighAccuracy: false,
        timeout: 15000,
        maximumAge: 60000,
    });
}

function startLocationTracking() {
    if (guard.locationWatchId !== null) return;
    if (!navigator.geolocation) return;

    guard.locationWatchId = navigator.geolocation.watchPosition(handlePosition, () => {}, {
        enableHighAccuracy: true,
        maximumAge: 1000,
        timeout: 20000,
    });
}

function stopLocationTracking() {
    if (guard.locationWatchId === null) return;
    navigator.geolocation.clearWatch(guard.locationWatchId);
    guard.locationWatchId = null;
}

function handlePosition(position) {
    const coords = position.coords;
    const now = Date.now();

    // Kirim ke laptop maksimal 1 kali per detik
    if (now - guard.lastLocationSent < 1000) return;
    guard.lastLocationSent = now;
    send({
        type: "location",
        lat: coords.latitude,
        lng: coords.longitude,
        acc: coords.accuracy || 0,
        ts: now / 1000,
    });
}

/* Tampilan */

function guardRender() {
    const monitorBadge = el["monitor-badge"];
    const monitorOnline = Boolean(guard.monitorPeer && guard.monitorPeer.connected);
    monitorBadge.className = monitorOnline ? "badge-dot dot-green" : "badge-dot dot-red";
    monitorBadge.textContent = monitorOnline ? "Online" : "Offline";

    el["btn-bag-mode"].classList.toggle("hidden", !guard.monitoring);

    if (!laptopConnected()) {
        setStatusCard("warning", "Tidak terhubung ke laptop",
            guard.monitoring ? "Penjagaan berjalan; mencoba menyambung kembali..." : "Mencoba menyambung kembali...");
        return;
    }

    if (!guard.monitoring) {
        setStatusCard("warning", "Penjagaan nonaktif",
            guard.notice || "Tekan Mulai Penjagaan, lalu masukkan HP ini ke dalam tas.");
        return;
    }

    if (guard.lightSource === "unavailable") {
        setStatusCard("warning", "Kamera tidak tersedia",
            "Izinkan akses kamera lalu tekan Izinkan kamera. Tanpa kamera AI tidak dapat bekerja.");
        return;
    }

    const self = guard.self || {};
    const phase = self.ai && self.ai.phase;

    if (self.status === "danger") {
        setStatusCard("danger", "Aktivitas mencurigakan terdeteksi",
            monitorOnline ? "Peringatan sudah dikirim ke HP A. HP ini tetap senyap."
                          : "HP A belum terhubung sehingga belum menerima peringatan.");
        return;
    }

    if (phase === "calibrating") {
        const left = self.ai.calibration_left_s !== null && self.ai.calibration_left_s !== undefined
            ? ` (${self.ai.calibration_left_s} dtk)` : "";
        setStatusCard("warning", "Kalibrasi...",
            `Masukkan HP ke dalam tas dan jaga tetap diam${left}. Hitungan mengulang bila HP bergerak.`);
    } else if (phase === "unsupported_light") {
        setStatusCard("warning", "Sumber cahaya tidak didukung AI",
            "Aplikasi memakai sensor cahaya bawaan (lux). Nonaktifkan Generic Sensor Extra Classes di chrome://flags, lalu muat ulang.");
    } else if (phase === "unavailable") {
        setStatusCard("warning", "Model AI tidak tersedia di laptop", "Penjagaan berjalan tetapi tidak ada deteksi.");
    } else if (phase === "ready") {
        setStatusCard("safe", "Penjagaan aktif",
            monitorOnline ? "Tas dipantau. Peringatan akan dikirim ke HP A."
                          : "Tas dipantau, tetapi HP A belum terhubung sehingga peringatan belum bisa diterima.");
    } else {
        setStatusCard("warning", "Memulai penjagaan...", "Menunggu data sensor diproses di laptop.");
    }
}
