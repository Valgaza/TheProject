"use client";

import React, { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/icons";

const MEMORY_URL = process.env.NEXT_PUBLIC_MEMORY_URL ?? "http://localhost:8000";
const USER_ID = "user_001";

export default function Conversation() {
  const router = useRouter();
  const [messages, setMessages] = useState<any[]>([
    {
      role: "user", id: 1,
      text: "Compare the three most common vector similarity metrics used in embedding search — include their formulas, use cases, and performance trade-offs.",
    },
    { role: "ai", id: 2, type: "table", finished: true },
    {
      role: "user", id: 3,
      text: "Show me the cosine similarity formula with a worked numeric example using two short vectors.",
    },
    { role: "ai", id: 4, type: "formula", finished: true },
  ]);
  const [streaming, setStreaming] = useState("");
  const [val, setVal] = useState("");

  useEffect(() => {
    setTimeout(() => setStreaming("done"), 300);
  }, []);

  const send = () => {
    if (!val.trim()) return;
    const id = Date.now();
    const text = val.trim();
    setMessages(m => [...m, { role: "user", id, text }]);
    setVal("");

    fetch(`${MEMORY_URL}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, user_id: USER_ID }),
    }).catch(err => console.error("[memory] ingest failed:", err));

    setTimeout(() => {
      setMessages(m => [...m, { role: "ai", id: id + 1, type: "followup", finished: true }]);
    }, 600);
  };

  return (
    <div className="convo">
      <div className="convo__head">
        <div className="convo__meta">
          <span className="tag tag--accent">
            <span className="dot" style={{background: "var(--d-rag)"}}/>
            RAG Q&A
          </span>
          <span>4 messages</span>
          <span>·</span>
          <span>rag pipeline · local</span>
        </div>
        <h1 className="convo__title">Vector similarity metrics</h1>
      </div>

      {messages.map(m => m.role === "user"
        ? <UserMsg key={m.id} text={m.text}/>
        : <AIMsg key={m.id} m={m} streaming={streaming} router={router}/>)}

      <div className="composer-wrap" style={{ marginTop: 32 }}>
        <div className="composer">
          <div className="composer__top">
            <div className="tag tag--accent">
              <span className="dot" style={{background: "var(--d-rag)"}}/>
              RAG Q&A
            </div>
            <span className="composer__hint mono">↑ to edit last · ⌘K to switch</span>
          </div>
          <textarea
            className="composer__input"
            placeholder="Continue the conversation…"
            value={val}
            onChange={e => setVal(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }}
            rows={2}
          />
          <div className="composer__bot">
            <div className="composer__tools">
              <button className="ctool"><Icon name="paper" size={16}/></button>
              <button className="ctool"><Icon name="mic" size={16}/></button>
              <button className="ctool"><Icon name="book" size={16}/></button>
            </div>
            <button className="btn btn--primary" onClick={send}>Send <Icon name="arrow" size={14}/></button>
          </div>
        </div>
      </div>
    </div>
  );
}

const UserMsg = ({ text }: { text: string }) => (
  <div className="msg msg--user msg-enter">
    <div className="msg__hd">
      <div className="msg__avatar">U</div>
      <strong>You</strong>
    </div>
    <div className="msg__body"><p>{text}</p></div>
  </div>
);

const AIMsg = ({ m, streaming, router }: any) => {
  if (m.type === "followup") {
    return (
      <div className="msg msg--ai msg-enter">
        <div className="msg__hd">
          <div className="msg__avatar">N</div>
          <strong>Nexus · RAG</strong>
        </div>
        <div className="msg__body">
          <p>Working on that — retrieving relevant context and composing a response.</p>
          <div style={{display:"flex", gap:8, marginTop:12}}>
            <div className="skeleton" style={{height:14, width:"90%"}}/>
          </div>
          <div style={{display:"flex", gap:8, marginTop:8}}>
            <div className="skeleton" style={{height:14, width:"75%"}}/>
          </div>
        </div>
      </div>
    );
  }

  if (m.type === "table") {
    return (
      <div className="msg msg--ai msg-enter" style={{animationDelay:"200ms"}}>
        <div className="msg__hd">
          <div className="msg__avatar">N</div>
          <strong>Nexus · RAG</strong>
          <span>14:04</span>
          <span style={{flex:1}}/>
          <span style={{color:"var(--ink-3)"}}>· 1.8s · 842 tokens</span>
        </div>
        <div className="msg__body">
          <h4>Three metrics, three trade-offs.</h4>
          <p>
            Vector search performance hinges on the right similarity metric for your data distribution.
            Cosine similarity is the standard for normalised text embeddings; dot product is faster but
            magnitude-sensitive; Euclidean distance generalises better to dense numerical spaces.
          </p>
          <table className="tbl">
            <thead>
              <tr><th>Metric</th><th>Formula</th><th>Best For</th><th>Notes</th></tr>
            </thead>
            <tbody>
              <tr>
                <td>Cosine Similarity</td>
                <td className="mono" style={{fontSize:12}}>(A·B) / (‖A‖·‖B‖)</td>
                <td>Semantic NLP, RAG</td>
                <td style={{color:"var(--ink-2)", fontSize:12.5}}>Normalised; ignores magnitude</td>
              </tr>
              <tr>
                <td>Dot Product</td>
                <td className="mono" style={{fontSize:12}}>Σ aᵢ·bᵢ</td>
                <td>Recommendation, ANN</td>
                <td style={{color:"var(--ink-2)", fontSize:12.5}}>Fastest; magnitude-sensitive</td>
              </tr>
              <tr>
                <td>Euclidean Distance</td>
                <td className="mono" style={{fontSize:12}}>√Σ (aᵢ−bᵢ)²</td>
                <td>Computer vision, clustering</td>
                <td style={{color:"var(--ink-2)", fontSize:12.5}}>Sensitive to vector scale</td>
              </tr>
            </tbody>
          </table>
          <p>
            For most RAG pipelines operating on unit-normalised embeddings, cosine similarity and dot
            product are numerically equivalent — dot product is preferred for throughput.
          </p>
          <div className="trace">
            <span>PIPELINE TRACE</span>
            <span className="trace__line"/>
            <span className="trace__dots">
              <span className="trace__dot is-on"/><span style={{color:"var(--ink-3)"}}>router</span>
              <span className="trace__line" style={{width:24, flex:"0 0 24px"}}/>
              <span className="trace__dot is-on"/><span style={{color:"var(--ink-3)"}}>retrieve · 8 docs</span>
              <span className="trace__line" style={{width:24, flex:"0 0 24px"}}/>
              <span className="trace__dot is-on"/><span style={{color:"var(--ink-3)"}}>rag · compose</span>
              <span className="trace__line" style={{width:24, flex:"0 0 24px"}}/>
              <span className="trace__dot is-on"/><span style={{color:"var(--ink-3)"}}>cite · 3 sources</span>
            </span>
            <span className="trace__line"/>
            <span style={{cursor:"pointer", color:"var(--accent)"}} onClick={() => router.push("/graph")}>view in graph ↗</span>
          </div>
        </div>
      </div>
    );
  }

  if (m.type === "formula") {
    return (
      <div className="msg msg--ai msg-enter" style={{animationDelay:"200ms"}}>
        <div className="msg__hd">
          <div className="msg__avatar">N</div>
          <strong>Nexus · RAG</strong>
          <span>14:05</span>
          <span style={{flex:1}}/>
          <span style={{color:"var(--ink-3)"}}>· 1.1s · 394 tokens</span>
        </div>
        <div className="msg__body">
          <h4>Cosine similarity, step by step.</h4>
          <p>
            Cosine similarity measures the angle between two vectors, not their magnitude.
            A result of 1 means identical direction; 0 means orthogonal; −1 means opposite.
          </p>
          <pre className="code">{`// Formula
cosine_sim(A, B)  =  (A · B) / (‖A‖ × ‖B‖)

// Example  —  A = [1, 2, 3],  B = [4, 0, 2]

dot(A,B)  =  (1×4) + (2×0) + (3×2)  =  4 + 0 + 6  =  10
‖A‖       =  √(1² + 2² + 3²)         =  √14        ≈  3.742
‖B‖       =  √(4² + 0² + 2²)         =  √20        ≈  4.472

result    =  10 / (3.742 × 4.472)    ≈  0.5976`}</pre>
          <p>
            A score of ≈ 0.60 indicates moderate directional similarity — these vectors
            point roughly toward the same region of the embedding space.
          </p>
          <div className="trace">
            <span>PIPELINE TRACE</span>
            <span className="trace__line"/>
            <span className="trace__dots">
              <span className="trace__dot is-on"/><span style={{color:"var(--ink-3)"}}>router</span>
              <span className="trace__line" style={{width:24, flex:"0 0 24px"}}/>
              <span className="trace__dot is-on"/><span style={{color:"var(--ink-3)"}}>retrieve · 3 docs</span>
              <span className="trace__line" style={{width:24, flex:"0 0 24px"}}/>
              <span className="trace__dot is-on"/><span style={{color:"var(--ink-3)"}}>rag · compose</span>
            </span>
            <span className="trace__line"/>
          </div>
        </div>
      </div>
    );
  }

  return null;
};
