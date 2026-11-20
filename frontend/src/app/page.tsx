import Link from "next/link";
import type { CSSProperties } from "react";
import ContactForm from "../components/ContactForm";
import ScrollReveal from "../components/landing/ScrollReveal";
import styles from "./landing.module.css";

const stages = [
  {
    number: "01",
    label: "VALIDATE",
    title: "Challenge the idea before the investment.",
    text: "Run a pre-mortem, pressure-test assumptions, and see whether the economics support a launch.",
  },
  {
    number: "02",
    label: "DISCOVER",
    title: "Give the business an identity.",
    text: "Explore names, domain availability, and a clear direction for what comes next.",
  },
  {
    number: "03",
    label: "LAUNCH",
    title: "Turn a stronger idea into a presence.",
    text: "Generate and deploy a focused static website when the foundations are ready.",
  },
  {
    number: "04",
    label: "EVALUATE",
    title: "Learn how the market might respond.",
    text: "Simulated customer persona evaluation is coming soon, giving you another lens on the launch.",
  },
];

const capabilities = [
  ["Business pre-mortem", "Surface failure modes, competitive pressure, and risks before they become expensive."],
  ["Financial viability analysis", "See contribution margin, break-even, cash, and runway through deterministic scenarios."],
  ["Assumption editing", "Change the numbers you know best and recalculate the verdict with a clear source trail."],
  ["Business name generation", "Find a sharper identity with name candidates shaped around your idea."],
  ["Domain availability checks", "Check promising domains without making a purchase or commitment."],
  ["Static website generation", "Create a focused, responsive launch page from structured business content."],
  ["Website deployment", "Put a validated concept in front of real people with a versioned static release."],
  ["Simulated persona evaluation", "Coming soon: test the page with a reusable panel of simulated customers."],
];

const delay = (ms: number) => ({ "--d": `${ms}ms` }) as CSSProperties;

const workflow = ["Assessment", "Naming", "Website Launch", "Persona Arena", "Improvement Studio"];

