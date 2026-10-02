import type { NextConfig } from "next";

const api = process.env.GRIOT_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Proxy the FastAPI backend under /api so the browser never needs CORS.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/:path*` }];
  },
};

export default nextConfig;
