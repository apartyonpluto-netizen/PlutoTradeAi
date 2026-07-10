def calculate_risk_plan(options_plan):
    if options_plan["contract_type"] == "NO TRADE":
        return {
            "risk_reward_ratio": None,
            "max_loss_rule": "No trade",
            "risk_status": "Blocked",
            "risk_notes": ["No clean trade setup."]
        }

    entry = options_plan["entry_trigger"]
    stop = options_plan["stop_loss"]
    target_1 = options_plan["target_1"]

    if entry is None or stop is None or target_1 is None:
        return {
            "risk_reward_ratio": None,
            "max_loss_rule": "Missing trade levels",
            "risk_status": "Blocked",
            "risk_notes": ["Entry, stop, or target is missing."]
        }

    risk = abs(entry - stop)
    reward = abs(target_1 - entry)

    if risk == 0:
        ratio = 0
    else:
        ratio = round(reward / risk, 2)

    risk_notes = []

    if ratio >= 2:
        risk_status = "Allowed for review"
        risk_notes.append("Risk/reward is acceptable.")
    elif ratio >= 1:
        risk_status = "Paper trade only"
        risk_notes.append("Risk/reward is weak but testable.")
    else:
        risk_status = "Blocked"
        risk_notes.append("Risk/reward is not worth taking.")

    risk_notes.append("Use small size until this system has proven results.")
    risk_notes.append("Options can lose value quickly from time decay and volatility changes.")

    return {
        "risk_reward_ratio": ratio,
        "max_loss_rule": "Risk no more than 1–2% of account per trade",
        "risk_status": risk_status,
        "risk_notes": risk_notes,
    }