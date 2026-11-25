import { useState } from "react";

import { KenyaAirwaysLogo } from "../components/KenyaAirwaysLogo";
import {
  authenticateWithEmail,
  authenticateWithGoogle,
  authenticationAvailable,
  authenticationError,
} from "./authentication";
import "../passenger/styles/AuthScreen.css";

function GoogleIcon() {
  return (
    <svg
      className="google-auth-icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.4-.18-2.07H12v3.92h5.38a4.6 4.6 0 0 1-2 3.02v2.55h3.24c1.9-1.75 2.98-4.33 2.98-7.42Z" />
      <path fill="#34A853" d="M12 22c2.7 0 4.97-.9 6.62-2.35l-3.24-2.55c-.9.6-2.05.96-3.38.96-2.6 0-4.81-1.76-5.6-4.13H3.05v2.63A10 10 0 0 0 12 22Z" />
      <path fill="#FBBC05" d="M6.4 13.93A6.02 6.02 0 0 1 6.08 12c0-.67.12-1.32.32-1.93V7.44H3.05A10 10 0 0 0 2 12c0 1.61.38 3.14 1.05 4.56l3.35-2.63Z" />
      <path fill="#EA4335" d="M12 5.94c1.47 0 2.8.5 3.84 1.5l2.85-2.85A9.58 9.58 0 0 0 12 2a10 10 0 0 0-8.95 5.44l3.35 2.63C7.19 7.7 9.4 5.94 12 5.94Z" />
    </svg>
  );
}

export function AuthScreen({ initialScreen = "sign-in", onCancel }) {
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

  const nameValid = (
    firstName.trim().length > 0
    && surname.trim().length > 0
  );

  const emailValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());

  function displayAuthenticationError(reason, fallbackMessage) {
    const message = authenticationError(reason, fallbackMessage);

    if (message) {
      setError(message);
    }
  }

  async function handleGoogleAuthentication() {
    if (!authenticationAvailable()) {
      setError("Authentication is not configured. Contact the administrator.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      await authenticateWithGoogle();
    } catch (reason) {
      displayAuthenticationError(
        reason,
        "We could not sign you in with Google. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function handleEmailAuthentication(event) {
    event.preventDefault();
    setAttemptedSubmit(true);

    if (!authenticationAvailable()) {
      setError("Authentication is not configured. Contact the administrator.");
      return;
    }

    if (
      !emailValid
      || !passwordValid
      || (screen === "register" && !nameValid)
    ) {
      setError("Please correct the highlighted fields before continuing.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      await authenticateWithEmail({
        email,
        firstName,
        password,
        screen,
        surname,
      });
    } catch (reason) {
      displayAuthenticationError(
        reason,
        "We could not complete your account request. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  function toggleScreen() {
    setScreen((currentScreen) => (
      currentScreen === "sign-in" ? "register" : "sign-in"
    ));
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
          <button
            className="auth-close"
            type="button"
            onClick={onCancel}
            aria-label="Close authentication"
          >
            ×
          </button>
        )}

        <div className="auth-card-topline">
          <p className="wordmark">
            <span>KQ</span> SUPPORT
          </p>
        </div>

        <h2 id="auth-heading">
          {screen === "sign-in" ? "Welcome back" : "Create your account"}
        </h2>

        <p className="muted">
          {screen === "sign-in"
            ? "Sign in to continue your conversations."
            : "Create an account to save your conversations and access your chat history anytime."}
        </p>

        <button
          className="google-auth-button"
          type="button"
          onClick={handleGoogleAuthentication}
          disabled={submitting}
          aria-busy={submitting}
        >
          <GoogleIcon />
          <span>{submitting ? "Connecting..." : "Continue with Google"}</span>
        </button>

        <div className="auth-divider" aria-hidden="true">
          <span>or continue with email</span>
        </div>

        <form onSubmit={handleEmailAuthentication}>
          {screen === "register" && (
            <>
              <label>
                First name
                <input
                  className={attemptedSubmit && !firstName.trim() ? "invalid" : ""}
                  type="text"
                  value={firstName}
                  onChange={(event) => setFirstName(event.target.value)}
                  autoComplete="given-name"
                  aria-invalid={attemptedSubmit && !firstName.trim()}
                  required
                />
                {attemptedSubmit && !firstName.trim() && (
                  <small className="field-error">Enter your first name.</small>
                )}
              </label>

              <label>
                Surname
                <input
                  className={attemptedSubmit && !surname.trim() ? "invalid" : ""}
                  type="text"
                  value={surname}
                  onChange={(event) => setSurname(event.target.value)}
                  autoComplete="family-name"
                  aria-invalid={attemptedSubmit && !surname.trim()}
                  required
                />
                {attemptedSubmit && !surname.trim() && (
                  <small className="field-error">Enter your surname.</small>
                )}
              </label>
            </>
          )}

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
              <button
                type="button"
                onClick={() => setShowPassword((current) => !current)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </span>
          </label>

          {screen === "register" && (
            <div className="password-requirements" aria-live="polite">
              <p>Your password must include:</p>
              <ul>
                <li className={passwordChecks.length ? "met" : ""}>
                  At least 8 characters
                </li>
                <li className={passwordChecks.lowercase ? "met" : ""}>
                  A lowercase letter
                </li>
                <li className={passwordChecks.uppercase ? "met" : ""}>
                  An uppercase letter
                </li>
                <li className={passwordChecks.number ? "met" : ""}>
                  A number
                </li>
              </ul>
            </div>
          )}

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

        <p className="auth-footnote">
          By continuing, you agree to use KQ Support for travel information and assistance.
        </p>
      </section>
    </main>
  );
}
