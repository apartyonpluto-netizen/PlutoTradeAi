(function () {
    const warpLayer = document.getElementById("warpLayer");

    function spawnStreak() {
        if (!warpLayer) return;
        const streak = document.createElement("span");
        streak.className = "warp-streak";
        streak.style.top = `${Math.random() * 100}%`;
        streak.style.animationDuration = `${(Math.random() * 1.8 + 1.8).toFixed(2)}s`;
        warpLayer.appendChild(streak);
        setTimeout(() => streak.remove(), 3200);
    }

    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!prefersReducedMotion) {
        setInterval(spawnStreak, 1200);
    }

    const layers = document.querySelectorAll(".parallax-layer, .hero-planet-wrap");
    document.addEventListener("mousemove", (event) => {
        if (prefersReducedMotion) return;
        const x = (event.clientX / window.innerWidth - 0.5) * 16;
        const y = (event.clientY / window.innerHeight - 0.5) * 16;
        layers.forEach((layer, index) => {
            const depth = index === 0 ? 0.8 : 1.2;
            layer.style.transform = `translate(${x * depth}px, ${y * depth}px)`;
        });
    });
})();
