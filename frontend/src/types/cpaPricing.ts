import type { CPAModelPricing } from "./settings";

export interface CPAPricingInventory {
  pricing: CPAModelPricing;
  models: {
    model: string;
    request_count: number;
    token_count: number;
    missing: boolean;
    pricing_model: string | null;
  }[];
  missing_model_count: number;
  unpriced_request_count: number;
  source: string;
  source_url: string;
  generated_at: string;
}
export interface CPAPricingSync extends CPAPricingInventory {
  added: {
    model: string;
    source_model: string;
    price: CPAModelPricing[string];
  }[];
  unresolved: { model: string; reason: string }[];
}
