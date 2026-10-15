import type { NextConfig } from 'next';

const nextConfig: NextConfig = { output: process.env.WEB_PLATFORM === 'node' ? 'standalone' : undefined };

export default nextConfig;
