// Fixed categorical order, validated via the dataviz skill's
// scripts/validate_palette.js against this app's dark chart surface
// (#0D1526 / --surface-2): all checks pass (lightness band, chroma floor,
// CVD separation >= 8 ΔE, normal-vision floor, contrast >= 3:1). Colors are
// assigned by policy IDENTITY in this fixed order — never reassigned or
// cycled based on which policies happen to be selected in a given run.
export const POLICY_COLORS: Record<string, string> = {
  none: '#3987e5',            // blue
  random: '#d95926',          // orange
  critical_aware: '#199e70',  // aqua
  grace_period: '#c98500',    // yellow
  custom: '#d55181',          // magenta — "My Algorithm"
}

export const POLICY_LABELS: Record<string, string> = {
  none: 'none',
  random: 'random',
  critical_aware: 'critical_aware',
  grace_period: 'grace_period',
  custom: 'my algorithm',
}

export function colorFor(policyKey: string): string {
  return POLICY_COLORS[policyKey] ?? '#898781'
}

export const CHART_CHROME = {
  gridColor: 'rgba(228, 233, 242, 0.08)',
  axisColor: '#4B5872',
  tickColor: '#7C8AA5',
  surface: '#0D1526',
}
