import { useEffect, useMemo, useState } from "react";
import { api, useApi } from "./api";
import { formatter } from "./format";
import { LANGS, translator, type Key, type Lang } from "./i18n";
import { History } from "./pages/History";
import { Optimizer } from "./pages/Optimizer";
import { PaperTrading } from "./pages/PaperTrading";
import { AppCtx, ErrorNote } from "./ui";

// No router: the page and language live in the query string (?page=optimizer&lang=el).
const PAGES = ["paper", "history", "optimizer"] as const;
type Page = (typeof PAGES)[number];
const NAV: Record<Page, Key> = { paper: "navPaper", history: "navHistory", optimizer: "navOptimizer" };

function fromUrl(): { page: Page; lang: Lang } {
  const q = new URLSearchParams(window.location.search);
  const page = PAGES.find((p) => p === q.get("page")) ?? "paper";
  const lang = LANGS.find((l) => l === q.get("lang")) ?? (navigator.language.startsWith("el") ? "el" : "en");
  return { page, lang };
}

export function App() {
  const [{ page, lang }, setNav] = useState(fromUrl);
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
    setNav(n);
  };

  return (
    <>
      <header>
        <nav aria-label={t("appTitle")}>
          <strong>{t("appTitle")}</strong>
          {PAGES.map((p) => (
            <a key={p} href={`?page=${p}&lang=${lang}`} aria-current={p === page ? "page" : undefined}
               onClick={(e) => { e.preventDefault(); go({ page: p }); }}>
              {t(NAV[p])}
            </a>
          ))}
        </nav>
        <div role="group" aria-label={t("language")} className="langs">
          {LANGS.map((l) => (
            <button key={l} type="button" aria-pressed={l === lang} onClick={() => go({ lang: l })}>
              {l.toUpperCase()}
            </button>
          ))}
        </div>
      </header>
      <main>
        {catalog.error ? (
          <ErrorNote error={catalog.error} t={t} />
        ) : !catalog.data ? (
          <p aria-busy="true">{t("loading")}</p>
        ) : (
          <AppCtx.Provider value={{ t, f, catalog: catalog.data }}>
            {page === "paper" ? <PaperTrading /> : page === "history" ? <History /> : <Optimizer />}
          </AppCtx.Provider>
        )}
      </main>
    </>
  );
}
