export type Mode = "mock" | "hardware";
export type SourceLabel = "LIVE" | "SIMULATION" | "REPLAY";

export interface Capabilities {
  hardware_motion: boolean;
  hardware_read: boolean;
  mock_faults: boolean;
  replay: boolean;
  min_duration_s: number;
  max_duration_s: number;
  max_step_rad: number;
  supervised_motion: boolean;
  single_joint_only: boolean;
  motion_mode: string;
  motion_scope: string;
  max_velocity_rad_s: number | null;
  gripper_control: boolean;
  pose_gate: boolean;
}

export interface GripperState {
  joint: string;
  servo_id: number;
  available: boolean;
  target_rad: number | null;
  target_raw: number | null;
  position_rad: number | null;
  raw_position: number | null;
  truth: string;
  validity: string;
  sample_age_ms: number | null;
  source: string;
  min_rad: number;
  max_rad: number;
  open_raw: number;
  close_raw: number;
  measured_min_raw: number;
  measured_max_raw: number;
  completion: string;
  notes: string[];
}

export interface JointState {
  name: string;
  servo_id: number;
  target_rad: number | null;
  target_raw: number | null;
  position_rad: number | null;
  raw_position: number | null;
  truth: string;
  validity: string;
  sample_age_ms: number | null;
  source: string;
  limit_min_rad: number;
  limit_max_rad: number;
  limit_verified: boolean;
}

export interface Fault {
  fault_id: string;
  level: string;
  code: string;
  message: string;
  source: string;
  first_seen: string;
  last_seen: string;
  remediation: string;
  details: Record<string, unknown>;
}

export interface RobotState {
  robot_id: string;
  mode: Mode;
  connection_state: string;
  source_label: SourceLabel;
  control_owner: string | null;
  received_at: string;
  monotonic_ms: number;
  ros_time_ns: number | null;
  joints: JointState[];
  faults: Fault[];
  mapping_version: string;
  limit_version: string;
  mapping_verified: boolean;
  capabilities: Capabilities;
  stop_state: string | null;
  last_command_id: string | null;
  gripper: GripperState | null;
}

export interface SegmentStatus {
  name: string;
  state: string;
  detail: string;
  evidence: string;
}

export interface ConnectionReport {
  segments: SegmentStatus[];
  serial_available: boolean;
  driver_online: boolean;
  gateway_online: boolean;
  feedback_fresh: boolean;
  notes: string[];
}

export interface SafetyCheck {
  check_id: string;
  label: string;
  passed: boolean;
  blocking: boolean;
  detail: string;
  severity: string;
}

export interface SafetyReport {
  ready: boolean;
  checks: SafetyCheck[];
  blockers: string[];
  warnings: string[];
  mode: Mode;
  generated_at: string;
}

export interface MappingJoint {
  name: string;
  servo_id: number;
  init_raw: number;
  min_raw: number;
  max_raw: number;
  direction: number;
  radians_per_tick: number;
  min_rad: number;
  max_rad: number;
  limit_source: string;
  verified: boolean;
}

export interface MappingInfo {
  schema_version: number;
  mapping_version: string;
  source: string;
  verified: boolean;
  verification_status: string;
  radians_per_tick: number;
  mapping_hash: string;
  joints: MappingJoint[];
  excluded: Array<Record<string, unknown>>;
  safety: Record<string, number>;
}

export interface KnownDefect {
  id: string;
  summary: string;
  source: string;
  impact: string;
}

export interface MockFault {
  kind: string;
  code: string;
  level: string;
  joint: string | null;
}

export interface Diagnostics {
  mode: Mode;
  connection: ConnectionReport;
  mapping: MappingInfo;
  safety: SafetyReport;
  capabilities: Capabilities;
  known_defects: KnownDefect[];
  probe_available: boolean;
  environment: Record<string, unknown>;
  mock_faults: MockFault[];
  storage_error: string | null;
}

export interface EventRecord {
  seq: number;
  event_id: string;
  command_id: string | null;
  stage: string;
  wall_time: string;
  monotonic_ms: number;
  source: string;
  result: string;
  error_code: string | null;
  details: Record<string, unknown>;
  schema_version: number;
}

export interface EventsPayload {
  items: EventRecord[];
  last_seq: number;
  schema_version: number;
}

export interface ValidationIssue {
  code: string;
  message: string;
  joint: string | null;
  details: Record<string, unknown>;
}