function DashboardPreview() {
  return (
    <div className={`${styles.preview} ${styles.enter}`} style={delay(260)} aria-label="Illustration of the NOVENTRA founder workspace">
      <div className={styles.previewTop}>
        <span className={styles.previewLogo}>NOVENTRA</span>
        <span className={styles.previewStatus}>WORKSPACE</span>
      </div>
      <div className={styles.previewBody}>
        <div className={styles.previewIntro}>
          <span>LAUNCH READINESS</span>
          <strong>Know what needs to work.</strong>
        </div>
        <div className={styles.previewGrid}>
          <div className={styles.previewCard}>
            <span>THE IDEA</span>
            <p>A considered starting point for the next venture.</p>
            <div className={styles.previewLine} />
            <div className={styles.previewLineShort} />
          </div>
          <div className={styles.previewCard}>
            <span>ANALYSIS</span>
            <div className={styles.previewRows}>
              <i /><i /><i />
            </div>
            <small>Evidence over instinct</small>
          </div>
        </div>
        <ol className={styles.flow} aria-label="NOVENTRA workflow">
          {workflow.map((name, i) => (
            <li key={name} className={styles.flowItem} style={delay(700 + i * 110)}>
              <span className={styles.flowNum}>{i + 1}</span>
              <span className={styles.flowName}>{name}</span>
              <span className={styles.flowTrack} aria-hidden="true"><i /></span>
            </li>
          ))}
        </ol>
        <div className={styles.previewFooter}>
          <span>PRE-MORTEM</span>
          <span>ECONOMICS</span>
          <span>RISK</span>
          <b>IN PROGRESS</b>
        </div>
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <main className={styles.page}>
      <ScrollReveal />
      <nav className={styles.nav} aria-label="Main navigation">
        <Link href="/" className={styles.wordmark}>NOVENTRA</Link>
        <div className={styles.navLinks}>
          <a href="#process">How it works</a>
          <a href="#capabilities">Capabilities</a>
          <a href="#about">About</a>
        </div>
        <Link href="/build" className={styles.navCta}>Start validating <span aria-hidden="true">↗</span></Link>
      </nav>

      <section className={styles.hero}>
        <div className={styles.heroCopy}>
          <p className={`${styles.eyebrow} ${styles.enter}`}>THE FOUNDER&apos;S LAUNCHPAD</p>
          <h1 className={styles.enter} style={delay(90)}>Don&apos;t just build a business. <em className={styles.heroAccent}>Build one that&apos;s ready.</em></h1>
          <p className={`${styles.heroLead} ${styles.enter}`} style={delay(190)}>
            Stress-test your idea, challenge your assumptions, discover your business identity and
            launch your website — before investing in the wrong business.
          </p>
          <div className={`${styles.heroActions} ${styles.enter}`} style={delay(290)}>
            <Link href="/build" className={styles.primaryButton}>Test your business idea <span aria-hidden="true">↗</span></Link>
            <a href="#process" className={styles.textButton}>Explore how it works <span aria-hidden="true">↓</span></a>
          </div>
        </div>
        <DashboardPreview />
      </section>

      <section className={styles.positioning} id="about">
        <p className={`${styles.eyebrow} ${styles.reveal}`} data-reveal="">A BETTER STARTING POINT</p>
        <div className={styles.reveal} data-reveal="" style={delay(80)}>
          <h2>Most founders build the <em className={styles.positioningAccent}>surface</em> first.</h2>
          <p>
            Branding. Product. A polished website. But none of it answers the question that matters
            most: is this idea ready to become a business?
          </p>
        </div>
        <div className={`${styles.positioningNote} ${styles.reveal}`} data-reveal="" style={delay(160)}>
          <span className={styles.noteMark}>+</span>
          <p>NOVENTRA challenges the idea before it helps you launch it.</p>
        </div>
      </section>

      <section className={styles.process} id="process">
        <div className={`${styles.sectionHeading} ${styles.reveal}`} data-reveal="">
          <p className={styles.eyebrow}>FROM IDEA TO EVIDENCE</p>
          <h2>A more deliberate way to move forward.</h2>
        </div>
        <div className={styles.stageGrid}>
          {stages.map((stage) => (
            <article key={stage.number} className={`${styles.stage} ${styles.reveal}`} data-reveal="" style={delay(stages.indexOf(stage) * 90)}>
              <span className={styles.stageNumber}>{stage.number}</span>
              <p className={styles.stageLabel}>{stage.label}</p>
              <h3>{stage.title}</h3>
              <p>{stage.text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className={styles.capabilities} id="capabilities">
        <div className={`${styles.sectionHeading} ${styles.reveal}`} data-reveal="">
          <p className={styles.eyebrow}>WHAT&apos;S INSIDE</p>
          <h2>Clarity for every early decision.</h2>
        </div>
        <div className={styles.capabilityGrid}>
          {capabilities.map(([title, text], index) => (
            <article key={title} className={`${styles.capability} ${styles.reveal}`} data-reveal="" style={delay((index % 4) * 80)}>
              <span>0{index + 1}</span>
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className={`${styles.closing} ${styles.reveal}`} data-reveal="">
        <p className={styles.eyebrow}>BEGIN WITH THE RIGHT QUESTION</p>
        <h2>Your idea deserves more than a guess.</h2>
        <p>Find out what needs to work before you make it real.</p>
        <Link href="/build" className={styles.primaryButton}>Start validating your idea <span aria-hidden="true">↗</span></Link>
      </section>

      <footer className={styles.footer}>
        <div className={styles.footerContact}>
          <div>
            <p className={styles.eyebrow}>GET IN TOUCH</p>
            <h2>Have a question? Let&apos;s talk.</h2>
          </div>
          <div className={styles.footerContactCopy}>
            <p>Whether you&apos;re a founder exploring an idea, a potential partner, or simply curious about NOVENTRA, we&apos;d love to hear from you.</p>
            <ContactForm />
          </div>
        </div>
        <div className={styles.footerMeta}>
          <div>
            <Link href="/" className={styles.wordmark}>NOVENTRA</Link>
            <p>The founder&apos;s launchpad.</p>
          </div>
          <nav aria-label="Footer navigation">
            <a href="#process">How it works</a>
            <a href="#capabilities">Capabilities</a>
            <a href="#about">About</a>
          </nav>
          <p>© 2026 NOVENTRA. All rights reserved.</p>
        </div>
      </footer>
    </main>
  );
}
