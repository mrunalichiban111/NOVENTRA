export class ApiError extends Error {
  constructor(message, status, code = null) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function parseError(res) {
  if (res.status === 429) {
    return {
      message: "Too many requests. Please wait a minute and try again.",
      code: "rate_limited",
    };
  }
  if (res.status === 502) {
    return {
      message: "Couldn't send right now, please try again",
      code: "smtp_failure",
    };
  }

  try {
    const body = await res.json();
    const d = body.detail;
    if (typeof d === "string") return { message: d, code: null };
    if (Array.isArray(d)) {
      const message = d
        .map((e) => (e.msg ?? "Invalid input").replace(/^Value error, /, ""))
        .join(" ");
      return { message, code: null };
    }
    if (d && typeof d === "object" && typeof d.message === "string") {
      return {
        message: d.message,
        code: typeof d.code === "string" ? d.code : null,
      };
    }
  } catch {
    // Fall through to the generic status-based error.
  }

  return { message: `Request failed (${res.status}).`, code: null };
}

export async function executeApiRequest(path, init, fetchImpl = fetch) {
  let res;
  try {
    res = await fetchImpl(`/api${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      "Could not reach the server. Check your connection and try again.",
      0,
      "network",
    );
  }

  if (!res.ok) {
    const { message, code } = await parseError(res);
    throw new ApiError(message, res.status, code);
  }

  if (res.status === 204 || res.status === 205) return {};
  try {
    return await res.json();
  } catch {
    return {};
  }
}
