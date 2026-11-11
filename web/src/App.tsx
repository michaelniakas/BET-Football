import { useEffect, useMemo, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { api, useApi, type Schemas } from "./api";
import { formatter } from "./format";
import { LANGS, translator, type Key, type Lang } from "./i18n";
import { Coupon } from "./pages/Coupon";
import { Explained } from "./pages/Explained";
import { Picks } from "./pages/Picks";
import { Results } from "./pages/Results";
import { AppCtx, Choice, ErrorNote, GlossPopovers, Note, reducedMotion, useMedia, type Rule } from "./ui";

// No router: the page and language live in the query string (?page=results&lang=el).
const PAGES = ["picks", "results", "coupon", "learn"] as const;
type Page = (typeof PAGES)[number];
const OLD_PAGES: Partial<Record<string, Page>> = { paper: "picks", history: "results", optimizer: "coupon" };
const NAV: Record<Page, Key> = { picks: "navPicks", results: "navResults", coupon: "navCoupon", learn: "navLearn" };
const ICON: Record<Page, ReactNode> = {
  picks: <path d="M13 2L4 14h7l-1 8 9-12h-7z" />,
  results: <path d="M4 19V5M4 19h16M8 15l4-4 3 3 5-6" />,
  coupon: (
    <>
      <path d="M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4z" />
      <path d="M14 5v12" strokeDasharray="2 2.5" />
    </>
  ),
  learn: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17.5v.01" />
    </>
  ),
};

type Theme = "light" | "dark";

function fromUrl(): { page: Page; lang: Lang } {
  const q = new URLSearchParams(window.location.search);
  const asked = q.get("page") ?? "";
  const page = PAGES.find((p) => p === asked) ?? OLD_PAGES[asked] ?? "picks";
  const lang = LANGS.find((l) => l === q.get("lang")) ?? (navigator.language.startsWith("el") ? "el" : "en");
  return { page, lang };
}

function storedTheme(): Theme | null {
  try {
    const v = localStorage.getItem("theme");
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;                                      // storage blocked: follow the system
  }
}

/** Runs a state change as a view transition (cross-fade) where the browser has them and motion is welcome. */
function transition(update: () => void) {
  if (!("startViewTransition" in document) || reducedMotion()) update();
  else document.startViewTransition(() => flushSync(update));
}

function defaultRule(s: Schemas["Strategy"]): Rule {
  return { edge: s.default_min_edge, book: s.books.includes("avg") ? "avg" : (s.books[0] ?? "avg") };
}

export function App() {
  const [{ page, lang }, setNav] = useState(fromUrl);
  const [theme, setTheme] = useState<Theme | null>(storedTheme);
  const systemDark = useMedia("(prefers-color-scheme: dark)");
  const dark = theme ? theme === "dark" : systemDark;
  const [strategyId, setStrategyId] = useState<Schemas["StrategyId"] | null>(null);
  const [rules, setRules] = useState<Partial<Record<Schemas["StrategyId"], Rule>>>({});
  const catalog = useApi((signal) => api.GET("/api/v1/catalog", { signal }), []);
  const t = useMemo(() => translator(lang), [lang]);
  const f = useMemo(() => formatter(lang), [lang]);

  useEffect(() => {
    const back = () => setNav(fromUrl());
    window.addEventListener("popstate", back);
    return () => window.removeEventListener("popstate", back);
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = `${t(NAV[page])} · ${t("appTitle")}`;
  }, [lang, page, t]);

  const go = (next: { page?: Page; lang?: Lang }) => {
    const n = { page, lang, ...next };
    window.history.pushState(null, "", `?page=${n.page}&lang=${n.lang}`);
    if (n.page === page) setNav(n);
    else {
      transition(() => setNav(n));
      window.scrollTo({ top: 0 });
    }
  };
  const toggleTheme = () => {
    const next: Theme = dark ? "light" : "dark";
    try {
      localStorage.setItem("theme", next);
    } catch {
      // storage blocked: the choice lasts for this visit
    }
    transition(() => {
      document.documentElement.dataset.theme = next;
      setTheme(next);
    });
  };

  const strategies = catalog.data?.strategies ?? [];
  const s = strategies.find((x) => x.id === strategyId) ?? strategies.find((x) => x.id === "1x2") ?? strategies[0];
  const ctx = catalog.data && s && {
    t, f, lang, dark, catalog: catalog.data, s,
    setStrategy: setStrategyId,
    rule: rules[s.id] ?? defaultRule(s),
    setRule: (r: Rule) => setRules((all) => ({ ...all, [s.id]: r })),
  };

  const brand = (
    <div className="brand">
      <svg viewBox="0 0 32 32" aria-hidden="true">
        <rect width="32" height="32" rx="9" fill="var(--accent)" />
        <circle cx="16" cy="16" r="8.5" fill="none" stroke="var(--accent-ink)" strokeWidth="2" />
        <path d="M16 11.5l3.6 2.6-1.4 4.2h-4.4l-1.4-4.2z" fill="var(--accent-ink)" />
      </svg>
      Bet<b>Football</b>
    </div>
  );
  const themeButton = (withLabel: boolean) => (
    <button type="button" className="theme-btn" aria-pressed={dark} aria-label={withLabel ? undefined : t("darkTheme")}
            onClick={toggleTheme}>
      <span className="knob" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          {dark ? <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
            : <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>}
        </svg>
      </span>
      {withLabel && t("darkTheme")}
    </button>
  );
  const langSwitch = (
    <Choice label={t("language")} value={lang} onChange={(l) => go({ lang: l })}
            options={LANGS.map((l) => ({ value: l, label: <span lang={l}>{l === "el" ? "ΕΛ" : "EN"}</span> }))} />
  );

  return (
    <div className="shell">
      <nav className="rail" aria-label={t("mainNav")}>
        {brand}
        {PAGES.map((p) => (
          <a key={p} href={`?page=${p}&lang=${lang}`} aria-current={p === page ? "page" : undefined}
             onClick={(e) => { e.preventDefault(); go({ page: p }); }}>
            <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICON[p]}</svg>
            {t(NAV[p])}
          </a>
        ))}
        <div className="rail-foot">
          {themeButton(true)}
          {langSwitch}
        </div>
      </nav>

      <main>
        <div className="mobile-top">
          {brand}
          <div className="row">{langSwitch}{themeButton(false)}</div>
        </div>
        {catalog.error ? (
          <div className="page"><ErrorNote error={catalog.error} t={t} /></div>
        ) : !catalog.data ? (
          <div className="page"><div className="skeleton" aria-busy="true" style={{ minHeight: 320 }} /></div>
        ) : !ctx ? (
          <div className="page"><Note>{t("noStrategies")}</Note></div>
        ) : (
          <AppCtx.Provider value={ctx}>
            <div key={page} className="page anim">
              {page === "picks" ? <Picks /> : page === "results" ? <Results /> : page === "coupon" ? <Coupon /> : <Explained />}
            </div>
            <GlossPopovers />
          </AppCtx.Provider>
        )}
      </main>
    </div>
  );
}
