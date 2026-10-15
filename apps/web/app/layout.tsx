import type { Metadata } from 'next';
import './fonts.css';
import './globals.css';
import './heritage.css';
import './heritage-v2.css';
import './heritage-v3.css';
import './heritage-cursor.css';
export const metadata: Metadata = {
  title: '榆林有志 · 一河千年，塞上春秋',
  description: '沿历史长河读榆林地名沿革与时代大事，对照方志原页与识别成果，询问有据可查的史料讲解。',
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
