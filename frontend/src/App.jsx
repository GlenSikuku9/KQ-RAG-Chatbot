import { useEffect, useState } from "react";
import { MessageCircleMore } from "lucide-react";

import { getPassengerTheme, syncProfile } from "./api";
import { AuthScreen } from "./auth/AuthScreen";
import {
  authenticationAvailable,
  observeAuthentication,
  signOutCurrentUser,
} from "./auth/authentication";
import { KenyaAirwaysLogo } from "./components/KenyaAirwaysLogo";
import { AdminDashboard } from "./admin/AdminDashboard";
import { PassengerChat } from "./passenger/pages/PassengerChat";
import { PassengerLanding } from "./passenger/pages/PassengerLanding";

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
                <MessageCircleMore className="launcher-chat-icon" aria-hidden="true" />
                <span className="launcher-hover-content" aria-hidden="true">
                  <span>Chat with KQ Support</span>
                </span>
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

function InitialLoadingScreen({ desktopText }) {
  return (
    <main className="setup-notice initial-loading-screen" aria-live="polite" aria-busy="true">
      <span className="initial-loading-desktop">{desktopText}</span>
      <div className="initial-loading-mobile">
        <div className="initial-loading-bubbles" aria-hidden="true">
          <i />
          <i />
          <i />
          <i />
          <i />
        </div>
        <strong>In progress</strong>
        <span>Please wait.</span>
      </div>
    </main>
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
    if (!authenticationAvailable()) {
      setLoading(false);

      return undefined;
    }

    // Firebase is the single source of session truth, including expiry and sign-out.
    return observeAuthentication(async (nextUser) => {
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
    return <InitialLoadingScreen desktopText="Loading your secure session…" />;
  }

  if (user && error) {
    return (
      <main className="setup-notice">
        <h1>We could not load your profile</h1>
        <p className="notice error">{error}</p>
        <button className="button primary" onClick={() => establishProfile(user)}>
          Try again
        </button>
        <button className="link-button" type="button" onClick={signOutCurrentUser}>
          Sign out
        </button>
      </main>
    );
  }

  if (user && !profile) {
    return <InitialLoadingScreen desktopText="Loading profile..." />;
  }

  return (
    <>
      <AppShell
        profile={profile}
        user={user}
        onSignOut={signOutCurrentUser}
        onRequestAuth={setAuthScreen}
      />
      {!user && authScreen && (
        <div className="auth-modal" role="dialog" aria-modal="true" aria-label="Passenger account access">
          <AuthScreen
            initialScreen={authScreen}
            onCancel={() => setAuthScreen(null)}
          />
        </div>
      )}
    </>
  );
}