export interface PreviewTarget {
  joint: string;
  servo_id: number;
  from_rad: number;
  target_rad: number;
  delta_rad: number;
  duration_s: number;
  target_raw: number;
  limit_min_rad: number;
  limit_max_rad: number;
  limit_verified: boolean;
  limit_source: string;
  completion?: string;
}

export interface PoseCheck {
  ok: boolean;
  tcp_m: number[] | null;
  table_clearance_m: number | null;
  samples: number;
  start_tcp_m: number[] | null;
  start_table_clearance_m: number | null;
  start_compliant: boolean;
  violations: Array<{
    code: string;
    message: string;
    joint: string | null;
    details: Record<string, unknown>;
  }>;
  warning?: string;
}

export interface PreviewResult {
  command_id: string;
  session_id: string;
  valid: boolean;
  targets: PreviewTarget[];
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  preview_token: string | null;
  expires_at: string | null;
  mapping_version: string;
  evaluated_at: string;
  pose?: PoseCheck | null;
}

export interface MotionPreset {
  id: string;
  label: string;
  label_en: string;
  joints: Record<string, number>;
  target_raw: Record<string, number | null>;
  verified_on_hardware: boolean;
  source: string;
  limit_ok: boolean;
  bad_limits: string[];
  pose_check: PoseCheck | null;
}

export interface MotionPresetList {
  schema_version: number;
  generated_at: string;
  scope: string;
  available: boolean;
  contract: Record<string, unknown> | null;
  presets: MotionPreset[];
  current: {
    joints: Record<string, number>;
    gripper: GripperState | null;
    tcp_m: number[] | null;
    measured: boolean;
    source: string;
  };
  gate_error: string[] | null;
}

export interface PreviewProfile {
  vmax_rad_s: number;
  amax_rad_s2: number;
  dt_s?: number;
}

export interface TaskCommand {
  kind: "pose" | "grip";
  label: string;
  joints?: Record<string, number>;
  from_joints?: Record<string, number>;
  targets?: TargetPayload[];
  duration_s: number;
  profile?: PreviewProfile & { segments?: number; total_s?: number };
  target_rad?: number;
  from_rad?: number;
  target_raw?: number | Record<string, number | null> | null;
  pose_check?: PoseCheck | null;
  fast?: boolean;
}

export interface TaskStep {
  kind: "pose" | "grip" | "mixed" | "verify";
  label: string;
  commands: TaskCommand[];
  duration_s: number;
  detail?: string;
  status: string;
}

export interface TaskIssue {
  label?: string;
  code: string;
  message: string;
  joint?: string | null;
}

export interface TaskFit {
  joints: number[];
  tcp: number[];
  target: number[];
  residual_mm: number[];
  xy_error_mm: number;
  reach_limited: boolean;
  j4_source?: "form" | string;
  j4_fit_rad?: number | null;
}

export type TaskFormMode = "task" | "pose" | "refused";

export interface TaskForm {
  form_id: string;
  label: string;
  label_en: string;
  mode: TaskFormMode;
  reason: string;
  size_text: string;
  top_height_mm: number | null;
  close_register: number | null;
  grasp_j2: number | null;
  grasp_j3: number | null;
  grasp_j4: number | null;
  grasp_j5: number | null;
  preclose_rad: number | null;
  grip_rad: number | null;
  correction_mm: number[] | null;
  base_point_m: number[] | null;
  base_tolerance_m: number;
  recorded_pose: Record<string, number> | null;
  notes: string;
  catalog: string;
}

export interface TaskApproach {
  kind: string;
  vx: number;
  vy: number;
  duty: number;
  duration_s: number;
  nominal_travel_mm: number;
  travel_m: number;
  forward_mm: number;
  left_mm: number;
  point_m: number[];
  target_point_m: number[];
}

export interface TaskPlan {
  schema_version: number;
  valid: boolean;
  errors: TaskIssue[];
  warnings: TaskIssue[];
  simulated: boolean;
  form?: TaskForm | null;
  form_id?: string | null;
  point?: number[];
  target?: number[];
  correction_mm?: number[];
  preclose_rad?: number;
  grip_rad?: number;
  grasp_j2?: number;
  grasp_j3?: number;
  grasp_j4?: number | null;
  grasp_j5?: number;
  fit?: TaskFit | null;
  grasp_joints?: Record<string, number>;
  approach_joints?: Record<string, number>;
  pre_grasp_joints?: Record<string, number>;
  view_joints?: Record<string, number>;
  lift_joints?: Record<string, number>;
  view_preset?: string;
  lift_preset?: string;
  start_joints?: Record<string, number>;
  start_gripper_rad?: number;
  source?: string;
  locate?: Record<string, unknown>;
  hand_eye?: Record<string, unknown>;
  steps: TaskStep[];
  plan_id?: string | null;
  generated_at?: string;
  cloud?: Record<string, unknown>;
  current_joints?: Record<string, number>;
  approach?: TaskApproach | null;
  fast?: Record<string, unknown> | null;
}

