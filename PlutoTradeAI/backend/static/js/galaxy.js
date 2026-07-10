(function () {
    const canvas = document.getElementById("starfield");
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const stars = [];
    const starCount = 170;

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }

    function makeStar() {
        return {
            x: Math.random() * canvas.width,
            y: Math.random() * canvas.height,
            r: Math.random() * 1.6 + 0.4,
            v: Math.random() * 0.45 + 0.1,
            o: Math.random() * 0.7 + 0.2,
        };
    }

    function init() {
        resize();
        stars.length = 0;
        for (let i = 0; i < starCount; i += 1) stars.push(makeStar());
    }

    function draw() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        for (const star of stars) {
            star.y += star.v;
            if (star.y > canvas.height) {
                star.y = -2;
                star.x = Math.random() * canvas.width;
            }
            ctx.beginPath();
            ctx.arc(star.x, star.y, star.r, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(255, 255, 255, ${star.o})`;
            ctx.fill();
        }
        requestAnimationFrame(draw);
    }

    init();
    draw();
    window.addEventListener("resize", init);
})();
