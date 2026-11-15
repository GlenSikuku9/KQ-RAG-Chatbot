export function safeOfficialKenyaAirwaysUrl(value) {
  try {
    const url = new URL(value);
    const officialHost = [
      "kenya-airways.com",
      "www.kenya-airways.com",
    ].includes(url.hostname.toLowerCase());

    return url.protocol === "https:" && officialHost
      ? url.href
      : null;
  } catch {
    return null;
  }
}
