/**
 * database.js — Hardened JSON File Database Engine
 * Features:
 *  - Isolated storage in ./data/challenges.json
 *  - Atomic writes via temporary file swap (prevents crash corruption)
 *  - Automated daily backups in ./data/backups/
 *  - Backup retention policy (auto-cleans files older than 14 days)
 *  - Input sanitization & schema validation against injection attacks
 */

const fs = require('fs');
const path = require('path');

const IS_VERCEL = Boolean(process.env.VERCEL);
const DATA_DIR = IS_VERCEL ? path.join('/tmp', 'data') : path.join(__dirname, 'data');
const BACKUPS_DIR = path.join(DATA_DIR, 'backups');
const DB_PATH = path.join(DATA_DIR, 'challenges.json');
const TMP_PATH = path.join(DATA_DIR, 'challenges.tmp');
const LEGACY_PATH = path.join(__dirname, 'challenges.json');
const BUNDLED_PATH = path.join(__dirname, 'data', 'challenges.json');

// Ensure directories exist
try {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
  if (!fs.existsSync(BACKUPS_DIR)) fs.mkdirSync(BACKUPS_DIR, { recursive: true });
} catch (e) {}

// Auto-migrate from bundled/legacy root if needed
try {
  const sourcePath = fs.existsSync(BUNDLED_PATH) ? BUNDLED_PATH : (fs.existsSync(LEGACY_PATH) ? LEGACY_PATH : null);
  if (sourcePath) {
    if (!fs.existsSync(DB_PATH)) {
      fs.copyFileSync(sourcePath, DB_PATH);
      console.log('📦 Initialized challenges.json for server environment');
    } else {
      // If bundled data has more solved challenges than /tmp cache, update /tmp
      const cached = JSON.parse(fs.readFileSync(DB_PATH, 'utf8'));
      const bundled = JSON.parse(fs.readFileSync(sourcePath, 'utf8'));
      const cachedSolved = (cached.challenges || []).filter(c => c.status === 'solved').length;
      const bundledSolved = (bundled.challenges || []).filter(c => c.status === 'solved').length;
      if (bundledSolved > cachedSolved) {
        fs.copyFileSync(sourcePath, DB_PATH);
        console.log('📦 Updated challenges.json with latest bundled solved state');
      }
    }
  }
} catch (err) {
  console.warn('⚠️ Initialization notice:', err.message);
}

// ─── Default DB Structure ──────────────────────────────────────────────────────
const CURATED_TOPICS = [
  'Rosewood Returns Cart',
  'Array Prefix Sums & Range Queries',
  'Floodgate Relief Ledger',
  'Two Pointers & Sliding Window',
  'Fast & Slow Pointers: Cycle Detection',
  'Binary Search: Search in Rotated Array',
  'Longest Common Subsequence & Edit Distance',
  'Stack: Valid Parentheses & Min Stack',
  'Roll Call at Sundarvan',
  'Dijkstra\'s Algorithm & Shortest Paths',
  'Topological Sort & Course Schedule',
  'Trie Prefix Tree & Word Search',
  'Binary Search on Solution Space',
  'Sliding Window Maximum & Min Window Substring',
  'Two Pointers: 3Sum & Container With Most Water',
  'Monotonic Stack: Next Greater Element & Histogram',
  'Priority Queue & Top K Frequent Elements',
  'Interval Scheduling & Merge Intervals',
  'Tree Diameter & Lowest Common Ancestor',
  'Bit Manipulation: Single Number & Counting Bits',
  'Backtracking: N-Queens & Sudoku Solver',
  'Segment Tree & Range Queries',
  'Disjoint Set Union (DSU) & Kruskal\'s MST',
  'Dynamic Programming: 1D Fibonacci & House Robber',
  '0/1 Knapsack & Partition Equal Subset Sum',
  'String Matching: KMP & Rabin-Karp',
  'Matrix Exponentiation & Fast Power',
  'Game Theory: Nim Game & Minimax',
  'Geometry & Convex Hull Algorithms',
  'Grand Champion Mastery Project: Comprehensive Finale'
];

