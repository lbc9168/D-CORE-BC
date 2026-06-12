const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa");

// ---------- palette ----------
const BG = "F5F3EE";       // warm off-white (dominant)
const DARK = "0C3B3A";     // deep petrol (title / closing)
const DARK2 = "10504D";    // lighter petrol
const INK = "1C2B2A";      // near-black text
const MUTED = "6B7B79";    // muted gray-green
const CARD = "FFFFFF";
const BORDER = "E5DFD4";
// model colors
const TEAL = "12736E";     // M1 equity JV (Indonesia)
const AMBER = "BE7C1E";    // M2 blended equity (Gotion)  -- darkened for contrast on light
const BLUE = "2F6690";     // M3 green debt (COBCO)
const TEAL_T = "E6EFEE";
const AMBER_T = "F6EEDD";
const BLUE_T = "E7EDF2";
const MUTEDBAR = "B9C2C0";

const HEAD = "Georgia";
const BODY = "Calibri";

const W = 13.33, Hh = 7.5;
const M = 0.6;

const makeShadow = () => ({ type: "outer", color: "000000", blur: 7, offset: 3, angle: 135, opacity: 0.12 });

// ---------- icon rasterization ----------
function renderIconSvg(Icon, color, size = 256) {
  return ReactDOMServer.renderToStaticMarkup(React.createElement(Icon, { color, size: String(size) }));
}
async function icon(Icon, color) {
  const svg = renderIconSvg(Icon, color.startsWith("#") ? color : "#" + color, 256);
  const png = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + png.toString("base64");
}

