#!/usr/bin/env node
/**
 * skill-doctor.mjs
 * 极速、只读、零资费的 SKILL 功能有效性探测引擎。
 * 纯原生 Node.js (ESM)，零外部 npm 依赖。
 *
 * 核心指标：全量检测耗时 < 150ms，零外部 API 业务 Query（零扣费/零消耗配额）。
 */

import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import process from 'node:process';
import os from 'node:os';

const SKILLS_ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const REGISTRATION_ROOTS = Array.from(new Set([
  SKILLS_ROOT,
  path.join(os.homedir(), '.claude', 'skills'),
  path.join(os.homedir(), '.dsh', 'skills')
]));
const IGNORED_DIRS = new Set([
  '.git', '.omo', '.codegraph', '.pytest_cache', '.uv-cache', '.venv', '.ruff_cache',
  'archive', 'scripts', 'docs', 'node_modules', '__pycache__'
]);
const RESOURCE_ONLY_DIRS = new Set([
  'diagram-draft', 'diagram-er', 'diagram-ers', 'diagram-flow',
  'diagram-module', 'diagram-sequence', 'diagram-usecase'
]);

// 默认已知的启发式映射表（用于存量技能兼容）
const HEURISTIC_MAP = {
  'kimi-webbridge': {
    daemons: [{ host: '127.0.0.1', port: 10086, name: 'WebBridge Daemon' }],
    fallback: 'mcp__browser__* (Playwright Headless)'
  },
  'lark-cli': {
    bins: ['lark-cli']
  },
  'github-workflow': {
    bins: ['gh', 'git']
  },
  'officecli': {
    bins: ['officecli']
  },
  'unified-search': {
    soft_bins: ['keenable'],
    env: ['TAVILY_API_KEY', 'BOCHA_API_KEY', 'FIRECRAWL_API_KEY'],
    fallback: 'built-in web search'
  },
  'diagram': {
    bins: ['node'],
    soft_bins: ['mmdc', 'graph-easy']
  },
  'diagram-flow': { bins: ['node'] },
  'diagram-sequence': { soft_bins: ['mmdc'] },
  'diagram-draft': { soft_bins: ['graph-easy'] },
  'drawio-xml': { bins: ['node', 'npx'] },
  'db-skill': { soft_bins: ['mysql'] },
  'paper-reader': {
    bins: ['uv'],
    venvs: ['venvs/marker', 'venvs/mineru'],
    fallback: 'Marker/MinerU 单路引擎'
  },
  'md-to-thesis-latex': { bins: ['python3'] }
};

// 预热 PATH 缓存以保证 <1ms 快速定位可执行文件
let _pathCache = null;
function getPathDirs() {
  if (!_pathCache) {
    _pathCache = (process.env.PATH || '')
      .split(path.delimiter)
      .filter(Boolean)
      .filter(p => fs.existsSync(p));
  }
  return _pathCache;
}

/** 静态检查命令是否存在且可执行 */
function checkBin(binName) {
  for (const dir of getPathDirs()) {
    const fullPath = path.join(dir, binName);
    try {
      fs.accessSync(fullPath, fs.constants.X_OK);
      return { ok: true, path: fullPath };
    } catch {
      // continue
    }
  }
  return { ok: false, path: null };
}

/** 纯 TCP 握手探针（150ms 强制超时熔断，连上秒断） */
function probePort(host, port, timeoutMs = 150) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;

    const cleanup = (ok, err = null) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve({ ok, err });
    };

    socket.setTimeout(timeoutMs);
    socket.once('connect', () => cleanup(true));
    socket.once('timeout', () => cleanup(false, 'TIMEOUT'));
    socket.once('error', (err) => cleanup(false, err.code || err.message));

    try {
      socket.connect(port, host);
    } catch (e) {
      cleanup(false, e.message);
    }
  });
}

