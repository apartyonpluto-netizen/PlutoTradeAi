#!/bin/bash

echo "🚀 Installing PlutoTrade AI Galaxy Dashboard..."

mkdir -p backend/templates
mkdir -p backend/static/css
mkdir -p backend/static/js
mkdir -p data

cat > backend/app.py << 'EOF'
from flask import Flask, render_template
from watchlist import get_watchlist
from market_scanner import scan_market

app = Flask(__name__)

@app.route("/")
def dashboard():
    try:
        movers = scan_market()
    except Exception:
        movers = []

    try:
        watchlist = get_watchlist()
    except Exception:
        watchlist = []

    return render_template("dashboard.html", movers=movers, watchlist=watchlist)

if __name__ == "__main__":
    app.run(debug=True)
EOF

cat > backend/templates/dashboard.html << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>PlutoTrade AI</title>
    <link rel="stylesheet" href="/static/css/style.css">
</head>
<body>
    <div class="stars"></div>
    <div class="warp"></div>

    <aside class="sidebar">
        <div class="logo">🪐 PLUTOTRADE AI</div>
        <p>Trade the stars. Master the markets.</p>

        <nav>
            <a class="active">🚀 Dashboard</a>
            <a>⭐ Watchlist</a>
            <a>📈 Market Scanner</a>
            <a>🕯 Candle Brain</a>
            <a>📊 Volume Detector</a>
            <a>🧠 Confidence Engine</a>
            <a>📰 News Sentiment</a>
            <a>🧾 Trade Journal</a>
        </nav>
    </aside>

    <main class="main">
        <header class="topbar">
            <input placeholder="Search markets, news, or AI insights...">
            <div class="status">Market Status <strong>OPEN</strong></div>
            <div class="captain">Welcome back,<br><strong>Captain Curtis</strong></div>
        </header>

        <section class="hero">
            <div class="planet"></div>
            <h1>Navigating the Markets</h1>
            <p>Powered by Artificial Intelligence</p>
            <h2>Financial Freedom</h2>
        </section>

        <section class="cards">
            <div class="card"><p>Market Sentiment</p><h2>🐂 Bullish</h2></div>
            <div class="card"><p>Portfolio Confidence</p><h2>78%</h2></div>
            <div class="card"><p>Today’s Opportunities</p><h2>12</h2></div>
            <div class="card"><p>Risk Level</p><h2>3.2 / 10</h2></div>
        </section>

        <section class="grid">
            <div class="panel large">
                <h3>Top Market Movers</h3>
                <table>
                    <tr>
                        <th>Ticker</th>
                        <th>Price</th>
                        <th>Change</th>
                        <th>Rel Volume</th>
                        <th>Score</th>
                    </tr>
                    {% for stock in movers[:6] %}
                    <tr>
                        <td>{{ stock.ticker }}</td>
                        <td>${{ stock.price }}</td>
                        <td class="green">{{ stock.percent_change }}%</td>
                        <td>{{ stock.relative_volume }}x</td>
                        <td class="green">{{ stock.scanner_score }}</td>
                    </tr>
                    {% endfor %}
                </table>
            </div>

            <div class="panel">
                <h3>AI Trade Navigation</h3>
                <div class="orbit">
                    <span class="center"></span>
                    <span class="node top">Idea</span>
                    <span class="node right">Analyze</span>
                    <span class="node bottom">Confirm</span>
                    <span class="node left">Execute</span>
                </div>

                <h3>Watchlist Overview</h3>
                {% for stock in watchlist[:7] %}
                <div class="watch-row">
                    <span>{{ stock.ticker }}</span>
                    <span>{{ stock.category }}</span>
                    <strong>{{ stock.ai_score }}</strong>
                </div>
                {% endfor %}
            </div>
        </section>
    </main>
</body>
</html>
EOF

cat > backend/static/css/style.css << 'EOF'
body {
    margin: 0;
    background: #02000b;
    color: white;
    font-family: Arial, sans-serif;
    overflow-x: hidden;
}