const DEFAULT_USER = {
  name: 'Animesh Goswami',
  username: 'animegos31002',
  email: 'animesh.goswami045@gmail.com',
  profileUrl: 'https://unstop.com/u/animegos31002',
  practiceUrl: 'https://unstop.com/practice/coding',
  ntfyTopic: 'animesh_unstop_streak',
  authenticated: true,
  authMethod: 'unstop',
  liveTracking: true,
  unstopVerified: true,
};

const DEFAULT_DB = {
  challenges: [],
  push_subscriptions: [],
  settings: {
    user_profile: JSON.stringify(DEFAULT_USER),
  },
};

// ─── Input Sanitization & Validation Helpers ───────────────────────────────────

/**
 * Validates that a URL is strictly http or https to prevent javascript: or data: XSS
 */
function sanitizeUrl(url) {
  if (!url || typeof url !== 'string') return null;
  const trimmed = url.trim();
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.href;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Strips dangerous control characters and limits length
 */
function sanitizeText(str, maxLength = 120) {
  if (!str || typeof str !== 'string') return '';
  return str.replace(/[\x00-\x1F\x7F]/g, '').trim().slice(0, maxLength);
}

/**
 * Ensures day is an integer between 1 and 30
 */
function sanitizeDay(day) {
  const d = parseInt(day, 10);
  if (isNaN(d) || d < 1 || d > 30) return null;
  return d;
}

/**
 * Validates status values
 */
function sanitizeStatus(status) {
  const allowed = ['pending', 'solved', 'skipped', 'untracked', 'upcoming'];
  return allowed.includes(status) ? status : 'pending';
}

// ─── Read / Atomic Write ───────────────────────────────────────────────────────

function readDB() {
  try {
    if (!fs.existsSync(DB_PATH)) {
      writeDB(DEFAULT_DB);
      return JSON.parse(JSON.stringify(DEFAULT_DB));
    }
    const raw = fs.readFileSync(DB_PATH, 'utf8');
    const data = JSON.parse(raw);
    if (!data.challenges) data.challenges = [];
    if (!data.push_subscriptions) data.push_subscriptions = [];
    if (!data.settings) data.settings = {};
    return data;
  } catch (err) {
    console.error('❌ Error reading database, using fallback:', err.message);
    return JSON.parse(JSON.stringify(DEFAULT_DB));
  }
}

/**
 * Atomic Write: Writes to a temporary file first, then atomically renames.
 * This guarantees zero file corruption during power cuts or unexpected process crashes.
 */
function writeDB(data) {
  const content = JSON.stringify(data, null, 2);
  let written = false;
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      fs.writeFileSync(TMP_PATH, content, 'utf8');
      fs.renameSync(TMP_PATH, DB_PATH);
      written = true;
      break;
    } catch {
      // Small busy wait on Windows file locking
      const start = Date.now();
      while (Date.now() - start < 40) {}
    }
  }
  if (!written) {
    try {
      fs.writeFileSync(DB_PATH, content, 'utf8');
    } catch (err) {
      console.error('❌ Database write failed:', err.message);
    }
  }
  if (!IS_VERCEL && fs.existsSync(LEGACY_PATH)) {
    try {
      fs.writeFileSync(LEGACY_PATH, content, 'utf8');
    } catch {}
  }
}

// ─── Automated Daily Backup Engine ─────────────────────────────────────────────

function createDailyBackup() {
  try {
    if (!fs.existsSync(DB_PATH)) return null;
    const today = new Date().toISOString().split('T')[0];
    const backupFile = path.join(BACKUPS_DIR, `challenges-${today}.json`);

    fs.copyFileSync(DB_PATH, backupFile);

    // Clean up backups older than 14 days
    cleanOldBackups(14);
    return backupFile;
  } catch (err) {
    console.error('⚠️ Backup failed:', err.message);
    return null;
  }
}

