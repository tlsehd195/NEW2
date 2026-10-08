"""Learning loop on top of the paper/live journal (ADR-0044).

Everything here is OBSERVATION and SHADOW EVALUATION only: it reads the
layered journal, writes to the `learning` layer, and never changes a
running strategy, a config or the strategy registry (AST test). A
retrained model or setting can only reach trading through the normal
validation path (pre-registration -> locked window -> walk-forward ->
PBO/DSR -> one held-out TEST, CLAUDE.md rule 1).
"""
