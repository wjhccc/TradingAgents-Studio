// Shared timestamp formatters.
//
// Backend stores timestamps as UTC ISO strings (e.g. "2026-06-04T05:29:10.202133Z").
// Rendering them raw shows the ugly ISO string AND the wrong (UTC) time. These
// helpers parse to a Date and render in the user's local timezone. All are
// null/parse-safe: a falsy or unparseable input returns a dash so a missing
// timestamp never blows up a table cell.

const DASH = '—'

function parse(iso?: string | null): Date | null {
  if (!iso) return null
  const d = new Date(iso)
  return isNaN(d.getTime()) ? null : d
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** Full local date-time: "2026-06-04 13:29:10". For tables / detail views. */
export function formatDateTime(iso?: string | null): string {
  const d = parse(iso)
  if (!d) return iso || DASH
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** Local time only: "13:29:10". For dense timelines where the date is implied. */
export function formatTime(iso?: string | null): string {
  const d = parse(iso)
  if (!d) return iso || DASH
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
