import {
  collection,
  doc,
  onSnapshot,
  query,
  where,
} from "@firebase/firestore";

import { db } from "./firebase";

// Collection-wide support changes are consumed in more than one admin
// component, but Firestore needs only one underlying watch target.
const supportEventSubscribers = new Set();
const supportEventErrorSubscribers = new Set();
const supportRequestListeners = new Map();
let stopSupportEventsListener = null;
let supportEventsTeardownTimer = null;


function serializeFirestoreValue(value) {
  if (value?.toDate) return value.toDate().toISOString();
  if (Array.isArray(value)) return value.map(serializeFirestoreValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        serializeFirestoreValue(item),
      ]),
    );
  }
  return value;
}


export function subscribeToSupportRequest(
  requestId,
  onRequest,
  onMessages,
  onError,
) {
  if (!db || !requestId) return () => {};

  let listener = supportRequestListeners.get(requestId);

  if (!listener) {
    listener = {
      requestSubscribers: new Set(),
      messageSubscribers: new Set(),
      errorSubscribers: new Set(),
      stopRequest: null,
      stopMessages: null,
      teardownTimer: null,
    };
    supportRequestListeners.set(requestId, listener);

    // The backend remains the only writer. One pair of watch targets is shared
    // by every mounted view of this case and survives brief React remounts.
    listener.stopRequest = onSnapshot(
      doc(db, "support_requests", requestId),
      (snapshot) => {
        if (!snapshot.exists()) return;
        const request = serializeFirestoreValue({
          request_id: snapshot.id,
          ...snapshot.data(),
        });
        listener.requestSubscribers.forEach((subscriber) => (
          subscriber(request)
        ));
      },
      (reason) => listener.errorSubscribers.forEach((subscriber) => (
        subscriber(reason)
      )),
    );

    const messageQuery = query(
      collection(db, "support_messages"),
      where("request_id", "==", requestId),
    );
    listener.stopMessages = onSnapshot(
      messageQuery,
      (snapshot) => {
        const messages = snapshot.docs
          .map((item) => serializeFirestoreValue({
            message_id: item.id,
            ...item.data(),
          }))
          .sort((left, right) => left.sequence - right.sequence);
        listener.messageSubscribers.forEach((subscriber) => (
          subscriber(messages)
        ));
      },
      (reason) => listener.errorSubscribers.forEach((subscriber) => (
        subscriber(reason)
      )),
    );
  }

  window.clearTimeout(listener.teardownTimer);
  listener.requestSubscribers.add(onRequest);
  listener.messageSubscribers.add(onMessages);
  if (onError) listener.errorSubscribers.add(onError);

  let active = true;
  return () => {
    if (!active) return;
    active = false;
    listener.requestSubscribers.delete(onRequest);
    listener.messageSubscribers.delete(onMessages);
    if (onError) listener.errorSubscribers.delete(onError);

    if (
      !listener.requestSubscribers.size
      && !listener.messageSubscribers.size
    ) {
      listener.teardownTimer = window.setTimeout(() => {
        if (
          listener.requestSubscribers.size
          || listener.messageSubscribers.size
        ) {
          return;
        }

        listener.stopRequest?.();
        listener.stopMessages?.();
        supportRequestListeners.delete(requestId);
      }, 250);
    }
  };
}


export function subscribeToSupportEvents(onChange, onError) {
  if (!db) return () => {};

  supportEventSubscribers.add(onChange);
  if (onError) supportEventErrorSubscribers.add(onError);
  window.clearTimeout(supportEventsTeardownTimer);

  if (!stopSupportEventsListener) {
    let initialSnapshot = true;

    // The navbar and Human Support page share one watch target. Delayed
    // teardown also prevents React Strict Mode from rapidly recycling the
    // target during its development-only effect verification pass.
    stopSupportEventsListener = onSnapshot(
      collection(db, "support_requests"),
      () => {
        if (initialSnapshot) {
          initialSnapshot = false;
          return;
        }
        supportEventSubscribers.forEach((subscriber) => subscriber());
      },
      (reason) => {
        supportEventErrorSubscribers.forEach((subscriber) => (
          subscriber(reason)
        ));
      },
    );
  }

  let active = true;
  return () => {
    if (!active) return;
    active = false;
    supportEventSubscribers.delete(onChange);
    if (onError) supportEventErrorSubscribers.delete(onError);

    if (!supportEventSubscribers.size) {
      supportEventsTeardownTimer = window.setTimeout(() => {
        if (!supportEventSubscribers.size && stopSupportEventsListener) {
          stopSupportEventsListener();
          stopSupportEventsListener = null;
        }
      }, 250);
    }
  };
}
