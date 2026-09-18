import type { NextRequest } from "next/server";

/* Runtime proxy to the API.
 *
 * This was a `rewrites()` entry, which is the obvious way to do it and is wrong for
 * a container image: Next evaluates `rewrites()` at **build time** and serialises the
 * result into the route manifest. `process.env.API_ORIGIN` is therefore read during
 * `docker build`, where it is unset, so every deployment ships with whatever the
 * fallback was — `http://localhost:8000`, pointing at the web container itself.
 * The symptom is a 500 with ECONNREFUSED 127.0.0.1:8000 while the API is healthy and
 * reachable by name.
 *
 * A route handler reads the variable per request, so one image runs unchanged in
 * development, compose and anywhere else. The cost is a JS hop per request, which is
 * noise next to a retrieval call.
 *
 * Keeping the browser same-origin is the point: no CORS to configure, and the bearer
 * token is never attached to a third-party origin.
 */

export const dynamic = "force-dynamic";

const API_ORIGIN = () => process.env.API_ORIGIN ?? "http://localhost:8000";

// Hop-by-hop headers, plus the ones the upstream must set for itself.
const STRIP = new Set([
  "host",
  "connection",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
  "content-length",
]);

async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const target = new URL(`${API_ORIGIN()}/${path.join("/")}`);
  target.search = request.nextUrl.search;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIP.has(key.toLowerCase())) headers.set(key, value);
  });

  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      // Required by undici when streaming a request body rather than buffering it,
      // which is what keeps a 50 MB upload from being held in memory here.
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
    } as RequestInit);
  } catch (cause) {
    return Response.json(
      {
        code: "upstream_unreachable",
        message: "The API is not reachable from the web server.",
        detail: { target: target.origin, cause: (cause as Error).message },
        correlation_id: null,
      },
      { status: 502 },
    );
  }

  // Stream the response straight through: PDFs are served this way and must not be
  // buffered.
  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

type Context = { params: Promise<{ path: string[] }> };

const handler = async (request: NextRequest, context: Context) =>
  proxy(request, (await context.params).path);

export const GET = handler;
export const POST = handler;
export const PUT = handler;
export const PATCH = handler;
export const DELETE = handler;
export const HEAD = handler;
export const OPTIONS = handler;
