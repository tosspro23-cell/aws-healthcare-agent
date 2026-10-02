import { useEffect, useState } from "react";
import { getPatientData, ApiError, type PatientData, type QuestionnaireFact } from "../api";
import { config } from "../config";

type LoadState = { status: "loading" } | { status: "error"; message: string } | { status: "loaded"; data: PatientData };

/** Maps a biomarker's classification string onto the existing, generic
 * `badge-pill` color variants (already used for run status, engine,
 * persona, and mode tags in `ConversationPanel.tsx` -- not safety-
 * specific) instead of inventing new colors for this panel. A fixed
 * lookup, not a substring heuristic, so "high" and "borderline_high" map
 * to deliberately different variants; anything this doesn't recognize
 * degrades to "neutral" rather than guessing. */
const CLASSIFICATION_BADGE: Record<string, "safe" | "unsafe" | "pending" | "neutral"> = {
  adequate: "safe",
  elevated: "pending",
  borderline: "pending",
  borderline_high: "pending",
  suboptimal: "pending",
  high: "unsafe",
  low: "unsafe",
};

function badgeVariant(classification: string | null): string {
  return (classification && CLASSIFICATION_BADGE[classification]) || "neutral";
}

/** Humanizes the sample dataset's snake_case enum-like values
 * ("3_4_days_per_week" -> "3-4 days/week"). Tuned to the actual values in
 * data/sample_questionnaire_context.json, not a generic humanizer -- a
 * value this doesn't special-case still becomes readable (underscores to
 * spaces), just not as polished. */
const SPECIAL_CASE_VALUES: Record<string, string> = {
  "3_4_days_per_week": "3-4 days/week",
  "0_1_servings_per_day": "0-1 servings/day",
  less_than_60_min_per_week: "less than 60 min/week",
  "5_6_hours": "5-6 hours",
};

function humanizeValue(value: string): string {
  return SPECIAL_CASE_VALUES[value] ?? value.replace(/_/g, " ");
}

function humanizeFieldName(field: string): string {
  const afterCategory = field.includes(".") ? field.slice(field.indexOf(".") + 1) : field;
  return afterCategory.replace(/_/g, " ");
}

function groupByCategory(facts: QuestionnaireFact[]): [string, QuestionnaireFact[]][] {
  const groups = new Map<string, QuestionnaireFact[]>();
  for (const fact of facts) {
    const category = fact.field.includes(".") ? fact.field.split(".")[0] : "other";
    groups.set(category, [...(groups.get(category) ?? []), fact]);
  }
  return [...groups.entries()];
}

/** The rail's second tab (see `ControlRail.tsx`): a read-only view of the
 * exact raw source records (bloodwork panel, questionnaire responses) an
 * answer's `grounded_facts` cite -- lets a reviewer independently verify
 * a claim against the ground truth, not just see which fact was cited.
 * Fixed to `config.demoUserId` -- this app has exactly one demo patient,
 * no selector needed.
 *
 * Deliberately omits questionnaire preferences/unknowns/declined from
 * display (the API response carries them; this panel just doesn't render
 * them yet) -- kept to the facts and cautions an answer's reasoning
 * actually draws on, not every field the dataset happens to have. */
export function PatientDataPanel() {
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    getPatientData(config.demoUserId)
      .then((data) => {
        if (!cancelled) setState({ status: "loaded", data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ status: "error", message: err instanceof ApiError ? err.message : String(err) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") return <p className="meta patient-data-empty">Loading patient data…</p>;
  if (state.status === "error") return <p className="error patient-data-empty">{state.message}</p>;

  const { profile, bloodwork, questionnaire } = state.data;
  const latest = bloodwork.latest_panel;

  return (
    <div className="patient-data-panel">
      <div className="rail-section-label">Profile</div>
      <p className="patient-summary">
        {profile.display_name}
        {profile.age !== null ? `, ${profile.age}` : ""}
        {profile.sex ? `, ${profile.sex}` : ""}
      </p>
      {profile.medications.length > 0 && <p className="meta">Medications: {profile.medications.map((m) => m.name).join(", ")}</p>}
      {profile.allergies.length > 0 && <p className="meta">Allergies: {profile.allergies.map((a) => a.name).join(", ")}</p>}

      <div className="rail-section-label">Bloodwork{latest ? ` — ${latest.measurement_date}` : ""}</div>
      {!latest && <p className="meta">No bloodwork on file.</p>}
      {latest && (
        <ul className="biomarker-list">
          {latest.biomarkers.map((b) => (
            <li key={b.concept_id} className="biomarker-row">
              <span className="biomarker-name">{b.display_name}</span>
              <span className="biomarker-value">
                {b.value} {b.unit}
              </span>
              {b.classification && <span className={`badge-pill ${badgeVariant(b.classification)}`}>{b.classification.replace(/_/g, " ")}</span>}
            </li>
          ))}
        </ul>
      )}

      {bloodwork.previous_panels.length > 0 && (
        <details className="previous-panels">
          <summary>
            Previous panel{bloodwork.previous_panels.length > 1 ? "s" : ""} ({bloodwork.previous_panels.length})
          </summary>
          {bloodwork.previous_panels.map((panel) => (
            <div key={panel.panel_id} className="previous-panel">
              <p className="meta">{panel.measurement_date}</p>
              <ul className="biomarker-list">
                {panel.biomarkers.map((b) => (
                  <li key={b.concept_id} className="biomarker-row">
                    <span className="biomarker-name">{b.display_name}</span>
                    <span className="biomarker-value">
                      {b.value} {b.unit}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </details>
      )}

      <div className="rail-section-label">Questionnaire</div>
      {groupByCategory(questionnaire.facts).map(([category, facts]) => (
        <div key={category} className="fact-group">
          <div className="fact-group-label">{category}</div>
          <ul className="fact-list">
            {facts.map((f) => (
              <li key={f.field}>
                {humanizeFieldName(f.field)}: <strong>{humanizeValue(f.value)}</strong>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {questionnaire.cautions.length > 0 && (
        <div className="fact-group">
          <div className="fact-group-label">Cautions</div>
          <ul className="fact-list">
            {questionnaire.cautions.map((c, i) => (
              <li key={i}>{c.detail}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
