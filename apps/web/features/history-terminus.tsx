import type { ReactElement } from 'react';

export function HistoryTerminus(): ReactElement {
  return (
    <section className="river-terminus" id="journey-end" data-road="asphalt" aria-labelledby="journey-end-title">
      <header className="terminus-heading">
        <div className="terminus-title">
          <span className="section-kicker">仍在路上 · 今日榆林</span>
          <h2 id="journey-end-title">城向新，<br />绿长存。</h2>
        </div>
        <span className="river-node terminus-node" aria-hidden="true"><span>续</span></span>
        <p className="terminus-intro">从塞上古城，走向宜居新城。<br />城市建设与生态治理相伴，<br />榆林的下一程，仍在书写。</p>
      </header>
      <figure className="terminus-city">
        <img src="/images/yulin-city-finale-v4.webp" alt="根据榆林城区实景改绘的城市画卷：现代楼宇、城市大道与小面积滨水绿带相映" width={2160} height={728} loading="lazy" decoding="async" />
        <figcaption><span>榆林 · 城市与生态共生</span><small>据榆林城区及榆溪河生态长廊实景艺术改绘 · <a href="https://www.sxdaily.com.cn/2024-10/29/content_10917543.html" target="_blank" rel="noreferrer">城区参考</a> / <a href="https://esb.sxdaily.com.cn/pad/content/202512/28/content_928492.html" target="_blank" rel="noreferrer">河岸参考</a></small></figcaption>
      </figure>
    </section>
  );
}
