/**
 * 策略说明视图。
 *
 * 独立于结果列表存在，因为当天没选出票的策略在结果页里根本不出现——
 * 恰恰是那些策略最需要解释。
 */

import type { StrategyDoc } from '../api'

interface StrategyGuideProps {
  docs: StrategyDoc[]
  /** 当天真的跑出结果的策略类名，用于标出"今日有结果" */
  activeToday: Set<string>
}

export function StrategyGuide({ docs, activeToday }: StrategyGuideProps) {
  return (
    <>
      <header className="guide-head">
        <h1>策略</h1>
        <p>
          七个策略各自独立运行，互不影响。下面的判据直接来自
          <code>sequoia_x/strategy/</code> 里各策略类自身的属性，
          与 run() 用的阈值同处一个文件。
        </p>
      </header>

      {docs.length === 0 ? (
        <p className="state">读取策略说明失败，接口没有返回内容。</p>
      ) : (
        docs.map((doc) => (
          <article className="guide-item" key={doc.class_name}>
            <div className="guide-item-head">
              <h2>{doc.title}</h2>
              <code className="guide-class">{doc.class_name}</code>
              {activeToday.has(doc.class_name) && <span className="guide-live">今日有结果</span>}
            </div>

            <p className="guide-summary">{doc.summary}</p>

            <ol className="guide-criteria">
              {doc.criteria.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>

            <dl className="guide-meta">
              <dt>数据来源</dt>
              <dd>{doc.data_source}</dd>
              <dt>结果顺序</dt>
              <dd>{doc.ordering}</dd>
              <dt>最少数据</dt>
              <dd>{doc.min_bars}</dd>
            </dl>

            {doc.caveat && <p className="guide-caveat">{doc.caveat}</p>}
          </article>
        ))
      )}

      <p className="guide-foot">以上只是选股条件的说明，不构成任何投资建议。</p>
    </>
  )
}