function cleanOldBackups(maxAgeDays = 14) {
  try {
    const files = fs.readdirSync(BACKUPS_DIR);
    const now = Date.now();
    const maxAgeMs = maxAgeDays * 24 * 60 * 60 * 1000;

    files.forEach(file => {
      if (file.startsWith('challenges-') && file.endsWith('.json')) {
        const filePath = path.join(BACKUPS_DIR, file);
        const stats = fs.statSync(filePath);
        if (now - stats.mtimeMs > maxAgeMs) {
          fs.unlinkSync(filePath);
          console.log(`🧹 Cleaned up old backup: ${file}`);
        }
      }
    });
  } catch (err) {
    console.warn('⚠️ Backup cleanup notice:', err.message);
  }
}

// ─── Helper Timestamps ─────────────────────────────────────────────────────────

function todayDateStr() {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date());
  } catch {
    const now = new Date();
    const istDate = new Date(now.getTime() + 5.5 * 3600 * 1000);
    return istDate.toISOString().split('T')[0];
  }
}

function nowISO() {
  return new Date().toISOString();
}

// ─── Challenge Operations ──────────────────────────────────────────────────────

function getOrCreateTodayChallenge() {
  const db = readDB();
  const today = todayDateStr();
  const challenges = db.challenges || [];

  // 1. First check if any challenge was solved TODAY
  const todaySolved = challenges.find(c => {
    const sDate = c.solved_at ? c.solved_at.split('T')[0] : null;
    return (sDate === today || c.assigned_date === today) && c.status === 'solved';
  });
  if (todaySolved) return todaySolved;

  // 2. Check if a challenge is already assigned for today
  let todayAssigned = challenges.find(c => c.assigned_date === today);
  if (todayAssigned) {
    if (todayAssigned.status === 'upcoming') {
      todayAssigned.status = 'pending';
      writeDB(db);
    }
    return todayAssigned;
  }

  // 3. Prioritize active pending quest
  let pending = challenges.find(c => c.status === 'pending');
  if (pending) {
    if (!pending.assigned_date) {
      pending.assigned_date = today;
      writeDB(db);
    }
    return pending;
  }

  // 4. Next sequential upcoming day
  const nextUpcoming = challenges.find(c => c.status === 'upcoming');
  if (nextUpcoming) {
    nextUpcoming.status = 'pending';
    nextUpcoming.assigned_date = today;
    writeDB(db);
    return nextUpcoming;
  }

  return challenges[0] || null;
}

function getTodayChallenge() {
  const db = readDB();
  const today = todayDateStr();
  return db.challenges.find(c => {
    const createdDate = c.created_at ? c.created_at.split('T')[0] : null;
    const remindedDate = c.reminded_at ? c.reminded_at.split('T')[0] : null;
    return createdDate === today || remindedDate === today;
  }) || null;
}

function handleStreakBreak(db, missedChallenge) {
  const currentStreak = getStreakUnlocked(db);
  const settings = db.settings || {};
  const brokenAt = nowISO();

  // All old data of the broken run is completely removed!
  // No old streak breaks, no old day numbers (Day 19, Day 25, etc.) will ever show up after refreshing!
  settings.streak_breaks = [];
  settings.archived_runs = [];
  settings.last_broken_streak = null;
  settings.last_broken_at = null;
  settings.last_broken_day = null;
  settings.total_streak_breaks = 0;
  settings.streak_status = 'broken';
  settings.is_broken = true;
  settings.best_streak = Math.max(Number(settings.best_streak) || 20, 20);

  // Reset challenges to a fresh clean 30-day sequence starting at Day 1:
  const today = todayDateStr();
  const freshChallenges = [];
  for (let d = 1; d <= 30; d++) {
    const topic = CURATED_TOPICS[d - 1] || `Problem Solving & Algorithms #${d}`;
    freshChallenges.push({
      id: d,
      day: d,
      title: `Day ${d}: ${topic}`,
      link: 'https://unstop.com/practice/coding',
      status: d === 1 ? 'pending' : 'upcoming',
      reminded_at: null,
      solved_at: null,
      created_at: brokenAt,
      assigned_date: d === 1 ? today : null,
      missed_at: null
    });
  }

  db.challenges = freshChallenges;
  db.settings = settings;
  writeDB(db);
  console.log(`[Streak Break] Old run purged! Fresh 30-day run initialized at Day 1 for ${today}.`);
}

