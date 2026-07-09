from watchlist import (
    get_watchlist,
    is_on_watchlist,
    add_to_watchlist,
    update_stock_score,
    approve_stock,
)

print("Current Watchlist:")
print(get_watchlist())

print("\nIs TSLA on watchlist?")
print(is_on_watchlist("TSLA"))

print("\nAdding PLTR:")
print(add_to_watchlist("PLTR", ai_score=72, notes="AI detected strong volume and breakout setup"))

print("\nUpdating PLTR score:")
print(update_stock_score("PLTR", 78))

print("\nApproving PLTR:")
print(approve_stock("PLTR"))

print("\nUpdated Watchlist:")
print(get_watchlist())