/** Shapes emitted by `python -m pipeline.build`. Kept in sync by hand. */

/** A count withheld because it would be re-identifying. */
export interface Suppressed {
  suppressed: true;
  lt: number;
}

export type Count = number | Suppressed;

export interface SourceRef {
  source: string;
  url?: string;
  as_of: string | null;
  retrieved_at?: string | null;
  note?: string;
}

export interface Demographics {
  measure: string;
  measure_note: string;
  window_start: string;
  window_end: string;
  attribution_basis: string;
  suppression_threshold: number;
  stays_in_window: number;
  age_bands: Record<string, Count>;
  age_known: Count;
  age_median: number | null;
  gender: Record<string, Count>;
  top_citizenship: Record<string, Count>;
  release_reason: Record<string, Count>;
  book_in_criminality: Record<string, Count>;
  classification: Record<string, Count>;
  length_of_stay_days: {
    median: number | null;
    p90: number | null;
    n_completed: Count;
  };
  bond: {
    median_set: number | null;
    n_set: Count;
    n_posted: Count;
  };
}

export interface FacilityProps {
  /** Flat styling duplicates — see pipeline/build.py. */
  adp: number | null;
  rating: string | null;
  contract: string | null;
  /** 1 when the coordinates were derived rather than published. */
  approx: 0 | 1;

  /** How the coordinates were arrived at: 'exact', 'city_centroid', … */
  location_precision: string;
  /** Plain-language explanation, present whenever `approx` is 1. */
  location_note: string | null;
  location_matched_to?: string;

  code: string;
  name: string | null;
  address: string | null;
  city: string | null;
  county: string | null;
  state: string | null;
  zip: string | null;
  field_office: string | null;
  federal_court_district: string | null;
  federal_court_circuit: string | null;

  population: {
    ddp_avg_daily_trailing_year: number | null;
    ddp_max_daily_trailing_year: number | null;
    ice_fy_adp_total: number | null;
    ice_fy_adp_male_criminal: number | null;
    ice_fy_adp_male_noncriminal: number | null;
    ice_fy_adp_female_criminal: number | null;
    ice_fy_adp_female_noncriminal: number | null;
    ice_fy_adp_mandatory: number | null;
    ddp_figure_stale: string | null;
  };
  classification_adp: Record<string, number | null>;
  operator: {
    contract_type: string | null;
    company: string | null;
    company_status: string;
  };
  inspection: {
    last_type: string | null;
    last_end_date: string | null;
    last_standard: string | null;
    last_rating: string | null;
  };
  avg_length_of_stay_days: number | null;
  guaranteed_minimum_beds: number | null;
  sex_designation: string | null;
  demographics: Demographics | null;
  not_publicly_reported: Record<string, string>;
  sources: Record<string, SourceRef | null>;
  has_ice_attributes: boolean;
  directions_url?: string;
}

export interface BuildMeta {
  built_at: string;
  sources: Array<{
    source: string;
    url: string;
    as_of: string | null;
    retrieved_at?: string | null;
    sha256?: string | null;
    local_file: string;
    rows: number;
  }>;
  crosswalk: { matched: number; total: number; rate: number };
  counts: {
    facilities_total: number;
    mapped: number;
    unplaced: number;
    exact_location: number;
    approximate_location: number;
    with_ice_attributes: number;
    with_demographics: number;
  };
}

export interface FacilityCollection {
  type: 'FeatureCollection';
  metadata: BuildMeta;
  features: Array<{
    type: 'Feature';
    geometry: { type: 'Point'; coordinates: [number, number] };
    properties: FacilityProps;
  }>;
}

export interface UnplacedFile {
  metadata: BuildMeta;
  facilities: FacilityProps[];
}

export function isSuppressed(v: Count | null | undefined): v is Suppressed {
  return typeof v === 'object' && v !== null && 'suppressed' in v;
}