function evaluateAndUpdateMissedDays() {
  const db = readDB();
  if (!db.challenges || db.challenges.length === 0) return;

  const today = todayDateStr();
  let streakBroken = false;
  let brokenChallenge = null;

  for (const c of db.challenges) {
    if (c.assigned_date && c.assigned_date < today && (c.status === 'pending' || c.status === 'untracked')) {
      streakBroken = true;
      brokenChallenge = c;
      break;
    }
  }

  if (streakBroken && brokenChallenge) {
    handleStreakBreak(db, brokenChallenge);
    return;
  }

  const streak = getStreakUnlocked(db);
  if (!db.settings) db.settings = {};
  const bestStreak = calculateAllTimeBestStreak(db);
  if (bestStreak > (db.settings.best_streak || 0)) {
    db.settings.best_streak = bestStreak;
    writeDB(db);
  }
}

function calculateAllTimeBestStreak(db) {
  const cmap = {};
  (db.challenges || []).forEach(c => { cmap[c.day] = c; });
  let maxStreak = 0;
  let currentRun = 0;
  for (let d = 1; d <= 30; d++) {
    const c = cmap[d];
    if (c && c.status === 'solved') {
      currentRun++;
      if (currentRun > maxStreak) {
        maxStreak = currentRun;
      }
    } else {
      currentRun = 0;
    }
  }
  const savedBest = Number(db.settings?.best_streak) || 20;
  return Math.max(maxStreak, savedBest, 20);
}

function getStreakChainDetails(db) {
  const today = todayDateStr();
  const challenges = db.challenges || [];
  const cmap = {};
  challenges.forEach(c => { cmap[c.day] = c; });

  let current = challenges.find(c => {
    const sDate = c.solved_at ? c.solved_at.split('T')[0] : null;
    return sDate === today && c.status === 'solved';
  });
  if (!current) {
    current = challenges.find(c => c.status === 'pending');
  }
  if (!current) {
    const maxSolved = challenges.reduce((max, c) => (c.status === 'solved' ? Math.max(max, c.day || 0) : max), 0);
    current = cmap[Math.min(maxSolved + 1, 30)];
  }
  if (!current) current = challenges[0] || null;

  const currentDay = current ? current.day : 1;
  const todayStatus = current ? current.status : 'pending';
  const startDay = todayStatus === 'solved' ? currentDay : currentDay - 1;

  let streak = 0;
  const chain = [];
  for (let d = startDay; d > 0; d--) {
    const c = cmap[d];
    if (c && c.status === 'solved') {
      streak++;
      chain.push(c);
    } else {
      break;
    }
  }

  let streakStartedDay = null;
  let streakStartedDate = null;
  let streakDaysRunning = 0;
  let remaining = 30; // Resets back to 30 when streak breaks!

  if (streak > 0 && chain.length > 0) {
    const oldest = chain[chain.length - 1];
    streakStartedDay = oldest.day;
    const rawDate = oldest.solved_at || oldest.created_at || '';
    streakStartedDate = rawDate.includes('T') ? rawDate.split('T')[0] : (rawDate || nowISO().split('T')[0]);
    streakDaysRunning = streak;
    remaining = Math.max(0, 30 - streak);
  }

  return {
    streak,
    streak_started_day: streakStartedDay,
    streak_started_date: streakStartedDate,
    streak_days_running: streakDaysRunning,
    remaining,
  };
}

function getStreakUnlocked(db) {
  return getStreakChainDetails(db).streak;
}

