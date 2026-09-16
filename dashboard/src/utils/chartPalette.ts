/**
 * Chart data-mark colors — kept as the FA template's validated categorical
 * set (contrast-checked against a dark surface via the dataviz skill's
 * validate_palette.js). Intentionally a separate system from the
 * --color-fa-* UI chrome tokens — data-encoding colors and brand decoration
 * serve different jobs. CHART_SURFACE/AXIS_MUTED/TEXT_SECONDARY below are
 * updated to match Heart Smart's retinted warm-dark surface (index.css).
 */
export const CATEGORICAL = [
  '#d95926', // 1 orange — primary series (e.g. Ads)
  '#3987e5', // 2 blue — secondary series (e.g. Organic)
  '#199e70', // 3 aqua
  '#c98500', // 4 yellow
  '#d55181', // 5 magenta
  '#008300', // 6 green
  '#9085e9', // 7 violet
  '#e66767', // 8 red
] as const

export const CHART_SURFACE = '#211313'
export const GRIDLINE = 'rgba(237, 237, 238, 0.1)'
export const AXIS_MUTED = '#a37d7d'
export const TEXT_SECONDARY = '#d9a3a3'
