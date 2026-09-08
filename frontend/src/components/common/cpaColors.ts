// Participant identity, rather than sorted display position, owns the color.
const colors = [
  "#3b82f6",
  "#d97706",
  "#8b5cf6",
  "#059669",
  "#db2777",
  "#0891b2",
];
export const cpaColor = (id: number) => colors[Math.abs(id) % colors.length]!;
