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
if (!fs.existsSync(DB_PATH)) {
  try {
    if (fs.existsSync(BUNDLED_PATH)) {
      fs.copyFileSync(BUNDLED_PATH, DB_PATH);
    } else if (fs.existsSync(LEGACY_PATH)) {
      fs.copyFileSync(LEGACY_PATH, DB_PATH);
    }
    console.log('📦 Initialized challenges.json for server environment');
  } catch (err) {
    console.warn('⚠️ Initialization notice:', err.message);
  }
}

// ─── Default DB Structure ──────────────────────────────────────────────────────
const DEFAULT_USER = {
  name: 'Animesh Goswami',
  username: 'animegos31002',
  email: 'animesh.goswami045@gmail.com',
  profileUrl: 'https://unstop.com/u/animegos31002',
  practiceUrl: 'https://unstop.com/practice/coding',
  ntfyTopic: 'animesh_unstop_streak_9823',
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
  try {
    const content = JSON.stringify(data, null, 2);
    fs.writeFileSync(TMP_PATH, content, 'utf8');
    fs.renameSync(TMP_PATH, DB_PATH);
  } catch (err) {
    console.error('❌ Database atomic write failed:', err.message);
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

  // 1. First check if any challenge was solved TODAY (preserves active streak & suppresses alerts)
  const todaySolved = db.challenges.find(c => {
    const sDate = c.solved_at ? c.solved_at.split('T')[0] : null;
    return sDate === today && c.status === 'solved';
  });
  if (todaySolved) return todaySolved;

  // 2. Prioritize active pending quest
  const pending = db.challenges.find(c => c.status === 'pending');
  if (pending) return pending;

  // 3. Fallback to next sequential day
  const maxSolved = db.challenges.reduce((max, c) => (c.status === 'solved' ? Math.max(max, c.day || 0) : max), 0);
  const nextDay = Math.min(maxSolved + 1, 30);
  const match = db.challenges.find(c => c.day === nextDay);
  if (match) return match;

  return db.challenges[0] || null;
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

function evaluateAndUpdateMissedDays() {
  const db = readDB();
  if (!db.challenges || db.challenges.length === 0) return;

  const current = getOrCreateTodayChallenge();
  const currDay = current ? current.day : 1;

  let changed = false;
  for (const c of db.challenges) {
    if ((c.day || 0) < currDay && (c.status === 'pending' || c.status === 'untracked')) {
      c.status = 'missed';
      changed = true;
    }
  }

  const streak = getStreakUnlocked(db);
  if (!db.settings) db.settings = {};
  const bestStreak = calculateAllTimeBestStreak(db);
  if (bestStreak > (db.settings.best_streak || 0)) {
    db.settings.best_streak = bestStreak;
    changed = true;
  }

  if (changed) {
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
  const savedBest = db.settings?.best_streak || 0;
  return Math.max(maxStreak, savedBest);
}

function getStreakUnlocked(db) {
  const current = getOrCreateTodayChallenge();
  const currentDay = current ? current.day : 1;
  const cmap = {};
  (db.challenges || []).forEach(c => { cmap[c.day] = c; });

  const todayStatus = current ? current.status : 'pending';
  const startDay = todayStatus === 'solved' ? currentDay : currentDay - 1;

  let streak = 0;
  for (let d = startDay; d > 0; d--) {
    const c = cmap[d];
    if (c && c.status === 'solved') {
      streak++;
    } else {
      break;
    }
  }
  return streak;
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
  return [...db.challenges].sort((a, b) => b.day - a.day);
}

function getStats() {
  evaluateAndUpdateMissedDays();
  const db = readDB();
  const solved = db.challenges.filter(c => c.status === 'solved').length;
  const missed = db.challenges.filter(c => c.status === 'missed').length;
  const total = db.challenges.length;
  const streak = getStreakUnlocked(db);
  const bestStreak = Math.max(calculateAllTimeBestStreak(db), streak);

  const current = getOrCreateTodayChallenge();
  const todayStatus = current ? current.status : 'pending';
  const nowHour = new Date().getHours();
  const streakAtRisk = (streak > 0 || solved > 0) && todayStatus === 'pending' && nowHour >= 18;

  return {
    total,
    solved,
    missed,
    streak,
    best_streak: bestStreak,
    remaining: Math.max(0, 30 - solved),
    streak_at_risk: streakAtRisk,
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

function get30DayRoadmap() {
  const db = readDB();
  const current = getOrCreateTodayChallenge();
  const challengesMap = new Map(db.challenges.map(c => [c.day, c]));

  const roadmap = [];
  for (let d = 1; d <= 30; d++) {
    if (challengesMap.has(d)) {
      roadmap.push(challengesMap.get(d));
    } else {
      roadmap.push({
        id: null,
        day: d,
        title: `Day ${d} Challenge`,
        link: null,
        status: d < current.day ? 'untracked' : 'upcoming',
        reminded_at: null,
        solved_at: null,
        created_at: null,
      });
    }
  }
  return roadmap;
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

module.exports = {
  getTodayChallenge,
  getOrCreateTodayChallenge,
  updateChallengeStatus,
  updateChallengeLink,
  markReminded,
  getAllChallenges,
  get30DayRoadmap,
  setChallengeDay,
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
  createDailyBackup,
  sanitizeUrl,
  sanitizeText,
  sanitizeDay,
};
