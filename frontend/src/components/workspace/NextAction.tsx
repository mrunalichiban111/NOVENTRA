"use client";

import Link from "next/link";
import type { Business } from "@/lib/api";
import { isEligibleForLaunchPlan } from "@/lib/stages";
import ui from "../ui.module.css";
import styles from "./workspace.module.css";

// What the founder should do next. Eligibility here is a display hint: the backend re-checks
// the verdict and status and its 409 response is authoritative.
export default function NextAction({ business }: { business: Business }) {
  const base = `/b/${business.id}`;
  const { status } = business;

  let title = "";
  let text = "";
  let cta: { label: string; href: string } | null = null;
  let lockedLabel: string | null = null;

  if (isEligibleForLaunchPlan(business)) {
    title = "Next step";
    text = "Your idea passed the pre-launch check. Generate and compare business names, check domains, and claim your NOVENTRA address.";
    cta = { label: "Explore your launch plan", href: `${base}/names` };
  } else if (status === "GATED" && business.verdict === "rethink") {
    title = "Launch planning is locked";
    text = "A Rethink verdict does not unlock naming or a website. Revisit the assumptions below and recalculate, or start a new idea.";
    lockedLabel = "Explore your launch plan";
  } else if (status === "REJECTED") {
    title = "Launch planning is not available";
    text = "This idea was refused for safety reasons, so there is no next step for it.";
    lockedLabel = "Explore your launch plan";
  } else if (status === "DOMAIN_PENDING") {
    title = "Next step";
    text = "A name is claimed. Continue to the website stage.";
    cta = { label: "Continue to Website Launch", href: `${base}/website` };
  } else if (status === "DEPLOYING" || status === "LIVE") {
    title = "Next step";
    text = "Follow your website's deployment and verification.";
    cta = { label: "Open Website Launch", href: `${base}/website` };
  } else {
    return null; // in progress or failed: the progress tracker already says what is happening
  }

  return (
    <section className={styles.next} aria-label="Next action">
      <div>
        <h2 className={ui.h2}>{title}</h2>
        <p className={ui.muted}>{text}</p>
      </div>
      {cta && <Link href={cta.href} className={ui.button}>{cta.label}</Link>}
      {lockedLabel && (
        <button className={ui.button} disabled aria-disabled="true">{lockedLabel}</button>
      )}
    </section>
  );
}
