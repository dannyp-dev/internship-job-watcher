import fs from "node:fs/promises";

const candidateFile = process.argv.find((argument) => argument.startsWith("--candidate-file="))?.split("=", 2)[1] ?? "preferred_direct_candidates.json";
const path = new URL(`./${candidateFile}`, import.meta.url);
const candidates = JSON.parse(await fs.readFile(path, "utf8"));

function endpoint(candidate) {
  const id = encodeURIComponent(candidate.id);
  if (candidate.type === "greenhouse") {
    return `https://boards-api.greenhouse.io/v1/boards/${id}/jobs?content=false`;
  }
  if (candidate.type === "ashby") {
    return `https://api.ashbyhq.com/posting-api/job-board/${id}`;
  }
  if (candidate.type === "lever") {
    return `https://api.lever.co/v0/postings/${id}?mode=json`;
  }
  if (candidate.type === "smartrecruiters") {
    return `https://api.smartrecruiters.com/v1/companies/${id}/postings?limit=1`;
  }
  throw new Error(`Unsupported source type: ${candidate.type}`);
}

async function validate(candidate) {
  let lastStatus = "validation failed";
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const response = await fetch(endpoint(candidate), {
        headers: { "User-Agent": "internship-job-watcher/1.0" },
        signal: AbortSignal.timeout(30000),
      });
      if (!response.ok) {
        return { ...candidate, valid: false, status: response.status, jobs: 0 };
      }
      const data = await response.json();
      const jobs = Array.isArray(data)
        ? data.length
        : Array.isArray(data.jobs)
          ? data.jobs.length
          : Array.isArray(data.content)
            ? data.content.length
            : 0;
      return { ...candidate, valid: true, status: response.status, jobs };
    } catch (error) {
      lastStatus = String(error);
    }
  }
  return { ...candidate, valid: false, status: lastStatus, jobs: 0 };
}

const results = [];
for (let index = 0; index < candidates.length; index += 8) {
  results.push(...await Promise.all(candidates.slice(index, index + 8).map(validate)));
}

for (const result of results) {
  console.log(JSON.stringify(result));
}

if (process.argv.includes("--merge")) {
  const configUrl = new URL("../config.json", import.meta.url);
  const config = JSON.parse(await fs.readFile(configUrl, "utf8"));
  const retained = config.sources ?? [];
  const existing = new Set(retained.map((source) => `${source.type}:${source.board ?? source.site ?? ""}`));
  const sources = results.filter((result) => result.valid && result.jobs > 0 && !existing.has(`${result.type}:${result.id}`)).map((result) => {
    const source = {
      type: result.type,
      name: `${result.type[0].toUpperCase()}${result.type.slice(1)}: ${result.company}`,
      company: result.company,
      enabled: true,
      managed_group: "expanded_companies",
    };
    if (result.type === "lever") {
      source.site = result.id;
    } else {
      source.board = result.id;
    }
    return source;
  });
  config.sources = [...retained, ...sources];
  await fs.writeFile(configUrl, `${JSON.stringify(config, null, 2)}\n`, "utf8");
  console.error(`Merged ${sources.length} validated preferred-company feeds.`);
}
