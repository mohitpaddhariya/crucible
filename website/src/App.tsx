import {
  ArrowRight,
  AudioLines,
  BookOpen,
  Braces,
  Check,
  Github,
  ShieldCheck,
} from "lucide-react";
import { AsciiVideo, type AsciiVideoSource } from "./components/AsciiVideo";
import { EvalDemo } from "./components/EvalDemo";
import { VoiceMatrix } from "./components/VoiceMatrix";
import { treatments } from "./data";

const heroAnimations: AsciiVideoSource[] = [
  {
    id: "lotus",
    label: "Lotus",
    src: "/landing-page-animation.webm",
    fallbackSrc: "/landing-page-animation.mp4",
    poster: "/landing-page-poster.webp",
  },
];

const tickerTreatments = [...treatments, ...treatments];

function Brand() {
  return (
    <a className="brand" href="#top" aria-label="Crucible home">
      <img src="/lotus-logo-transparent.svg" alt="" width="1254" height="1254" />
    </a>
  );
}

function App() {
  return (
    <div id="top">
      <header className="site-header">
        <Brand />
        <nav className="hero-nav" aria-label="Primary navigation">
          <a href="#product">Product</a>
          <a href="#dimensions">Evals</a>
          <a href="#benchmark">Benchmark</a>
          <a className="dashboard-link" href="#dashboard">
            Go to dashboard
            <ArrowRight size={15} />
          </a>
        </nav>
      </header>

      <main>
        <section className="video-hero" aria-labelledby="hero-title">
          <AsciiVideo
            sources={heroAnimations}
            ariaLabel="An animated lotus rendered as colored ASCII characters"
          />
          <div className="hero-content">
            <h1 id="hero-title">
              Break your voice agent
              <br />
              {" before your users do."}
            </h1>
            <p>
              Pressure-test every voice, dialect, edge case, and adversarial turn before your agent
              reaches a real customer.
            </p>
            <div className="hero-cta">
              <a className="hero-cta-primary" href="#product">
                Explore Crucible
                <ArrowRight size={17} />
              </a>
              <a
                className="hero-cta-secondary"
                href="https://github.com/mohitpaddhariya/crucible"
                target="_blank"
                rel="noreferrer"
              >
                <Github size={17} />
                View on GitHub
              </a>
            </div>
          </div>
        </section>

        <div className="signal-strip" aria-label="Evaluation dimensions">
          <div className="signal-track">
            {[...tickerTreatments, ...tickerTreatments].map((treatment, index) => (
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

        <section className="demo-section" id="dashboard" aria-labelledby="demo-title">
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