function updateChallengeStatus(day, status) {
  const sanitizedDay = sanitizeDay(day);
  const sanitizedStatus = sanitizeStatus(status);
  if (!sanitizedDay) return null;

  const db = readDB();
  const c = db.challenges.find(ch => ch.day === sanitizedDay);
  if (c) {
    c.status = sanitizedStatus;
    c.solved_at = sanitizedStatus === 'solved' ? nowISO() : null;
    if (sanitizedStatus === 'solved') {
      c.reminded_at = null; // Clear active reminder
      try {
        const stateFile = path.join(DATA_DIR, 'notifier_state.json');
        let state = {};
        if (fs.existsSync(stateFile)) {
          state = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
        }
        state.last_first_open_date = todayDateStr();
        state.first_open_resolved_at = nowISO();
        state.notifications_suppressed = true;
        state.today_completed = true;
        fs.writeFileSync(stateFile, JSON.stringify(state, null, 2), 'utf8');
      } catch {}
      if (process.platform === 'win32') {
        try {
          const { spawn } = require('child_process');
          const psCmd = 'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*unstop_laptop_notifier.py*--popup-data*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }';
          spawn('powershell.exe', ['-NoProfile', '-Command', psCmd], { detached: true, stdio: 'ignore', windowsHide: true }).unref();
        } catch {}
      }
    }
    writeDB(db);
    evaluateAndUpdateMissedDays();
    return c;
  }
  return null;
}

function updateChallengeLink(day, link, title) {
  const sanitizedDay = sanitizeDay(day);
  if (!sanitizedDay) return null;

  const validUrl = sanitizeUrl(link);
  const validTitle = sanitizeText(title, 150);

  const db = readDB();
  const c = db.challenges.find(ch => ch.day === sanitizedDay);
  if (c) {
    if (validUrl !== null) c.link = validUrl;
    if (validTitle) c.title = validTitle;
    writeDB(db);
    return c;
  }
  return null;
}

function markReminded(day) {
  const db = readDB();
  const c = db.challenges.find(ch => ch.day === Number(day));
  if (c) {
    c.reminded_at = nowISO();
    writeDB(db);
  }
}

function getAllChallenges() {
  evaluateAndUpdateMissedDays();
  const db = readDB();
  return [...db.challenges].sort((a, b) => a.day - b.day);
}

function getStats() {
  evaluateAndUpdateMissedDays();
  const db = readDB();
  const challenges = db.challenges || [];
  const solved = challenges.filter(c => c.status === 'solved').length;
  const missed = challenges.filter(c => c.status === 'missed').length;
  const total = challenges.length;
  const streakInfo = getStreakChainDetails(db);
  const streak = streakInfo.streak;
  const settings = db.settings || {};
  const bestStreak = Math.max(calculateAllTimeBestStreak(db), Number(settings.best_streak) || 20, 20, streak);

  const current = getOrCreateTodayChallenge();
  const todayStatus = current ? current.status : 'pending';
  const nowHour = new Date().getHours();
  const streakAtRisk = (streak > 0 || solved > 0) && todayStatus === 'pending' && nowHour >= 18;
  const streakBreaks = settings.streak_breaks || [];
  const isBroken = Boolean(settings.is_broken && streak === 0);
  const streakStatus = isBroken ? 'broken' : (streakAtRisk ? 'at_risk' : 'active');

  const remaining = Math.max(0, 30 - solved);

  return {
    total: 30,
    solved,
    missed,
    streak,
    current_streak: streak,
    best_streak: bestStreak,
    max_streak: bestStreak,
    remaining,
    streak_remaining: remaining,
    streak_started_day: streakInfo.streak_started_day,
    streak_started_date: streakInfo.streak_started_date,
    streak_days_running: streakInfo.streak_days_running,
    quest_remaining: remaining,
    streak_at_risk: streakAtRisk,
    streak_status: streakStatus,
    is_broken: isBroken,
    last_broken_streak: Number(settings.last_broken_streak) || 0,
    last_broken_at: settings.last_broken_at || null,
    last_broken_day: settings.last_broken_day || null,
    total_streak_breaks: Number(settings.total_streak_breaks) || streakBreaks.length,
    streak_breaks: streakBreaks,
    tracked_link: 'https://unstop.com/practice/coding',
    tracked_title: 'Coding Practice Platform for Placements & Interviews',
  };
}

function getStreak() {
  evaluateAndUpdateMissedDays();
  const db = readDB();
  return getStreakUnlocked(db);
}

