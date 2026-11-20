import type { Config, Context } from "@netlify/edge-functions";

const DEFAULT_UPSTREAM = "https://structured-measurements-violations-computation.trycloudflare.com";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
]);

function rewriteCookieDomain(cookie: string, upstreamHost: string): string {
  // Drop upstream Domain=... attributes so cookies bind cleanly to dispatch.theresolvia.com.
  return cookie.replace(
    new RegExp(`;\\s*Domain=\\.?${upstreamHost.replace(/\./g, "\\.")}`, "gi"),
    "",
  );
}

export default async (request: Request, context: Context) => {
  const incomingUrl = new URL(request.url);
  if (incomingUrl.pathname === "/offline.html") {
    return;
  }

  const upstreamBase = (Netlify.env.get("UPSTREAM_ORIGIN") || DEFAULT_UPSTREAM).replace(/\/+$/, "");
  const upstreamUrl = new URL(incomingUrl.pathname + incomingUrl.search, upstreamBase);

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP_HEADERS.has(lower)) return;
    headers.set(key, value);
  });

  // Preserve original public host/proto for Better Auth & OAuth callbacks.
  headers.set("ngrok-skip-browser-warning", "1");
  headers.set("x-forwarded-host", incomingUrl.host);
  headers.set("x-forwarded-proto", "https");
  if (context.ip) {
    headers.set("x-forwarded-for", context.ip);
  }
  if (request.headers.has("origin")) {
    headers.set("origin", incomingUrl.origin);
  }

  const method = request.method.toUpperCase();
  const hasBody = method !== "GET" && method !== "HEAD";
  const isCriticalCallback = incomingUrl.pathname.startsWith("/api/social/callback") || incomingUrl.pathname.startsWith("/api/auth");
  const maxAttempts = isCriticalCallback ? 3 : 1;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const upstreamResponse = await fetch(upstreamUrl.toString(), {
        method,
        headers,
        body: hasBody ? request.body : undefined,
        redirect: "manual",
      });

      // If Cloudflare tunnel returns 530 (tunnel connecting) or ngrok error, retry if critical callback.
      const isTunnelError =
        upstreamResponse.status === 530 ||
        (((upstreamResponse.status === 502 || upstreamResponse.status === 503 || upstreamResponse.status === 504 || upstreamResponse.status === 403) &&
          upstreamResponse.headers.has("ngrok-error-code")));

      if (isTunnelError && isCriticalCallback && attempt < maxAttempts - 1) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        continue;
      }

      if (isTunnelError && (request.headers.get("accept") || "").includes("text/html")) {
        const offlineRes = await context.next(new Request(new URL("/offline.html", incomingUrl)));
        return new Response(offlineRes.body, {
          status: 503,
          headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
        });
      }

      const responseHeaders = new Headers();
      upstreamResponse.headers.forEach((value, key) => {
        const lower = key.toLowerCase();
        if (HOP_BY_HOP_HEADERS.has(lower) || lower === "set-cookie") return;
        if (lower === "location") {
          // Rewrite redirects targeting the upstream tunnel host back to the custom domain.
          responseHeaders.set(
            "location",
            value.startsWith(upstreamBase)
              ? `${incomingUrl.origin}${value.slice(upstreamBase.length)}`
              : value,
          );
          return;
        }
        responseHeaders.append(key, value);
      });

      // Preserve multi-value Set-Cookie headers cleanly.
      const setCookies =
        typeof upstreamResponse.headers.getSetCookie === "function"
          ? upstreamResponse.headers.getSetCookie()
          : upstreamResponse.headers.get("set-cookie")
            ? [upstreamResponse.headers.get("set-cookie")!]
            : [];
      for (const cookie of setCookies) {
        responseHeaders.append("set-cookie", rewriteCookieDomain(cookie, upstreamUrl.host));
      }

      return new Response(upstreamResponse.body, {
        status: upstreamResponse.status,
        statusText: upstreamResponse.statusText,
        headers: responseHeaders,
      });
    } catch (err) {
      if (isCriticalCallback && attempt < maxAttempts - 1) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        continue;
      }

      if ((request.headers.get("accept") || "").includes("text/html")) {
        const offlineRes = await context.next(new Request(new URL("/offline.html", incomingUrl)));
        return new Response(offlineRes.body, {
          status: 503,
          headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
        });
      }
      return new Response(JSON.stringify({ error: "Upstream engine offline" }), {
        status: 503,
        headers: { "content-type": "application/json", "cache-control": "no-store" },
      });
    }
  }
};

export const config: Config = {
  path: "/*",
  excludedPath: ["/offline.html"],
};
