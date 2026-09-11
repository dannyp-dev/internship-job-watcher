const EASTERN_TIME_ZONE = "America/New_York";
const START_MINUTES = 10 * 60;
const END_MINUTES = 24 * 60;

export function easternMinutes(date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: EASTERN_TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);

  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return Number(values.hour) * 60 + Number(values.minute);
}

export function shouldDispatch(date) {
  const minutes = easternMinutes(date);
  return minutes >= START_MINUTES && minutes < END_MINUTES;
}

export async function dispatchGitHubWorkflow(env) {
  if (!env.GITHUB_TOKEN) {
    throw new Error("GITHUB_TOKEN secret is not configured");
  }

  const response = await fetch(
    "https://api.github.com/repos/dannyp-dev/internship-job-watcher/actions/workflows/check-jobs.yml/dispatches",
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        "User-Agent": "internship-job-watcher-scheduler",
        "X-GitHub-Api-Version": "2026-03-10",
      },
      body: JSON.stringify({ ref: "main" }),
    },
  );

  if (!response.ok) {
    throw new Error(`GitHub workflow dispatch failed with HTTP ${response.status}`);
  }

  console.log({ event: "github_workflow_dispatched", status: response.status });
}

export default {
  async scheduled(controller, env, ctx) {
    const scheduledAt = new Date(controller.scheduledTime);
    if (!shouldDispatch(scheduledAt)) {
      console.log({
        event: "outside_notification_window",
        scheduledAt: scheduledAt.toISOString(),
      });
      return;
    }

    ctx.waitUntil(dispatchGitHubWorkflow(env));
  },
};