export interface TaskVerify {
  held: boolean | null;
  verified: boolean;
  simulated: boolean;
  code?: string;
  message?: string;
  warning?: string | null;
  point?: number[];
  clusters?: number;
  report?: Record<string, unknown>;
  source?: string;
}

export interface Lease {
  lease_id: string;
  session_id: string;
  acquired_at: string;
  expires_at: string;
  ttl_s: number;
}

export interface CommandRecord {
  command_id: string;
  session_id: string;
  status: string;
  targets: PreviewTarget[];
  created_at: string;
  updated_at: string;
  error_code: string | null;
  details: Record<string, unknown>;
  stage_history: Array<{ stage: string; at: string; error_code?: string | null; details?: Record<string, unknown> }>;
}

export interface StopResult {
  state: string;
  command_id: string;
  detail: string;
  evidence: string;
  requested_at: string;
  observed_at: string | null;
  storage_error: Record<string, unknown> | null;
}

export interface ProbeResult {
  truth: string;
  note: string;
  results: Array<{
    joint: string;
    servo_id: number;
    ok: boolean;
    raw_position: number | null;
    position_rad: number | null;
    duration_ms: number;
    truth: string;
    error: string | null;
  }>;
}

export interface BatteryState {
  available: boolean;
  voltage_mv: number | null;
  voltage_v: number | null;
  percent_estimate: number | null;
  cell_count: number;
  status: string;
  truth: string;
  validity: string;
  source: string;
  topic: string;
  received_at: string | null;
  sample_age_ms: number | null;
  notes: string[];
}

export interface CameraStatus {
  available: boolean;
  truth: string;
  validity: string;
  source: string;
  topic: string;
  width: number | null;
  height: number | null;
  encoding: string | null;
  fps: number | null;
  last_frame_at: string | null;
  sample_age_ms: number | null;
  frame_count: number;
  notes: string[];
}

export interface Peripherals {
  battery: BatteryState;
  camera: CameraStatus;
  aux_camera: CameraStatus;
}

export interface TargetPayload {
  joint: string;
  target_rad: number;
  duration_s: number;
}

export interface TrajectoryPoint {
  t_s: number;
  positions: number[];
  velocities?: number[];
}

export interface TrajectoryStream {
  truth: string;
  time_offset_s: number;
  source: string;
  points: TrajectoryPoint[];
}

export interface TrajectoryExecutor {
  status: string;
  detail: string;
  truth: string;
}

export interface TrajectoryDataset {
  schema_version: number;
  name: string;
  description: string;
  timebase: string;
  joint_names: string[];
  planned: TrajectoryStream;
  commanded: TrajectoryStream;
  feedback: TrajectoryStream;
  executor: TrajectoryExecutor | null;
  metadata: Record<string, unknown>;
}

export interface JointErrorMetrics {
  max_abs_error_rad: number;
  rmse_rad: number;
  mean_abs_error_rad: number;
  final_error_rad: number;
}

export interface TrajectoryAggregateMetrics {
  max_abs_error_rad: number;
  rmse_rad: number;
  mean_abs_error_rad: number;
  max_abs_final_error_rad: number;
}

export interface TrajectoryComparison {
  available: boolean;
  reason: string | null;
  reference_stream: string;
  sample_stream: string;
  sample_truth: string;
  samples_compared: number;
  overlap_start_s?: number;
  overlap_end_s?: number;
  per_joint: Record<string, JointErrorMetrics>;
  aggregate: TrajectoryAggregateMetrics | null;
  note?: string;
}

export interface StreamQuality {
  truth: string;
  time_offset_s: number;
  samples: number;
  start_s: number;
  end_s: number;
  duration_s: number;
  median_interval_s: number | null;
  mean_interval_s: number | null;
  effective_rate_hz: number | null;
  max_gap_s: number | null;
  gaps_over_1_5x_median: number;
  dropout_ratio: number | null;
  interval_jitter_ms: number | null;
}

