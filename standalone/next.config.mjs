/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Proxy API calls to the FastAPI backend during local dev so the
  // browser doesn't have to deal with CORS. Set NEXT_PUBLIC_API_URL in
  // production to point at the deployed backend directly.
  async rewrites() {
    const apiUrl = process.env.API_URL || "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${apiUrl}/:path*` },
    ];
  },
};

export default nextConfig;
