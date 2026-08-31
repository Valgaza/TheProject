/**
 * Pipeline identities.
 *
 * The user no longer picks one of these — the orchestrator decides. They
 * survive as labels: which handler answered a turn, and what colour to tint it.
 */

export type DomainId = "rag" | "general" | "speech" | "vision";

export type Domain = {
  id: DomainId;
  name: string;
  icon: string;
  blurb: string;
};

export const DOMAINS: Domain[] = [
  { id: "rag",     name: "RAG Q&A",        icon: "book",  blurb: "Document retrieval, cited answers, knowledge search" },
  { id: "general", name: "Conversational", icon: "chat",  blurb: "Open-ended dialogue, summaries, and drafting" },
  { id: "speech",  name: "Speech",         icon: "speak", blurb: "STT transcription, TTS narration, voice workflows" },
  { id: "vision",  name: "Vision",         icon: "spark", blurb: "Image analysis, video understanding, captioning" },
];

export const domainById = (id: string): Domain =>
  DOMAINS.find(d => d.id === id) ?? DOMAINS[1];
