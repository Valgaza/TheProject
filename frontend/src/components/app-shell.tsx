"use client";

import React, { createContext, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { RailNav, TopBar } from "./shell";
import { ProjectProvider } from "@/lib/project-context";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { useTweaks } from "./tweaks-panel";

const TWEAKS = {
  accent: "teal",
  fontPairing: "fraunces_inter",
  heroBackground: "particles",
  animationIntensity: "restrained",
};

/** Read by the "Accent" and "Typography" controls now living in
 * Settings > Appearance (src/app/settings/page.tsx) -- exported so that
 * page doesn't have to redeclare the same option lists. */
export const ACCENTS: Record<string, Record<string, string>> = {
  teal: { primary: "0.42 0.060 195", deep: "0.32 0.055 200", soft: "0.78 0.040 195", veil: "0.94 0.020 195", ink: "0.16 0.030 215", line: "0.86 0.025 195" },
  gold: { primary: "0.66 0.075 78", deep: "0.55 0.075 70", soft: "0.85 0.050 80", veil: "0.95 0.025 80", ink: "0.30 0.060 60", line: "0.88 0.030 78" },
  copper: { primary: "0.55 0.090 45", deep: "0.44 0.085 40", soft: "0.80 0.060 50", veil: "0.95 0.025 50", ink: "0.26 0.070 30", line: "0.86 0.035 45" },
  slate: { primary: "0.36 0.045 250", deep: "0.26 0.045 250", soft: "0.74 0.035 250", veil: "0.94 0.018 250", ink: "0.16 0.030 250", line: "0.86 0.025 250" },
};

export const FONTS: Record<string, Record<string, string>> = {
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

/**
 * Gatekeeper between AuthProvider and everything that assumes a logged-in
 * user. "/" and "/login" are the only routes that don't need a session;
 * everything else redirects to /login until `/auth/me` says otherwise, and
 * /login itself redirects away once it does -- so nobody sees the login
 * form and the chat UI in the same breath.
 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isPublic = pathname === "/" || pathname === "/login";

  useEffect(() => {
    if (loading) return;
    if (!user && !isPublic) router.replace("/login");
    else if (user && pathname === "/login") router.replace("/chat");
  }, [loading, user, isPublic, pathname, router]);

  // A protected route with no confirmed session yet: render nothing rather
  // than flash real content right before the redirect above fires.
  if (!isPublic && (loading || !user)) return null;

  return <>{children}</>;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [tweaks, setTweak] = useTweaks(TWEAKS);

  useEffect(() => {
    applyTweaks(tweaks);
  }, [tweaks]);

  const isLanding = pathname === "/";
  // Login gets the same bare-canvas treatment as the landing page -- no
  // rail, no topbar, nothing that implies there's a logged-in user yet.
  const hideChrome = isLanding || pathname === "/login";

  return (
    <TweaksContext.Provider value={{ tweaks, setTweak }}>
      <AuthProvider>
      <RequireAuth>
      <ProjectProvider>
      <div className={`app ${hideChrome ? "is-bare" : ""}`}>
        {!hideChrome && <RailNav />}
        <main className="main">
          {!hideChrome && <TopBar />}
          <div className="page" key={pathname} style={{ animation: "pageIn 480ms cubic-bezier(0.16, 1, 0.3, 1)" }}>
            {children}
          </div>
        </main>

        <style>{`
          @keyframes pageIn {
            from { opacity: 0; transform: translateY(8px); }
            to   { opacity: 1; transform: translateY(0); }
          }
        `}</style>
      </div>
      </ProjectProvider>
      </RequireAuth>
      </AuthProvider>
    </TweaksContext.Provider>
  );
}
