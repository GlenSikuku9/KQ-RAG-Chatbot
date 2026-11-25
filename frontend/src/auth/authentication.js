import {
  createUserWithEmailAndPassword,
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signInWithPopup,
  signInWithRedirect,
  signOut,
  updateProfile,
} from "firebase/auth";

import { auth } from "../firebase";

const googleProvider = new GoogleAuthProvider();

googleProvider.setCustomParameters({
  prompt: "select_account",
});

const authenticationErrors = {
  "auth/account-exists-with-different-credential": "An account already exists for this email address. Sign in with your email and password first.",
  "auth/email-already-in-use": "An account already exists for this email address. Sign in instead.",
  "auth/invalid-credential": "The email address or password is incorrect.",
  "auth/invalid-email": "Enter a valid email address.",
  "auth/network-request-failed": "The authentication service could not be reached. Check your connection and try again.",
  "auth/operation-not-allowed": "Google sign-in is not enabled yet. Contact the administrator.",
  "auth/popup-blocked": "Your browser blocked the Google sign-in window. Allow pop-ups and try again.",
  "auth/too-many-requests": "Too many attempts were made. Wait a moment and try again.",
  "auth/unauthorized-domain": "Google sign-in is not enabled for this website domain. Contact the administrator.",
  "auth/weak-password": "Choose a stronger password that meets every requirement below.",
};

export function authenticationAvailable() {
  return Boolean(auth);
}

export function authenticationError(reason, fallbackMessage) {
  const ignoredErrors = new Set([
    "auth/cancelled-popup-request",
    "auth/popup-closed-by-user",
  ]);

  if (ignoredErrors.has(reason?.code)) {
    return null;
  }

  return authenticationErrors[reason?.code] || fallbackMessage;
}

export async function authenticateWithEmail({
  email,
  firstName,
  password,
  screen,
  surname,
}) {
  if (!auth) {
    throw new Error("Authentication is not configured.");
  }

  if (screen === "sign-in") {
    return signInWithEmailAndPassword(auth, email, password);
  }

  const credential = await createUserWithEmailAndPassword(
    auth,
    email,
    password,
  );

  await updateProfile(credential.user, {
    displayName: `${firstName.trim()} ${surname.trim()}`.trim(),
  });

  return credential;
}

export async function authenticateWithGoogle() {
  if (!auth) {
    throw new Error("Authentication is not configured.");
  }

  const mobileViewport = window.matchMedia("(max-width: 760px)").matches;

  if (mobileViewport) {
    return signInWithRedirect(auth, googleProvider);
  }

  try {
    return await signInWithPopup(auth, googleProvider);
  } catch (reason) {
    if (reason?.code !== "auth/popup-blocked") {
      throw reason;
    }

    return signInWithRedirect(auth, googleProvider);
  }
}

export function observeAuthentication(callback) {
  if (!auth) {
    return null;
  }

  return onAuthStateChanged(auth, callback);
}

export function signOutCurrentUser() {
  if (!auth) {
    return Promise.resolve();
  }

  return signOut(auth);
}
