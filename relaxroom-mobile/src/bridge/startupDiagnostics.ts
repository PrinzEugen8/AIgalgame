const APP_START_MS = Date.now();

type MilestoneSource = 'rn' | 'native' | 'unity';

type TimelineEntry = {
  source: MilestoneSource;
  stage: string;
  elapsedMs: number;
  detail?: string;
};

type StartupStage =
  | 'unity_view_mount'
  | 'session_ready'
  | 'unity_ready'
  | 'stable_frame'
  | 'unity_load_timeout'
  | 'room_ready'
  | 'environment_ready'
  | 'configure_sent'
  | 'bridge_queue_flushed';

const milestones: TimelineEntry[] = [];
const milestoneKeys = new Set<string>();
let summaryLogged = false;

function isRepeatableStage(stage: string) {
  return (
    stage.includes('loading') ||
    stage.includes('heartbeat') ||
    stage.startsWith('splash_') ||
    stage === 'native_unity_stage'
  );
}

function milestoneKey(source: MilestoneSource, stage: string, detail?: string) {
  if (isRepeatableStage(stage)) {
    return `${source}:${stage}:${detail ?? milestones.length}`;
  }
  return `${source}:${stage}`;
}

function formatDetail(detail?: string) {
  return detail ? ` detail=${detail}` : '';
}

export function logStartupStage(stage: StartupStage, detail?: string) {
  recordMilestone('rn', stage, Date.now() - APP_START_MS, detail);
}

export function recordMilestone(
  source: MilestoneSource,
  stage: string,
  elapsedMs: number,
  detail?: string,
) {
  const key = milestoneKey(source, stage, detail);
  if (milestoneKeys.has(key)) {
    return;
  }

  milestoneKeys.add(key);
  milestones.push({source, stage, elapsedMs, detail});
  console.log(
    `[RelaxRoomStartup] layer=${source} stage=${stage} elapsed_ms=${elapsedMs}${formatDetail(detail)}`,
  );
}

export function mergeUnityTimeline(
  stages: Array<{stage: string; elapsed_ms: number}>,
) {
  for (const entry of stages) {
    recordMilestone('unity', entry.stage, entry.elapsed_ms);
  }
}

export function buildStartupSummary() {
  const lines: string[] = ['=== RelaxRoom Startup Summary ==='];
  const sources: MilestoneSource[] = ['rn', 'native', 'unity'];

  for (const source of sources) {
    const entries = milestones.filter(item => item.source === source);
    if (entries.length === 0) {
      continue;
    }

    lines.push(`[${source.toUpperCase()}]`);
    let previousMs = entries[0].elapsedMs;
    for (let index = 0; index < entries.length; index += 1) {
      const entry = entries[index];
      const deltaMs = index === 0 ? 0 : entry.elapsedMs - previousMs;
      previousMs = entry.elapsedMs;
      lines.push(
        `  ${index === 0 ? 'start' : `+${deltaMs}ms`} -> ${entry.stage} @ ${entry.elapsedMs}ms${formatDetail(entry.detail)}`,
      );
    }
  }

  lines.push('=== End Startup Summary ===');
  return lines.join('\n');
}

export function maybeLogStartupSummary(trigger: string) {
  if (summaryLogged) {
    return;
  }

  const hasNative = milestones.some(item => item.source === 'native');
  const hasUnity = milestones.some(item => item.source === 'unity');
  const hasRnStable = milestones.some(
    item => item.source === 'rn' && item.stage === 'stable_frame',
  );

  if (!hasNative || !hasUnity || !hasRnStable) {
    return;
  }

  summaryLogged = true;
  console.log(buildStartupSummary());
  console.log(`[RelaxRoomStartup] summary_trigger=${trigger}`);
}

export function getAppElapsedMs() {
  return Date.now() - APP_START_MS;
}

export function getStartupSummaryText() {
  return buildStartupSummary();
}
