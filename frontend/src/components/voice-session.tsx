"use client";

/**
 * The bar that replaces the composer while someone is talking to a chat.
 *
 * Hands-free: there is no button to press to speak. Core hears when they
 * have finished and when they are interrupting (lib/voice.ts has the
 * plumbing); a few words over the answer stop it, "mm-hmm" doesn't. The
 * thread above fills in exactly as for typed messages, through the same
 * callbacks the page uses for a streamed answer.
 */

import React, { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import { VoiceClient, type VoiceEvents, type VoiceState } from "@/lib/voice";

const LABEL: Record<VoiceState, string> = {
  connecting: "Getting ready…",
  loading: "Loading the speech models…",
  listening: "Listening",
  hearing: "Hearing you",
  thinking: "Thinking",
  searching: "Looking it up online…",
  speaking: "Speaking · talk over it to interrupt",
  closed: "Ended",
};

export function VoiceSession({
  chatId,
  events,
  onEnd,
}: {
  chatId: string;
  events: VoiceEvents;
  onEnd: (error?: string) => void;
}) {
  const [state, setState] = useState<VoiceState>("connecting");
  const [heard, setHeard] = useState("");
  const client = useRef<VoiceClient | null>(null);
  // The page's handlers change identity on every render; the socket should not.
  const latest = useRef({ events, onEnd });
  useEffect(() => {
    latest.current = { events, onEnd };
  });

  useEffect(() => {
    const c = new VoiceClient(chatId, {
      onState: setState,
      onTranscript: (turn, text) => {
        setHeard(text);
        latest.current.events.onTranscript?.(turn, text);
      },
      onPrepared: (turn, p) => latest.current.events.onPrepared?.(turn, p),
      onToken: (turn, t) => latest.current.events.onToken?.(turn, t),
      onDone: (turn, m, title) => latest.current.events.onDone?.(turn, m, title),
      onInterrupted: turn => latest.current.events.onInterrupted?.(turn),
      onError: detail => latest.current.events.onError?.(detail),
      onEnd: error => latest.current.onEnd(error),
    });
    client.current = c;
    c.start().catch(err => {
      c.stop();
      latest.current.onEnd(
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "Microphone access was refused."
          : err instanceof Error ? err.message : "Voice could not start."
      );
    });
    return () => c.stop();
  }, [chatId]);

  return (
    <div className="composer-wrap">
      <div className={`voicebar voicebar--${state}`} role="status" aria-live="polite">
        <span className="voicebar__orb" aria-hidden />
        <div className="voicebar__text">
          <strong>{LABEL[state]}</strong>
          {heard && <span className="voicebar__heard">“{heard}”</span>}
        </div>
        {state === "speaking" && (
          <button className="ctool" type="button" title="Stop the answer" onClick={() => client.current?.interrupt()}>
            <Icon name="close" size={16} />
          </button>
        )}
        <button
          className="btn"
          type="button"
          onClick={() => {
            client.current?.stop();
            onEnd();
          }}
        >
          End voice
        </button>
      </div>
    </div>
  );
}
