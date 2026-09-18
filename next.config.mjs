/** @type {import('next').NextConfig} */
const nextConfig = {
  async redirects() {
    return [
      {
        source: '/',
        destination: '/coming-soon',
        permanent: false, // Để false để sau này bạn ra mắt web thật thì Google không nhớ cache
      },
    ]
  },
};

export default nextConfig;
