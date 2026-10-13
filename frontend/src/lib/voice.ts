/**
 * Hands-free voice: a Pipecat client on a WebRTC connection to core.
 *
 * The offer goes to POST /voice/{chatId}/offer (core/nexus_core/routers/
 * voice_rtc.py); after that the microphone and the spoken answer travel over
 * the peer connection, with the browser's own echo cancellation, and core
 * decides when the person has finished and when they are interrupting
 * (core/nexus_core/voice/pipeline.py). What a turn means arrives as RTVI
 * server messages, one per step, and is passed on through VoiceEvents:
 *
 *   {type: "transcript", turn, text}          what they said, final
 *   {type: "searching", turn}                 this turn looks something up online
 *   {type: "prepared", turn, ...}             stored, with route and sources
 *   {type: "token", turn, text}               the answer as it is written
 *   {type: "interrupted", turn}               cut off; "done" follows
 *   {type: "done", turn, assistant_message, chat_title, interrupted?}
 *   {type: "error", detail}
 */

import Daily from "@daily-co/daily-js";
import { PipecatClient } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";
import { CORE_URL, type Message, type PreparedTurn } from "@/lib/api";

export type VoiceState =
  | "connecting" | "loading" | "listening" | "hearing" | "thinking" | "searching" | "speaking" | "closed";

export type VoiceEvents = {
  onState?: (state: VoiceState) => void;
  onTranscript?: (turn: number, text: string) => void;
  onPrepared?: (turn: number, prepared: PreparedTurn) => void;
  onToken?: (turn: number, text: string) => void;
  onDone?: (turn: number, assistant: Message, chatTitle: string | null) => void;
  onInterrupted?: (turn: number) => void;
  onError?: (detail: string) => void;
  /** The session ended by itself (connection lost, microphone refused). */
  onEnd?: (error?: string) => void;
};

export const voiceOfferUrl = (chatId: string) => `${CORE_URL}/voice/${chatId}/offer`;

type ServerMessage = { type: string; turn: number; [key: string]: unknown };

export class VoiceClient {
  private client: PipecatClient | null = null;
  private audio: HTMLAudioElement | null = null;
  private state: VoiceState = "connecting";
  private stopped = false;
  private turnPending = false;
  private searching = false;

  constructor(private chatId: string, private events: VoiceEvents) {}

  async start(): Promise<void> {
    this.setState("connecting");
    // The transport takes the microphone through Daily's call object, one
    // per page, and ending a session stops its microphone track without
    // discarding the object. A second session would reuse that stopped
    // track and send silence, so the old object goes first.
    await Daily.getCallInstance()?.destroy().catch(() => undefined);
    if (this.stopped) return;
    const client = new PipecatClient({
      // Gathering every ICE candidate before the offer leaves keeps it to one
      // request; on localhost that takes milliseconds.
      transport: new SmallWebRTCTransport({ waitForICEGathering: true }),
      enableMic: true,
      enableCam: false,
      callbacks: {
        onBotReady: () => this.setState("listening"),
        onUserStartedSpeaking: () => {
          if (!this.turnPending) this.setState("hearing");
        },
        onUserStoppedSpeaking: () => {
          if (!this.turnPending && this.state === "hearing") this.setState("listening");
        },
        onBotStartedSpeaking: () => this.setState("speaking"),
        onBotStoppedSpeaking: () => {
          if (this.state === "speaking") this.setState(this.turnPending ? (this.searching ? "searching" : "thinking") : "listening");
        },
        onServerMessage: data => this.receive(data as ServerMessage),
        // The WebRTC transport reports the bot's track with no participant
        // at all, so only a track marked local is the microphone.
        onTrackStarted: (track, participant) => {
          if (track.kind === "audio" && !participant?.local) this.play(track);
        },
        onDeviceError: error => {
          this.end(error.type === "permissions" ? "Microphone access was refused." : "The microphone isn't available.");
        },
        onDisconnected: () => this.end("The voice connection closed."),
      },
    });
    this.client = client;
    // A Request rather than a URL, so the session cookie goes with it: core
    // is on another port, and fetch leaves cookies off cross-origin requests.
    // The transport reuses it for the ICE candidate requests too.
    const endpoint = new Request(voiceOfferUrl(this.chatId), {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
    });
    try {
      await client.connect({ webrtcRequestParams: { endpoint } });
    } catch (err) {
      if (this.stopped) return;
      throw err instanceof Error ? err : new Error("Voice could not start.");
    }
    // Stopped while connecting (React mounts effects twice in development):
    // without this, the first connection would stay open beside the second.
    if (this.stopped) void client.disconnect();
  }

  /** The stop button: cut the assistant off without saying anything. */
  interrupt(): void {
    this.client?.sendClientMessage("interrupt");
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.setState("closed");
    void this.client?.disconnect().catch(() => undefined);
    if (this.audio) {
      this.audio.pause();
      this.audio.srcObject = null;
      this.audio = null;
    }
  }

  private end(error: string) {
    if (this.stopped) return;
    this.stop();
    this.events.onEnd?.(error);
  }

  private setState(state: VoiceState) {
    if (this.state === "closed" && state !== "closed") return;
    this.state = state;
    this.events.onState?.(state);
  }

  private play(track: MediaStreamTrack) {
    this.audio ??= new Audio();
    this.audio.autoplay = true;
    this.audio.srcObject = new MediaStream([track]);
    void this.audio.play().catch(() => undefined);
  }

  private receive(m: ServerMessage) {
    switch (m.type) {
      case "transcript":
        this.turnPending = true;
        this.searching = false;
        this.setState("thinking");
        this.events.onTranscript?.(m.turn, String(m.text ?? ""));
        break;
      case "searching":
        this.searching = true;
        if (this.state !== "speaking") this.setState("searching");
        break;
      case "prepared":
        this.events.onPrepared?.(m.turn, m as unknown as PreparedTurn);
        break;
      case "token":
        this.searching = false;
        this.events.onToken?.(m.turn, String(m.text ?? ""));
        break;
      case "interrupted":
        this.turnPending = false;
        this.setState("hearing");
        this.events.onInterrupted?.(m.turn);
        break;
      case "done":
        // Sent once the answer has finished playing; an interrupted one's
        // arrives while the next utterance is being heard.
        if (!m.interrupted) {
          this.turnPending = false;
          if (this.state !== "hearing") this.setState("listening");
        }
        this.events.onDone?.(m.turn, m.assistant_message as Message, (m.chat_title as string | null) ?? null);
        break;
      case "error":
        this.events.onError?.(String(m.detail ?? "voice error"));
        break;
    }
  }
}
