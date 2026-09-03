PRAGMA foreign_keys = ON;

DROP VIEW IF EXISTS marker_search_view;
DROP TABLE IF EXISTS marker_action_field;
DROP TABLE IF EXISTS marker_alias;
DROP TABLE IF EXISTS biomarker_catalog;

CREATE TABLE biomarker_catalog (
  biomarker_catalog_id TEXT PRIMARY KEY,
  biomarker_name TEXT NOT NULL UNIQUE,
  display_name TEXT,
  public_display_name TEXT,
  applies_to_sex TEXT DEFAULT 'any',
  canonical_biomarker_name TEXT,
  source_type TEXT DEFAULT 'MEASURED',
  tier INTEGER,
  importance TEXT,
  clinical_rationale TEXT,
  biomarker_function TEXT,
  healthspan_importance TEXT,
  long_description TEXT,
  unit TEXT,
  direction TEXT,
  optimal_range_min REAL,
  optimal_range_max REAL,
  suboptimal_range_min REAL,
  suboptimal_range_max REAL,
  adequate_range_min REAL,
  adequate_range_max REAL,
  pathological_low_min REAL,
  pathological_low_max REAL,
  pathological_high_min REAL,
  pathological_high_max REAL,
  domain_key TEXT,
  domain_label TEXT,
  subdomain_key TEXT,
  subdomain_label TEXT,
  aliases_json TEXT NOT NULL DEFAULT '[]',
  acronyms_json TEXT NOT NULL DEFAULT '[]',
  action_fields_json TEXT NOT NULL DEFAULT '[]',
  unit_policy_json TEXT NOT NULL DEFAULT '{}',
  interpretation_notes TEXT,
  safety_notes TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT
);

CREATE TABLE marker_alias (
  biomarker_name TEXT NOT NULL REFERENCES biomarker_catalog(biomarker_name) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  language TEXT DEFAULT 'en',
  PRIMARY KEY (biomarker_name, normalized_alias)
);

CREATE TABLE marker_action_field (
  biomarker_name TEXT NOT NULL REFERENCES biomarker_catalog(biomarker_name) ON DELETE CASCADE,
  action_field TEXT NOT NULL,
  PRIMARY KEY (biomarker_name, action_field)
);

CREATE INDEX idx_biomarker_catalog_domain ON biomarker_catalog(domain_key, subdomain_key);
CREATE INDEX idx_biomarker_catalog_active ON biomarker_catalog(is_active);
CREATE INDEX idx_marker_alias_norm ON marker_alias(normalized_alias);
CREATE INDEX idx_marker_action_field ON marker_action_field(action_field);

