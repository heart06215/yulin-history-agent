'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ArrowUpRight, BookOpen, Landmark } from 'lucide-react';
import type { ReactElement, ReactNode } from 'react';
import { HistoryAssistant } from './history-assistant';
import { HeritageCursor } from './heritage-cursor';

export function Shell({ children, fullWidth = false }: { children: ReactNode; fullWidth?: boolean }): ReactElement {
  const pathname = usePathname();
  return (
    <div className="heritage-site">
      <a className="skip-link" href="#main-content">跳到正文</a>
      <div className="official-strip"><span>榆林地方志数字整理项目</span><span>存史 · 资政 · 育人</span></div>
      <header className="heritage-header">
        <div className="heritage-header-inner">
          <Link href="/" className="heritage-brand" aria-label="榆林有志首页"><span className="brand-seal" aria-hidden="true">榆</span><span><strong>榆林有志</strong><small>一座城的来路，一方人的记忆</small></span></Link>
          <nav className="heritage-nav" aria-label="主导航">
            <Link href="/" aria-current={pathname === '/' || pathname === '/timeline' ? 'page' : undefined}><Landmark size={16} />历史长卷</Link>
            <Link href="/library" aria-current={pathname.startsWith('/library') ? 'page' : undefined}><BookOpen size={16} />方志文献库</Link>
            <Link href="/workbench/ops" className="workbench-link" aria-current={pathname.startsWith('/workbench') ? 'page' : undefined}>整理工作台<ArrowUpRight size={16} /></Link>
          </nav>
        </div>
      </header>
      <main id="main-content" className={fullWidth ? 'heritage-main full-bleed' : 'shell heritage-main'}>{children}</main>
      <footer className="heritage-footer">
        <div><Link className="footer-brand" href="/">榆林有志<span>让历史可读，让记忆有据。</span></Link><p>原页存真，方志存史。识别初稿与讲解供辅助阅读，疑难之处可回到原页核对。</p></div>
        <nav aria-label="页脚导航"><Link href="/library">阅读原志</Link><Link href="/workbench">智能体工作台</Link><Link href="/workbench/dev">开发者接口</Link><Link href="/workbench/usage">调用记录</Link></nav>
        <small>榆林地方志数字整理项目 · 道路与景观为时代艺术意象，非历史复原</small>
      </footer>
      <HistoryAssistant />
      <HeritageCursor />
    </div>
  );
}
