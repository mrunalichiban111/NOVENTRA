import styles from "./page.module.css";

export default function Home() {
  return (
    <div className={styles.page}>
      <main className={styles.main}>
        <p className={styles.eyebrow}>NOVENTRA</p>
        <h1>Validate the next venture with evidence.</h1>
        <p className={styles.description}>
          An evidence-backed launch workspace for founders testing demand,
          economics, and risk.
        </p>
        <p className={styles.note}>The app is under construction.</p>
      </main>
    </div>
  );
}
