"""synth — turns a run's scorecards into one report. Reads files, never sockets."""
from synth.patterns import RunAnalysis, SynthError, analyse_run, load_run

__all__ = ["RunAnalysis", "SynthError", "analyse_run", "load_run"]
