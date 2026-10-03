// Display helpers. Every time shown in the UI is Colombo time, like the agent's business rules.

const TZ = "Asia/Colombo";

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: TZ,
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(
    new Date(iso),
  );
}

export function formatLkr(amount: number | null | undefined): string {
  if (amount == null) return "—";
  return `LKR ${amount.toLocaleString("en-US")}`;
}

export function formatPercent(rate: number | null | undefined): string {
  return rate == null ? "—" : `${Math.round(rate * 1000) / 10}%`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${Math.round(seconds % 60)} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

export function secondsBetween(start: string | null, end: string | null): number | null {
  if (!start || !end) return null;
  return (new Date(end).getTime() - new Date(start).getTime()) / 1000;
}

export function humanize(value: string | null | undefined): string {
  return value ? value.replaceAll("_", " ") : "—";
}

/** Today's date in Colombo (YYYY-MM-DD), moved to Monday on a Sunday: there are no Sunday shifts. */
export function demoDate(now: Date = new Date()): string {
  const weekday = new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short" }).format(now);
  const day = weekday === "Sun" ? new Date(now.getTime() + 24 * 3600 * 1000) : now;
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(day); // en-CA formats as YYYY-MM-DD
}

export function demoClock(date: string): string {
  return `${date}T09:00:00+05:30`;
}
