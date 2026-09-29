"""goldtrack — track the big players in gold across every major exchange.

Public, legally available large-player evidence, on the shortest lag each
feed permits:

  positioning  CFTC Commitments of Traders — declared large-trader books,
               including the 4/8-largest-trader concentration ratio
  allocation   World Gold Council ETF tonnage by region
  physical     Shanghai Gold Exchange premium over London
  tape         minute-level volume anomalies in COMEX gold
  venues       COMEX, London, Shanghai, Tokyo, Hong Kong, Mumbai, New York

Quick start:
    python -m goldtrack brief
    python -m goldtrack watch --interval 30
    python -m goldtrack dashboard
"""
from . import config  # noqa: F401

__version__ = "1.0.0"

__all__ = ["analytics", "alerts", "config", "dashboard", "report",
           "snapshot", "store", "sources"]
