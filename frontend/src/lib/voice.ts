/**
 * Hands-free voice: the microphone out to core, the answer's audio back.
 *
 * Talks to WS /ws/voice/{chatId} (core/nexus_core/routers/voice.py, which
 * documents the protocol). Core decides when the person has finished, when
 * they are interrupting, and what is said; this file only moves sound:
 *
 *   capture   microphone -> AudioWorklet in a 16 kHz AudioContext -> PCM16
 *             frames over the socket. echoCancellation keeps the assistant's
 *             own voice, coming out of the speakers, from being heard as speech.
 *   playback  each sentence arrives as a JSON header and one binary frame of
 *             24 kHz PCM16; sentences are scheduled back to back, and core is
 *             told when each starts and finishes, which is how it knows what
 *             the person actually heard if they cut in.
 */

import { CORE_WS_URL, type PreparedTurn, type Message } from "@/lib/api";

export type VoiceState = "connecting" | "loading" | "listening" | "hearing" | "thinking" | "speaking" | "closed";

export type VoiceEvents = {
  onState?: (state: VoiceState) => void;
  onTranscript?: (turn: number, text: string) => void;
  onPrepared?: (turn: number, prepared: PreparedTurn) => void;
  onToken?: (turn: number, text: string) => void;
  onDone?: (turn: number, assistant: Message, chatTitle: string | null) => void;
  onInterrupted?: (turn: number) => void;
  onError?: (detail: string) => void;
};

export const voiceSocketUrl = (chatId: string) => `${CORE_WS_URL}/ws/voice/${chatId}`;

// Runs on the audio thread: batches the microphone into ~32 ms of PCM16.
const CAPTURE_WORKLET = `
class Capture extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Int16Array(512); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.buf.length) { this.port.postMessage(this.buf.buffer, [this.buf.buffer]); this.buf = new Int16Array(512); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("nexus-capture", Capture);
`;

type Header = { turn: number; index: number; text: string; sample_rate: number };

export class VoiceClient {
  private ws: WebSocket | null = null;
  private mic: MediaStream | null = null;
  private captureCtx: AudioContext | null = null;
  private playCtx: AudioContext | null = null;
  private header: Header | null = null;
  private playhead = 0;
  private sources: AudioBufferSourceNode[] = [];
  private timers: number[] = [];
  private state: VoiceState = "connecting";
  private turnPending = false;

  constructor(private chatId: string, private events: VoiceEvents) {}

  async start(): Promise<void> {
    this.setState("connecting");
    // Asked first: if the person says no, there is no socket to tidy up.
    this.mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.captureCtx = new AudioContext({ sampleRate: 16000 });
    this.playCtx = new AudioContext({ sampleRate: 24000 });
    const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: "application/javascript" }));
    await this.captureCtx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);

    const ws = new WebSocket(voiceSocketUrl(this.chatId));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = e => this.receive(e.data);
    ws.onclose = e => {
      if (this.state !== "closed" && e.code !== 1000) this.events.onError?.(e.reason || "voice connection closed");
      this.stop();
    };
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("could not open the voice connection"));
    });

    const source = this.captureCtx.createMediaStreamSource(this.mic);
    const node = new AudioWorkletNode(this.captureCtx, "nexus-capture");
    node.port.onmessage = e => {
      if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(e.data as ArrayBuffer);
    };
    source.connect(node);
  }

  /** The stop button: cut the assistant off without saying anything. */
  interrupt(): void {
    this.send({ type: "interrupt" });
  }

  stop(): void {
    if (this.state === "closed") return;
    this.setState("closed");
    this.clearPlayback();
    this.ws?.close(1000);
    this.mic?.getTracks().forEach(t => t.stop());
    void this.captureCtx?.close();
    void this.playCtx?.close();
  }

  private setState(state: VoiceState) {
    if (this.state === "closed" && state !== "closed") return;
    this.state = state;
    this.events.onState?.(state);
  }

  private send(message: object) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(message));
  }

  private receive(data: string | ArrayBuffer) {
    if (data instanceof ArrayBuffer) {
      if (this.header) this.play(this.header, data);
      this.header = null;
      return;
    }
    const m = JSON.parse(data);
    switch (m.type) {
      case "loading": this.setState("loading"); break;
      case "ready": this.setState("listening"); break;
      case "vad":
        if (!this.turnPending) this.setState(m.speaking ? "hearing" : "listening");
        break;
      case "transcript":
        this.turnPending = true;
        this.setState("thinking");
        this.events.onTranscript?.(m.turn, m.text);
        break;
      case "prepared": this.events.onPrepared?.(m.turn, m as PreparedTurn); break;
      case "token": this.events.onToken?.(m.turn, m.text); break;
      case "audio": this.header = m; break;
      case "pause": void this.playCtx?.suspend(); break;
      case "resume": void this.playCtx?.resume(); break;
      case "interrupted":
        this.clearPlayback();
        void this.playCtx?.resume();
        this.turnPending = false;
        this.setState("hearing");
        this.events.onInterrupted?.(m.turn);
        break;
      case "done":
        // An interrupted turn's "done" arrives while the next one is being
        // heard; only a finished answer returns to listening.
        if (!m.interrupted) {
          this.turnPending = false;
          this.setState("listening");
        }
        this.events.onDone?.(m.turn, m.assistant_message, m.chat_title ?? null);
        break;
      case "error": this.events.onError?.(m.detail); break;
    }
  }

  private play(header: Header, pcm: ArrayBuffer) {
    const ctx = this.playCtx;
    if (!ctx) return;
    const samples = new Int16Array(pcm);
    const buffer = ctx.createBuffer(1, samples.length, header.sample_rate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 0x8000;
    const node = ctx.createBufferSource();
    node.buffer = buffer;
    node.connect(ctx.destination);
    const at = Math.max(ctx.currentTime + 0.02, this.playhead);
    node.start(at);
    this.playhead = at + buffer.duration;
    this.sources.push(node);
    const { turn, index } = header;
    // Wall-clock, so a pause in between can make this early: core may count
    // one more sentence as heard than was. Only matters when cut off mid-pause.
    const delay = Math.max(0, (at - ctx.currentTime) * 1000);
    this.timers.push(window.setTimeout(() => {
      this.setState("speaking");
      this.send({ type: "playing", turn, index });
    }, delay));
    node.onended = () => {
      this.sources = this.sources.filter(s => s !== node);
      this.send({ type: "played", turn, index });
      if (this.sources.length === 0 && this.state === "speaking") this.setState("thinking");
    };
  }

  private clearPlayback() {
    this.timers.forEach(t => window.clearTimeout(t));
    this.timers = [];
    for (const node of this.sources) {
      node.onended = null;
      try { node.stop(); } catch { /* already stopped */ }
    }
    this.sources = [];
    this.playhead = 0;
    this.header = null;
  }
}
