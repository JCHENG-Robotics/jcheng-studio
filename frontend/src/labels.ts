/**
 * 协议值的中文显示名。
 *
 * 协议里的英文代码仍然保留：日志、CSV 与 evidence 里用的就是它们。界面显示
 * 中文标签，需要对照原文时把代码放在 tooltip（detail/title）里。
 */

function pick(map: Record<string, string>, value: string): string {
  return map[value] ?? value;
}

const STAGES: Record<string, string> = {
  Requested: "已请求",
  Validated: "已校验",
  Dispatched: "已下发",
  Acknowledged: "已确认",
  Observed: "已观测",
  Completed: "已完成",
  Failed: "失败",
  Unknown: "结果未知",
  Rejected: "已拒绝",
  Timeout: "超时",
  Succeeded: "成功",
};

export function stageLabel(stage: string): string {
  return pick(STAGES, stage);
}

const STOP_STATES: Record<string, string> = {
  StopRequested: "停止已请求",
  StopDispatched: "停止已下发",
  StopObserved: "停止已确认",
  StopUnknown: "停止结果未知",
};

export function stopStateLabel(state: string): string {
  return pick(STOP_STATES, state);
}

const CONNECTION_STATES: Record<string, string> = {
  Disconnected: "未连接",
  Connecting: "连接中",
  ReadOnly: "只读",
  Ready: "就绪",
  Executing: "执行中",
  Degraded: "降级",
  Fault: "故障",
  EStopOrPowerCutReported: "疑似急停/断电",
  Replay: "回放",
  Simulation: "模拟",
};

export function connectionLabel(state: string): string {
  return pick(CONNECTION_STATES, state);
}

const MODES: Record<string, string> = {
  mock: "模拟（Mock）",
  hardware: "硬件",
};

export function modeLabel(mode: string): string {
  return pick(MODES, mode);
}

const SOURCES: Record<string, string> = {
  LIVE: "实机",
  SIMULATION: "模拟",
  REPLAY: "回放",
};

export function sourceLabel(source: string): string {
  return pick(SOURCES, source);
}

const TRUTHS: Record<string, string> = {
  simulated: "模拟",
  commanded: "指令回显",
  measured: "实测",
  measured_on_demand: "实测（按需）",
  planned: "计划",
  model: "模型",
  mixed: "混合（实测+指令）",
  unknown: "未知",
};

export function truthLabel(truth: string): string {
  return pick(TRUTHS, truth);
}

const VALIDITIES: Record<string, string> = {
  valid: "有效",
  stale: "过期",
  unavailable: "不可用",
  invalid: "无效",
};

export function validityLabel(validity: string): string {
  return pick(VALIDITIES, validity);
}

const SEVERITIES: Record<string, string> = {
  info: "信息",
  warning: "警告",
  fault: "故障",
  critical: "严重",
};

export function severityLabel(level: string): string {
  return pick(SEVERITIES, level);
}

const SEGMENT_STATES: Record<string, string> = {
  simulated: "模拟",
  online: "在线",
  offline: "离线",
  unavailable: "不可用",
  unknown: "未知",
  degraded: "降级",
};

export function segmentStateLabel(state: string): string {
  return pick(SEGMENT_STATES, state);
}

const BATTERY_STATES: Record<string, string> = {
  ok: "正常",
  low: "偏低",
  critical: "严重偏低",
  unavailable: "不可用",
};

export function batteryLabel(status: string): string {
  return pick(BATTERY_STATES, status);
}

const STEP_KINDS: Record<string, string> = {
  pose: "姿态",
  grip: "夹爪",
  verify: "验证",
  mixed: "混合",
};

export function stepKindLabel(kind: string): string {
  return pick(STEP_KINDS, kind);
}

const TASK_STEPS: Record<string, string> = {
  open_gripper: "张开夹爪",
  approach: "接近",
  grasp: "抓取",
  close: "闭爪",
  lift: "抬升到观察位",
  grip_verify: "夹持验证",
  release: "放回",
  return_view: "返回观察位",
  task: "任务",
};

export function taskStepLabel(label: string): string {
  return pick(TASK_STEPS, label);
}

const STEP_STATUS: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  held: "已夹持",
};

export function stepStatusLabel(status: string): string {
  return pick(STEP_STATUS, status);
}

const COMMAND_LABELS: Record<string, string> = {
  open_gripper: "张开夹爪",
  approach: "接近",
  descend: "下探",
  grasp: "抓取终点",
  close_pre: "预闭合",
  close_grip: "夹紧",
  lift: "抬升",
  release_descend: "放回下探",
  release_final: "放回终点",
  release_open: "松开",
  return_view: "返回观察位",
};

export function commandLabel(label: string): string {
  return pick(COMMAND_LABELS, label);
}

const EXPERIMENT_STATUS: Record<string, string> = {
  planned: "已计划",
  in_progress: "进行中",
  complete: "已完成",
};

export function experimentStatusLabel(status: string): string {
  return pick(EXPERIMENT_STATUS, status);
}

const METHODS: Record<string, string> = {
  baseline: "基线",
  intervention: "干预",
};

export function methodLabel(method: string): string {
  return pick(METHODS, method);
}

const MEASUREMENT_SOURCES: Record<string, string> = {
  unknown: "未知",
  vision: "视觉",
  caliper: "卡尺",
  fixture: "夹具",
  operator: "操作员",
  simulated: "模拟",
};

export function measurementSourceLabel(source: string): string {
  return pick(MEASUREMENT_SOURCES, source);
}

const FAILURE_CODES: Record<string, string> = {
  COMMUNICATION: "通信失败",
  TIMEOUT: "超时",
  JOINT_LIMIT: "关节限位",
  PLANNING_FAILED: "规划失败",
  TRAJECTORY_FAILED: "轨迹执行失败",
  VISION_FAILED: "视觉定位失败",
  CALIBRATION_INVALID: "标定无效",
  GRASP_FAILED: "抓取失败",
  SAFETY_VETO: "安全门禁否决",
  UNKNOWN: "未知",
};

export function failureCodeLabel(code: string): string {
  return pick(FAILURE_CODES, code);
}

const STREAMS: Record<string, string> = {
  planned: "计划",
  commanded: "指令",
  feedback: "反馈",
};

export function streamLabel(stream: string): string {
  return pick(STREAMS, stream);
}

const TF_STATUS: Record<string, string> = {
  live: "实时",
  live_static_only: "仅静态 TF",
  model_only: "仅模型",
  degraded: "降级",
  unavailable: "不可用",
};

export function tfStatusLabel(status: string): string {
  return pick(TF_STATUS, status);
}

const ORIGINS: Record<string, string> = {
  demo: "示例",
  imported: "导入",
  recorded: "录制",
  hardware: "硬件",
  simulated: "模拟",
  unknown: "未知",
};

export function originLabel(origin: string): string {
  return pick(ORIGINS, origin);
}

const JOINT_TYPES: Record<string, string> = {
  revolute: "转动关节",
  continuous: "连续关节",
  prismatic: "移动关节",
  fixed: "固定",
};

export function jointTypeLabel(type: string): string {
  return pick(JOINT_TYPES, type);
}