INSERT INTO biomarker_catalog (
  biomarker_catalog_id, biomarker_name, display_name, public_display_name,
  applies_to_sex, canonical_biomarker_name, source_type, tier, importance,
  clinical_rationale, biomarker_function, healthspan_importance, long_description,
  unit, direction, optimal_range_min, optimal_range_max, suboptimal_range_min,
  suboptimal_range_max, adequate_range_min, adequate_range_max,
  pathological_low_min, pathological_low_max, pathological_high_min,
  pathological_high_max, domain_key, domain_label, subdomain_key, subdomain_label,
  aliases_json, acronyms_json, action_fields_json, unit_policy_json,
  interpretation_notes, safety_notes
) VALUES
('mock-cat-001','ldl_c_mg_dl','LDL-C','LDL cholesterol','any','ldl_c_mg_dl','MEASURED',1,'high','LDL-C is a core lipid marker used in cardiovascular risk discussions.','Carries cholesterol particles in blood.','Useful for heart and circulation prioritization.','Fake challenge marker. Use as a grounded lipid signal, not as a diagnosis.','mg/dL','lower_is_better',50,99,100,159,50,129,NULL,NULL,190,NULL,'heart_and_circulation','Heart and circulation','cholesterol_profile','Cholesterol profile','["LDL cholesterol","LDL-C","low density lipoprotein cholesterol"]','["LDL","LDL-C"]','["NUTRITION","EXERCISE","MEDICAL"]','{"canonical_unit":"mg/dL","accepted_units":["mg/dL","mmol/L"],"conversion_note":"mock only"}','High LDL should be discussed with overall risk context.','Do not diagnose cardiovascular disease or recommend medication changes.'),
('mock-cat-002','hdl_c_mg_dl','HDL-C','HDL cholesterol','any','hdl_c_mg_dl','MEASURED',2,'medium','HDL-C is interpreted alongside LDL-C and triglycerides.','Participates in reverse cholesterol transport.','Adds context to lipid panel interpretation.','Fake challenge marker. Use as context, not as a single decisive risk marker.','mg/dL','higher_is_better',50,90,40,49,40,90,NULL,NULL,NULL,NULL,'heart_and_circulation','Heart and circulation','cholesterol_profile','Cholesterol profile','["HDL cholesterol","HDL-C","high density lipoprotein cholesterol"]','["HDL","HDL-C"]','["EXERCISE","NUTRITION"]','{"canonical_unit":"mg/dL","accepted_units":["mg/dL","mmol/L"],"conversion_note":"mock only"}','Discuss HDL with the full lipid profile.','Do not overstate protection from one HDL value.'),
('mock-cat-003','triglycerides_mg_dl','Triglycerides','Triglycerides','any','triglycerides_mg_dl','MEASURED',1,'high','Triglycerides can support a broader metabolic and lipid-pattern discussion.','Blood fats used for energy storage and transport.','Useful for metabolic and heart-health prioritization.','Fake challenge marker. May be affected by fasting status and recent intake.','mg/dL','lower_is_better',40,149,150,199,40,199,NULL,NULL,500,NULL,'metabolic_and_energy','Metabolic and energy','lipid_energy','Lipid energy','["Triglycerides","triacylglycerols","TAG"]','["TG","TAG"]','["NUTRITION","EXERCISE"]','{"canonical_unit":"mg/dL","accepted_units":["mg/dL","mmol/L"],"conversion_note":"mock only"}','If fasting status is unknown, avoid overconfident trend claims.','Very high values need clinician review; do not provide emergency triage unless symptoms are reported.'),
('mock-cat-004','total_cholesterol_mg_dl','Total cholesterol','Total cholesterol','any','total_cholesterol_mg_dl','MEASURED',3,'medium','Total cholesterol is a broad lipid measure best interpreted with LDL, HDL, and triglycerides.','Aggregate cholesterol carried by lipoproteins.','Contextual lipid marker.','Fake challenge marker. Avoid using it alone for prioritization when detailed lipid markers exist.','mg/dL','lower_is_better',120,199,200,239,120,239,NULL,NULL,240,NULL,'heart_and_circulation','Heart and circulation','cholesterol_profile','Cholesterol profile','["Total cholesterol","cholesterol total"]','["TC"]','["NUTRITION","EXERCISE","MEDICAL"]','{"canonical_unit":"mg/dL","accepted_units":["mg/dL","mmol/L"],"conversion_note":"mock only"}','Use as secondary context when LDL and HDL are available.','Do not derive treatment decisions from total cholesterol alone.'),
('mock-cat-005','non_hdl_c_mg_dl','Non-HDL-C','Non-HDL cholesterol','any','non_hdl_c_mg_dl','DERIVED',2,'medium','Non-HDL-C can summarize atherogenic cholesterol burden in a lipid panel.','Calculated as total cholesterol minus HDL-C.','Useful when triglycerides are elevated.','Fake derived marker. Only use when explicitly present or deterministically calculated by code.','mg/dL','lower_is_better',70,129,130,189,70,159,NULL,NULL,190,NULL,'heart_and_circulation','Heart and circulation','cholesterol_profile','Cholesterol profile','["Non-HDL cholesterol","non HDL C","non-HDL-C"]','["non-HDL","non-HDL-C"]','["NUTRITION","EXERCISE","MEDICAL"]','{"canonical_unit":"mg/dL","accepted_units":["mg/dL"],"formula":"total_cholesterol_mg_dl - hdl_c_mg_dl"}','Do not invent non-HDL-C if inputs are missing.','Derived values must be marked as derived.'),
('mock-cat-006','apob_mg_dl','ApoB','Apolipoprotein B','any','apob_mg_dl','MEASURED',2,'medium','ApoB can represent the number of atherogenic particles.','Structural protein on atherogenic lipoproteins.','May refine heart-risk discussions when available.','Fake challenge marker not present in the sample bloodwork by default.','mg/dL','lower_is_better',40,89,90,119,40,119,NULL,NULL,120,NULL,'heart_and_circulation','Heart and circulation','cholesterol_profile','Cholesterol profile','["Apolipoprotein B","Apo B","ApoB"]','["ApoB"]','["NUTRITION","EXERCISE","MEDICAL"]','{"canonical_unit":"mg/dL","accepted_units":["mg/dL"]}','If absent, do not imply it was measured.','Do not recommend advanced lipid testing as mandatory.'),
('mock-cat-007','lp_a_mg_dl','Lp(a)','Lipoprotein(a)','any','lp_a_mg_dl','MEASURED',2,'medium','Lp(a) is a genetically influenced lipid marker sometimes used in cardiovascular risk assessment.','LDL-like particle with apolipoprotein(a).','Can add inherited-risk context when measured.','Fake challenge marker not present in the default sample panel.','mg/dL','lower_is_better',0,29,30,49,0,49,NULL,NULL,50,NULL,'heart_and_circulation','Heart and circulation','cholesterol_profile','Cholesterol profile','["Lipoprotein a","Lipoprotein(a)","Lp(a)"]','["Lp(a)"]','["MEDICAL"]','{"canonical_unit":"mg/dL","accepted_units":["mg/dL","nmol/L"],"conversion_note":"do not convert in challenge"}','Avoid unit conversion between mg/dL and nmol/L without validated policy.','Do not infer inherited risk if Lp(a) is missing.'),
('mock-cat-008','hba1c_percent','HbA1c','HbA1c','any','hba1c_percent','MEASURED',1,'high','HbA1c reflects average blood glucose over roughly 2 to 3 months.','Glycated hemoglobin percentage.','Core metabolic marker for glucose-priority discussions.','Fake challenge marker. Use supplied classification and avoid diagnosis.','%','lower_is_better',4.8,5.6,5.7,6.4,4.8,6.4,NULL,NULL,6.5,NULL,'metabolic_and_energy','Metabolic and energy','glucose_regulation','Glucose regulation','["Hemoglobin A1c","Glycated hemoglobin","HbA1c","A1C"]','["A1C","HbA1c"]','["NUTRITION","EXERCISE","MEDICAL"]','{"canonical_unit":"%","accepted_units":["%"],"conversion_note":"mock only"}','A1C in this mock range can be described as elevated risk context, not a diagnosis.','Do not diagnose diabetes or prediabetes from this dataset.'),
('mock-cat-009','fasting_glucose_mg_dl','Fasting glucose','Fasting glucose','any','fasting_glucose_mg_dl','MEASURED',1,'high','Fasting glucose adds point-in-time glucose context alongside A1C.','Blood glucose measured after fasting.','Useful for metabolic-priority discussions.','Fake challenge marker. Interpret with fasting status and clinician context.','mg/dL','lower_is_better',70,99,100,125,70,125,NULL,NULL,126,NULL,'metabolic_and_energy','Metabolic and energy','glucose_regulation','Glucose regulation','["Fasting blood glucose","fasting plasma glucose","glucose fasting"]','["FPG","FBG"]','["NUTRITION","EXERCISE","MEDICAL"]','{"canonical_unit":"mg/dL","accepted_units":["mg/dL","mmol/L"],"conversion_note":"mock only"}','If fasting status is unknown, state the limitation.','Do not diagnose diabetes from one result.'),
('mock-cat-010','fasting_insulin_uiu_ml','Fasting insulin','Fasting insulin','any','fasting_insulin_uiu_ml','MEASURED',3,'medium','Fasting insulin can add metabolic context but is not always part of routine panels.','Insulin concentration in fasting blood.','Can support insulin-sensitivity discussions when measured.','Fake challenge marker absent from default sample panel.','uIU/mL','lower_is_better',2,8,9,19,2,19,NULL,NULL,20,NULL,'metabolic_and_energy','Metabolic and energy','glucose_regulation','Glucose regulation','["Fasting insulin","insulin fasting"]','["INS"]','["NUTRITION","EXERCISE","MEDICAL"]','{"canonical_unit":"uIU/mL","accepted_units":["uIU/mL","mIU/L"]}','Do not discuss insulin level if it is missing.','Do not diagnose insulin resistance from this marker alone.'),
('mock-cat-011','hs_crp_mg_l','hs-CRP','High-sensitivity C-reactive protein','any','hs_crp_mg_l','MEASURED',2,'medium','hs-CRP is a non-specific inflammation marker.','Acute phase protein measured with high-sensitivity assay.','Can add inflammation and recovery context.','Fake challenge marker. Elevated values do not identify cause.','mg/L','lower_is_better',0,1.0,1.1,3.0,0,3.0,NULL,NULL,10,NULL,'inflammation_and_recovery','Inflammation and recovery','inflammation_markers','Inflammation markers','["High sensitivity CRP","hsCRP","C-reactive protein high sensitivity"]','["hs-CRP","CRP"]','["NUTRITION","EXERCISE","MIND","MEDICAL"]','{"canonical_unit":"mg/L","accepted_units":["mg/L"]}','Use as a broad context marker; do not name a cause.','Very high CRP context should be reviewed clinically.'),
('mock-cat-012','ferritin_ng_ml','Ferritin','Ferritin','any','ferritin_ng_ml','MEASURED',2,'medium','Ferritin reflects iron storage but can also rise with inflammation.','Iron storage protein.','Useful for energy, iron, and inflammation context.','Fake challenge marker not present in sample panel.','ng/mL','in_range_is_better',30,150,15,250,15,250,NULL,10,300,NULL,'metabolic_and_energy','Metabolic and energy','iron_status','Iron status','["Serum ferritin","ferritin"]','["FER"]','["NUTRITION","MEDICAL"]','{"canonical_unit":"ng/mL","accepted_units":["ng/mL","ug/L"]}','Interpret ferritin with blood count, inflammation, and sex context.','Do not recommend iron supplements without clinician review.'),
('mock-cat-013','vitamin_d_25oh_ng_ml','Vitamin D','25-OH vitamin D','any','vitamin_d_25oh_ng_ml','MEASURED',2,'medium','25-OH vitamin D is commonly used to assess vitamin D status.','Circulating vitamin D storage marker.','Can support bone and general health context.','Fake challenge marker. Do not provide dosing.','ng/mL','in_range_is_better',30,60,20,29,20,80,NULL,12,100,NULL,'metabolic_and_energy','Metabolic and energy','vitamins_minerals','Vitamins and minerals','["25 hydroxy vitamin D","25-OH vitamin D","Vitamin D 25-OH"]','["25(OH)D","Vit D"]','["NUTRITION","MEDICAL"]','{"canonical_unit":"ng/mL","accepted_units":["ng/mL","nmol/L"],"conversion_note":"mock only"}','Low vitamin D can be flagged for review.','Do not recommend high-dose vitamin D or product regimens.'),
('mock-cat-014','vitamin_b12_pg_ml','Vitamin B12','Vitamin B12','any','vitamin_b12_pg_ml','MEASURED',3,'medium','Vitamin B12 supports blood and nerve function context.','Water-soluble vitamin marker.','Can add fatigue/energy context when present.','Fake challenge marker not present in default sample panel.','pg/mL','in_range_is_better',300,900,200,299,200,1100,NULL,150,1200,NULL,'metabolic_and_energy','Metabolic and energy','vitamins_minerals','Vitamins and minerals','["Cobalamin","Vitamin B-12","B12"]','["B12"]','["NUTRITION","MEDICAL"]','{"canonical_unit":"pg/mL","accepted_units":["pg/mL","pmol/L"],"conversion_note":"mock only"}','Do not infer symptoms from B12 alone.','Do not prescribe injections or supplements.'),
('mock-cat-015','tsh_miu_l','TSH','Thyroid-stimulating hormone','any','tsh_miu_l','MEASURED',2,'medium','TSH is used in thyroid evaluation and depends on symptoms, medication, and other thyroid tests.','Pituitary hormone that signals the thyroid gland.','Can affect energy and metabolic context.','Fake challenge marker. Interpret carefully if thyroid medication is present.','mIU/L','in_range_is_better',0.5,3.5,3.6,5.0,0.4,5.0,NULL,0.1,10,NULL,'hormones_stress_and_sleep','Hormones, stress, and sleep','thyroid_axis','Thyroid axis','["Thyroid stimulating hormone","thyrotropin","TSH"]','["TSH"]','["MEDICAL"]','{"canonical_unit":"mIU/L","accepted_units":["mIU/L","uIU/mL"]}','Borderline TSH with levothyroxine should be reviewed with the treating clinician.','Do not suggest medication dose changes.'),
('mock-cat-016','free_t4_ng_dl','Free T4','Free thyroxine','any','free_t4_ng_dl','MEASURED',3,'medium','Free T4 can help interpret thyroid status with TSH.','Circulating free thyroxine hormone.','Adds thyroid-axis context when available.','Fake challenge marker absent from default sample panel.','ng/dL','in_range_is_better',0.8,1.8,0.7,2.0,0.7,2.0,NULL,0.5,2.5,NULL,'hormones_stress_and_sleep','Hormones, stress, and sleep','thyroid_axis','Thyroid axis','["Free thyroxine","FT4","free T4"]','["FT4"]','["MEDICAL"]','{"canonical_unit":"ng/dL","accepted_units":["ng/dL","pmol/L"],"conversion_note":"mock only"}','Do not infer free T4 if absent.','Do not provide thyroid medication advice.'),
('mock-cat-017','cortisol_morning_ug_dl','Morning cortisol','Morning cortisol','any','cortisol_morning_ug_dl','MEASURED',3,'low','Morning cortisol can be difficult to interpret and depends on timing and clinical context.','Glucocorticoid hormone marker.','May relate to stress-axis context when clinically indicated.','Fake challenge marker not present in sample panel.','ug/dL','in_range_is_better',6,18,4,22,4,22,NULL,2,30,NULL,'hormones_stress_and_sleep','Hormones, stress, and sleep','stress_axis','Stress axis','["AM cortisol","morning serum cortisol","cortisol AM"]','["AM cortisol"]','["MEDICAL","MIND"]','{"canonical_unit":"ug/dL","accepted_units":["ug/dL","nmol/L"],"conversion_note":"mock only"}','Timing matters; avoid casual interpretation.','Do not diagnose adrenal problems.'),
('mock-cat-018','egfr_ml_min_1_73m2','eGFR','Estimated glomerular filtration rate','any','egfr_ml_min_1_73m2','DERIVED',1,'high','eGFR estimates kidney filtering function.','Derived kidney filtration estimate.','Important safety context for supplement and medication discussions.','Fake challenge marker. Do not diagnose kidney disease from one value.','mL/min/1.73m2','higher_is_better',90,130,60,89,60,130,NULL,45,NULL,NULL,'metabolic_and_energy','Metabolic and energy','kidney_function','Kidney function','["Estimated GFR","glomerular filtration rate","eGFR"]','["eGFR","GFR"]','["MEDICAL"]','{"canonical_unit":"mL/min/1.73m2","accepted_units":["mL/min/1.73m2"]}','Use for safety context, especially supplements.','Do not diagnose kidney disease.'),
('mock-cat-019','creatinine_mg_dl','Creatinine','Creatinine','any','creatinine_mg_dl','MEASURED',2,'medium','Creatinine is commonly used to estimate kidney function.','Waste product filtered by kidneys.','Supports kidney context.','Fake challenge marker not present by default.','mg/dL','in_range_is_better',0.6,1.1,0.5,1.3,0.5,1.3,NULL,0.4,2.0,NULL,'metabolic_and_energy','Metabolic and energy','kidney_function','Kidney function','["Serum creatinine","creatinine"]','["CREA","Cr"]','["MEDICAL"]','{"canonical_unit":"mg/dL","accepted_units":["mg/dL","umol/L"],"conversion_note":"mock only"}','Interpret with muscle mass, age, sex, and eGFR.','Do not diagnose kidney disease.'),
('mock-cat-020','alt_u_l','ALT','Alanine aminotransferase','any','alt_u_l','MEASURED',2,'medium','ALT is often used as a liver enzyme marker.','Enzyme found mostly in liver cells.','Useful safety context for supplement and metabolic discussions.','Fake challenge marker. Interpret with other liver tests and context.','U/L','lower_is_better',7,35,36,55,7,55,NULL,NULL,80,NULL,'metabolic_and_energy','Metabolic and energy','liver_markers','Liver markers','["Alanine aminotransferase","SGPT","ALT"]','["ALT","SGPT"]','["MEDICAL"]','{"canonical_unit":"U/L","accepted_units":["U/L"]}','One ALT value is not enough to explain liver health.','Avoid supplement stacks if liver markers are abnormal.'),
('mock-cat-021','ast_u_l','AST','Aspartate aminotransferase','any','ast_u_l','MEASURED',3,'medium','AST is a liver and muscle enzyme often interpreted with ALT.','Enzyme present in liver, muscle, and other tissues.','Adds liver and tissue-injury context.','Fake challenge marker absent from default sample.','U/L','lower_is_better',8,35,36,55,8,55,NULL,NULL,80,NULL,'metabolic_and_energy','Metabolic and energy','liver_markers','Liver markers','["Aspartate aminotransferase","SGOT","AST"]','["AST","SGOT"]','["MEDICAL"]','{"canonical_unit":"U/L","accepted_units":["U/L"]}','Interpret AST with ALT and symptoms/context.','Do not infer liver disease from AST alone.'),
('mock-cat-022','ggt_u_l','GGT','Gamma-glutamyl transferase','any','ggt_u_l','MEASURED',3,'medium','GGT can add liver and alcohol/metabolic context but is non-specific.','Liver and bile duct enzyme marker.','Useful in liver-context discussions when present.','Fake challenge marker not present by default.','U/L','lower_is_better',5,40,41,70,5,70,NULL,NULL,100,NULL,'metabolic_and_energy','Metabolic and energy','liver_markers','Liver markers','["Gamma glutamyl transferase","GGT","gamma GT"]','["GGT"]','["MEDICAL","NUTRITION"]','{"canonical_unit":"U/L","accepted_units":["U/L"]}','Alcohol context may matter, but do not assume alcohol use.','Do not diagnose liver disease.'),
('mock-cat-023','hemoglobin_g_dl','Hemoglobin','Hemoglobin','any','hemoglobin_g_dl','MEASURED',2,'medium','Hemoglobin carries oxygen and helps contextualize blood count and energy questions.','Oxygen-carrying protein in red blood cells.','Useful for fatigue and blood-count context.','Fake challenge marker not present in default sample.','g/dL','in_range_is_better',12.0,15.5,11.0,16.5,11.0,16.5,NULL,9.0,18.0,NULL,'metabolic_and_energy','Metabolic and energy','blood_count','Blood count','["Hb","Hgb","hemoglobin"]','["Hb","Hgb"]','["MEDICAL","NUTRITION"]','{"canonical_unit":"g/dL","accepted_units":["g/dL"]}','Interpret with sex, ferritin, B12, and full blood count.','Do not diagnose anemia.'),
('mock-cat-024','wbc_10e3_ul','WBC','White blood cell count','any','wbc_10e3_ul','MEASURED',3,'medium','White blood cell count can reflect immune or inflammation context but is non-specific.','Count of white blood cells in blood.','Useful for broad inflammation/infection context when present.','Fake challenge marker not present in default sample.','10^3/uL','in_range_is_better',4.0,10.0,3.5,11.5,3.5,11.5,NULL,2.5,15.0,NULL,'inflammation_and_recovery','Inflammation and recovery','blood_count','Blood count','["White blood cells","leukocytes","white cell count"]','["WBC"]','["MEDICAL"]','{"canonical_unit":"10^3/uL","accepted_units":["10^3/uL","x10^9/L"],"conversion_note":"mock only"}','Interpret with differential, symptoms, and clinical context.','Do not diagnose infection or immune disease.');

