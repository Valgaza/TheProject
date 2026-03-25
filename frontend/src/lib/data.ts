export interface Domain {
  id: string;
  name: string;
  emoji: string;
  description: string;
  pipelineSteps: string[];
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  pipelineSteps?: string[];
}

export interface Chat {
  id: string;
  title: string;
  domainId: string;
  messages: Message[];
  projectId?: string;
}

export interface Project {
  id: string;
  title: string;
  description: string;
  domainId: string;
  chatIds: string[];
  lastActive: string;
}

export interface PipelineTrace {
  chatId: string;
  inputType: string;
  intent: string;
  confidence: number;
  steps: { name: string; icon: string; status: "complete" | "processing" | "pending" }[];
  metrics: { latency: string; tokens: number; cost: string; model: string };
}

export const DOMAINS: Domain[] = [
  { id: "general", name: "General", emoji: "🌐", description: "Multi-domain routing — auto-detects intent and routes automatically", pipelineSteps: ["Intent Classifier", "Domain Router", "Pipeline Executor", "Response Synthesizer"] },
  { id: "medical", name: "Medical", emoji: "🩺", description: "Clinical Q&A, symptom analysis, drug interaction checks, medical record parsing", pipelineSteps: ["NLP Preprocessing", "Medical LLM", "Summary Engine"] },
  { id: "legal", name: "Legal", emoji: "⚖️", description: "Contract review, case law lookup, clause extraction, compliance checks", pipelineSteps: ["Document Parser", "Clause Extractor", "Risk Scorer"] },
  { id: "financial", name: "Financial", emoji: "📊", description: "Report analysis, fraud detection, budget forecasting, data extraction", pipelineSteps: ["Data Extractor", "Financial LLM", "Anomaly Detector"] },
  { id: "research", name: "Research", emoji: "🔬", description: "Paper summarization, literature review, citation extraction, hypothesis generation", pipelineSteps: ["Paper Search", "Summarizer", "Citation Engine"] },
  { id: "engineering", name: "Engineering", emoji: "🏗️", description: "Technical documentation parsing, code review, architecture analysis", pipelineSteps: ["Code Parser", "Architecture Analyzer", "Documentation Generator"] },
];

