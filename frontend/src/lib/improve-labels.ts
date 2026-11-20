// Founder-facing labels for the website fields the Improvement Studio can change.
// The API keeps sending internal paths like "faq.2.answer"; they are for the backend only and
// must never be shown. Mapping follows the real content schema (backend/app/pipeline/improve.py
// `editable_fields`): pricing bullets are generic plan bullet points, so they are numbered
// rather than given invented meanings.

const ordinal = (i: number) => String(i + 1);

const SIMPLE: Record<string, string> = {
  "hero.headline": "Main headline",
  "hero.subhead": "Headline supporting text",
  "hero.button_label": "Main button text",
  "proof.founder_story": "Founder story",
  "proof.process": "How you work",
  "proof.guarantees": "Guarantee",
  "pricing.0.name": "Pricing plan name",
  "cta.headline": "Sign-up headline",
  "cta.button_label": "Sign-up button text",
};

// [pattern, label builder]. Patterns match the path with its indices captured.
const INDEXED: [RegExp, (n: number) => string][] = [
  [/^problem\.(\d+)$/, (n) => `Problem statement ${ordinal(n)}`],
  [/^offer\.(\d+)\.title$/, (n) => `Offer ${ordinal(n)} headline`],
  [/^offer\.(\d+)\.body$/, (n) => `Offer ${ordinal(n)} description`],
  [/^how_it_works\.(\d+)\.title$/, (n) => `Step ${ordinal(n)} headline`],
  [/^how_it_works\.(\d+)\.body$/, (n) => `Step ${ordinal(n)} description`],
  [/^pricing\.\d+\.bullets\.(\d+)$/, (n) => `Pricing plan detail ${ordinal(n)}`],
  [/^faq\.(\d+)\.question$/, (n) => `FAQ question ${ordinal(n)}`],
  [/^faq\.(\d+)\.answer$/, (n) => `FAQ answer ${ordinal(n)}`],
];

const SECTION: Record<string, string> = {
  hero: "Top of page", problem: "Problem", offer: "Offer", how_it_works: "How it works",
  proof: "About and proof", pricing: "Pricing", faq: "FAQ", cta: "Sign-up", header: "Header",
  footer: "Footer", none: "Whole page",
};

function sentence(s: string): string {
  const t = s.replace(/[._]+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  return t ? t[0].toUpperCase() + t.slice(1) : "Website text";
}

/** Readable label for an internal field path. Never returns the raw path. */
export function fieldLabel(path: string): string {
  if (SIMPLE[path]) return SIMPLE[path];
  for (const [re, build] of INDEXED) {
    const m = re.exec(path);
    if (m) return build(Number(m[1]));
  }
  // Unknown path: drop array indices, turn separators into spaces.
  return sentence(path.split(".").filter((p) => !/^\d+$/.test(p)).join(" "));
}

/** Readable name for a website section key such as "how_it_works". */
export function sectionLabel(section: string): string {
  return SECTION[section] ?? sentence(section);
}

const OBJECTION: Record<string, string> = {
  missing_info: "Missing information", price: "Price concern", trust: "Trust concern",
  fit: "Not the right fit", clarity: "Unclear message", other: "Other concern",
};

/** Readable name for a simulated-objection type. */
export function objectionLabel(type: string): string {
  return OBJECTION[type] ?? sentence(type);
}

/** Founder answer limit; must match ANSWER_MAX in backend/app/pipeline/improve.py. */
export const ANSWER_MAX = 300;

/** Live counter for an answer. Text is never cut: over the limit, saving is blocked instead. */
export function answerCounter(text: string): { label: string; over: boolean; message: string | null } {
  const n = text.length;
  const over = n > ANSWER_MAX;
  return {
    label: `${n} / ${ANSWER_MAX} characters`,
    over,
    message: over ? `Shorten this answer by ${n - ANSWER_MAX} character${n - ANSWER_MAX === 1 ? "" : "s"} to save it.` : null,
  };
}
