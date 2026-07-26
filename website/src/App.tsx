import { useEffect, useState } from "react";
import {
  ArrowRight,
  AudioLines,
  BookOpen,
  Braces,
  Check,
  Github,
  Menu,
  ShieldCheck,
  X,
} from "lucide-react";
import { EvalDemo } from "./components/EvalDemo";
import { HeroScene } from "./components/HeroScene";
import { VoiceMatrix } from "./components/VoiceMatrix";
import { treatments } from "./data";

function Brand() {
  return (
    <a className="brand" href="#top" aria-label="Crucible home">
      <span className="brand-mark">
        <i />
        <i />
        <i />
      </span>
      <span>Crucible</span>
    </a>
  );
}

function App() {
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const close = () => setMenuOpen(false);
    window.addEventListener("resize", close);
    return () => window.removeEventListener("resize", close);
  }, []);

  return (
    <div id="top">
      <header className="site-header">
        <Brand />
        <nav className={menuOpen ? "site-nav open" : "site-nav"} aria-label="Primary navigation">
          <a href="#product" onClick={() => setMenuOpen(false)}>
            Product
          </a>
          <a href="#dimensions" onClick={() => setMenuOpen(false)}>
            Test dimensions
          </a>
          <a href="#benchmark" onClick={() => setMenuOpen(false)}>
            Benchmark
          </a>
          <a href="https://github.com/mohitpaddhariya/crucible" target="_blank" rel="noreferrer">
            Docs
          </a>
          <a
            className="nav-github"
            href="https://github.com/mohitpaddhariya/crucible"
            target="_blank"
            rel="noreferrer"
          >
            <Github size={16} />
            GitHub
          </a>
        </nav>
        <button
          className="menu-button"
          type="button"
          aria-label={menuOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          {menuOpen ? <X /> : <Menu />}
        </button>
      </header>

      <main>
        <section className="hero" aria-labelledby="hero-title">
          <HeroScene />
          <div className="hero-copy">
            <h1 id="hero-title">Crucible</h1>
            <p className="hero-line">Break your voice agent before your users do.</p>
            <p className="hero-detail">
              Adaptive callers pressure-test safety, persuasion, dialects, code-mixing, and the messy
              reality of human speech.
            </p>
            <div className="hero-actions">
              <a className="button button-primary" href="#product">
                See it work
                <ArrowRight size={17} />
              </a>
              <a
                className="button button-secondary"
                href="https://github.com/mohitpaddhariya/crucible"
                target="_blank"
                rel="noreferrer"
              >
                <Github size={17} />
                View source
              </a>
            </div>
          </div>
          <div className="hero-meta">
            <span>VOICE AGENT EVALUATION</span>
            <span>TEXT · AUDIO · REAL TIME</span>
          </div>
        </section>

        <div className="signal-strip" aria-label="Evaluation dimensions">
          <div className="signal-track">
            {[...treatments, ...treatments].map((treatment, index) => (
              <span key={`${treatment}-${index}`}>
                {treatment}
                <i />
              </span>
            ))}
          </div>
        </div>

        <section className="section product-intro" id="product">
          <div className="section-heading">
            <h2>One harness. Every hard call.</h2>
            <p>
              Connect an agent, define what must hold true, and let simulated callers search for the
              failures manual QA misses.
            </p>
          </div>

          <div className="system-steps">
            <article>
              <span>01</span>
              <AudioLines />
              <h3>Simulate</h3>
              <p>
                Sarvam-powered personas speak naturally across languages, dialects, emotions, and attack
                strategies.
              </p>
            </article>
            <article>
              <span>02</span>
              <ShieldCheck />
              <h3>Prove</h3>
              <p>
                Every score points back to a transcript span, audio interval, tool call, or expected
                behavior.
              </p>
            </article>
            <article>
              <span>03</span>
              <Braces />
              <h3>Regress</h3>
              <p>
                Turn production failures into permanent tests and compare every prompt, model, and
                provider release.
              </p>
            </article>
          </div>
        </section>

        <section className="demo-section" aria-labelledby="demo-title">
          <div className="section demo-heading">
            <h2 id="demo-title">A failure you can replay.</h2>
            <p>
              Not a vague score. The exact caller, speech treatment, agent response, and evidence behind
              the verdict.
            </p>
          </div>
          <div className="section demo-container">
            <EvalDemo />
          </div>
        </section>

        <section className="section dimensions-section" id="dimensions">
          <div className="section-heading compact">
            <h2>Change one variable. Measure what breaks.</h2>
            <p>
              Crucible runs controlled counterfactuals so teams can separate genuine voice bias from
              random conversation variance.
            </p>
          </div>
          <VoiceMatrix />
        </section>

        <section className="benchmark-section" id="benchmark">
          <div className="section benchmark-inner">
            <div className="benchmark-copy">
              <h2>A capability frontier for voice agents.</h2>
              <p>
                Static checklists age quickly. Crucible evolves the challenge as agents improve, with
                adaptive adversaries, hidden suites, repeated trials, and human-calibrated scoring.
              </p>
              <a
                className="text-link"
                href="https://github.com/mohitpaddhariya/crucible"
                target="_blank"
                rel="noreferrer"
              >
                <BookOpen size={17} />
                Read the methodology
                <ArrowRight size={16} />
              </a>
            </div>
            <ol className="frontier-list">
              <li>
                <span>Level 0</span>
                <strong>Conversation policy</strong>
                <em>Built</em>
              </li>
              <li>
                <span>Level 1</span>
                <strong>Controlled speech variation</strong>
                <em>Next</em>
              </li>
              <li>
                <span>Level 2</span>
                <strong>Real-time adversarial calls</strong>
                <em>Planned</em>
              </li>
              <li>
                <span>Frontier</span>
                <strong>Private, adaptive benchmark</strong>
                <em>North star</em>
              </li>
            </ol>
          </div>
        </section>

        <section className="section principles">
          <h2>Built for decisions, not demos.</h2>
          <div className="principle-list">
            <div>
              <Check />
              <p>Provider-neutral target adapters</p>
            </div>
            <div>
              <Check />
              <p>Audio, transcript, timing, and tool evidence</p>
            </div>
            <div>
              <Check />
              <p>Deterministic checks plus calibrated judges</p>
            </div>
            <div>
              <Check />
              <p>Versioned scenarios and reproducible runs</p>
            </div>
          </div>
        </section>

        <section className="closing">
          <div className="section closing-inner">
            <div>
              <h2>Put your agent in the Crucible.</h2>
              <p>The benchmark for voice systems that need to work beyond the happy path.</p>
            </div>
            <a
              className="button button-light"
              href="https://github.com/mohitpaddhariya/crucible"
              target="_blank"
              rel="noreferrer"
            >
              <Github size={18} />
              Follow the build
            </a>
          </div>
        </section>
      </main>

      <footer className="site-footer">
        <Brand />
        <p>Open evaluation infrastructure for voice agents.</p>
        <div>
          <a href="https://github.com/mohitpaddhariya/crucible" target="_blank" rel="noreferrer">
            GitHub
          </a>
          <a href="#top">Back to top</a>
        </div>
      </footer>
    </div>
  );
}

export default App;
