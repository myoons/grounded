import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";

export const frontendAssets = Object.freeze([
  "index.html",
  "app.mjs",
  "cards.mjs",
  "style.css",
  "audio-input.mjs",
  "capture-status.mjs",
  "capture-lifecycle.mjs",
  "native-audio-input.mjs",
  "transcript-view.mjs",
  "chunker.mjs",
  "pcm-worklet.js",
  "upload-queue.mjs",
]);
const assets = new Set(frontendAssets);

export async function startMeetingServer(directory) {
  let origin;
  const server = createServer(async (request, response) => {
    if (
      request.method !== "GET" ||
      request.headers.host !== new URL(origin).host ||
      request.headers["sec-fetch-site"] === "cross-site"
    ) {
      response.writeHead(403);
      response.end();
      return;
    }
    const name = new URL(request.url, origin).pathname.slice(1) || "index.html";
    if (!assets.has(name)) {
      response.writeHead(404);
      response.end();
      return;
    }
    response.setHeader(
      "Content-Security-Policy",
      "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; worker-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'",
    );
    response.setHeader("Cache-Control", "no-store");
    response.setHeader("X-Content-Type-Options", "nosniff");
    response.setHeader(
      "Content-Type",
      name.endsWith(".css")
        ? "text/css"
        : name.endsWith(".html")
          ? "text/html"
          : "text/javascript",
    );
    try {
      response.end(await readFile(path.join(directory, name)));
    } catch {
      response.writeHead(404);
      response.end();
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  origin = `http://127.0.0.1:${server.address().port}`;
  return {
    origin,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}
