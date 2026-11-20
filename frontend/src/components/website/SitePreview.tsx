import ui from "../ui.module.css";
import type { Site } from "@/lib/sites-api";
import { levelContradictsReachability } from "./VerificationBadge";
import styles from "./website.module.css";

// The backend has no preview endpoint, so the only real preview is the public site itself. A
// link or frame appears only when ALL of these hold, using values the API returned:
//   - publicly_reachable is true and the level is `public`
//   - `url` is present, https, has no credentials, and its host is exactly the claimed host
//   - the site record's own host (if given) agrees with the claimed host
// The URL is never built from the business name, domain or host. Nothing is mocked,
// screenshotted or framed as a stand-in.
type UrlCheck = { ok: true; href: string } | { ok: false; why: "missing" | "malformed" | "not_https" | "host_mismatch" };

function checkUrl(url: string | null, claimedHost: string | null, siteHost: string | null): UrlCheck {
  if (!url) return { ok: false, why: "missing" };
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return { ok: false, why: "malformed" };
  }
  if (u.protocol !== "https:" || u.username || u.password) return { ok: false, why: "not_https" };
  const host = claimedHost?.toLowerCase();
  if (!host || u.host.toLowerCase() !== host) return { ok: false, why: "host_mismatch" };
  if (siteHost && siteHost.toLowerCase() !== host) return { ok: false, why: "host_mismatch" };
  return { ok: true, href: u.href };
}

const URL_PROBLEM: Record<string, string> = {
  missing: "The server reports the site as public but returned no address, so there is nothing safe to open.",
  malformed: "The address the server returned is not a valid URL, so it was not opened.",
  not_https: "The address the server returned is not a plain HTTPS link, so it was not opened.",
  host_mismatch: "The address the server returned does not belong to your claimed subdomain, so it was not opened.",
};

interface Props {
  site: Site | null;
  host: string | null; // the claimed subdomain (business.domain)
  status: string; // the business status
  notEnabled: boolean; // a deploy request in this session was refused as not configured
}

interface Unavailable { title: string; text: string }

function explain({ site, status, notEnabled }: Props, check: UrlCheck): Unavailable {
  const level = site?.verification_level ?? null;
  const live = site !== null && site.live_version !== null;
  const mismatch = live && site !== null && levelContradictsReachability(level, site.publicly_reachable);

  if (status === "DEPLOYING") {
    return { title: "Deployment in progress", text: "A preview can only appear after the new release finishes and is verified as publicly reachable. Nothing is shown in the meantime." };
  }
  if (status === "FAILED") {
    return {
      title: "Deployment failed",
      text: live
        ? "The latest deployment failed. The previously deployed release is not confirmed publicly reachable, so no preview is shown."
        : "The deployment failed before any release went live, so there is nothing to preview.",
    };
  }
  if (!live) {
    if (notEnabled) return { title: "Deployment is not enabled", text: "This environment has no deployment provider configured, so no site exists to preview." };
    return { title: "Not deployed yet", text: "No website has been deployed. A preview can only appear after a deployment is verified as publicly reachable." };
  }
  if (mismatch) {
    return { title: "Verification data is inconsistent", text: "The server's verification level and reachability flag disagree, so the site is not treated as public and no preview is shown." };
  }
  if (level === "simulated") {
    return { title: "Simulated deployment", text: "Verification was simulated, so no real website was published and there is nothing to preview." };
  }
  if (level === "local") {
    return { title: "Deployed locally, not publicly reachable", text: "The release was verified on the server only. NOVENTRA cannot confirm the address is reachable from the internet, so no preview is shown." };
  }
  if (site && site.publicly_reachable && !check.ok) {
    return { title: "Preview address rejected", text: URL_PROBLEM[check.why] };
  }
  return { title: "Not confirmed publicly reachable", text: "Without confirmed public reachability no preview is shown." };
}

export default function SitePreview(props: Props) {
  const { site, host } = props;
  const eligible =
    site !== null &&
    site.live_version !== null &&
    site.publicly_reachable &&
    !levelContradictsReachability(site.verification_level, site.publicly_reachable);
  const check = eligible ? checkUrl(site.url, host, site.host) : ({ ok: false, why: "missing" } as UrlCheck);

  if (!eligible || !check.ok) {
    const u = explain(props, check);
    return (
      <div className={styles.previewOff} role="status">
        <p className={styles.previewEyebrow}>Preview unavailable</p>
        <p className={styles.previewTitle}>{u.title}</p>
        <p className={ui.muted}>{u.text}</p>
      </div>
    );
  }
  return (
    <div>
      <p className={ui.small + " " + ui.muted}>
        Version {site.live_version} · verified publicly reachable
        {props.status === "DEPLOYING" && " · a newer deployment is in progress"}
        {props.status === "FAILED" && " · the latest deployment failed; this is the earlier release"}
      </p>
      <p>
        <a className={styles.siteLink} href={check.href} target="_blank" rel="noopener noreferrer">
          {check.href}<span className={styles.srOnly}> (opens in a new tab)</span>
        </a>
      </p>
      <iframe
        className={styles.frame}
        src={check.href}
        title="Your published website"
        sandbox=""
        loading="lazy"
        referrerPolicy="no-referrer"
      />
      <p className={ui.small + " " + ui.muted}>If the frame stays blank, open the link above.</p>
    </div>
  );
}
