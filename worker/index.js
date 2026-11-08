// Brewflix Worker: the site itself is static assets (site/). This script only
// runs for /photos/*, /thumbs/* and /labels/* (see run_worker_first in
// wrangler.jsonc) and serves your backed-up images from the private R2 bucket.
//
// Photos are served only to requests carrying a valid Cloudflare Access token
// for this app, so they stay private even though the code is public. It fails
// closed: if Access isn't configured (ACCESS_TEAM_DOMAIN / ACCESS_AUD vars),
// no photo is ever served and the site falls back to Untappd's own image.

const PHOTO = /^\/(?:(photos|thumbs)\/(\d{1,20})|(labels)\/([0-9a-f]{16}))\.webp$/;
let jwks = { keys: null, fetched: 0 };

export default {
  async fetch(request, env) {
    const m = PHOTO.exec(new URL(request.url).pathname);
    if (!m) return text(404, "Not found");
    if (!env.PHOTOS) return text(404, "Photo backup isn't set up");
    if (!(await accessAllowed(request, env))) return text(403, "Sign in through Cloudflare Access to view photos");

    const obj = await env.PHOTOS.get(m[1] ? `${m[1]}/${m[2]}.webp` : `${m[3]}/${m[4]}.webp`);
    if (!obj) return text(404, "Not found");
    return new Response(obj.body, {
      headers: {
        "content-type": "image/webp",
        "cache-control": "private, max-age=86400",
        etag: obj.httpEtag,
        "x-robots-tag": "noindex",
      },
    });
  },
};

function text(status, body) {
  return new Response(body, { status, headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } });
}

// ---- Cloudflare Access JWT check (RS256, team certs, audience, expiry) ----

async function accessAllowed(request, env) {
  const team = (env.ACCESS_TEAM_DOMAIN || "").replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const aud = env.ACCESS_AUD;
  if (!team || !aud) return false;
  const token = request.headers.get("cf-access-jwt-assertion") || cookie(request, "CF_Authorization");
  if (!token) return false;
  try {
    const [h, p, s] = token.split(".");
    const header = JSON.parse(b64urlText(h));
    const claims = JSON.parse(b64urlText(p));
    if (header.alg !== "RS256") return false;
    const now = Math.floor(Date.now() / 1000);
    if (!claims.exp || claims.exp < now) return false;
    if (claims.nbf && claims.nbf > now + 60) return false;
    if (claims.iss !== `https://${team}`) return false;
    const auds = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    if (!auds.includes(aud)) return false;
    const jwk = (await teamKeys(team)).find((k) => k.kid === header.kid);
    if (!jwk) return false;
    const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
    return await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64urlBytes(s), new TextEncoder().encode(`${h}.${p}`));
  } catch {
    return false;
  }
}

async function teamKeys(team) {
  if (jwks.keys && Date.now() - jwks.fetched < 3600_000) return jwks.keys;
  const res = await fetch(`https://${team}/cdn-cgi/access/certs`);
  if (!res.ok) throw new Error("certs unavailable");
  jwks = { keys: (await res.json()).keys || [], fetched: Date.now() };
  return jwks.keys;
}

function cookie(request, name) {
  const c = request.headers.get("cookie") || "";
  const m = c.match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
  return m ? m[1] : null;
}

function b64urlBytes(s) {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

function b64urlText(s) {
  return new TextDecoder().decode(b64urlBytes(s));
}