export interface TrajectoryAnalysis {
  schema_version: number;
  analysis_version: string;
  dataset_sha256: string;
  joint_names: string[];
  timebase: string;
  sampling_quality: {
    planned: StreamQuality;
    commanded: StreamQuality;
    feedback: StreamQuality;
  };
  command_fidelity: TrajectoryComparison;
  tracking: TrajectoryComparison;
  executor: TrajectoryExecutor | null;
  notes: string[];
}

export interface TrajectorySummary {
  trajectory_id: string;
  created_at: string;
  name: string;
  source: string;
  schema_version: number;
  dataset_sha256: string;
  analysis_version: string;
  joint_names: string[];
  planned_samples: number;
  commanded_samples: number;
  feedback_samples: number;
  feedback_truth: string;
  duration_s: number;
  tracking_available: boolean;
  tracking_reason?: string | null;
}

export interface TrajectoryRecord extends TrajectorySummary {
  dataset: TrajectoryDataset;
  analysis: TrajectoryAnalysis;
  duplicate?: boolean;
  storage_error?: Record<string, unknown> | null;
}

export interface TrajectoryList {
  items: TrajectorySummary[];
  schema_version: number;
}

export interface TrajectoryRecompute {
  trajectory_id: string;
  analysis_version: string;
  stored_analysis_version: string;
  matches_stored: boolean;
  analysis: TrajectoryAnalysis;
}

export type DataOrigin = "hardware" | "simulated";
export type ExperimentStatus = "planned" | "in_progress" | "complete";

export interface SummaryStats {
  n: number;
  mean: number | null;
  std: number | null;
  min: number | null;
  max: number | null;
  ci95_mean: number | null;
}

export interface ExperimentSummary {
  experiment_id: string;
  created_at: string;
  name: string;
  data_origin: DataOrigin;
  operator: string;
  truth: "measured" | "simulated";
  schema_version: number;
  analysis_version: string;
  planned_pairs: number;
  seed: number;
  n_trials: number;
  n_complete_pairs: number;
  n_baseline: number;
  n_intervention: number;
  baseline_success_rate: number | null;
  intervention_success_rate: number | null;
  status: ExperimentStatus;
}

export interface ExperimentProtocol {
  planned_pairs: number;
  seed: number;
  fixtures: string[];
  bootstrap_samples: number;
  confidence: number;
  primary_metric: string;
}

export interface PlanEntry {
  sequence: number;
  pair_id: string;
  trial_order: number;
  method: "baseline" | "intervention";
  fixture: string;
}

export interface ExperimentPlan {
  schema_version: number;
  seed: number;
  n_pairs: number;
  pairs: Array<{ pair_id: string; fixture: string; order: string[] }>;
  entries: PlanEntry[];
}

export interface ExperimentRecord {
  experiment_id: string;
  created_at: string;
  name: string;
  purpose: string;
  data_origin: DataOrigin;
  operator: string;
  robot_id: string;
  hardware_config: string;
  calibration_version: string;
  mapping_version: string;
  stop_rules: string;
  protocol: ExperimentProtocol;
  plan: ExperimentPlan;
  notes: string;
  schema_version: number;
  analysis_version: string;
  signature: string | null;
}

export interface TrialArtifact {
  kind: string;
  path: string;
  note: string;
}

export interface TrialRecord {
  trial_id: string;
  experiment_id: string;
  created_at: string;
  pair_id: string;
  trial_order: number;
  method: "baseline" | "intervention";
  fixture: string;
  grasp_success: boolean;
  continuous_error_mm: number | null;
  rotation_error_deg: number | null;
  failure_code: string | null;
  failure_detail: string;
  duration_s: number | null;
  measurement_source: string;
  trajectory_id: string | null;
  artifacts: TrialArtifact[];
  operator_notes: string;
  target: Record<string, number>;
  truth: "measured" | "simulated";
  schema_version: number;
}

export interface MethodSummary {
  n: number;
  error: SummaryStats;
  rotation_error_deg: SummaryStats;
  duration_s: SummaryStats;
  successes: number;
  success_rate: number | null;
  failure_codes: Record<string, number>;
}

export interface PairedSummary {
  n_pairs: number;
  n_pairs_with_error: number;
  difference_mm: SummaryStats;
  improvement_mm: number | null;
  ci95_difference_mm: { samples: number; seed: number; low: number | null; high: number | null; mean: number | null };
  effect_size_dz: number | null;
  sign_test: { wins: number; losses: number; ties: number; p_value: number };
}

