"use client";

import React, { useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { useAuth } from "@/lib/auth-context";
import { ApiError, authConfig, googleLoginUrl, login, signup } from "@/lib/api";

/** Google's own four-color "G" mark -- the standard way to render a
 * "Sign in with Google" button; there's no equivalent in our own icon set. */
const GoogleG = () => (
  <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden="true">
    <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.9c1.7-1.56 2.7-3.87 2.7-6.62Z"/>
    <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.26c-.8.54-1.84.86-3.06.86-2.35 0-4.34-1.59-5.05-3.72H.94v2.33A9 9 0 0 0 9 18Z"/>
    <path fill="#FBBC05" d="M3.95 10.7A5.4 5.4 0 0 1 3.67 9c0-.59.1-1.17.28-1.7V4.97H.94A9 9 0 0 0 0 9c0 1.45.35 2.83.94 4.03l3.01-2.33Z"/>
    <path fill="#EA4335" d="M9 3.58c1.32 0 2.51.46 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .94 4.97l3.01 2.33C4.66 5.17 6.65 3.58 9 3.58Z"/>
  </svg>
);

type Mode = "login" | "signup";

export default function Login() {
  const { user, loading: authLoading, refresh } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [googleEnabled, setGoogleEnabled] = useState(false);

  // Whether to render the Google button at all -- there's nothing useful a
  // click can do if GOOGLE_CLIENT_ID/SECRET were never set on the server.
  useEffect(() => {
    authConfig()
      .then(cfg => setGoogleEnabled(cfg.google_oauth_enabled))
      .catch(() => setGoogleEnabled(false));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "signup") {
        await signup({ email, password, display_name: displayName });
      } else {
        await login({ email, password });
      }
      // The request above already set the session cookie -- this just
      // brings the rest of the app (AuthProvider's `user`) up to date so
      // the redirect below lands on a screen that already knows who's in.
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setSubmitting(false);
    }
  };

  // Already logged in and just landed here directly (e.g. a stale tab):
  // RequireAuth in app-shell.tsx sends this case to /chat too, but that
  // redirect only fires after `authLoading` resolves -- this avoids a
  // flash of the form in the meantime.
  if (!authLoading && user) return null;

  return (
    <div className="auth">
      <div className="auth__card">
        <div className="auth__brand">
          <div className="rail__mark" style={{ width: 30, height: 30, fontSize: 17 }}>N</div>
          <span className="landing__brand-name">Nex<em>u</em>s</span>
        </div>

        <div className="seg" style={{ marginBottom: 24 }}>
          <button
            type="button"
            className={mode === "login" ? "is-on" : ""}
            onClick={() => { setMode("login"); setError(null); }}
          >
            Log in
          </button>
          <button
            type="button"
            className={mode === "signup" ? "is-on" : ""}
            onClick={() => { setMode("signup"); setError(null); }}
          >
            Sign up
          </button>
        </div>

        <form onSubmit={submit}>
          {mode === "signup" && (
            <div className="field">
              <label className="field__l" htmlFor="display_name">Name</label>
              <input
                id="display_name"
                className="input"
                type="text"
                required
                value={displayName}
                onChange={e => setDisplayName(e.target.value)}
                autoComplete="name"
              />
            </div>
          )}

          <div className="field">
            <label className="field__l" htmlFor="email">Email</label>
            <input
              id="email"
              className="input"
              type="email"
              required
              value={email}
              onChange={e => setEmail(e.target.value)}
              autoComplete="email"
            />
          </div>

          <div className="field">
            <label className="field__l" htmlFor="password">Password</label>
            <input
              id="password"
              className="input"
              type="password"
              required
              minLength={mode === "signup" ? 8 : undefined}
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
            />
          </div>

          {error && (
            <div className="auth__error">
              <Icon name="close" size={13} />
              <span>{error}</span>
            </div>
          )}

          {/* Shown for every login failure alike, on purpose -- wrong
              password and "no such account" get the identical error above
              (see core/nexus_core/routers/auth.py) so this form can't be
              used to check which emails have accounts here. A nudge that
              also appears every time doesn't leak anything a fixed error
              message doesn't already; one that appeared only for unknown
              emails would defeat the whole point. */}
          {error && mode === "login" && (
            <p className="auth__hint">
              New here?{" "}
              <button type="button" onClick={() => { setMode("signup"); setError(null); }}>
                Create an account
              </button>
            </p>
          )}

          <button type="submit" className="btn btn--primary" style={{ width: "100%" }} disabled={submitting}>
            {submitting ? "Please wait…" : mode === "signup" ? "Create account" : "Log in"}
          </button>
        </form>

        {googleEnabled && (
          <>
            <div className="auth__divider"><span>or</span></div>
            <a href={googleLoginUrl()} className="btn" style={{ width: "100%", justifyContent: "center" }}>
              <GoogleG />
              <span>Continue with Google</span>
            </a>
          </>
        )}
      </div>
    </div>
  );
}
