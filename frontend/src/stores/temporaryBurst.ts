import { shallowRef } from "vue";
import type { TemporaryBurstData } from "@/types/temporaryBurst";

// Shared by the dashboard card and the persistent layout status strip.
export const temporaryBurstState = shallowRef<TemporaryBurstData | null>(null);
