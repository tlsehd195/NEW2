"""ML research track: small, pure-Python models and the leak-free dataset
builder that feeds them.

Structure adopted from tlsehd195/NEW- (`ml/linear_model.py`,
`ml/tree_model.py`, `ml/features.py`, `ml/target.py`), adapted to candle
data. Nothing here is a validated strategy: an `MLStrategy` is a
CANDIDATE like any other and must go through pre-registration ->
walk-forward -> PBO/DSR -> one held-out TEST (CLAUDE.md rule 1).
"""
