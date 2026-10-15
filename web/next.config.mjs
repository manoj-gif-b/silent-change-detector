/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // three / @react-three/fiber ship ESM; let Next transpile them cleanly.
  transpilePackages: ["three"],
};

export default nextConfig;