export const CHATS: Chat[] = [
  {
    id: "chat-1",
    title: "Summarize patient discharge report",
    domainId: "medical",
    projectId: "proj-1",
    messages: [
      { id: "m1", role: "user", content: "Here is a patient discharge summary. Identify key diagnoses, medications, and follow-up actions." },
      {
        id: "m2", role: "assistant",
        content: `**Diagnoses**\n\n1. Acute anterior myocardial infarction (STEMI), treated with primary PCI and DES placement to LAD\n2. Hypertension, stage 2, currently managed\n3. Type 2 Diabetes Mellitus — HbA1c 8.2%, requiring medication adjustment\n\n**Medications Prescribed**\n\n- Aspirin 81mg daily (indefinite)\n- Clopidogrel 75mg daily (minimum 12 months post-stent)\n- Atorvastatin 80mg at bedtime\n- Metoprolol Succinate 50mg daily\n- Metformin 1000mg twice daily (increased from 500mg)\n- Lisinopril 20mg daily\n\n**Follow-Up Actions Required**\n\n- Cardiology follow-up in 2 weeks for post-PCI assessment\n- Endocrinology referral within 4 weeks for diabetes management optimization\n- Cardiac rehabilitation program enrollment — first session within 10 days\n- Repeat echocardiogram at 6 weeks to reassess LV function (EF 42% at discharge)\n- Lab work in 1 week: lipid panel, renal function, fasting glucose`,
        pipelineSteps: ["Medical", "NLP Extraction", "Summary Engine"],
      },
    ],
  },
  {
    id: "chat-2",
    title: "Review NDA for IP clauses",
    domainId: "legal",
    projectId: "proj-2",
    messages: [
      { id: "m3", role: "user", content: "Review this NDA and highlight any unusual intellectual property clauses." },
      {
        id: "m4", role: "assistant",
        content: `**Clause Analysis — Intellectual Property Provisions**\n\nAfter reviewing the Non-Disclosure Agreement dated March 2025, I identified three clauses that warrant attention:\n\n**1. Section 4.2 — Pre-existing IP Assignment (⚠️ High Risk)**\nThis clause states that any intellectual property "conceived during the term of this agreement, whether or not related to the disclosed information," becomes jointly owned. This is unusually broad — standard NDAs only cover IP directly derived from confidential information. This could inadvertently assign rights to your independent work.\n\n**2. Section 6.1 — Residual Knowledge Exception (⚠️ Medium Risk)**\nThe residual knowledge clause permits the receiving party to use "retained information in unaided memory" without restriction. While common, the definition of "unaided memory" is not bounded, creating potential for disputes.\n\n**3. Section 8.4 — Perpetual IP License (⚠️ High Risk)**\nUnlike the agreement's 3-year confidentiality term, the IP license granted in 8.4 is perpetual and irrevocable. This asymmetry means IP rights persist indefinitely even after the NDA expires.\n\n**Recommendation:** Negotiate Section 4.2 to limit scope to IP directly derived from disclosed materials. Add a clear definition boundary to Section 6.1. Align Section 8.4's term with the overall agreement duration.`,
        pipelineSteps: ["Legal", "Clause Extractor", "Risk Scorer"],
      },
    ],
  },
  {
    id: "chat-3",
    title: "Q3 Financial Report Analysis",
    domainId: "financial",
    projectId: "proj-3",
    messages: [
      { id: "m5", role: "user", content: "Analyze the attached Q3 report and flag any anomalies." },
      {
        id: "m6", role: "assistant",
        content: `**Q3 2024 Financial Analysis — Anomaly Report**\n\n**Revenue Summary**\nTotal revenue: $14.2M (↑ 8.3% QoQ). However, the growth is unevenly distributed across segments.\n\n**Flagged Anomalies**\n\n🔴 **Accounts Receivable Spike** — AR increased 34% while revenue grew only 8.3%. Days Sales Outstanding jumped from 42 to 57 days. This divergence suggests either aggressive revenue recognition or deteriorating collection efficiency.\n\n🟡 **SG&A to Revenue Ratio** — Selling, General & Administrative expenses rose to 31.2% of revenue (from 26.8% in Q2). The $1.4M increase lacks corresponding footnote disclosure. Warrants investigation into one-time charges vs. structural cost increases.\n\n🔴 **Inventory Turnover Decline** — Inventory turnover ratio dropped from 6.2 to 4.1. Combined with the AR spike, this may indicate channel stuffing or demand softening not yet reflected in revenue figures.\n\n🟢 **Gross Margin Stable** — Gross margin held at 64.3%, consistent with prior quarters. No anomalies detected in COGS composition.\n\n**Risk Assessment:** Medium-High. The AR and inventory signals together suggest potential revenue quality concerns. Recommend forensic analysis of Q3 booking patterns and customer payment terms.`,
        pipelineSteps: ["Financial", "Data Extractor", "Anomaly Detector"],
      },
    ],
  },
  {
    id: "chat-4",
    title: "Literature review on transformer architectures",
    domainId: "research",
    messages: [
      { id: "m7", role: "user", content: "Give me a literature review on transformer architecture improvements in 2023–2024." },
      {
        id: "m8", role: "assistant",
        content: `**Transformer Architecture Advances: 2023–2024 Literature Review**\n\n**1. Efficient Attention Mechanisms**\n\nFlashAttention-2 (Dao, 2023) achieved 2x speedup over the original FlashAttention by optimizing work partitioning between GPU thread blocks. This has become the de facto standard for training large models. GQA (Grouped Query Attention), introduced in Llama 2 (Touvron et al., 2023), reduces KV cache memory by grouping queries, enabling longer context windows without proportional memory increase.\n\n**2. Mixture of Experts (MoE) Scaling**\n\nMixtral 8x7B (Jiang et al., 2024) demonstrated that sparse MoE architectures can match dense model performance at 6x lower inference cost. DeepSeek-MoE (Dai et al., 2024) introduced fine-grained expert segmentation, showing that 2B active parameters from a 16B total can outperform dense 7B models on reasoning benchmarks.\n\n**3. Context Length Extension**\n\nRoPE scaling methods — YaRN (Peng et al., 2023) and Code Llama's frequency-based extension — enabled 100K+ context windows from models trained on 4K contexts. Ring Attention (Liu et al., 2023) distributed long sequences across devices, theoretically removing context length limits.\n\n**4. Post-Transformer Architectures**\n\nMamba (Gu & Dao, 2023) introduced selective state space models achieving transformer-quality language modeling with linear scaling in sequence length. Jamba (AI21, 2024) hybridized Mamba with transformer layers, combining the strengths of both. These represent the most significant architectural departures from pure attention.\n\n**5. Training Innovations**\n\nμP (Yang et al., 2023) enabled hyperparameter transfer across model scales, reducing tuning costs. DiPaCo (Douillard et al., 2024) demonstrated distributed training of LLMs across poorly connected devices.\n\n**Key Trend:** The field is moving from "scale everything" toward architectural efficiency — doing more with less compute through sparsity, better attention, and hybrid architectures.`,
        pipelineSteps: ["Research", "Paper Search", "Summarizer", "Citation Engine"],
      },
    ],
  },
];

