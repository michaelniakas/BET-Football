import type { Lang } from "./i18n";

const LOCALE: Record<Lang, string> = { en: "en-GB", el: "el-GR" };
const DASH = "–";

export interface Fmt {
  /** Odds rounded *up* to 2 decimals: a minimum price shown rounded down would let a bet miss the edge. */
  minOdds: (x: number) => string;
  pct: (x: number | null | undefined, digits?: number, signed?: boolean) => string;
  num: (x: number | null | undefined, digits?: number, signed?: boolean) => string;
  eur: (x: number | null | undefined, signed?: boolean) => string;
  date: (iso: string) => string;
  dateTime: (iso: string) => string;
}

export function formatter(lang: Lang): Fmt {
  const loc = LOCALE[lang];
  const num: Fmt["num"] = (x, digits = 2, signed = false) =>
    x == null
      ? DASH
      : x.toLocaleString(loc, {
          minimumFractionDigits: digits,
          maximumFractionDigits: digits,
          signDisplay: signed ? "exceptZero" : "auto",
        });
  // Match days are plain dates: format in UTC so the browser's timezone never moves them a day.
  const day = new Intl.DateTimeFormat(loc, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
  const stamp = new Intl.DateTimeFormat(loc, { dateStyle: "medium", timeStyle: "short" });
  return {
    num,
    minOdds: (x) => num(Math.ceil(x * 100 - 1e-9) / 100),
    pct: (x, digits = 1, signed = false) =>
      x == null
        ? DASH
        : x.toLocaleString(loc, {
            style: "percent",
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
            signDisplay: signed ? "exceptZero" : "auto",
          }),
    eur: (x, signed = false) => (x == null ? DASH : `${num(x, 2, signed)} €`),
    date: (iso) => day.format(new Date(`${iso}T00:00:00Z`)),
    dateTime: (iso) => stamp.format(new Date(iso)),
  };
}
