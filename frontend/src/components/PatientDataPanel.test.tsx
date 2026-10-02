import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { PatientDataPanel } from "./PatientDataPanel";
import * as api from "../api";
import { ApiError, type PatientData } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, getPatientData: vi.fn() };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function patientData(overrides: Partial<PatientData> = {}): PatientData {
  return {
    user_id: "user_demo_001",
    profile: {
      user_id: "user_demo_001",
      display_name: "Alex",
      age: 42,
      sex: "female",
      country: "PT",
      height_cm: 168,
      weight_kg: 81,
      known_conditions: [],
      medications: [],
      allergies: [],
    },
    bloodwork: {
      user_id: "user_demo_001",
      latest_panel: {
        panel_id: "panel_2026_05_06_demo",
        measurement_date: "2026-05-06",
        biomarkers: [
          { concept_id: "ldl_c_mg_dl", display_name: "LDL-C", value: 162, unit: "mg/dL", classification: "high", classification_basis: null, action_fields: [], source: null },
        ],
        overall_flags: [],
      },
      previous_panels: [],
    },
    questionnaire: {
      user_id: "user_demo_001",
      completed_at: null,
      facts: [{ field: "nutrition.sugary_foods", value: "3_4_days_per_week", state: "known_present", source: "questionnaire" }],
      cautions: [],
      style_hint: null,
    },
    ...overrides,
  };
}

describe("PatientDataPanel", () => {
  it("shows a loading state, then renders a biomarker with its classification badge", async () => {
    let resolve!: (data: PatientData) => void;
    vi.mocked(api.getPatientData).mockReturnValue(new Promise((res) => (resolve = res)));

    render(<PatientDataPanel />);
    expect(screen.getByText(/Loading patient data/)).toBeInTheDocument();

    resolve(patientData());
    await waitFor(() => expect(screen.getByText("LDL-C")).toBeInTheDocument());
    expect(screen.getByText("high")).toBeInTheDocument();
  });

  it("renders questionnaire facts grouped by category with humanized values", async () => {
    vi.mocked(api.getPatientData).mockResolvedValue(patientData());
    render(<PatientDataPanel />);

    await waitFor(() => expect(screen.getByText("nutrition")).toBeInTheDocument());
    expect(screen.getByText("3-4 days/week")).toBeInTheDocument();
  });

  it("renders the error state on an API failure", async () => {
    vi.mocked(api.getPatientData).mockRejectedValue(new ApiError(404, "No data on file for user_id='user_demo_001'."));
    render(<PatientDataPanel />);

    await waitFor(() => expect(screen.getByText(/No data on file/)).toBeInTheDocument());
  });
});
