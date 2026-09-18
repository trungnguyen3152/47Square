import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "47Square",
  description: "47Square - Simple but different",
  icons: {
    icon: "/images/logo/logo006.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="vi">
      <head>
        <link rel="stylesheet" href="/css/style.css?v=14" />
      </head>
      <body>{children}</body>
    </html>
  );
}
