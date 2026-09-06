import type { CorrectionBreakdown } from "@/types/common";

/** Prefer total completeness; old servers only exposed the FAST flag. */
export function correctionCalculated(
  value: CorrectionBreakdown & { fast_correction_calculated?: boolean },
): boolean {
  return (
    value.correction_calculated ??
    value.correction_facts_complete ??
    value.fast_correction_calculated ??
    false
  );
}