export interface SuccessSummary {
  baseline_rate: number | null;
  intervention_rate: number | null;
  difference: number | null;
  discordant: { baseline_only: number; intervention_only: number };
  mcnemar_exact_p: number;
}

export interface ExperimentAnalysis {
  schema_version: number;
  analysis_version: string;
  experiment_id: string | null;
  data_origin: DataOrigin;
  generated_at: string;
  seed: number;
  bootstrap_samples: number;
  confidence: number;
  available: boolean;
  reason: string | null;
  validation: {
    n_input: number;
    n_valid: number;
    n_invalid: number;
    invalid: Array<Record<string, unknown>>;
    n_complete_pairs: number;
    incomplete_pairs: Array<{ pair_id: string; methods: string[]; n_records: number }>;
  };
  methods: Record<string, MethodSummary>;
  paired: PairedSummary | null;
  success: SuccessSummary | null;
  randomization: { baseline_first: number; intervention_first: number };
  order_effect: {
    groups: Record<string, SummaryStats>;
    difference_mm: number | null;
    note: string;
  };
  failure_codes: Record<string, Record<string, number>>;
  warnings: string[];
  limitations: string[];
}

export interface ExperimentDetail {
  experiment: ExperimentRecord;
  trials: TrialRecord[];
  analysis: ExperimentAnalysis;
  duplicate?: boolean;
  storage_error?: Record<string, unknown> | null;
}

export interface ExperimentList {
  items: ExperimentSummary[];
  failure_codes: Record<string, string>;
  methods: Record<string, string>;
  schema_version: number;
}

export interface VisualFrame {
  name: string;
  parent: string | null;
  parent_joint: string | null;
  position_m: [number, number, number];
  rotation: number[];
  source: string;
  nominal: boolean;
  is_camera: boolean;
  optical?: boolean;
  calibration_version?: string;
  truth?: string;
}

export interface VisualJoint {
  name: string;
  type: string;
  position_rad: number | null;
  position_source: string;
  truth: string;
  lower_rad: number | null;
  upper_rad: number | null;
  urdf_lower_rad: number | null;
  urdf_upper_rad: number | null;
  velocity_limit: number | null;
  limit_source: string;
  verified: boolean;
  within_limits: boolean | null;
  mimic_of: string | null;
  mimic_multiplier: number | null;
}

export interface VisualCameraCalibration {
  available: boolean;
  parent_link: string | null;
  frame: string;
  body_frame: string;
  calibration_version: string;
  calibration_source: string;
  truth: string;
  translation_m: number[];
  rpy_deg: number[];
  notes: string[];
}

export interface VisualCamera extends CameraStatus {
  snapshot_url: string;
  frame: string | null;
  optical_frame: string | null;
  calibration: VisualCameraCalibration;
  warnings: string[];
  tf_status?: string;
  tf_frames?: string[];
}

export interface VisualSnapshot {
  schema_version: number;
  generated_at: string;
  mode: Mode;
  source_label: SourceLabel;
  robot_id: string;
  urdf: {
    available: boolean;
    path: string;
    sha256: string | null;
    robot_name: string | null;
    verified: boolean;
    links: number;
    joints: number;
    source_note: string;
    warnings?: string[];
  };
  tf: {
    status: string;
    source: string;
    live: boolean;
    truth: string;
    joint_truth: string;
    max_sample_age_ms: number | null;
    gateway_status?: string | null;
    dynamic_edges?: number;
    static_edges?: number;
    max_tf_age_ms?: number | null;
    frames: VisualFrame[];
    camera_frame: VisualFrame | null;
    warnings: string[];
  };
  joints: VisualJoint[];
  camera: VisualCamera;
  warnings: string[];
}

export interface CameraReference {
  reference_id: string;
  created_at: string;
  mode: string;
  source_label: string;
  truth: string;
  camera_topic: string;
  width: number | null;
  height: number | null;
  encoding: string | null;
  fps: number | null;
  frame_at: string | null;
  sample_age_ms: number | null;
  calibration_version: string;
  calibration_source: string;
  tf_status: string;
  tf_source: string;
  note: string;
  snapshot_url: string;
  schema_version: number;
}

export interface CameraReferenceList {
  items: CameraReference[];
  schema_version: number;
}
