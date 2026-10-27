import { useEffect, useState } from "react";
import { createUserWithEmailAndPassword, onAuthStateChanged, signInWithEmailAndPassword, signOut } from "firebase/auth";

import { auth, firebaseConfigured } from "./firebase";
import { syncProfile } from "./api";

function AuthScreen({ onAuthenticated }) {
  const [screen, setScreen] = useState("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (!auth) return;
    setSubmitting(true); setError(null);
    try {
      // Public registration creates only a Firebase account. The backend derives the
      // passenger role from verified claims; a browser never gets to choose a role.
      const credential = screen === "register"
        ? await createUserWithEmailAndPassword(auth, email, password)
        : await signInWithEmailAndPassword(auth, email, password);
      await onAuthenticated(credential.user);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to sign in. Please try again.");
    } finally { setSubmitting(false); }
  }

  return <main className="auth-layout"><section className="brand-panel"><p className="eyebrow">KENYA AIRWAYS</p><h1>Support that travels with you.</h1><p>Find policy information for bookings, baggage, check-in, refunds, and more.</p></section>
    <section className="auth-card" aria-labelledby="auth-heading"><p className="eyebrow">KQ SUPPORT</p><h2 id="auth-heading">{screen === "sign-in" ? "Welcome back" : "Create your account"}</h2><p className="muted">{screen === "sign-in" ? "Sign in to continue your conversations." : "Register as a passenger to use the support assistant."}</p>
      <form onSubmit={submit}><label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required /></label><label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={screen === "sign-in" ? "current-password" : "new-password"} minLength={6} required /></label>
        {error && <p className="notice error" role="alert">{error}</p>}<button className="button primary" disabled={submitting}>{submitting ? "Please wait…" : screen === "sign-in" ? "Sign in" : "Create account"}</button></form>
      <button className="link-button" onClick={() => { setScreen(screen === "sign-in" ? "register" : "sign-in"); setError(null); }}>{screen === "sign-in" ? "New here? Create a passenger account" : "Already registered? Sign in"}</button>
    </section></main>;
}

function AppShell({ profile, onSignOut }) {
  const isAdmin = profile.role === "admin";
  return <div className="app-shell"><header className="topbar"><a className="wordmark" href="#home" aria-label="KQ Support home"><span>KQ</span> SUPPORT</a><nav aria-label="Primary navigation"><a href="#home">Home</a><a href={isAdmin ? "#admin" : "#chat"}>{isAdmin ? "Admin dashboard" : "Support chat"}</a></nav><div className="account"><span>{profile.name || profile.email || "Passenger"}</span><button className="link-button" onClick={onSignOut}>Sign out</button></div></header>
    <main className="dashboard" id="home"><section className="hero"><p className="eyebrow">{isAdmin ? "ADMINISTRATION" : "KENYA AIRWAYS CUSTOMER SUPPORT"}</p><h1>{isAdmin ? "Manage the support experience" : "How can we help you today?"}</h1><p>{isAdmin ? "Administrative tools will appear here as their protected features are implemented." : "Ask about Kenya Airways policies in English, Kiswahili, or a mix of both."}</p>{!isAdmin && <a className="button primary" href="#chat">Start a conversation</a>}</section>
      <section className="feature-grid" aria-label="Available areas"><article><h2>Secure account</h2><p>Your Firebase session is sent to the backend only as an authenticated ID token.</p></article><article><h2>Grounded answers</h2><p>Responses are designed to cite Kenya Airways policy information.</p></article><article><h2>{isAdmin ? "Protected tools" : "Conversation history"}</h2><p>{isAdmin ? "Only verified administrator claims unlock administrative routes." : "Your saved conversations will appear here in the chat experience."}</p></article></section>
      <section className="coming-soon" id={isAdmin ? "admin" : "chat"}><h2>{isAdmin ? "Administrator dashboard" : "Passenger chat"}</h2><p>{isAdmin ? "Document, user, analytics, theme, and model-management screens are delivered in later issues." : "The dedicated chat screen and conversation history are delivered in I18 and I19."}</p></section>
    </main></div>;
}

export function App() {
  const [user, setUser] = useState(null);
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  async function establishProfile(nextUser) { setError(null); setProfile(await syncProfile(nextUser)); }
  useEffect(() => {
    if (!auth) { setLoading(false); return undefined; }
    // Firebase is the single source of session truth, including expiry and sign-out.
    return onAuthStateChanged(auth, async (nextUser) => {
      setUser(nextUser); setProfile(null); setLoading(false);
      if (nextUser) { try { await establishProfile(nextUser); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load your profile."); } }
    });
  }, []);
  if (!firebaseConfigured) return <main className="setup-notice"><h1>Configure Firebase to start KQ Support</h1><p>Copy <code>frontend/.env.example</code> to <code>frontend/.env</code> and add the public Firebase web configuration values.</p></main>;
  if (loading) return <main className="setup-notice" aria-live="polite">Loading your secure session…</main>;
  if (!user) return <AuthScreen onAuthenticated={establishProfile} />;
  if (error) return <main className="setup-notice"><h1>We could not load your profile</h1><p className="notice error">{error}</p><button className="button primary" onClick={() => establishProfile(user)}>Try again</button><button className="link-button" onClick={() => signOut(auth)}>Sign out</button></main>;
  return profile ? <AppShell profile={profile} onSignOut={() => signOut(auth)} /> : <main className="setup-notice">Loading profile…</main>;
}
