import {
  collection,
  doc,
  onSnapshot,
  query,
  where,
} from "@firebase/firestore";

import { db } from "./firebase";


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

  // The backend remains the only writer. These listeners expose only the
  // owner/administrator reads permitted by firestore.rules.
  const stopRequest = onSnapshot(
    doc(db, "support_requests", requestId),
    (snapshot) => {
      if (snapshot.exists()) {
        onRequest(serializeFirestoreValue({
          request_id: snapshot.id,
          ...snapshot.data(),
        }));
      }
    },
    onError,
  );
  const messageQuery = query(
    collection(db, "support_messages"),
    where("request_id", "==", requestId),
  );
  const stopMessages = onSnapshot(
    messageQuery,
    (snapshot) => {
      const messages = snapshot.docs
        .map((item) => serializeFirestoreValue({
          message_id: item.id,
          ...item.data(),
        }))
        .sort((left, right) => left.sequence - right.sequence);
      onMessages(messages);
    },
    onError,
  );

  return () => {
    stopRequest();
    stopMessages();
  };
}


export function subscribeToSupportEvents(onChange, onError) {
  if (!db) return () => {};

  // Admin security rules restrict this collection listener to administrators.
  return onSnapshot(
    collection(db, "support_requests"),
    () => onChange(),
    onError,
  );
}
