import { useEffect, useState } from "react";
import {
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signOut,
  updateProfile,
} from "firebase/auth";

import { getPassengerTheme, syncProfile } from "./api";
import { PassengerChat } from "./components/PassengerChat";
import { PassengerLanding } from "./components/PassengerLanding";
import { KenyaAirwaysLogo } from "./components/KenyaAirwaysLogo";
import { AdminDashboard } from "./admin/AdminDashboard";
import { auth } from "./firebase";
import "./AuthScreen.css";

function AuthScreen({ initialScreen = "sign-in", onAuthenticated, onCancel }) {
  const [screen, setScreen] = useState(initialScreen);
  const [email, setEmail] = useState("");
  const [firstName, setFirstName] = useState("");
  const [surname, setSurname] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [attemptedSubmit, setAttemptedSubmit] = useState(false);

  const passwordChecks = {
    length: password.length >= 8,
    lowercase: /[a-z]/.test(password),
    uppercase: /[A-Z]/.test(password),
    number: /\d/.test(password),
  };
  const passwordValid = screen === "sign-in"
    ? password.length >= 6
    : Object.values(passwordChecks).every(Boolean);
  const nameValid = firstName.trim().length > 0 && surname.trim().length > 0;
  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());

  async function submit(event) {
    event.preventDefault();
    setAttemptedSubmit(true);

    if (!auth) {
      return;
    }

    if (!emailValid || !passwordValid || (screen === "register" && !nameValid)) {
      setError("Please correct the highlighted fields before continuing.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      // The backend derives roles from verified claims; this form never chooses a role.
      const credential = screen === "register"
        ? await createUserWithEmailAndPassword(auth, email, password)
        : await signInWithEmailAndPassword(auth, email, password);

      if (screen === "register") {
        // Firebase displayName is copied into the trusted backend profile at sync time.
        await updateProfile(credential.user, {
          displayName: `${firstName.trim()} ${surname.trim()}`.trim(),
        });
      }

      await onAuthenticated(credential.user);
    } catch (reason) {
      const code = reason?.code;
      const friendlyErrors = {
        "auth/email-already-in-use": "An account already exists for this email address. Sign in instead.",
        "auth/invalid-credential": "The email address or password is incorrect.",
        "auth/invalid-email": "Enter a valid email address.",
        "auth/network-request-failed": "The registration service could not be reached. Check your connection and try again.",
        "auth/too-many-requests": "Too many attempts were made. Wait a moment and try again.",
        "auth/weak-password": "Choose a stronger password that meets every requirement below.",
      };
      setError(friendlyErrors[code] || "We could not complete your account request. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  function toggleScreen() {
    setScreen((currentScreen) =>
      currentScreen === "sign-in" ? "register" : "sign-in",
    );
    setError(null);
    setAttemptedSubmit(false);
    setShowPassword(false);
  }

  return (
    <main className="auth-layout">
      <section className="brand-panel">
        <KenyaAirwaysLogo className="auth-airline-logo" />
        <p className="eyebrow">CUSTOMER CARE</p>
        <h1>Your journey, supported.</h1>
        <p>
          Get clear answers about bookings, baggage, check-in, refunds, and more.
        </p>
      </section>

      <section className="auth-card" aria-labelledby="auth-heading">
        {onCancel && (
          <button className="auth-close" type="button" onClick={onCancel} aria-label="Close authentication">
            ×
          </button>
        )}
        <div className="auth-card-topline">
          <p className="wordmark"><span>KQ</span> SUPPORT</p>
        </div>
        <h2 id="auth-heading">
          {screen === "sign-in" ? "Welcome back" : "Create your account"}
        </h2>
        <p className="muted">
          {screen === "sign-in"
            ? "Sign in to continue your conversations."
            : "Create an account to save your conversations and access your chat history anytime."}
        </p>

        <form onSubmit={submit}>
          {screen === "register" && <>
            <label>First name<input className={attemptedSubmit && !firstName.trim() ? "invalid" : ""} type="text" value={firstName} onChange={(event) => setFirstName(event.target.value)} autoComplete="given-name" aria-invalid={attemptedSubmit && !firstName.trim()} required />{attemptedSubmit && !firstName.trim() && <small className="field-error">Enter your first name.</small>}</label>
            <label>Surname<input className={attemptedSubmit && !surname.trim() ? "invalid" : ""} type="text" value={surname} onChange={(event) => setSurname(event.target.value)} autoComplete="family-name" aria-invalid={attemptedSubmit && !surname.trim()} required />{attemptedSubmit && !surname.trim() && <small className="field-error">Enter your surname.</small>}</label>
          </>}
          <label>
            Email
            <input
              className={attemptedSubmit && !emailValid ? "invalid" : ""}
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              aria-invalid={attemptedSubmit && !emailValid}
              required
            />
            {attemptedSubmit && !emailValid && (
              <small className="field-error">Enter a valid email address.</small>
            )}
          </label>

          <label>
            Password
            <span className="password-field">
              <input
                className={attemptedSubmit && !passwordValid ? "invalid" : ""}
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete={screen === "sign-in" ? "current-password" : "new-password"}
                minLength={screen === "register" ? 8 : 6}
                aria-invalid={attemptedSubmit && !passwordValid}
                required
              />
              <button type="button" onClick={() => setShowPassword((current) => !current)} aria-label={showPassword ? "Hide password" : "Show password"}>{showPassword ? "Hide" : "Show"}</button>
            </span>
          </label>
          {screen === "register" && <div className="password-requirements" aria-live="polite"><p>Your password must include:</p><ul><li className={passwordChecks.length ? "met" : ""}>At least 8 characters</li><li className={passwordChecks.lowercase ? "met" : ""}>A lowercase letter</li><li className={passwordChecks.uppercase ? "met" : ""}>An uppercase letter</li><li className={passwordChecks.number ? "met" : ""}>A number</li></ul></div>}

          {error && (
            <p className="notice error" role="alert">
              {error}
            </p>
          )}

          <button
            className="button primary auth-submit"
            disabled={submitting}
            aria-busy={submitting}
          >
            {submitting
              ? screen === "sign-in"
                ? "Signing in..."
                : "Creating account..."
              : screen === "sign-in"
                ? "Sign in"
                : "Create account"}
          </button>
        </form>

        <button className="link-button" type="button" onClick={toggleScreen}>
          {screen === "sign-in"
            ? "New here? Create a passenger account"
            : "Already registered? Sign in"}
        </button>
        <p className="auth-footnote">By continuing, you agree to use KQ Support for travel information and assistance.</p>
      </section>
    </main>
  );
}

function AppShell({ profile, user, onSignOut, onRequestAuth }) {
  const isAdmin = profile?.role === "admin";
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMounted, setChatMounted] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [suggestedQuestion, setSuggestedQuestion] = useState("");

  function openChat(question = "") {
    setSuggestedQuestion(question);
    setChatMounted(true);
    setChatOpen(true);
  }

  return (
    <div className={isAdmin ? "app-shell admin-app" : "app-shell passenger-app"}>
      <header className="topbar">
        <a className="wordmark passenger-airline-brand" href="#support-home" aria-label="Kenya Airways Support home">
          <KenyaAirwaysLogo variant="colour" />
        </a>

        <nav aria-label="Primary navigation">
          {!isAdmin && <a href="#support-home">Customer Support</a>}
          {!isAdmin && <a href="#faq-heading">FAQs</a>}
          {isAdmin && <a href="#admin">Admin dashboard</a>}
        </nav>

        <div className="account">
          {user ? (
            <>
              <span>{profile?.name || profile?.email || "Passenger"}</span>
              <button className="link-button" type="button" onClick={onSignOut}>Sign out</button>
            </>
          ) : (
            <button className="button account-login" type="button" onClick={() => onRequestAuth("sign-in")}>
              Log in
            </button>
          )}
        </div>
      </header>

      <div className={isAdmin ? "dashboard admin-dashboard-page" : "public-support-page"} id="home">
        {!isAdmin ? (
          <>
            <PassengerLanding
              onOpenChat={() => openChat()}
              onAskQuestion={(question) => openChat(question)}
            />
            {!chatOpen && (
              <button className="chat-launcher" type="button" onClick={() => openChat()} aria-label="Open KQ Support chat">
                {/* Crop the official white artwork to its circular airline mark. */}
                <span className="launcher-logo-crop" aria-hidden="true">
                  <KenyaAirwaysLogo className="launcher-airline-logo" />
                </span>
                <span>Chat with KQ Support</span>
              </button>
            )}
            {chatMounted && (
              <div className={`chat-window-wrap ${drawerOpen ? "drawer-open" : ""} ${chatOpen ? "" : "chat-minimized"}`}>
                <PassengerChat
                  user={user}
                  initialQuestion={suggestedQuestion}
                  onClose={() => setChatOpen(false)}
                  onMinimize={() => setChatOpen(false)}
                  onRequestAuth={onRequestAuth}
                  onDrawerToggle={setDrawerOpen}
                />
              </div>
            )}
          </>
        ) : (
          <AdminDashboard
            user={user}
            profile={profile}
            onSignOut={onSignOut}
          />
        )}
      </div>
    </div>
  );
}

export function App() {
  const [user, setUser] = useState(null);
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [authScreen, setAuthScreen] = useState(null);

  useEffect(() => {
    // Passenger colours are published configuration, not admin-dashboard styling.
    getPassengerTheme().then((theme) => {
      const root = document.documentElement;
      Object.entries(theme).forEach(([key, value]) => root.style.setProperty(`--passenger-${key.replaceAll("_", "-")}`, value));
    }).catch(() => {
      // Built-in CSS defaults keep the chatbot usable if configuration is unavailable.
    });
  }, []);

  async function establishProfile(nextUser) {
    setError(null);
    try {
      setProfile(await syncProfile(nextUser));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not load your profile. The backend may be misconfigured.",
      );
    }
  }

  useEffect(() => {
    if (!auth) {
      setLoading(false);

      return undefined;
    }

    // Firebase is the single source of session truth, including expiry and sign-out.
    return onAuthStateChanged(auth, async (nextUser) => {
      setUser(nextUser);
      setProfile(null);
      setLoading(false);

      if (nextUser) {
        try {
          await establishProfile(nextUser);
        } catch (reason) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load your profile.",
          );
        }
      }
    });
  }, []);

  if (loading) {
    return (
      <main className="setup-notice" aria-live="polite">
        Loading your secure session…
      </main>
    );
  }

  if (user && error) {
    return (
      <main className="setup-notice">
        <h1>We could not load your profile</h1>
        <p className="notice error">{error}</p>
        <button className="button primary" onClick={() => establishProfile(user)}>
          Try again
        </button>
        <button className="link-button" type="button" onClick={() => signOut(auth)}>
          Sign out
        </button>
      </main>
    );
  }

  if (user && !profile) {
    return <main className="setup-notice">Loading profile...</main>;
  }

  return (
    <>
      <AppShell
        profile={profile}
        user={user}
        onSignOut={() => signOut(auth)}
        onRequestAuth={setAuthScreen}
      />
      {!user && authScreen && (
        <div className="auth-modal" role="dialog" aria-modal="true" aria-label="Passenger account access">
          <AuthScreen
            initialScreen={authScreen}
            onAuthenticated={establishProfile}
            onCancel={() => setAuthScreen(null)}
          />
        </div>
      )}
    </>
  );
}
