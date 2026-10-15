import { ArrowRight, BookOpen, ExternalLink } from 'lucide-react';
import type { ReactElement } from 'react';
import type { TimelineEvent } from './api';

const SOURCE = 'https://www.forestry.gov.cn/lyj/1/hmgzdt/20260326/665019.html';

export function OasisFeature({ event, onSelect }: { event: TimelineEvent; onSelect: (event: TimelineEvent) => void }): ReactElement {
  return <div className="inline-oasis">
    <header className="oasis-heading"><span className="node-kind">新时代 · 生态答卷</span><p className="node-year">{event.year_label}</p><h3 id="oasis-title"><button onClick={() => onSelect(event)}>绿水青山<span>就是金山银山</span></button></h3><p>从黄沙漫漫，到绿满家园。几代人接续奋斗，让毛乌素南缘从“沙进人退”走向“绿进沙退”。</p><button className="node-read" onClick={() => onSelect(event)}><BookOpen size={14} />读治沙的故事<ArrowRight size={14} /></button></header>
    <div className="oasis-landscape"><img className="oasis-main-image" src="/images/yulin-oasis.webp" alt="毛乌素南缘防护林、湿地与绿色村庄的生态治理艺术示意" width={2172} height={724} loading="lazy" decoding="async" /></div>
    <div className="oasis-evidence"><div><strong>860<small>万亩</small></strong><span>流沙得到基本治理</span></div><div><strong>90<small>%以上</small></strong><span>固定沙地占沙化土地比例</span></div><div><strong>30<small>倍</small></strong><span>林地面积较新中国成立初期增加</span></div></div>
    <div className="oasis-story"><p>在党的领导下，政府统筹推进“三北”工程、国土绿化与山水林田湖草沙系统治理。科研人员、治沙群众与一代代建设者共同把防护林扎进沙地，让生态改善与群众增收相互促进。</p><a href={SOURCE} target="_blank" rel="noreferrer">阅读治沙成果报道<ExternalLink size={14} /></a><button className="node-read" onClick={() => window.dispatchEvent(new CustomEvent('yulin:ask', { detail: '请结合资料库讲解榆林治沙与生态建设的历程，注明资料来源。' }))}>询问史料助手<ArrowRight size={14} /></button></div>
    <small className="oasis-source-note">国家林业和草原局 · 2026 年 3 月 26 日报道<br />景观为艺术意象，治沙与绿色守护仍在继续。</small>
  </div>;
}
