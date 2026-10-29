import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Website thumbnails only; the original report PDFs are never changed.
const destination = new URL("../public/reports/covers/", import.meta.url);
mkdirSync(destination, { recursive: true });
const editions = [
  ["q3-2026", "Q3 2026", "QUARTERLY REPORT", "#143231"],
  ["q2-2026", "Q2 2026", "QUARTERLY REPORT", "#1f4544"],
  ["q1-2026", "Q1 2026", "QUARTERLY REPORT", "#285957"],
  ["2022-2025", "2022–2025", "LANDSCAPE REPORT", "#243e4b"],
];

// Themes follow each PDF's Key Findings: 2022–2025 p5; Q1/Q2 p3; Q3 pp2–3.
// Keep typography/layout fixed and vary only the lower illustration.
const solar = (x, y, scale = 1) => `<g transform="translate(${x} ${y}) scale(${scale})" stroke="#96bfbd" stroke-width="1.5" fill="#143b43">
  <path d="M20 0H221L252 67H0Z"/><path d="M36 0L20 67M75 0L65 67M115 0V67M156 0L168 67M196 0L214 67M11 32H236"/>
</g>`;
const wind = (x, y, scale = 1) => `<g transform="translate(${x} ${y}) scale(${scale})" stroke="#e0eeee" stroke-width="4" stroke-linecap="round" fill="none">
  <path d="M0 0V-116M0-116L-5-163M0-116L41-94M0-116L-36-90"/>
</g>`;
const storage = (x, y, scale = 1) => `<g transform="translate(${x} ${y}) scale(${scale})">
  <rect width="202" height="99" rx="3" fill="#c2dddc"/><path d="M67 0V99M134 0V99" stroke="#52877a" stroke-width="3"/>
  <g fill="#2f6562"><rect x="15" y="15" width="37" height="6"/><rect x="82" y="15" width="37" height="6"/><rect x="149" y="15" width="37" height="6"/></g>
  <path d="M15 37H52M15 46H52M15 55H52M82 37H119M82 46H119M82 55H119M149 37H186M149 46H186M149 55H186" stroke="#639896" stroke-width="2"/>
  <path d="M-4 99H206" stroke="#143231" stroke-width="6"/>
</g>`;
const scenes = {
  // Solar-led portfolio, with regional technology diversity including hydropower.
  "2022-2025": `${wind(133, 494, .78)}
    <path d="M306 460H420L450 568H289Z" fill="#96bfbd"/><path d="M306 460H420" stroke="#e0eeee" stroke-width="7"/>
    <path d="M327 477L318 566M354 477V566M381 477L390 566M408 477L428 566" stroke="#386660" stroke-width="5"/>
    <path d="M282 574Q335 561 383 575T480 572V620H280Z" fill="#639896"/>
    <path d="M306 588H353M373 601H436M435 584H470" stroke="#c2dddc" stroke-width="2"/>
    ${solar(4, 537, 1.2)}`,
  // Western Asia capacity and bundled solar-plus-storage in Q1.
  "q1-2026": `<path d="M0 489Q120 419 252 477T480 466V570H0Z" fill="#8d987b"/>
    <path d="M0 537Q171 464 295 524T480 518V627H0Z" fill="#526f60"/>
    ${wind(328, 518, .66)}${solar(13, 514, 1.16)}${storage(307, 550, .66)}`,
  // Storage leads project count in Q2; solar remains part of integrated systems.
  "q2-2026": `${wind(118, 480, .65)}${solar(28, 468, .88)}
    <path d="M138 579H467L444 610H119Z" fill="#143231"/>
    ${storage(169, 490, 1.28)}
    <path d="M119 539H147V571H163" fill="none" stroke="#d3bd84" stroke-width="3"/>`,
  // Q3 expands tracking to transmission and clean-energy manufacturing.
  "q3-2026": `${wind(213, 470, .55)}
    <g stroke="#c2dddc" stroke-width="3.5" fill="none" stroke-linejoin="round">
      <path d="M77 546L113 388L149 546M90 487H136M97 453H130M105 419H122M90 487L139 518M136 487L84 518M97 453L136 487M130 453L90 487M78 423H148M86 401H140"/>
      <path d="M78 423Q41 445 0 437M148 423Q231 454 318 430" stroke-width="2"/>
    </g>
    <path d="M240 505L286 482V505L331 482V505L377 482V505H448V600H240Z" fill="#96bfbd"/>
    <path d="M399 505V459H424V505" fill="#639896"/>
    <path d="M252 527H438" stroke="#e0eeee" stroke-width="4"/>
    <g fill="#386660"><rect x="254" y="541" width="34" height="22"/><rect x="301" y="541" width="34" height="22"/><rect x="348" y="541" width="34" height="22"/><rect x="396" y="541" width="30" height="59"/></g>
    ${solar(10, 558, .78)}`,
};
for (const [slug, period, kind, color] of editions) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="660" viewBox="0 0 480 660">
  <rect width="480" height="660" fill="${color}"/>
  <g font-family="Arial, Helvetica, sans-serif" fill="#fff">
    <text x="38" y="66" font-size="30" font-weight="700" letter-spacing="1">D-CORE</text>
    <path d="M38 91H442" stroke="#96bfbd" stroke-width="1"/>
    <text x="38" y="124" font-size="12" letter-spacing="2" fill="#c2dddc">${kind}</text>
    <text x="38" y="190" font-size="44" font-weight="700">${period}</text>
    <g font-size="24"><text x="38" y="239">The Landscape of Chinese</text>
      <text x="38" y="272">Overseas Renewable</text><text x="38" y="305">Energy Projects</text></g>
  </g>
  <circle cx="385" cy="395" r="43" fill="#d3bd84"/>
  <path d="M0 455L78 413L152 450L253 398L360 452L480 407V660H0Z" fill="#386660"/>
  <path d="M0 505L105 452L213 504L337 452L480 502V660H0Z" fill="#52877a"/>
  <path d="M0 553L140 496L298 558L480 510V660H0Z" fill="#244e4b"/>
  ${scenes[slug]}
  <text x="38" y="644" font-family="Arial, Helvetica, sans-serif" font-size="11" letter-spacing="1.5" fill="#c2dddc">RESEARCH FOR THE ENERGY TRANSITION</text>
</svg>`;
  writeFileSync(new URL(`dcore-${slug}.svg`, destination), svg);
}
console.log(`Generated ${editions.length} report covers in ${fileURLToPath(destination)}`);
