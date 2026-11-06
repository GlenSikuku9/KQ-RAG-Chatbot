import { useEffect, useRef, useState } from "react";

const tablePageCache = new Map();

function useAdminTablePage(user, namespace, loader, filters, revision) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const dataRef = useRef(data);
  const filterKey = JSON.stringify(filters);

  dataRef.current = data;

  useEffect(() => {
    const controller = new AbortController();
    const cacheKey = `${namespace}:${filterKey}:${revision}`;
    const cached = tablePageCache.get(cacheKey);
    if (cached && Date.now() - cached.savedAt < 30_000) {
      setData(cached.data);
      setLoading(false);
      setRefreshing(false);
      setError(null);
      return () => controller.abort();
    }
    // Keep an existing page visible during sidebar and real-time refreshes.
    if (dataRef.current) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);
    const timer = window.setTimeout(
      async () => {
        try {
          const result = await loader(
            user,
            JSON.parse(filterKey),
            controller.signal,
          );
          tablePageCache.set(cacheKey, { data: result, savedAt: Date.now() });
          setData(result);
        } catch (reason) {
          if (!controller.signal.aborted) setError(reason.message);
        } finally {
          if (!controller.signal.aborted) {
            setLoading(false);
            setRefreshing(false);
          }
        }
      },
      filters.search ? 300 : 0,
    );
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [user, namespace, loader, filterKey, revision, filters.search]);

  return {
    data,
    setData,
    loading,
    refreshing,
    error,
  };
}
export { tablePageCache, useAdminTablePage };