INSERT INTO marker_alias (biomarker_name, alias, normalized_alias, language) VALUES
('ldl_c_mg_dl','LDL cholesterol','ldl cholesterol','en'),
('ldl_c_mg_dl','LDL-C','ldl c','en'),
('ldl_c_mg_dl','low density lipoprotein cholesterol','low density lipoprotein cholesterol','en'),
('hdl_c_mg_dl','HDL cholesterol','hdl cholesterol','en'),
('hdl_c_mg_dl','HDL-C','hdl c','en'),
('triglycerides_mg_dl','Triglycerides','triglycerides','en'),
('triglycerides_mg_dl','TG','tg','en'),
('total_cholesterol_mg_dl','Total cholesterol','total cholesterol','en'),
('non_hdl_c_mg_dl','Non-HDL cholesterol','non hdl cholesterol','en'),
('apob_mg_dl','ApoB','apob','en'),
('apob_mg_dl','Apolipoprotein B','apolipoprotein b','en'),
('lp_a_mg_dl','Lp(a)','lp a','en'),
('lp_a_mg_dl','Lipoprotein(a)','lipoprotein a','en'),
('hba1c_percent','HbA1c','hba1c','en'),
('hba1c_percent','A1C','a1c','en'),
('hba1c_percent','Glycated hemoglobin','glycated hemoglobin','en'),
('fasting_glucose_mg_dl','Fasting glucose','fasting glucose','en'),
('fasting_glucose_mg_dl','Fasting blood glucose','fasting blood glucose','en'),
('fasting_insulin_uiu_ml','Fasting insulin','fasting insulin','en'),
('hs_crp_mg_l','hs-CRP','hs crp','en'),
('hs_crp_mg_l','High sensitivity CRP','high sensitivity crp','en'),
('ferritin_ng_ml','Ferritin','ferritin','en'),
('vitamin_d_25oh_ng_ml','Vitamin D','vitamin d','en'),
('vitamin_d_25oh_ng_ml','25-OH vitamin D','25 oh vitamin d','en'),
('vitamin_b12_pg_ml','Vitamin B12','vitamin b12','en'),
('tsh_miu_l','TSH','tsh','en'),
('tsh_miu_l','Thyroid-stimulating hormone','thyroid stimulating hormone','en'),
('free_t4_ng_dl','Free T4','free t4','en'),
('cortisol_morning_ug_dl','Morning cortisol','morning cortisol','en'),
('egfr_ml_min_1_73m2','eGFR','egfr','en'),
('egfr_ml_min_1_73m2','Estimated GFR','estimated gfr','en'),
('creatinine_mg_dl','Creatinine','creatinine','en'),
('alt_u_l','ALT','alt','en'),
('alt_u_l','Alanine aminotransferase','alanine aminotransferase','en'),
('ast_u_l','AST','ast','en'),
('ast_u_l','Aspartate aminotransferase','aspartate aminotransferase','en'),
('ggt_u_l','GGT','ggt','en'),
('hemoglobin_g_dl','Hemoglobin','hemoglobin','en'),
('hemoglobin_g_dl','Hb','hb','en'),
('wbc_10e3_ul','White blood cells','white blood cells','en'),
('wbc_10e3_ul','WBC','wbc','en');

INSERT INTO marker_action_field (biomarker_name, action_field)
SELECT biomarker_name, value
FROM biomarker_catalog, json_each(action_fields_json);

CREATE VIEW marker_search_view AS
SELECT
  c.biomarker_name,
  c.display_name,
  c.public_display_name,
  c.domain_key,
  c.domain_label,
  c.subdomain_key,
  c.subdomain_label,
  c.unit,
  c.direction,
  c.importance,
  c.clinical_rationale,
  c.biomarker_function,
  c.long_description,
  c.interpretation_notes,
  c.safety_notes,
  group_concat(a.alias, ' | ') AS aliases,
  c.action_fields_json
FROM biomarker_catalog c
LEFT JOIN marker_alias a ON a.biomarker_name = c.biomarker_name
WHERE c.is_active = 1
GROUP BY c.biomarker_name;
