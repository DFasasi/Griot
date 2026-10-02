import type { NextConfig } from "next";

const api = process.env.GRIOT_API_URL ?? "http://127.0.0.1:8000";
const exporting = process.env.GRIOT_EXPORT === "1";

// Two builds from one codebase:
//  - dev / hosted:   Next server, /api proxied to FastAPI by rewrites
//  - desktop export: static files in out/, served by the local agent (which proxies /api itself)
const nextConfig: NextConfig = exporting
  ? { output: "export", trailingSlash: false, images: { unoptimized: true } }
  : {
      // Imports make many rate-limited catalog calls; give the dev proxy room (default ~30 s).
      experimental: { proxyTimeout: 120_000 },
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${api}/:path*` }];
      },
    };

export default nextConfig;
