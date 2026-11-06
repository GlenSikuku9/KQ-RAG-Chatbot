import {
  collection,
  onSnapshot,
} from "@firebase/firestore";

import { db } from "./firebase";


export function subscribeToAdminConversationChanges(onChange, onError) {
  if (!db) return () => {};

  let initialSnapshot = true;
  return onSnapshot(
    collection(db, "conversations"),
    () => {
      // The table already performs its initial paginated API request. Ignore
      // Firestore's first snapshot and use later snapshots only as refresh hints.
      if (initialSnapshot) {
        initialSnapshot = false;
        return;
      }
      onChange();
    },
    onError,
  );
}
