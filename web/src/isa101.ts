// ISA-101 status colors used by the viewer (HIS Controls ignition-conventions.md, PLAN 1.11).
// Gray means normal or stopped; color is reserved for running state and abnormal conditions.
export const ISA101 = {
  background: 0x1f2a36,
  normalText: 0xe5e7eb,
  running: 0x22c55e,
  stopped: 0x9ca3af,
  alarmHigh: 0xef4444,
  alarmMedium: 0xfacc15,
  highlight: 0x60a5fa,
} as const;

export type Isa101Color = keyof typeof ISA101;

/** CSS hex string (#RRGGBB) for a palette entry. */
export function cssColor(name: Isa101Color): string {
  return `#${ISA101[name].toString(16).padStart(6, "0").toUpperCase()}`;
}
