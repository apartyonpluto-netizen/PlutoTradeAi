"""User-defined strategies: the part of PlutoTrade where someone brings
their own rules, sees an honest record of how those rules did, and
compares them with the built-in strategies and the setup library.

Nothing in this package places, modifies, or cancels a broker order, and
nothing here is imported by the autonomous scan. Paper scoring is a stored
opt-in behind PLUTO_USER_STRATEGY_SCORING, which defaults off. While that
flag is off the scan cannot see these strategies. While it is on, the
catalog is still only a read API — the scan does not call it.
"""