async function main() {
  // pre-render icons
  const I = {
    handshakeW: await icon(fa.FaHandshake, "FFFFFF"),
    landmarkW: await icon(fa.FaLandmark, "FFFFFF"),
    shieldW: await icon(fa.FaShieldAlt, "FFFFFF"),
    gemW: await icon(fa.FaGem, "FFFFFF"),
    flaskW: await icon(fa.FaFlask, "FFFFFF"),
    batteryW: await icon(fa.FaCarBattery, "FFFFFF"),
    carW: await icon(fa.FaCarSide, "FFFFFF"),
    industryT: await icon(fa.FaIndustry, TEAL),
    arrow: await icon(fa.FaArrowRight, MUTED),
    check: await icon(fa.FaCheckCircle, "53C2B6"),
    warn: await icon(fa.FaExclamationTriangle, AMBER),
    bulbW: await icon(fa.FaLightbulb, "FFFFFF"),
    globeW: await icon(fa.FaGlobeAfrica, "FFFFFF"),
    boltW: await icon(fa.FaBolt, "FFFFFF"),
    mapT: await icon(fa.FaMapMarkedAlt, "FFFFFF"),
  };

  const pres = new pptxgen();
  pres.defineLayout({ name: "WIDE", width: W, height: Hh });
  pres.layout = "WIDE";
  pres.author = "BRI Renewable Energy Database";
  pres.title = "Financing the Batteries Behind the EV Transition";

  // ---------- helpers ----------
  function footer(slide, n, dark = false) {
    const col = dark ? "7F9C9A" : "A6AFAD";
    slide.addText("BRI Renewable Energy Database  ·  Chinese New-Energy Manufacturing Abroad", {
      x: M, y: 7.06, w: 9, h: 0.3, fontFace: BODY, fontSize: 9, color: col, margin: 0, valign: "middle",
    });
    slide.addText(String(n), { x: W - M - 0.5, y: 7.06, w: 0.5, h: 0.3, fontFace: BODY, fontSize: 9, color: col, align: "right", margin: 0, valign: "middle" });
  }

  function caseHeader(slide, kicker, kColor, iconData, title) {
    slide.addShape(pres.shapes.OVAL, { x: M, y: 0.5, w: 0.78, h: 0.78, fill: { color: kColor }, shadow: makeShadow() });
    slide.addImage({ data: iconData, x: M + 0.21, y: 0.71, w: 0.36, h: 0.36 });
    slide.addText(kicker, { x: M + 1.0, y: 0.5, w: 11, h: 0.3, fontFace: BODY, fontSize: 12, bold: true, color: kColor, charSpacing: 2, margin: 0, valign: "middle" });
    slide.addText(title, { x: M + 1.0, y: 0.79, w: 11.5, h: 0.55, fontFace: HEAD, fontSize: 27, bold: true, color: INK, margin: 0, valign: "middle" });
  }

  // facts/financing two-card layout for case slides
  function statChips(slide, y, chips, color) {
    const gap = 0.25;
    const totalW = W - 2 * M;
    const cw = (totalW - gap * (chips.length - 1)) / chips.length;
    chips.forEach((c, i) => {
      const x = M + i * (cw + gap);
      slide.addShape(pres.shapes.RECTANGLE, { x, y, w: cw, h: 0.92, fill: { color: CARD }, line: { color: BORDER, width: 1 }, shadow: makeShadow() });
      slide.addShape(pres.shapes.RECTANGLE, { x, y, w: 0.07, h: 0.92, fill: { color } });
      slide.addText(c.v, { x: x + 0.18, y: y + 0.1, w: cw - 0.3, h: 0.42, fontFace: HEAD, fontSize: 19, bold: true, color, margin: 0, valign: "middle" });
      slide.addText(c.l, { x: x + 0.18, y: y + 0.5, w: cw - 0.3, h: 0.36, fontFace: BODY, fontSize: 10, color: MUTED, margin: 0, valign: "middle" });
    });
  }

  function bullets(items, opts) {
    return items.map((t, i) => ({
      text: t,
      options: { bullet: { code: "2022", indent: 14 }, breakLine: true, paraSpaceAfter: (opts && opts.gap) || 9, fontSize: (opts && opts.fs) || 12.5, color: (opts && opts.color) || INK, fontFace: BODY },
    }));
  }

  // =================================================================
  // SLIDE 1 — TITLE
  // =================================================================
  let s = pres.addSlide();
  s.background = { color: DARK };
  // motif: three model dots top-left
  s.addShape(pres.shapes.OVAL, { x: M, y: 1.0, w: 0.22, h: 0.22, fill: { color: TEAL } });
  s.addShape(pres.shapes.OVAL, { x: M + 0.32, y: 1.0, w: 0.22, h: 0.22, fill: { color: AMBER } });
  s.addShape(pres.shapes.OVAL, { x: M + 0.64, y: 1.0, w: 0.22, h: 0.22, fill: { color: BLUE } });
  s.addText("GLOBAL SOUTH  ·  EV TRANSITION", { x: M, y: 1.45, w: 11, h: 0.4, fontFace: BODY, fontSize: 15, bold: true, color: "D9B25A", charSpacing: 3, margin: 0 });
  s.addText("Financing the Batteries Behind\nthe EV Transition", { x: M, y: 2.0, w: 12.0, h: 1.9, fontFace: HEAD, fontSize: 46, bold: true, color: "FFFFFF", margin: 0, lineSpacingMultiple: 1.02 });
  s.addText("How Chinese capital is building the Global South's new-energy supply chain — three financing models, three countries.", {
    x: M, y: 3.95, w: 10.5, h: 0.9, fontFace: BODY, fontSize: 18, color: "CFE0DE", margin: 0,
  });
  // bottom model legend
  const leg = [
    { c: TEAL, t: "Equity JV — Indonesia" },
    { c: AMBER, t: "Blended Equity — Morocco" },
    { c: BLUE, t: "Green Debt — Morocco" },
  ];
  leg.forEach((g, i) => {
    const x = M + i * 4.0;
    s.addShape(pres.shapes.OVAL, { x, y: 5.55, w: 0.2, h: 0.2, fill: { color: g.c } });
    s.addText(g.t, { x: x + 0.32, y: 5.46, w: 3.6, h: 0.38, fontFace: BODY, fontSize: 13, bold: true, color: "EAF1F0", margin: 0, valign: "middle" });
  });
  s.addShape(pres.shapes.LINE, { x: M, y: 6.55, w: W - 2 * M, h: 0, line: { color: "2A5B59", width: 1 } });
  s.addText("A 10-minute briefing  ·  May 2026  ·  Source: BRI Renewable Energy Database", { x: M, y: 6.7, w: 12, h: 0.35, fontFace: BODY, fontSize: 11, color: "8FA9A7", margin: 0, valign: "middle" });

  // =================================================================
  // SLIDE 2 — FRAMING
  // =================================================================
  s = pres.addSlide();
  s.background = { color: BG };
  s.addText("The EV transition is a supply-chain transition", { x: M, y: 0.5, w: 12, h: 0.7, fontFace: HEAD, fontSize: 30, bold: true, color: INK, margin: 0 });
  s.addText(
    "Global South countries no longer just want to import electric vehicles — they want to capture the value chain that builds them: critical minerals, battery materials, cells, and eventually the cars. Chinese firms are the dominant builders of that capacity abroad. For host governments, the decisive questions are how each plant is financed — and who ends up owning it.",
    { x: M, y: 1.35, w: 12.1, h: 1.2, fontFace: BODY, fontSize: 15, color: INK, margin: 0, lineSpacingMultiple: 1.05 }
  );

  // value chain flow
  const nodes = [
    { ic: I.gemW, t: "Critical minerals", s: "Nickel · Lithium" },
    { ic: I.flaskW, t: "Battery materials", s: "Precursors · Cathodes" },
    { ic: I.batteryW, t: "Cells & packs", s: "Gigafactories" },
    { ic: I.carW, t: "Electric vehicles", s: "Assembly · Export" },
  ];
  const usable = W - 2 * M;
  const nCenters = nodes.map((_, i) => M + (i + 0.5) * (usable / nodes.length));
  const cy = 3.35;
  nodes.forEach((nd, i) => {
    const cx = nCenters[i];
    s.addShape(pres.shapes.OVAL, { x: cx - 0.55, y: cy, w: 1.1, h: 1.1, fill: { color: i === 3 ? DARK : TEAL }, shadow: makeShadow() });
    s.addImage({ data: nd.ic, x: cx - 0.28, y: cy + 0.28, w: 0.56, h: 0.56 });
    s.addText(nd.t, { x: cx - 1.4, y: cy + 1.2, w: 2.8, h: 0.35, fontFace: BODY, fontSize: 14, bold: true, color: INK, align: "center", margin: 0 });
    s.addText(nd.s, { x: cx - 1.4, y: cy + 1.55, w: 2.8, h: 0.3, fontFace: BODY, fontSize: 11, color: MUTED, align: "center", margin: 0 });
    if (i < nodes.length - 1) {
      const mid = (nCenters[i] + nCenters[i + 1]) / 2;
      s.addImage({ data: I.arrow, x: mid - 0.22, y: cy + 0.36, w: 0.44, h: 0.44 });
    }
  });

  // big stat callout
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 5.6, w: W - 2 * M, h: 1.2, fill: { color: DARK }, shadow: makeShadow() });
  s.addText("198", { x: M + 0.3, y: 5.6, w: 1.8, h: 1.2, fontFace: HEAD, fontSize: 48, bold: true, color: "D9B25A", margin: 0, valign: "middle", align: "center" });
  s.addText(
    [
      { text: "Chinese overseas new-energy manufacturing projects tracked in this database", options: { fontSize: 16, bold: true, color: "FFFFFF", breakLine: true, fontFace: BODY } },
      { text: "spanning batteries, materials, solar and vehicles across 40+ countries", options: { fontSize: 12.5, color: "CFE0DE", fontFace: BODY } },
    ],
    { x: M + 2.3, y: 5.6, w: 9.8, h: 1.2, margin: 0, valign: "middle" }
  );
  footer(s, 2);

  // =================================================================
  // SLIDE 3 — LANDSCAPE (chart)
  // =================================================================
  s = pres.addSlide();
  s.background = { color: BG };
  s.addText("A wave of factories — and the Global South leads", { x: M, y: 0.5, w: 12, h: 0.7, fontFace: HEAD, fontSize: 30, bold: true, color: INK, margin: 0 });
  s.addText("Top destinations for Chinese new-energy manufacturing projects (number of projects)", { x: M, y: 1.22, w: 8, h: 0.4, fontFace: BODY, fontSize: 13, italic: true, color: MUTED, margin: 0 });

  const labels = ["Indonesia", "Malaysia", "Egypt", "Saudi Arabia", "Morocco", "Spain", "USA", "Turkey"];
  const vals = [29, 16, 11, 10, 10, 9, 9, 7];
  const barColors = [TEAL, MUTEDBAR, MUTEDBAR, MUTEDBAR, AMBER, MUTEDBAR, MUTEDBAR, MUTEDBAR];
  s.addChart(pres.charts.BAR, [{ name: "Projects", labels, values: vals }], {
    x: M, y: 1.75, w: 8.0, h: 4.9, barDir: "col",
    chartColors: barColors,
    chartArea: { fill: { color: BG } },
    plotArea: { fill: { color: BG } },
    catAxisLabelColor: INK, catAxisLabelFontSize: 11, catAxisLabelFontFace: BODY,
    valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
    valAxisLineShow: false, catAxisLineShow: false,
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: INK, dataLabelFontSize: 12, dataLabelFontBold: true, dataLabelFontFace: BODY,
    barGapWidthPct: 45,
    showLegend: false, showTitle: false,
  });

  // right callout
  const rx = 8.9, rw = W - M - rx;
  s.addShape(pres.shapes.RECTANGLE, { x: rx, y: 1.75, w: rw, h: 4.9, fill: { color: DARK }, shadow: makeShadow() });
  s.addImage({ data: I.mapT, x: rx + 0.3, y: 2.05, w: 0.55, h: 0.55 });
  s.addText("Where we zoom in", { x: rx + 0.3, y: 2.7, w: rw - 0.6, h: 0.4, fontFace: HEAD, fontSize: 18, bold: true, color: "FFFFFF", margin: 0 });
  s.addText(
    "We follow three projects building EV-battery capacity in two of these hubs — Indonesia and Morocco — each financed a different way.",
    { x: rx + 0.3, y: 3.2, w: rw - 0.6, h: 1.2, fontFace: BODY, fontSize: 13.5, color: "CFE0DE", margin: 0, lineSpacingMultiple: 1.06 }
  );
  // two highlighted chips
  s.addShape(pres.shapes.OVAL, { x: rx + 0.3, y: 4.55, w: 0.18, h: 0.18, fill: { color: TEAL } });
  s.addText("Indonesia — #1 destination", { x: rx + 0.58, y: 4.46, w: rw - 0.9, h: 0.35, fontFace: BODY, fontSize: 12.5, bold: true, color: "EAF1F0", margin: 0, valign: "middle" });
  s.addShape(pres.shapes.OVAL, { x: rx + 0.3, y: 5.0, w: 0.18, h: 0.18, fill: { color: AMBER } });
  s.addText("Morocco — Africa's battery hub", { x: rx + 0.58, y: 4.91, w: rw - 0.9, h: 0.35, fontFace: BODY, fontSize: 12.5, bold: true, color: "EAF1F0", margin: 0, valign: "middle" });
  footer(s, 3);

  // =================================================================
  // SLIDE 4 — THREE MODELS OVERVIEW
  // =================================================================
  s = pres.addSlide();
  s.background = { color: BG };
  s.addText("Three ways to finance a battery plant", { x: M, y: 0.5, w: 12, h: 0.7, fontFace: HEAD, fontSize: 30, bold: true, color: INK, margin: 0 });
  s.addText("The same goal — local battery capacity — reached through very different capital structures.", { x: M, y: 1.22, w: 12, h: 0.4, fontFace: BODY, fontSize: 14, italic: true, color: MUTED, margin: 0 });

  const models = [
    { n: "01", c: TEAL, t: TEAL_T, ic: I.handshakeW, name: "Equity Joint Venture", desc: "Chinese firm and host-country state companies co-own the project, often anchored on local minerals.", tag: "Indonesia · CATL" },
    { n: "02", c: AMBER, t: AMBER_T, ic: I.landmarkW, name: "Blended Equity + Incentives", desc: "Sponsor equity plus a stake from the host country's sovereign fund, sweetened by state incentives.", tag: "Morocco · Gotion" },
    { n: "03", c: BLUE, t: BLUE_T, ic: I.shieldW, name: "Green Syndicated Debt", desc: "International banks lend together on a green loan, de-risked by China's export-credit insurer.", tag: "Morocco · COBCO" },
  ];
  const cgap = 0.4, ccw = (W - 2 * M - 2 * cgap) / 3, ctop = 1.95, cch = 4.55;
  models.forEach((m, i) => {
    const x = M + i * (ccw + cgap);
    s.addShape(pres.shapes.RECTANGLE, { x, y: ctop, w: ccw, h: cch, fill: { color: CARD }, line: { color: BORDER, width: 1 }, shadow: makeShadow() });
    s.addShape(pres.shapes.RECTANGLE, { x, y: ctop, w: ccw, h: 0.14, fill: { color: m.c } });
    s.addShape(pres.shapes.OVAL, { x: x + 0.4, y: ctop + 0.45, w: 1.0, h: 1.0, fill: { color: m.c } });
    s.addImage({ data: m.ic, x: x + 0.66, y: ctop + 0.71, w: 0.48, h: 0.48 });
    s.addText(m.n, { x: x + ccw - 1.3, y: ctop + 0.4, w: 1.0, h: 0.8, fontFace: HEAD, fontSize: 40, bold: true, color: m.t, align: "right", margin: 0 });
    s.addText(m.name, { x: x + 0.4, y: ctop + 1.7, w: ccw - 0.8, h: 0.8, fontFace: HEAD, fontSize: 19, bold: true, color: INK, margin: 0 });
    s.addText(m.desc, { x: x + 0.4, y: ctop + 2.5, w: ccw - 0.8, h: 1.3, fontFace: BODY, fontSize: 13.5, color: INK, margin: 0, lineSpacingMultiple: 1.06 });
    s.addShape(pres.shapes.RECTANGLE, { x: x + 0.4, y: ctop + cch - 0.72, w: ccw - 0.8, h: 0.45, fill: { color: m.t } });
    s.addText(m.tag, { x: x + 0.4, y: ctop + cch - 0.72, w: ccw - 0.8, h: 0.45, fontFace: BODY, fontSize: 13, bold: true, color: m.c, align: "center", margin: 0, valign: "middle" });
  });
  footer(s, 4);

  // =================================================================
  // CASE SLIDE BUILDER
  // =================================================================
  function caseSlide(n, kicker, kColor, kTint, kIcon, title, facts, finLead, finBullets, finArrow, chips) {
    const sl = pres.addSlide();
    sl.background = { color: BG };
    caseHeader(sl, kicker, kColor, kIcon, title);
    const top = 1.62, ch = 3.95;
    const lx = M, lw = 6.0;
    const fx = 6.95, fw = W - M - fx;
    // left facts card
    sl.addShape(pres.shapes.RECTANGLE, { x: lx, y: top, w: lw, h: ch, fill: { color: CARD }, line: { color: BORDER, width: 1 }, shadow: makeShadow() });
    sl.addShape(pres.shapes.RECTANGLE, { x: lx, y: top, w: 0.08, h: ch, fill: { color: kColor } });
    sl.addText("THE PROJECT", { x: lx + 0.32, y: top + 0.22, w: lw - 0.6, h: 0.3, fontFace: BODY, fontSize: 12, bold: true, color: kColor, charSpacing: 2, margin: 0 });
    sl.addText(bullets(facts, { fs: 12.5, gap: 9 }), { x: lx + 0.32, y: top + 0.62, w: lw - 0.62, h: ch - 0.8, margin: 0, valign: "top" });
    // right financing card (tinted)
    sl.addShape(pres.shapes.RECTANGLE, { x: fx, y: top, w: fw, h: ch, fill: { color: kTint }, shadow: makeShadow() });
    sl.addShape(pres.shapes.RECTANGLE, { x: fx, y: top, w: 0.08, h: ch, fill: { color: kColor } });
    sl.addText("THE FINANCING MODEL", { x: fx + 0.32, y: top + 0.22, w: fw - 0.6, h: 0.3, fontFace: BODY, fontSize: 12, bold: true, color: kColor, charSpacing: 2, margin: 0 });
    sl.addText(finLead, { x: fx + 0.32, y: top + 0.58, w: fw - 0.62, h: 0.7, fontFace: HEAD, fontSize: 15.5, bold: true, color: INK, margin: 0, lineSpacingMultiple: 1.03 });
    sl.addText(bullets(finBullets, { fs: 12, gap: 8 }), { x: fx + 0.32, y: top + 1.4, w: fw - 0.62, h: 1.85, margin: 0, valign: "top" });
    // arrow takeaway strip
    sl.addShape(pres.shapes.RECTANGLE, { x: fx + 0.32, y: top + ch - 0.78, w: fw - 0.64, h: 0.52, fill: { color: kColor } });
    sl.addText(finArrow, { x: fx + 0.5, y: top + ch - 0.78, w: fw - 0.9, h: 0.52, fontFace: BODY, fontSize: 12.5, bold: true, italic: true, color: "FFFFFF", margin: 0, valign: "middle" });
    // stat chips
    statChips(sl, top + ch + 0.22, chips, kColor);
    footer(sl, n);
    return sl;
  }

  // SLIDE 5 — CATL Indonesia
  caseSlide(
    5, "CASE 1  ·  EQUITY JOINT VENTURE", TEAL, TEAL_T, I.handshakeW,
    "Indonesia: a nickel-to-battery ecosystem",
    [
      "Location: Halmahera (North Maluku) + Karawang (West Java)",
      "Scope: full chain — nickel mining & smelting → materials → cells → recycling",
      "Cell plant operational by end-2026: 6.9 GWh, ramping to 15 GWh",
      "Output ≈ batteries for 200,000–300,000 EVs per year",
      "Footprint over 2,000 hectares; groundbreaking July 2025",
    ],
    "An equity joint venture with Indonesian state enterprises.",
    [
      "CATL's subsidiary CBL partners with Antam (state-owned miner) and IBC (state battery corporation), plus Brunp and Lygend.",
      "Antam co-leads smelting; IBC co-manages the battery segments.",
      "Indonesia contributes nickel reserves and state equity; China brings cell technology and capital.",
    ],
    "→  Resource access is traded for a domestic battery industry.",
    [
      { v: "≈ $6B", l: "total investment" },
      { v: "200–300k", l: "EVs/yr of batteries" },
      { v: "15 GWh", l: "cell capacity" },
      { v: "5", l: "JV partners" },
    ]
  );

  // SLIDE 6 — Gotion Morocco
  caseSlide(
    6, "CASE 2  ·  BLENDED EQUITY + INCENTIVES", AMBER, AMBER_T, I.landmarkW,
    "Morocco: Africa's first battery gigafactory",
    [
      "Location: Kenitra industrial hub",
      "Up to $6.5B across five phases (phase 1: $1.3B)",
      "Capacity: 20 GWh, scaling toward 100 GWh",
      "Vertically integrated — cells, cathodes and anodes",
      "Production from Q3 2026; ~17,000 direct & indirect jobs",
      "Powered by a dedicated 500 MW wind + 2,000 MWh storage plant",
    ],
    "Sponsor equity + host sovereign-fund equity + state incentives.",
    [
      "Morocco's state fund CDG (via CDG Invest / NAMA) took a ~€300M equity stake in Gotion Power Morocco.",
      "The government layered on incentives under its automotive-industry strategy.",
      "Morocco's free-trade deals with the EU and US make the plant a tariff-free export platform.",
    ],
    "→  The host state co-invests to anchor an export industry.",
    [
      { v: "$6.5B", l: "across 5 phases" },
      { v: "20→100", l: "GWh capacity" },
      { v: "€300M", l: "host-state equity" },
      { v: "17,000", l: "jobs" },
    ]
  );

  // SLIDE 7 — COBCO Morocco
  caseSlide(
    7, "CASE 3  ·  GREEN SYNDICATED DEBT", BLUE, BLUE_T, I.shieldW,
    "Morocco: COBCO and export-credit finance",
    [
      "COBCO = CNGR + Morocco's Al Mada (local conglomerate), a joint venture",
      "Plant value ≈ $2B (€1.8B)",
      "Phase 1: 40,000 tonnes/year nickel-based precursor (cathode material)",
      "Operational since June 2025",
      "Supplies Umicore; first operational battery-chain plant in Africa",
    ],
    "An international green syndicated loan, insured by China's export-credit agency.",
    [
      "Lead arrangers: BNP Paribas, Standard Chartered and China Construction Bank (Paris).",
      "Lenders span Europe, mainland China, Hong Kong and Africa.",
      "Sinosure (China Export & Credit Insurance Corp.) provides cover that de-risks the commercial banks.",
    ],
    "→  Export-credit insurance unlocks global bank lending.",
    [
      { v: "≈ $2B", l: "plant value" },
      { v: "40,000 t", l: "precursor /yr" },
      { v: "3", l: "lead-arranger banks" },
      { v: "Sinosure", l: "insured" },
    ]
  );

  // =================================================================
  // SLIDE 8 — COMPARISON TABLE
  // =================================================================
  s = pres.addSlide();
  s.background = { color: BG };
  s.addText("Three models, side by side", { x: M, y: 0.5, w: 12, h: 0.7, fontFace: HEAD, fontSize: 30, bold: true, color: INK, margin: 0 });

  const hdr = (t) => ({ text: t, options: { fill: { color: DARK }, color: "FFFFFF", bold: true, fontFace: BODY, fontSize: 13, align: "left", valign: "middle" } });
  const cell = (t, b) => ({ text: t, options: { color: INK, fontFace: BODY, fontSize: 12, align: "left", valign: "middle", bold: !!b } });
  const modelCell = (t, c, tint) => ({ text: t, options: { fill: { color: tint }, color: c, bold: true, fontFace: BODY, fontSize: 12.5, align: "left", valign: "middle" } });

  const tableData = [
    [hdr("Model"), hdr("Capital provider"), hdr("Risk bearer"), hdr("What the host country gets"), hdr("Project")],
    [
      modelCell("Equity JV", TEAL, TEAL_T),
      cell("Sponsor + host state companies (shared equity)"),
      cell("Shared across the JV partners"),
      cell("Resource value-add, jobs, technology, a state ownership stake"),
      cell("CATL · Indonesia", true),
    ],
    [
      modelCell("Blended equity", AMBER, AMBER_T),
      cell("Sponsor + host sovereign fund + incentives"),
      cell("Sponsor and host state (both hold equity)"),
      cell("Export industry, jobs, equity upside for the state"),
      cell("Gotion · Morocco", true),
    ],
    [
      modelCell("Green debt", BLUE, BLUE_T),
      cell("International bank syndicate (debt)"),
      cell("Banks — backed by Sinosure insurance"),
      cell("Industrial capacity, materials exports, a local JV partner"),
      cell("COBCO · Morocco", true),
    ],
  ];
  s.addTable(tableData, {
    x: M, y: 1.5, w: W - 2 * M, colW: [1.9, 2.9, 2.7, 3.3, 1.31],
    rowH: [0.5, 1.25, 1.25, 1.25],
    border: { type: "solid", pt: 1, color: BORDER },
    fill: { color: CARD },
    valign: "middle",
    margin: [6, 8, 6, 8],
  });
  s.addText("In every case the host country holds equity or a local stake — these are partnerships, not enclaves.", {
    x: M, y: 6.35, w: W - 2 * M, h: 0.45, fontFace: BODY, fontSize: 14, italic: true, bold: true, color: DARK, align: "center", margin: 0,
  });
  footer(s, 8);

  // =================================================================
  // SLIDE 9 — WHAT IT MEANS (opportunities / watch-outs)
  // =================================================================
  s = pres.addSlide();
  s.background = { color: DARK };
  s.addText("What this means for the Global South", { x: M, y: 0.5, w: 12, h: 0.7, fontFace: HEAD, fontSize: 30, bold: true, color: "FFFFFF", margin: 0 });

  const colW = (W - 2 * M - 0.5) / 2;
  // opportunities
  const ox = M, oy = 1.5, ocH = 4.7;
  s.addShape(pres.shapes.RECTANGLE, { x: ox, y: oy, w: colW, h: ocH, fill: { color: "0F4A47" } });
  s.addShape(pres.shapes.RECTANGLE, { x: ox, y: oy, w: colW, h: 0.7, fill: { color: TEAL } });
  s.addText("OPPORTUNITIES", { x: ox + 0.3, y: oy, w: colW - 0.6, h: 0.7, fontFace: BODY, fontSize: 15, bold: true, color: "FFFFFF", charSpacing: 2, margin: 0, valign: "middle" });
  const opps = [
    "Move up the value chain — from exporting raw minerals to making materials, cells and EVs.",
    "Host states are not passive sites: they take equity (IBC / Antam, CDG) or local JV stakes (Al Mada).",
    "Jobs, technology transfer, and tariff-free export platforms into the EU and US.",
  ];
  opps.forEach((t, i) => {
    const yy = oy + 1.0 + i * 1.5;
    s.addImage({ data: I.check, x: ox + 0.32, y: yy + 0.03, w: 0.34, h: 0.34 });
    s.addText(t, { x: ox + 0.85, y: yy - 0.05, w: colW - 1.15, h: 1.05, fontFace: BODY, fontSize: 13.5, color: "E7F0EF", margin: 0, valign: "top", lineSpacingMultiple: 1.05 });
  });
  // watch-outs
  const wx = M + colW + 0.5;
  s.addShape(pres.shapes.RECTANGLE, { x: wx, y: oy, w: colW, h: ocH, fill: { color: "3B3320" } });
  s.addShape(pres.shapes.RECTANGLE, { x: wx, y: oy, w: colW, h: 0.7, fill: { color: AMBER } });
  s.addText("WATCH-OUTS", { x: wx + 0.3, y: oy, w: colW - 0.6, h: 0.7, fontFace: BODY, fontSize: 15, bold: true, color: "FFFFFF", charSpacing: 2, margin: 0, valign: "middle" });
  const watch = [
    "Concentration: technology, financing and offtake remain largely Chinese-anchored.",
    "Debt and currency exposure where projects lean on syndicated loans.",
    "Environmental and governance stakes — nickel processing, water use and land.",
  ];
  watch.forEach((t, i) => {
    const yy = oy + 1.0 + i * 1.5;
    s.addImage({ data: I.warn, x: wx + 0.32, y: yy + 0.03, w: 0.34, h: 0.34 });
    s.addText(t, { x: wx + 0.85, y: yy - 0.05, w: colW - 1.15, h: 1.05, fontFace: BODY, fontSize: 13.5, color: "F2E9D8", margin: 0, valign: "top", lineSpacingMultiple: 1.05 });
  });
  footer(s, 9, true);

  // =================================================================
  // SLIDE 10 — TAKEAWAYS
  // =================================================================
  s = pres.addSlide();
  s.background = { color: BG };
  s.addShape(pres.shapes.OVAL, { x: M, y: 0.5, w: 0.78, h: 0.78, fill: { color: AMBER }, shadow: makeShadow() });
  s.addImage({ data: I.bulbW, x: M + 0.24, y: 0.71, w: 0.32, h: 0.36 });
  s.addText("Takeaways", { x: M + 1.0, y: 0.5, w: 11, h: 0.78, fontFace: HEAD, fontSize: 30, bold: true, color: INK, margin: 0, valign: "middle" });

  const takes = [
    { c: TEAL, h: "The financing model follows the asset.", d: "Minerals invite an equity JV; an export platform attracts blended equity; bankable materials suit green debt." },
    { c: AMBER, h: "Host-state co-investment is becoming the norm.", d: "Equity stakes, sovereign funds and local partners now sit inside the deals — not just on the sidelines." },
    { c: BLUE, h: "Export-credit insurance is the quiet enabler.", d: "Sinosure cover pulls global commercial banks into projects they would otherwise hesitate to fund." },
    { c: DARK, h: "The Global South's EV supply chain is being financed by Chinese-anchored structures.", d: "That brings real industrialization — and real questions about balance and dependence." },
  ];
  const tTop = 1.7, tgap = 0.28, tH = (4.9 - tgap * 3) / 4;
  takes.forEach((tk, i) => {
    const y = tTop + i * (tH + tgap);
    s.addShape(pres.shapes.RECTANGLE, { x: M, y, w: W - 2 * M, h: tH, fill: { color: CARD }, line: { color: BORDER, width: 1 }, shadow: makeShadow() });
    s.addShape(pres.shapes.RECTANGLE, { x: M, y, w: 0.08, h: tH, fill: { color: tk.c } });
    s.addText(String(i + 1), { x: M + 0.3, y, w: 0.8, h: tH, fontFace: HEAD, fontSize: 30, bold: true, color: tk.c, align: "center", valign: "middle", margin: 0 });
    s.addText(
      [
        { text: tk.h + "   ", options: { bold: true, fontSize: 15, color: INK, fontFace: BODY } },
        { text: tk.d, options: { fontSize: 13, color: "44504E", fontFace: BODY } },
      ],
      { x: M + 1.25, y: y + 0.05, w: W - 2 * M - 1.6, h: tH - 0.1, margin: 0, valign: "middle", lineSpacingMultiple: 1.03 }
    );
  });
  footer(s, 10);

  // =================================================================
  // SLIDE 11 — CLOSING
  // =================================================================
  s = pres.addSlide();
  s.background = { color: DARK };
  s.addShape(pres.shapes.OVAL, { x: M, y: 1.2, w: 0.22, h: 0.22, fill: { color: TEAL } });
  s.addShape(pres.shapes.OVAL, { x: M + 0.32, y: 1.2, w: 0.22, h: 0.22, fill: { color: AMBER } });
  s.addShape(pres.shapes.OVAL, { x: M + 0.64, y: 1.2, w: 0.22, h: 0.22, fill: { color: BLUE } });
  s.addText("Thank you", { x: M, y: 1.7, w: 12, h: 1.0, fontFace: HEAD, fontSize: 48, bold: true, color: "FFFFFF", margin: 0 });
  s.addText("Three countries, three financing models — one supply chain taking shape across the Global South.", {
    x: M, y: 2.85, w: 11, h: 0.8, fontFace: BODY, fontSize: 18, color: "CFE0DE", margin: 0,
  });

  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 4.2, w: W - 2 * M, h: 2.0, fill: { color: "0F4A47" } });
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 4.2, w: 0.08, h: 2.0, fill: { color: "D9B25A" } });
  s.addText("SOURCES", { x: M + 0.35, y: 4.35, w: 11, h: 0.3, fontFace: BODY, fontSize: 12, bold: true, color: "D9B25A", charSpacing: 2, margin: 0 });
  s.addText(
    [
      { text: "Project data — BRI Renewable Energy Database (2026).", options: { breakLine: true, fontSize: 13, color: "E7F0EF", fontFace: BODY, paraSpaceAfter: 6 } },
      { text: "Case details — CATL, Gotion High-Tech and COBCO / CNGR corporate disclosures.", options: { breakLine: true, fontSize: 13, color: "E7F0EF", fontFace: BODY, paraSpaceAfter: 6 } },
      { text: "Reporting — Caixin Global, electrive, Energy-Storage.News, Benchmark Minerals, Morocco World News, Mysteel (2024–2025).", options: { fontSize: 13, color: "E7F0EF", fontFace: BODY } },
    ],
    { x: M + 0.35, y: 4.75, w: W - 2 * M - 0.7, h: 1.35, margin: 0, valign: "top", lineSpacingMultiple: 1.05 }
  );

  const out = "Financing_Chinese_EV_Supply_Chain_GlobalSouth.pptx";
  await pres.writeFile({ fileName: out });
  console.log("WROTE " + out);
}

main().catch((e) => { console.error(e); process.exit(1); });
