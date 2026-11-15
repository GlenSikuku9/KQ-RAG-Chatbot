import {
  collection,
  limit,
  onSnapshot,
  orderBy,
  query,
} from "@firebase/firestore";

import { db } from "./firebase";


export function subscribeToAdminConversationChanges(onChange, onError) {
  if (!db) return () => {};

  let initialSnapshot = true;
  // Only the newest records are needed as table-refresh signals. Listening to
  // the complete collection charged one read for every conversation at mount.
  const recentConversations = query(
    collection(db, "conversations"),
    orderBy("started_at", "desc"),
    limit(25),
  );
  return onSnapshot(
    recentConversations,
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
