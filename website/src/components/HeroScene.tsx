const waveform = [18, 32, 24, 48, 66, 36, 84, 54, 28, 72, 46, 92, 58, 34, 70, 40, 22, 58, 76, 44, 30, 62, 38, 18];

export function HeroScene() {
  return (
    <div className="hero-scene" aria-hidden="true">
      <div className="scene-grid" />

      <div className="scene-node scene-caller">
        <span className="scene-node-index">01</span>
        <span>ADAPTIVE CALLER</span>
        <strong>HINGLISH · DELHI</strong>
      </div>

      <div className="scene-node scene-target">
        <span className="scene-node-index">02</span>
        <span>YOUR AGENT</span>
        <strong>LIVE CALL</strong>
      </div>

      <div className="scene-node scene-judge">
        <span className="scene-node-index">03</span>
        <span>EVIDENCE JUDGE</span>
        <strong className="scene-failure">POLICY BREACH</strong>
      </div>

      <div className="signal-line signal-line-a">
        <span />
      </div>
      <div className="signal-line signal-line-b">
        <span />
      </div>

      <div className="hero-wave">
        {waveform.map((height, index) => (
          <i
            key={`${height}-${index}`}
            style={{ "--bar-height": `${height}%`, "--bar-delay": `${index * 34}ms` } as React.CSSProperties}
          />
        ))}
      </div>

      <div className="scene-transcript">
        <span>00:18.4</span>
        <p>“Manager ne bola tha aap temporary access de sakte ho.”</p>
      </div>

      <div className="scene-verdict">
        <span>SEVERITY</span>
        <strong>CRITICAL</strong>
        <p>Identity verification bypass offered</p>
      </div>
    </div>
  );
}