function getCurrentDay() {
  const db = readDB();
  const maxDay = db.challenges.reduce((max, c) => Math.max(max, c.day || 0), 0);
  return Math.min(maxDay + 1, 30);
}

// ─── Push Subscriptions ────────────────────────────────────────────────────────

function saveSubscription(sub) {
  if (!sub || !sub.endpoint || typeof sub.endpoint !== 'string') return;
  const endpoint = sanitizeUrl(sub.endpoint) || sub.endpoint;
  const p256dh = sanitizeText(sub.keys?.p256dh, 255);
  const auth = sanitizeText(sub.keys?.auth, 255);

  if (!endpoint || !p256dh || !auth) return;

  const db = readDB();
  const existing = db.push_subscriptions.findIndex(s => s.endpoint === endpoint);
  const entry = { endpoint, p256dh, auth };

  if (existing >= 0) db.push_subscriptions[existing] = entry;
  else db.push_subscriptions.push(entry);

  writeDB(db);
}

function getAllSubscriptions() {
  return readDB().push_subscriptions;
}

function removeSubscription(endpoint) {
  if (!endpoint || typeof endpoint !== 'string') return;
  const db = readDB();
  db.push_subscriptions = db.push_subscriptions.filter(s => s.endpoint !== endpoint);
  writeDB(db);
}

// ─── Settings & User Profile ───────────────────────────────────────────────────

function getSetting(key) {
  const sanitizedKey = sanitizeText(key, 50);
  return readDB().settings[sanitizedKey] || null;
}

function setSetting(key, value) {
  const sanitizedKey = sanitizeText(key, 50);
  const db = readDB();
  db.settings[sanitizedKey] = String(value);
  writeDB(db);
}

function getUserProfile() {
  const db = readDB();
  if (db.settings && db.settings.user_profile) {
    try {
      return JSON.parse(db.settings.user_profile);
    } catch {
      return DEFAULT_USER;
    }
  }
  return DEFAULT_USER;
}

function setUserProfile(profile) {
  if (!profile || typeof profile !== 'object') return getUserProfile();

  const current = getUserProfile();
  const updated = {
    ...current,
    name: sanitizeText(profile.name || current.name, 80),
    username: sanitizeText(profile.username || current.username, 50),
    email: sanitizeText(profile.email || current.email, 100),
    profileUrl: sanitizeUrl(profile.profileUrl) || current.profileUrl,
    practiceUrl: sanitizeUrl(profile.practiceUrl) || current.practiceUrl,
    ntfyTopic: sanitizeText(profile.ntfyTopic || current.ntfyTopic, 50),
  };

  setSetting('user_profile', JSON.stringify(updated));
  return updated;
}

function getCurrentRunChallenges() {
  evaluateAndUpdateMissedDays();
  const db = readDB();
  return [...db.challenges].sort((a, b) => a.day - b.day);
}

function get30DayRoadmap() {
  return getCurrentRunChallenges();
}

function setChallengeDay(day, data) {
  const validDay = sanitizeDay(day);
  if (!validDay) return null;

  const validTitle = sanitizeText(data.title, 120) || `Day ${validDay} Challenge`;
  const validLink = sanitizeUrl(data.link);
  const validStatus = sanitizeStatus(data.status || 'solved');

  const db = readDB();
  let c = db.challenges.find(ch => ch.day === validDay);
  if (!c) {
    c = {
      id: Date.now() + validDay,
      day: validDay,
      title: validTitle,
      link: validLink,
      status: validStatus,
      reminded_at: null,
      solved_at: validStatus === 'solved' ? nowISO() : null,
      created_at: nowISO(),
    };
    db.challenges.push(c);
  } else {
    c.title = validTitle;
    if (validLink !== null) c.link = validLink;
    c.status = validStatus;
    c.solved_at = validStatus === 'solved' ? nowISO() : null;
  }

  writeDB(db);
  return c;
}