.stars {
    position: fixed;
    inset: 0;
    background-image:
        radial-gradient(#fff 1px, transparent 1px),
        radial-gradient(#7b2cff 1px, transparent 1px),
        radial-gradient(#00eaff 1px, transparent 1px);
    background-size: 90px 90px, 140px 140px, 220px 220px;
    animation: starMove 80s linear infinite;
    opacity: .35;
    z-index: -3;
}

.warp {
    position: fixed;
    inset: 0;
    background:
        radial-gradient(circle at center, transparent 0%, #02000b 65%),
        repeating-radial-gradient(circle at center, rgba(120,50,255,.15) 0 1px, transparent 2px 8px);
    animation: warpPulse 7s ease-in-out infinite;
    z-index: -2;
}

@keyframes starMove {
    from { transform: translateY(0); }
    to { transform: translateY(-900px); }
}

@keyframes warpPulse {
    0%, 100% { opacity: .4; transform: scale(1); }
    50% { opacity: .9; transform: scale(1.08); }
}

.sidebar {
    position: fixed;
    left: 20px;
    top: 20px;
    bottom: 20px;
    width: 260px;
    padding: 22px;
    border: 1px solid rgba(159, 76, 255, .45);
    border-radius: 22px;
    background: rgba(7, 5, 25, .8);
    backdrop-filter: blur(20px);
    box-shadow: 0 0 45px rgba(111, 0, 255, .25);
}

.logo {
    font-size: 26px;
    font-weight: 900;
    color: #ffffff;
}

.sidebar p {
    color: #98a3d8;
    font-size: 12px;
}

nav a {
    display: block;
    padding: 15px;
    margin: 8px 0;
    border-radius: 14px;
    color: #dfe4ff;
    text-decoration: none;
    background: rgba(255,255,255,.03);
}

nav a.active,
nav a:hover {
    background: linear-gradient(90deg, rgba(132,0,255,.9), rgba(82,0,180,.35));
    box-shadow: 0 0 25px rgba(132,0,255,.45);
}

.main {
    margin-left: 315px;
    padding: 20px 25px 35px;
}

.topbar {
    height: 70px;
    display: grid;
    grid-template-columns: 1fr 170px 210px;
    gap: 18px;
    align-items: center;
}

.topbar input,
.status,
.captain {
    padding: 16px 18px;
    color: white;
    border-radius: 15px;
    border: 1px solid rgba(159, 76, 255, .55);
    background: rgba(255,255,255,.05);
}

.status strong,
.captain strong {
    color: #00ff99;
}

.hero {
    position: relative;
    height: 400px;
    border-radius: 24px;
    overflow: hidden;
    border: 1px solid rgba(159, 76, 255, .55);
    background:
        linear-gradient(to bottom, rgba(3,0,15,.25), rgba(3,0,15,.8)),
        radial-gradient(circle at 50% 40%, rgba(0,234,255,.5), transparent 12%),
        radial-gradient(circle at 60% 20%, rgba(255,42,214,.35), transparent 25%),
        radial-gradient(circle at 20% 40%, rgba(123,44,255,.55), transparent 25%),
        linear-gradient(135deg, #060018, #160032, #02000b);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
}

.hero::before {
    content: "";
    position: absolute;
    inset: -80px;
    background-image:
        linear-gradient(120deg, transparent 45%, rgba(255,255,255,.15) 50%, transparent 55%),
        linear-gradient(60deg, transparent 45%, rgba(0,234,255,.13) 50%, transparent 55%);
    background-size: 220px 220px;
    animation: travel 8s linear infinite;
}

@keyframes travel {
    from { transform: translateX(-100px) translateY(100px); }
    to { transform: translateX(220px) translateY(-220px); }
}

.planet {
    position: absolute;
    top: 90px;
    width: 210px;
    height: 210px;
    border-radius: 50%;
    background: radial-gradient(circle at 32% 30%, #89f7ff, #692cff 45%, #100026 75%);
    box-shadow: 0 0 80px rgba(123,44,255,.9);
    animation: float 6s ease-in-out infinite;
}

.planet::after {
    content: "";
    position: absolute;
    width: 330px;
    height: 85px;
    border: 3px solid rgba(255,255,255,.35);
    border-radius: 50%;
    left: -60px;
    top: 60px;
    transform: rotate(-14deg);
}

@keyframes float {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(18px); }
}

.hero h1,
.hero p,
.hero h2 {
    position: relative;
    z-index: 2;
    text-transform: uppercase;
    letter-spacing: 2px;
}

.hero h1 {
    font-size: 38px;
}

.hero h2 {
    color: #00eaff;
}

.cards {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 18px;
    margin: 18px 0;
}

.card,
.panel {
    border-radius: 20px;
    background: rgba(8, 5, 28, .8);
    border: 1px solid rgba(159, 76, 255, .35);
    backdrop-filter: blur(18px);
    padding: 20px;
}

.card h2 {
    font-size: 32px;
}

.grid {
    display: grid;
    grid-template-columns: 1fr 360px;
    gap: 18px;
}

table {
    width: 100%;
    border-collapse: collapse;
}

td, th {
    padding: 15px 10px;
    border-bottom: 1px solid rgba(255,255,255,.08);
    text-align: left;
}

th {
    color: #9fa8df;
    font-size: 12px;
    text-transform: uppercase;
}

.green {
    color: #00ff99;
}

.orbit {
    position: relative;
    width: 245px;
    height: 245px;
    margin: 20px auto;
    border-radius: 50%;
    border: 1px dashed rgba(126, 87, 255, .45);
    animation: rotateSlow 18s linear infinite;
}

.center {
    position: absolute;
    inset: 85px;
    border-radius: 50%;
    background: radial-gradient(circle, #b44cff, #190033);
    box-shadow: 0 0 45px #b44cff;
}

.node {
    position: absolute;
    color: #00eaff;
    font-size: 12px;
    text-transform: uppercase;
}

.node.top { top: 8px; left: 105px; }
.node.right { right: -15px; top: 113px; }
.node.bottom { bottom: 8px; left: 95px; }
.node.left { left: -18px; top: 113px; }

@keyframes rotateSlow {
    from { transform: rotate(0); }
    to { transform: rotate(360deg); }
}

.watch-row {
    display: grid;
    grid-template-columns: 1fr 1fr 40px;
    padding: 12px 0;
    border-bottom: 1px solid rgba(255,255,255,.08);
}

.watch-row strong {
    color: #ffbf2e;
}
EOF

echo "✅ PlutoTrade Galaxy Dashboard installed."
echo "Run: python3 backend/app.py"
echo "Then open: http://127.0.0.1:5000"