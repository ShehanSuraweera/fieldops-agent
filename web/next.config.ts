import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server for the Docker image (.next/standalone/server.js).
  output: "standalone",
};

export default nextConfig;