export const PROJECTS: Project[] = [
  { id: "proj-1", title: "Medical Record Analysis", description: "Automated extraction and analysis of patient medical records, discharge summaries, and clinical notes using specialized medical NLP pipelines.", domainId: "medical", chatIds: ["chat-1"], lastActive: "2025-03-18" },
  { id: "proj-2", title: "Legal Contract Review", description: "AI-powered contract analysis for identifying risk clauses, compliance gaps, and unusual terms across NDAs, service agreements, and employment contracts.", domainId: "legal", chatIds: ["chat-2"], lastActive: "2025-03-17" },
  { id: "proj-3", title: "Financial Report Audit", description: "Automated financial document analysis with anomaly detection, trend identification, and risk assessment for quarterly and annual reports.", domainId: "financial", chatIds: ["chat-3"], lastActive: "2025-03-15" },
  { id: "proj-4", title: "Research Summarizer", description: "Literature review automation with paper discovery, key finding extraction, citation mapping, and synthesis across academic databases.", domainId: "research", chatIds: ["chat-4"], lastActive: "2025-03-14" },
];

export const PIPELINE_TRACES: Record<string, PipelineTrace> = {
  "chat-1": {
    chatId: "chat-1",
    inputType: "Text",
    intent: "Summarization",
    confidence: 94,
    steps: [
      { name: "NLP Preprocessing", icon: "FileText", status: "complete" },
      { name: "Medical LLM", icon: "Brain", status: "complete" },
      { name: "Summary Engine", icon: "ListChecks", status: "complete" },
    ],
    metrics: { latency: "1.2s", tokens: 842, cost: "$0.0021", model: "MedPaLM-2" },
  },
  "chat-2": {
    chatId: "chat-2",
    inputType: "Document",
    intent: "Extraction",
    confidence: 91,
    steps: [
      { name: "Document Parser", icon: "FileText", status: "complete" },
      { name: "Clause Extractor", icon: "Search", status: "complete" },
      { name: "Risk Scorer", icon: "ShieldAlert", status: "complete" },
    ],
    metrics: { latency: "2.1s", tokens: 1247, cost: "$0.0038", model: "GPT-4-Legal" },
  },
  "chat-3": {
    chatId: "chat-3",
    inputType: "Document",
    intent: "Analysis",
    confidence: 88,
    steps: [
      { name: "Data Extractor", icon: "Database", status: "complete" },
      { name: "Financial LLM", icon: "Brain", status: "complete" },
      { name: "Anomaly Detector", icon: "AlertTriangle", status: "complete" },
    ],
    metrics: { latency: "3.4s", tokens: 1583, cost: "$0.0052", model: "FinGPT-v3" },
  },
  "chat-4": {
    chatId: "chat-4",
    inputType: "Text",
    intent: "Literature Review",
    confidence: 96,
    steps: [
      { name: "Paper Search", icon: "Search", status: "complete" },
      { name: "Summarizer", icon: "FileText", status: "complete" },
      { name: "Citation Engine", icon: "BookOpen", status: "complete" },
    ],
    metrics: { latency: "4.8s", tokens: 2104, cost: "$0.0071", model: "Scholar-LLM" },
  },
};

export const getDomain = (id: string) => DOMAINS.find((d) => d.id === id);
export const getChat = (id: string) => CHATS.find((c) => c.id === id);
export const getProject = (id: string) => PROJECTS.find((p) => p.id === id);
