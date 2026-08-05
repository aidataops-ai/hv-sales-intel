/** @type {import('next').NextConfig} */

// Where the FastAPI backend listens during local development.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig = {
  async rewrites() {
    // In production, `vercel.json` maps /api/(.*) onto the Python function.
    // `next dev` never reads vercel.json, so without this every /api call
    // 404s against the Next router — and since the lead client has no mock
    // fallback by design, that renders as an empty feed rather than an
    // error, which is a genuinely confusing way to be broken.
    //
    // Proxying (rather than pointing NEXT_PUBLIC_API_URL at :8000) keeps the
    // browser on one origin, so the Supabase session cookie is sent normally
    // and dev behaves the same way production does.
    if (process.env.NODE_ENV === "production") return [];
    return [
      { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
    ];
  },
};

export default nextConfig;
