import { at, GLOSS, GlossBody, useCtx } from "../ui";

export function Explained() {
  const { t } = useCtx();
  return (
    <>
      <div className="section-head" style={at(0)}>
        <div>
          <span className="eyebrow">{t("glossEyebrow")}</span>
          <h1 style={{ marginTop: 6 }}>{t("glossTitle")}</h1>
          <p className="muted">{t("glossLede")}</p>
        </div>
      </div>
      <div className="gloss" style={at(1)}>
        {GLOSS.map((g, i) => (
          <article key={g.id} id={g.id} className="card" style={at(i)} aria-labelledby={`${g.id}-h`}>
            <h3 id={`${g.id}-h`}>{t(g.title)}</h3>
            <GlossBody g={g} />
          </article>
        ))}
      </div>
    </>
  );
}
