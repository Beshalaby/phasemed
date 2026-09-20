// Optional browser-side hand tracking for the workspace camera.
// The model is loaded when a PatientModel opens or when the user retries the camera; no video leaves the browser.
(function gestureCameraModule() {
  // Keep the CDN version pinned to a published stable release. A missing
  // version is surfaced by browsers as a vague dynamic-import failure.
  const VISION_VERSION = "0.10.21";
  const VISION_CDN = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${VISION_VERSION}`;
  const VISION_BUNDLE = `${VISION_CDN}/vision_bundle.mjs`;
  const VISION_WASM = `${VISION_CDN}/wasm`;
  const HAND_MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
  const CONNECTIONS = [
    [0, 1], [1, 2], [2, 3], [3, 4],
    [0, 5], [5, 6], [6, 7], [7, 8],
    [0, 9], [9, 10], [10, 11], [11, 12],
    [0, 13], [13, 14], [14, 15], [15, 16],
    [0, 17], [17, 18], [18, 19], [19, 20],
    [5, 9], [9, 13], [13, 17],
  ];

  const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, (a.z || 0) - (b.z || 0));
  const screenPoint = (landmark) => ({ x: 1 - landmark.x, y: landmark.y });

  function classify(landmarks) {
    const wrist = landmarks[0];
    const palm = Math.max(.001, distance(wrist, landmarks[9]));
    const fingerPairs = [[8, 6], [12, 10], [16, 14], [20, 18]];
    const extended = fingerPairs.map(([tip, pip]) => distance(landmarks[tip], wrist) > distance(landmarks[pip], wrist) * 1.04);
    const thumbExtended = distance(landmarks[4], wrist) > distance(landmarks[3], wrist) * 1.02;
    const extendedCount = extended.filter(Boolean).length + (thumbExtended ? 1 : 0);
    const pinchDistance = distance(landmarks[4], landmarks[8]) / palm;
    const pinch = pinchDistance < .59;
    const point = extended[0] && !extended[1] && !extended[2] && !extended[3] && !pinch;
    const peace = extended[0] && extended[1] && !extended[2] && !extended[3] && !pinch;
    const open = extendedCount >= 4 && !pinch;
    const fist = extendedCount <= 1 && !pinch;
    const gesture = pinch ? "pinch" : point ? "point" : peace ? "peace" : open ? "open" : fist ? "fist" : "tracking";
    return {
      landmarks,
      gesture,
      pinch,
      point,
      peace,
      open,
      fist,
      palm,
      screen: screenPoint(landmarks[8]),
      center: screenPoint(landmarks[9]),
      confidence: Math.max(0, Math.min(1, 1 - pinchDistance / 2)),
    };
  }

  class GestureCamera {
    constructor({ video, overlay, onFrame, onStatus }) {
      this.video = video;
      this.overlay = overlay;
      this.onFrame = onFrame || (() => {});
      this.onStatus = onStatus || (() => {});
      this.stream = null;
      this.detector = null;
      this.raf = null;
      this.lastVideoTime = -1;
      this.running = false;
      this.loading = false;
    }

    async createDetector(vision) {
      const fileset = await vision.FilesetResolver.forVisionTasks(VISION_WASM);
      const options = {
        baseOptions: { modelAssetPath: HAND_MODEL, delegate: "GPU" },
        runningMode: "VIDEO",
        numHands: 2,
        minHandDetectionConfidence: .46,
        minHandPresenceConfidence: .42,
        minTrackingConfidence: .42,
      };
      try {
        return await vision.HandLandmarker.createFromOptions(fileset, options);
      } catch (error) {
        // Some browsers expose WebGL but do not support the Tasks GPU delegate.
        return vision.HandLandmarker.createFromOptions(fileset, { ...options, baseOptions: { modelAssetPath: HAND_MODEL, delegate: "CPU" } });
      }
    }

    async start() {
      if (this.running || this.loading) return;
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera access is unavailable in this browser");
      this.loading = true;
      this.onStatus("requesting", "Requesting camera permission…");
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: { ideal: "user" }, width: { ideal: 960 }, height: { ideal: 540 } },
        });
        this.video.srcObject = this.stream;
        await this.video.play();
        this.onStatus("loading", "Loading hand tracking…");
        const vision = await import(VISION_BUNDLE);
        this.detector = await this.createDetector(vision);
        this.running = true;
        this.lastVideoTime = -1;
        this.onStatus("ready", "Camera ready · show one or two hands");
        this.loop();
      } catch (error) {
        this.stop();
        throw error;
      } finally {
        this.loading = false;
      }
    }

    stop() {
      this.running = false;
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = null;
      if (this.detector?.close) this.detector.close();
      this.detector = null;
      this.stream?.getTracks().forEach((track) => track.stop());
      this.stream = null;
      if (this.video) {
        this.video.pause();
        this.video.srcObject = null;
      }
      this.clearOverlay();
      this.onStatus("off", "Camera off");
    }

    clearOverlay() {
      const context = this.overlay?.getContext("2d");
      if (context) context.clearRect(0, 0, this.overlay.width, this.overlay.height);
    }

    draw(hands) {
      if (!this.overlay || !this.video.videoWidth) return;
      const width = this.video.clientWidth || this.video.videoWidth;
      const height = this.video.clientHeight || this.video.videoHeight;
      const ratio = window.devicePixelRatio || 1;
      this.overlay.width = Math.max(1, Math.round(width * ratio));
      this.overlay.height = Math.max(1, Math.round(height * ratio));
      const context = this.overlay.getContext("2d");
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      hands.forEach((hand, index) => {
        const color = index === 0 ? "#a5fff0" : "#f1c77f";
        context.strokeStyle = color;
        context.fillStyle = color;
        context.lineWidth = 1.5;
        CONNECTIONS.forEach(([from, to]) => {
          const a = hand.landmarks[from];
          const b = hand.landmarks[to];
          context.beginPath();
          context.moveTo((1 - a.x) * width, a.y * height);
          context.lineTo((1 - b.x) * width, b.y * height);
          context.stroke();
        });
        hand.landmarks.forEach((landmark) => {
          context.beginPath();
          context.arc((1 - landmark.x) * width, landmark.y * height, 2.3, 0, Math.PI * 2);
          context.fill();
        });
      });
    }

    loop() {
      if (!this.running || !this.detector) return;
      if (this.video.readyState >= 2 && this.video.currentTime !== this.lastVideoTime) {
        this.lastVideoTime = this.video.currentTime;
        const result = this.detector.detectForVideo(this.video, performance.now());
        const hands = (result.landmarks || []).map(classify);
        this.draw(hands);
        this.onFrame({ hands, timestamp: performance.now() });
      }
      this.raf = requestAnimationFrame(() => this.loop());
    }
  }

  window.GestureCamera = GestureCamera;
})();