/** 简单解析 SKILL.md Frontmatter */
function parseFrontmatter(content) {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!match) return { raw: null, metadata: {} };
  const raw = match[1];
  
  // 简易 YAML 提取（针对 metadata: requires / dependencies）
  const metadata = {};
  const requiresBinsMatch = raw.match(/bins:\s*\[([^\]]+)\]/);
  if (requiresBinsMatch) {
    metadata.bins = requiresBinsMatch[1]
      .split(',')
      .map(s => s.trim().replace(/^['"]|['"]$/g, ''))
      .filter(Boolean);
  }
  return { raw, metadata };
}

/** 检测单个技能 */
async function inspectSkill(registration) {
  const { name: skillDirName, skillPath, realPath, sources } = registration;
  const skillMdPath = path.join(skillPath, 'SKILL.md');

  const content = fs.readFileSync(skillMdPath, 'utf8');
  const { metadata } = parseFrontmatter(content);
  const heuristic = HEURISTIC_MAP[skillDirName] || {};

  const requiredBins = Array.from(new Set([...(metadata.bins || []), ...(heuristic.bins || [])]));
  const softBins = heuristic.soft_bins || [];
  const requiredDaemons = heuristic.daemons || [];
  const envVars = heuristic.env || [];
  const requiredVenvs = heuristic.venvs || [];

  const checks = [];
  let isDegraded = false;
  let isUnavailable = false;

  // 1. 检查硬性二进制依赖
  for (const bin of requiredBins) {
    const res = checkBin(bin);
    if (!res.ok) {
      isUnavailable = true;
      checks.push({ type: 'bin', name: bin, status: 'missing', critical: true });
    } else {
      checks.push({ type: 'bin', name: bin, status: 'ok', path: res.path });
    }
  }

  // 2. 检查可选/软依赖二进制
  for (const bin of softBins) {
    const res = checkBin(bin);
    if (!res.ok) {
      isDegraded = true;
      checks.push({ type: 'soft_bin', name: bin, status: 'missing', critical: false });
    } else {
      checks.push({ type: 'soft_bin', name: bin, status: 'ok', path: res.path });
    }
  }

  // 3. 检查本地常驻守护进程
  for (const d of requiredDaemons) {
    const probe = await probePort(d.host, d.port);
    if (!probe.ok) {
      isUnavailable = true;
      checks.push({ type: 'daemon', name: `${d.name} (${d.host}:${d.port})`, status: 'down', err: probe.err });
    } else {
      checks.push({ type: 'daemon', name: `${d.name} (${d.host}:${d.port})`, status: 'ok' });
    }
  }

  // 4. 检查 Python 独立虚拟环境
  for (const relVenv of requiredVenvs) {
    const venvBin = path.join(skillPath, relVenv, 'bin', 'python');
    if (fs.existsSync(venvBin)) {
      checks.push({ type: 'venv', name: relVenv, status: 'ok' });
    } else {
      isDegraded = true;
      checks.push({ type: 'venv', name: relVenv, status: 'missing', critical: false });
    }
  }

  // 5. 检查 A 类共享 venv 链接
  const localVenvLink = path.join(skillPath, '.venv');
  if (fs.existsSync(localVenvLink)) {
    const target = path.resolve(skillPath, fs.readlinkSync(localVenvLink));
    if (fs.existsSync(target)) {
      checks.push({ type: 'shared_venv', name: '.venv -> ../.venv', status: 'ok' });
    } else {
      isUnavailable = true;
      checks.push({ type: 'shared_venv', name: '.venv (broken)', status: 'broken', critical: true });
    }
  }

  // 6. 检查环境变量/API凭据（只读无网检查）
  for (const ev of envVars) {
    const val = process.env[ev];
    if (val && val.length > 5 && !val.includes('YOUR_')) {
      checks.push({ type: 'env', name: ev, status: 'configured' });
    } else {
      checks.push({ type: 'env', name: ev, status: 'unconfigured' });
    }
  }

  // 综合判定
  let status = 'READY';
  if (isUnavailable) {
    status = 'UNAVAILABLE';
  } else if (isDegraded) {
    status = 'DEGRADED';
  }

  return {
    skill: skillDirName,
    status,
    path: realPath,
    sources,
    fallback: heuristic.fallback || null,
    checks
  };
}

/** 主流程 */
async function main() {
  const args = process.argv.slice(2);
  const jsonMode = args.includes('--json');
  const targetFilter = args.find(a => !a.startsWith('--'));

  const t0 = performance.now();
  const registrations = new Map();
  const registrationIssues = [];

  for (const root of REGISTRATION_ROOTS) {
    if (!fs.existsSync(root)) continue;
    let entries;
    try { entries = fs.readdirSync(root, { withFileTypes: true }); } catch (err) {
      registrationIssues.push({ root, issue: 'unreadable-root', detail: err.message });
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory() && !entry.isSymbolicLink()) continue;
      if (IGNORED_DIRS.has(entry.name) || RESOURCE_ONLY_DIRS.has(entry.name)) continue;
      const skillPath = path.join(root, entry.name);
      let realPath;
      try { realPath = fs.realpathSync(skillPath); } catch {
        registrationIssues.push({ root, skill: entry.name, path: skillPath, issue: 'broken-registration' });
        continue;
      }
      // 指向文件的软链（如 AGENT.md 的 CLAUDE.md/AGENTS.md 兼容链）不是技能注册，跳过
      let realStat;
      try { realStat = fs.statSync(realPath); } catch { continue; }
      if (!realStat.isDirectory()) continue;
      const skillMdPath = path.join(realPath, 'SKILL.md');
      if (!fs.existsSync(skillMdPath)) {
        registrationIssues.push({ root, skill: entry.name, path: skillPath, realPath, issue: 'missing-SKILL.md' });
        continue;
      }
      const key = realPath;
      const current = registrations.get(key);
      if (current) {
        current.sources.push({ root, name: entry.name, path: skillPath });
      } else {
        registrations.set(key, { name: entry.name, skillPath: realPath, realPath, sources: [{ root, name: entry.name, path: skillPath }] });
      }
    }
  }

  const results = [];
  for (const registration of [...registrations.values()].sort((a, b) => a.name.localeCompare(b.name))) {
    if (targetFilter && registration.name !== targetFilter && !registration.sources.some(s => s.name === targetFilter)) continue;
    results.push(await inspectSkill(registration));
  }

  const elapsedMs = (performance.now() - t0).toFixed(1);

  if (jsonMode) {
    console.log(JSON.stringify({
      scannedAt: new Date().toISOString(),
      elapsedMs: Number(elapsedMs),
      total: results.length,
      ready: results.filter(r => r.status === 'READY').length,
      degraded: results.filter(r => r.status === 'DEGRADED').length,
      unavailable: results.filter(r => r.status === 'UNAVAILABLE').length,
      registrationIssueCount: registrationIssues.length,
      registrationIssues,
      roots: REGISTRATION_ROOTS.filter(root => fs.existsSync(root)),
      skills: results
    }, null, 2));
    return;
  }

  // 终端美化输出
  console.log(`\n🩺 SKILL Doctor — 极速有效性体检 (耗时: ${elapsedMs}ms)`);
  console.log(`========================================================`);

  const readyList = results.filter(r => r.status === 'READY');
  const degradedList = results.filter(r => r.status === 'DEGRADED');
  const unavailList = results.filter(r => r.status === 'UNAVAILABLE');

  for (const item of results) {
    let icon = '🟢';
    if (item.status === 'DEGRADED') icon = '🟡';
    if (item.status === 'UNAVAILABLE') icon = '🔴';

    const failItems = item.checks
      .filter(c => c.status === 'missing' || c.status === 'down' || c.status === 'broken')
      .map(c => `${c.name}: ${c.status}`);

    const failMsg = failItems.length > 0 ? ` ⚠️  [${failItems.join(', ')}]` : '';
    const fallbackMsg = item.fallback && item.status !== 'READY' ? ` ↪ 建议回退: ${item.fallback}` : '';

    console.log(`${icon} ${item.skill.padEnd(22)} [${item.status.padEnd(11)}]${failMsg}${fallbackMsg}`);
  }

  if (registrationIssues.length) {
    console.log(`--------------------------------------------------------`);
    for (const issue of registrationIssues) {
      console.log(`⚠️  注册问题: ${issue.skill || issue.root} [${issue.issue}] ${issue.path || issue.detail || ''}`);
    }
  }
  console.log(`========================================================`);
  console.log(`统计: 唯一技能 ${results.length} | 就绪 ${readyList.length} | 降级 ${degradedList.length} | 不可用 ${unavailList.length} | 注册问题 ${registrationIssues.length}\n`);
}

main().catch(err => {
  console.error('Skill-doctor 执行异常:', err);
  process.exit(1);
});