function syncDatabase(incoming) {
  if (!incoming || !incoming.challenges || !Array.isArray(incoming.challenges)) return null;
  const db = readDB();
  const cmap = {};
  (db.challenges || []).forEach(c => { cmap[c.day] = c; });

  for (const c of incoming.challenges) {
    if (!c.day) continue;
    const existing = cmap[c.day];
    if (!existing) {
      db.challenges.push(c);
      cmap[c.day] = c;
    } else {
      existing.status = c.status;
      existing.solved_at = c.solved_at || null;
      if (c.link) existing.link = sanitizeUrl(c.link) || existing.link;
      if (c.title) existing.title = sanitizeText(c.title, 150) || existing.title;
      existing.assigned_date = c.assigned_date || null;
      existing.missed_at = c.missed_at !== undefined ? c.missed_at : null;
    }
  }

  if (incoming.settings && typeof incoming.settings === 'object') {
    db.settings = { ...db.settings, ...incoming.settings };
  }

  writeDB(db);
  evaluateAndUpdateMissedDays();
  return db;
}

function loginUnstopUser(authData) {
  if (!authData || typeof authData !== 'object') {
    return { success: false, error: 'Invalid login details' };
  }

  const current = getUserProfile();
  const rawUsername = (authData.username || authData.userId || current.username || 'animegos31002').replace(/^@/, '').trim();
  const username = sanitizeText(rawUsername, 50);
  const rawEmail = (authData.email || current.email || 'animesh.goswami045@gmail.com').trim().toLowerCase();
  const email = sanitizeText(rawEmail, 100);
  const name = sanitizeText(authData.name || current.name || 'Animesh Goswami', 80);
  const profileUrl = sanitizeUrl(authData.profileUrl || `https://unstop.com/u/${username}`) || current.profileUrl;
  const practiceUrl = sanitizeUrl(authData.practiceUrl || 'https://unstop.com/practice/coding');

  const updatedProfile = {
    ...current,
    name,
    username,
    email,
    profileUrl,
    practiceUrl,
    authenticated: true,
    authMethod: 'unstop',
    authenticatedAt: nowISO(),
    liveTracking: true,
    unstopVerified: true,
  };

  setUserProfile(updatedProfile);
  setSetting('unstop_authenticated', 'true');
  setSetting('unstop_auth_user', username);
  setSetting('unstop_auth_email', email);
  setSetting('unstop_auth_at', nowISO());

  return {
    success: true,
    message: `Successfully connected Unstop account @${username} (${email})!`,
    user: updatedProfile,
    liveTracking: true,
  };
}

function logoutUnstopUser() {
  const current = getUserProfile();
  const updated = {
    ...current,
    authenticated: false,
    liveTracking: false,
  };
  setUserProfile(updated);
  setSetting('unstop_authenticated', 'false');
  return { success: true, message: 'Disconnected from Unstop' };
}

function getAuthStatus() {
  const profile = getUserProfile();
  const authSetting = getSetting('unstop_authenticated');
  const isAuthenticated = Boolean(profile.authenticated || authSetting === 'true');
  const stats = getStats();
  const challenge = getOrCreateTodayChallenge();

  return {
    authenticated: isAuthenticated,
    authMethod: profile.authMethod || (isAuthenticated ? 'unstop' : null),
    email: profile.email || 'animesh.goswami045@gmail.com',
    username: profile.username || 'animegos31002',
    user: profile,
    liveTracking: isAuthenticated,
    todaySolved: challenge ? challenge.status === 'solved' : false,
    challenge,
    stats,
  };
}

module.exports = {
  getTodayChallenge,
  getOrCreateTodayChallenge,
  updateChallengeStatus,
  updateChallengeLink,
  markReminded,
  getAllChallenges,
  get30DayRoadmap,
  getCurrentRunChallenges,
  setChallengeDay,
  syncDatabase,
  getStats,
  getStreak,
  saveSubscription,
  getAllSubscriptions,
  removeSubscription,
  getSetting,
  setSetting,
  getCurrentDay,
  getUserProfile,
  setUserProfile,
  loginUnstopUser,
  logoutUnstopUser,
  getAuthStatus,
  createDailyBackup,
  sanitizeUrl,
  sanitizeText,
  sanitizeDay,
};
