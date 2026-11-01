import { NextResponse, type NextRequest } from "next/server";

/* A strict Content Security Policy, with a fresh nonce for every page.

   The CSP is the second line behind escaping: if a script ever did get injected into a
   page, the browser refuses to run it unless it carries this request's nonce, and
   refuses to send data anywhere but this origin. Next.js reads the nonce from the
   request header and stamps it on its own scripts; the root layout stamps the one
   inline script we write.

   - Scripts: only nonce-bearing ones, and what they load ('strict-dynamic').
   - Styles: <style> elements need the nonce; inline style attributes are allowed,
     because components set widths and highlight positions that way.
   - Workers and fonts: PDF.js runs a worker from this origin and may build fonts from
     blob: and data: URLs while rendering a document.
   - Nothing may frame the app, and forms post only here. */
export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const dev = process.env.NODE_ENV === "development";
  const policy = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self' blob: data:",
    "worker-src 'self' blob:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");

  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", policy);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", policy);
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  return response;
}

export const config = {
  // Pages only: API calls are proxied JSON and static assets need no policy of their own.
  matcher: [
    {
      source: "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|ico|webp|mjs)$).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
