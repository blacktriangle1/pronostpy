const STORAGE_KEY = "radarVivo.voice";

const SPANISH_PREFERENCE = ["es-mx", "es-us", "es-419", "es-es", "es-ar", "es-co", "es-cl"];

export class Voice {
  constructor() {
    this.supported =
      typeof window !== "undefined" &&
      "speechSynthesis" in window &&
      "SpeechSynthesisUtterance" in window;
    this.enabled = true;
    this.rate = 1;
    this.voiceURI = null;
    this.voice = null;
    this.voices = [];
    this._load();
    this._bind();
  }

  _load() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      if (typeof saved.enabled === "boolean") this.enabled = saved.enabled;
      if (typeof saved.rate === "number") this.rate = Math.min(1.6, Math.max(0.6, saved.rate));
      if (typeof saved.voiceURI === "string") this.voiceURI = saved.voiceURI;
    } catch {
      // Preferencias no disponibles.
    }
  }

  _save() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          enabled: this.enabled,
          rate: this.rate,
          voiceURI: this.voiceURI,
        })
      );
    } catch {
      // Almacenamiento no disponible.
    }
  }

  _bind() {
    if (!this.supported) return;
    const load = () => this.refreshVoices();
    load();
    if (typeof window.speechSynthesis.addEventListener === "function") {
      window.speechSynthesis.addEventListener("voiceschanged", load);
    }
    window.setTimeout(load, 350);
    window.setTimeout(load, 1200);
  }

  refreshVoices() {
    if (!this.supported) return [];
    const all = window.speechSynthesis.getVoices() || [];
    const spanish = all.filter((voice) => (voice.lang || "").toLowerCase().startsWith("es"));
    this.voices = spanish.length ? spanish : all;
    this._pickVoice();
    return this.voices;
  }

  _pickVoice() {
    if (!this.voices.length) {
      this.voice = null;
      return;
    }

    if (this.voiceURI) {
      const stored = this.voices.find((voice) => voice.voiceURI === this.voiceURI);
      if (stored) {
        this.voice = stored;
        return;
      }
    }

    for (const lang of SPANISH_PREFERENCE) {
      const found = this.voices.find((voice) => (voice.lang || "").toLowerCase() === lang);
      if (found) {
        this.voice = found;
        return;
      }
    }

    const anySpanish = this.voices.find((voice) =>
      (voice.lang || "").toLowerCase().startsWith("es")
    );
    this.voice = anySpanish || this.voices[0];
  }

  setEnabled(enabled) {
    this.enabled = Boolean(enabled);
    if (!this.enabled) this.cancel();
    this._save();
  }

  setRate(rate) {
    const value = Number(rate);
    if (!Number.isFinite(value)) return;
    this.rate = Math.min(1.6, Math.max(0.6, value));
    this._save();
  }

  setVoice(voiceURI) {
    if (!voiceURI || voiceURI === "auto") {
      this.voiceURI = null;
      this._pickVoice();
    } else {
      this.voiceURI = voiceURI;
      this._pickVoice();
    }
    this._save();
  }

  speak(text, { interrupt = true } = {}) {
    if (!this.supported || !this.enabled || !text) return false;
    try {
      if (interrupt) window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(String(text));
      utterance.lang = (this.voice && this.voice.lang) || "es-MX";
      utterance.rate = this.rate;
      utterance.pitch = 1;
      utterance.volume = 1;
      if (this.voice) utterance.voice = this.voice;
      window.speechSynthesis.speak(utterance);
      return true;
    } catch {
      return false;
    }
  }

  cancel() {
    if (!this.supported) return;
    try {
      window.speechSynthesis.cancel();
    } catch {
      // Sin soporte.
    }
  }
}
