import type { Metadata } from "next";
import "./globals.css";
import AppHeader from "@/components/layout/AppHeader";
import PageFooter from "@/components/layout/PageFooter";

export const metadata: Metadata = {
  title: "股票监测助手",
  description: "准实时股票监测 + 行业信息聚合 + 邮件提醒工具",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body>
        <AppHeader />
        <main style={{ minHeight: 'calc(100vh - 64px - 80px)' }}>
          {children}
        </main>
        <PageFooter />
      </body>
    </html>
  );
}
