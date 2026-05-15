"use client";

import React, { createContext, useContext, useEffect } from "react";
import { usePathname } from "next/navigation";
import { RailNav, TopBar } from "./shell";
import {
  useTweaks,
  TweaksPanel,
  TweakSection,
  TweakRadio,
  TweakSelect,
  TweakButton,
} from "./tweaks-panel";

const TWEAKS = {
  accent: "teal",
  fontPairing: "fraunces_inter",
  heroBackground: "particles",
  animationIntensity: "restrained",
};

const ACCENTS: Record<string, Record<string, string>> = {
  teal: { primary: "0.42 0.060 195", deep: "0.32 0.055 200", soft: "0.78 0.040 195", veil: "0.94 0.020 195", ink: "0.16 0.030 215", line: "0.86 0.025 195" },
  gold: { primary: "0.66 0.075 78", deep: "0.55 0.075 70", soft: "0.85 0.050 80", veil: "0.95 0.025 80", ink: "0.30 0.060 60", line: "0.88 0.030 78" },
  copper: { primary: "0.55 0.090 45", deep: "0.44 0.085 40", soft: "0.80 0.060 50", veil: "0.95 0.025 50", ink: "0.26 0.070 30", line: "0.86 0.035 45" },
  slate: { primary: "0.36 0.045 250", deep: "0.26 0.045 250", soft: "0.74 0.035 250", veil: "0.94 0.018 250", ink: "0.16 0.030 250", line: "0.86 0.025 250" },
};

const FONTS: Record<string, Record<string, string>> = {
  fraunces_inter: { display: '"Fraunces", "GT Sectra", Georgia, serif', sans: '"Inter Tight", "Söhne", system-ui, sans-serif' },
  sectra_inter: { display: '"Fraunces", Georgia, serif', sans: '"Inter Tight", system-ui, sans-serif' },
  editorial: { display: '"Fraunces", Georgia, serif', sans: '"Inter Tight", system-ui, sans-serif' },
  neue_tiempos: { display: '"Inter Tight", system-ui, sans-serif', sans: '"Fraunces", Georgia, serif' },
  sohne_only: { display: '"Inter Tight", system-ui, sans-serif', sans: '"Inter Tight", system-ui, sans-serif' },
};

const applyTweaks = (t: any) => {
  const r = document.documentElement;
  const a = ACCENTS[t.accent] || ACCENTS.teal;
  r.style.setProperty("--accent", `oklch(${a.primary})`);
  r.style.setProperty("--accent-deep", `oklch(${a.deep})`);
  r.style.setProperty("--accent-soft", `oklch(${a.soft})`);
  r.style.setProperty("--accent-veil", `oklch(${a.veil})`);
  r.style.setProperty("--accent-ink", `oklch(${a.ink})`);
  const f = FONTS[t.fontPairing] || FONTS.fraunces_inter;
  r.style.setProperty("--font-display", f.display);
  r.style.setProperty("--font-sans", f.sans);
};

export const TweaksContext = createContext<any>(null);

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [tweaks, setTweak] = useTweaks(TWEAKS);

  useEffect(() => {
    applyTweaks(tweaks);
  }, [tweaks]);

  const isLanding = pathname === "/";

  return (
    <TweaksContext.Provider value={{ tweaks, setTweak }}>
      <div className={`app ${isLanding ? "is-landing" : ""}`}>
        {!isLanding && <RailNav />}
        <main className="main">
          {!isLanding && <TopBar />}
          <div className="page" key={pathname} style={{ animation: "pageIn 480ms cubic-bezier(0.16, 1, 0.3, 1)" }}>
            {children}
          </div>
        </main>

        <TweaksPanel title="Tweaks" defaultOpen={true}>
          <TweakSection title="Accent">
            <TweakRadio
              label="Color"
              value={tweaks.accent}
              onChange={(v: string) => setTweak("accent", v)}
              options={[
                { value: "teal", label: "Teal" },
                { value: "gold", label: "Gold" },
                { value: "copper", label: "Copper" },
                { value: "slate", label: "Slate" },
              ]}
            />
          </TweakSection>
          <TweakSection title="Typography">
            <TweakSelect
              label="Pairing"
              value={tweaks.fontPairing}
              onChange={(v: string) => setTweak("fontPairing", v)}
              options={[
                { value: "fraunces_inter", label: "Fraunces × Inter Tight" },
                { value: "sectra_inter", label: "Sectra-style × Inter" },
                { value: "editorial", label: "Editorial × Inter" },
                { value: "neue_tiempos", label: "Sans display × Serif body" },
                { value: "sohne_only", label: "Söhne-style sans only" },
              ]}
            />
          </TweakSection>
          <TweakSection title="Hero background">
            <TweakRadio
              label="Style"
              value={tweaks.heroBackground}
              onChange={(v: string) => setTweak("heroBackground", v)}
              options={[
                { value: "particles", label: "Particles" },
                { value: "mesh", label: "Mesh" },
                { value: "drift", label: "Drift" },
                { value: "topo", label: "Topo" },
              ]}
            />
          </TweakSection>
          <TweakSection title="Motion">
            <TweakRadio
              label="Intensity"
              value={tweaks.animationIntensity}
              onChange={(v: string) => setTweak("animationIntensity", v)}
              options={[
                { value: "restrained", label: "Restrained" },
                { value: "generous", label: "Generous" },
                { value: "showcase", label: "Showcase" },
              ]}
            />
          </TweakSection>
        </TweaksPanel>

        <style>{`
          @keyframes pageIn {
            from { opacity: 0; transform: translateY(8px); }
            to   { opacity: 1; transform: translateY(0); }
          }
        `}</style>
      </div>
    </TweaksContext.Provider>
  );
}
