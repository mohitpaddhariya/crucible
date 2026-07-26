import { useState } from "react";
import { Check, Headphones, ShieldAlert, SlidersHorizontal } from "lucide-react";
import { scenarios } from "../data";

export function EvalDemo() {
  const [activeId, setActiveId] = useState(scenarios[0].id);
  const scenario = scenarios.find(({ id }) => id === activeId) ?? scenarios[0];

  return (
    <div className="eval-demo">
      <div className="demo-tabs" role="tablist" aria-label="Evaluation examples">
        {scenarios.map((item) => (
          <button
            className={item.id === activeId ? "active" : ""}
            key={item.id}
            type="button"
            role="tab"
            aria-selected={item.id === activeId}
            onClick={() => setActiveId(item.id)}
          >
            {item.shortLabel}
          </button>
        ))}
      </div>

      <div className="demo-body">
        <aside className="demo-context">
          <div className="demo-section-title">
            <SlidersHorizontal size={15} />
            Test configuration
          </div>
          <dl>
            <div>
              <dt>Scenario</dt>
              <dd>{scenario.title}</dd>
            </div>
            <div>
              <dt>Persona</dt>
              <dd>{scenario.persona}</dd>
            </div>
            <div>
              <dt>Voice treatment</dt>
              <dd>{scenario.treatment}</dd>
            </div>
          </dl>
          <div className="demo-call-state">
            <span className="status-dot" />
            Recorded call · 00:42
          </div>
        </aside>

        <div className="demo-transcript">
          <div className="demo-section-title">
            <Headphones size={15} />
            Evidence transcript
          </div>
          <div className="transcript-line">
            <span className="speaker caller">Caller</span>
            <p>{scenario.callerLine}</p>
            <time>00:14</time>
          </div>
          <div className="transcript-line flagged">
            <span className="speaker target">Agent</span>
            <p>{scenario.targetLine}</p>
            <time>00:19</time>
          </div>
          <div className="evidence-finding">
            <ShieldAlert size={17} />
            <div>
              <span>Evidence-pinned finding</span>
              <p>{scenario.finding}</p>
            </div>
          </div>
        </div>

        <aside className="demo-score">
          <div className="demo-section-title">
            <Check size={15} />
            Scorecard
          </div>
          <div className="score-total">
            <strong>{scenario.score}</strong>
            <span>/ 100</span>
            <em className={scenario.status.toLowerCase()}>{scenario.status}</em>
          </div>
          <div className="score-metrics">
            {scenario.metrics.map((metric) => (
              <div key={metric.label}>
                <span>{metric.label}</span>
                <strong className={metric.tone}>{metric.value}</strong>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}
