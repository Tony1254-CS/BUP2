"""
Shared constants for the GridWise energy optimizer.

Single source of truth for numerical tolerances and other
cross-module parameters.
"""

# Official absolute tolerance (kWh / BDT).
# The competition guide specifies 0.01; use this everywhere.
GRIDWISE_TOL = 0.01
