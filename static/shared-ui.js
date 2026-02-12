(function () {
  const formulas = [
    { formula: 'σ = M·y / I', result: '= 156.2 MPa' },
    { formula: 'M = P·L / 4', result: '= 125 kN·m' },
    { formula: 'δ = PL³ / 48EI', result: '= 2.4 mm' },
    { formula: 'τ = VQ / Ib', result: '= 42.8 MPa' },
    { formula: 'I = bh³ / 12', result: '= 2.67×10⁸ mm⁴' },
    { formula: 'A = π·r²', result: '= 314.16 m²' },
    { formula: 'V = L × W × H', result: '= 1,250 m³' },
    { formula: 'P = F / A', result: '= 2.5 N/mm²' },
    { formula: 'E = σ / ε', result: '= 200 GPa' },
    { formula: 'W = γ × V', result: '= 24.5 kN' },
    { formula: '応力度 σ = N/A', result: '= 85.3 N/mm²' },
    { formula: '断面係数 Z = I/y', result: '= 1,890 cm³' },
    { formula: '座屈荷重 Pcr', result: '= 2,450 kN' },
    { formula: 'たわみ δmax', result: '= L/300' },
    { formula: '曲げモーメント M', result: '= wL²/8' },
    { formula: 'sin²θ + cos²θ', result: '= 1' },
    { formula: '√(a² + b²)', result: '= 12.73 m' },
    { formula: 'tan θ = a/b', result: '= 0.577' },
  ];

  function createFormula(container) {
    const item = formulas[Math.floor(Math.random() * formulas.length)];
    const el = document.createElement('div');
    const side = Math.random() > 0.5 ? 'left' : 'right';
    const xPos = side === 'left' ? Math.random() * 25 + 5 : Math.random() * 25 + 70;
    const yPos = Math.random() * 80 + 10;

    el.style.cssText = [
      'position:absolute',
      `left:${xPos}%`,
      `top:${yPos}%`,
      "font-family:'Cormorant Garamond',serif",
      `font-size:${Math.random() * 6 + 14}px`,
      'color:#8b5a2b',
      'opacity:0',
      'white-space:nowrap',
      'transform:translateY(20px)',
      'transition:opacity 0.8s ease, transform 0.8s ease',
    ].join(';');

    el.innerHTML =
      `<span class="formula-text">${item.formula}</span>` +
      '<span class="formula-result" style="opacity:0; margin-left: 8px; color: #6b4423; font-weight: 500;"> ' +
      `${item.result}</span>`;
    container.appendChild(el);

    setTimeout(() => {
      el.style.opacity = '0.35';
      el.style.transform = 'translateY(0)';
    }, 50);

    setTimeout(() => {
      const resultSpan = el.querySelector('.formula-result');
      if (!resultSpan) return;
      resultSpan.style.transition = 'opacity 0.5s ease';
      resultSpan.style.opacity = '1';
    }, 1500);

    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(-20px)';
    }, 4000);

    setTimeout(() => {
      el.remove();
    }, 5000);
  }

  window.initFormulaAnimation = function initFormulaAnimation(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    createFormula(container);
    window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        createFormula(container);
      }
    }, 2500);
  };
})();
