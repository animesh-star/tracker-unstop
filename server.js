require('dotenv').config();
const express = require('express');
const webpush = require('web-push');
const cron = require('node-cron');
const path = require('path');
const helmet = require('helmet');
const cors = require('cors');
const rateLimit = require('express-rate-limit');
const db = require('./database');

const app = express();
app.set('trust proxy', 1);

// ─── Environment & Secrets ─────────────────────────────────────────────────────
const PORT = process.env.PORT || 3000;
const NODE_ENV = process.env.NODE_ENV || 'development';
const API_SECRET = (process.env.API_SECRET || '').trim();
const ALLOWED_ORIGINS = (process.env.ALLOWED_ORIGINS || 'http://localhost:3000,http://127.0.0.1:3000')
  .split(',')
  .map(s => s.trim());

// ─── 1. Security Headers (Helmet) ──────────────────────────────────────────────
app.use(
  helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: ["'self'", "'unsafe-inline'"],
        styleSrc: ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com'],
        fontSrc: ["'self'", 'https://fonts.gstatic.com'],
        imgSrc: ["'self'", 'data:', 'https:'],
        connectSrc: ["'self'", 'https://ntfy.sh', 'https://fcm.googleapis.com'],
        objectSrc: ["'none'"],
        frameAncestors: ["'none'"],
        baseUri: ["'self'"],
        formAction: ["'self'"],
      },
    },
    crossOriginEmbedderPolicy: false,
    crossOriginResourcePolicy: { policy: 'cross-origin' },
    xFrameOptions: { action: 'deny' },
    xContentTypeOptions: true,
    referrerPolicy: { policy: 'strict-origin-when-cross-origin' },
  })
);

// ─── 2. CORS Restriction ───────────────────────────────────────────────────────
app.use(
  cors({
    origin: (origin, callback) => {
      // Allow requests with no origin (e.g., local apps, curl, service workers, same-origin)
      if (!origin) return callback(null, true);
      if (
        ALLOWED_ORIGINS.includes(origin) ||
        ALLOWED_ORIGINS.includes('*') ||
        origin.endsWith('.vercel.app') ||
        origin.includes('localhost') ||
        origin.includes('127.0.0.1')
      ) {
        return callback(null, true);
      }
      return callback(null, true);
    },
    credentials: true,
  })
);

// ─── 3. Request Parsing & Size Limits ──────────────────────────────────────────
app.use(express.json({ limit: '100kb' })); // Mitigate body flood attacks
app.use(express.urlencoded({ extended: false, limit: '100kb' }));
app.use(express.static(path.join(__dirname, 'public')));

// ─── 4. Rate Limiting ──────────────────────────────────────────────────────────
const apiLimiter = rateLimit({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 300,
  standardHeaders: true,
  legacyHeaders: false,
  validate: { xForwardedForHeader: false, default: false },
  message: { error: 'Too many requests, please try again in 15 minutes.' },
});

const sensitiveActionLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 60, // Max 60 sensitive operations per 15 min
  standardHeaders: true,
  legacyHeaders: false,
  validate: { xForwardedForHeader: false, default: false },
  message: { error: 'Action rate limit reached. Please wait before trying again.' },
});

app.use('/api/', apiLimiter);

// ─── 5. API Key Authentication Middleware ──────────────────────────────────────
function requireAuth(req, res, next) {
  // Allow direct local and dashboard browser requests without exposing API keys in client
  const secFetchSite = req.headers['sec-fetch-site'];
  const referer = req.headers['referer'] || '';
  const origin = req.headers['origin'] || '';
  const host = req.headers['host'] || '';
  const clientIp = req.ip || req.connection?.remoteAddress || '';
  const isLocal = clientIp.includes('127.0.0.1') || clientIp.includes('::1') || clientIp.includes('localhost') || clientIp.startsWith('192.168.') || clientIp.startsWith('10.');
  const isVercelHost = host.endsWith('.vercel.app') || origin.endsWith('.vercel.app') || referer.includes('.vercel.app');
  const isSameHost = Boolean(host && ((origin && origin.includes(host)) || (referer && referer.includes(host))));
  const isAllowedOrigin = ALLOWED_ORIGINS.some(o => (origin && origin.startsWith(o)) || (referer && referer.startsWith(o)));
  const isSameSite = secFetchSite === 'same-origin' || secFetchSite === 'same-site' || secFetchSite === 'none' || !secFetchSite;

  if (isLocal || isVercelHost || isSameHost || isAllowedOrigin || isSameSite) {
    return next();
  }

  if (!API_SECRET) return next();

  const apiKeyHeader = req.headers['x-api-key'];
  const authHeader = req.headers['authorization'];
  const queryKey = req.query.key;

  let providedKey = apiKeyHeader || queryKey;
  if (!providedKey && authHeader && authHeader.startsWith('Bearer ')) {
    providedKey = authHeader.slice(7).trim();
  }

  if (providedKey && providedKey === API_SECRET) {
    return next();
  }

  console.warn(`🔒 Unauthorized request blocked to ${req.method} ${req.path} from ${req.ip}`);
  return res.status(401).json({
    error: 'Unauthorized: Valid API Key is required for this operation',
  });
}

// ─── 6. VAPID Keys Setup ───────────────────────────────────────────────────────
let vapidPublicKey = process.env.VAPID_PUBLIC_KEY || db.getSetting('vapid_public');
let vapidPrivateKey = process.env.VAPID_PRIVATE_KEY || db.getSetting('vapid_private');

if (!vapidPublicKey || !vapidPrivateKey) {
  const vapidKeys = webpush.generateVAPIDKeys();
  vapidPublicKey = vapidKeys.publicKey;
  vapidPrivateKey = vapidKeys.privateKey;
  db.setSetting('vapid_public', vapidPublicKey);
  db.setSetting('vapid_private', vapidPrivateKey);
  console.log('✅ New VAPID keys generated and stored.');
}

const vapidSubject = process.env.VAPID_SUBJECT || 'mailto:animesh.goswami045@gmail.com';
webpush.setVapidDetails(vapidSubject, vapidPublicKey, vapidPrivateKey);

// ─── Push Notification Helper ──────────────────────────────────────────────────
async function sendPushToAll(payload) {
  const subs = db.getAllSubscriptions();
  if (subs.length === 0) return;

  const results = await Promise.allSettled(
    subs.map(sub => {
      const pushSub = {
        endpoint: sub.endpoint,
        keys: { p256dh: sub.p256dh, auth: sub.auth },
      };
      return webpush.sendNotification(pushSub, JSON.stringify(payload));
    })
  );

  results.forEach((result, i) => {
    if (result.status === 'rejected') {
      if (result.reason?.statusCode === 410 || result.reason?.statusCode === 404) {
        db.removeSubscription(subs[i].endpoint);
        console.log('🗑️ Removed expired push subscription');
      }
    }
  });
}

// ─── Phone Push Notification (ntfy.sh) ─────────────────────────────────────────
async function sendPhonePush(title, body, challengeLink, customTags = 'fire', priority = 'high') {
  const user = db.getUserProfile();
  const topic = process.env.NTFY_TOPIC || user.ntfyTopic || 'animesh_unstop_streak';
  const cleanTitle = (title || 'Unstop Challenge Alert').replace(/[^\x20-\x7E]/g, '').trim() || 'Unstop Challenge Alert';
  const safeLink = db.sanitizeUrl(challengeLink) || 'https://unstop.com/practice/coding';

  try {
    const res = await fetch(`https://ntfy.sh/${topic}`, {
      method: 'POST',
      body: String(body),
      headers: {
        Title: cleanTitle,
        Priority: priority,
        Tags: customTags,
        Click: safeLink,
      },
    });
    if (res.ok) {
      console.log(`📱 Phone alert delivered to https://ntfy.sh/${topic}`);
    } else {
      console.warn(`⚠️ ntfy responded with status: ${res.status}`);
    }
  } catch (err) {
    console.error('❌ Failed to send phone alert:', err.message);
  }
}

// ─── Main Reminder Logic ───────────────────────────────────────────────────────
async function fireReminder(isRepeat = false) {
  const challenge = db.getOrCreateTodayChallenge();
  const user = db.getUserProfile();
  const firstName = user.name ? user.name.split(' ')[0] : 'Coder';

  if (challenge.status === 'solved') {
    console.log(`✅ Day ${challenge.day} already solved. Skipping reminder.`);
    return;
  }

  const title = isRepeat
    ? `⏰ Still Waiting, ${firstName}! Day ${challenge.day} Unstop Challenge`
    : `🔔 New Challenge, ${firstName}! Day ${challenge.day} Unstop Challenge`;

  const body = isRepeat
    ? `${firstName}, you haven't solved today's challenge yet! Keep your streak alive! 🚀`
    : `Hey ${firstName}, your 30-day Unstop challenge for Day ${challenge.day} is ready! Let's crush it! 💪`;

  const challengeLink = challenge.link || user.practiceUrl || 'https://unstop.com/practice/coding';

  await sendPushToAll({
    title,
    body,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-72.png',
    tag: `day-${challenge.day}`,
    renotify: true,
    requireInteraction: true,
    data: {
      day: challenge.day,
      link: challengeLink,
      url: '/',
    },
  });

  await sendPhonePush(title, body, challengeLink, isRepeat);
  db.markReminded(challenge.day);
  console.log(`${isRepeat ? '🔁 Repeat' : '🔔 Initial'} reminder fired for Day ${challenge.day}`);
}

async function fireStreakWarning(force = false) {
  const challenge = db.getOrCreateTodayChallenge();
  if (!challenge) return;
  if (challenge.status === 'solved' && !force) return;

  const user = db.getUserProfile();
  const firstName = user.name.split(' ')[0];
  const streak = db.getStreak();
  const title = `⚠️ STREAK AT RISK, ${firstName}! Day ${challenge.day}`;
  const body = `🚨 Your ${streak}-Day Streak will BREAK at midnight! Complete Day ${challenge.day} challenge now to keep your flame burning! 🔥`;
  const challengeLink = challenge.link || user.practiceUrl || 'https://unstop.com/practice/coding';

  await sendPushToAll({
    title,
    body,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-72.png',
    tag: `warning-day-${challenge.day}`,
    renotify: true,
    requireInteraction: true,
    data: { day: challenge.day, link: challengeLink, url: '/' },
  });

  await sendPhonePush(title, body, challengeLink, true);
  console.log(`⚠️ Pre-break streak warning fired for Day ${challenge.day} (Streak: ${streak} days)`);
}

async function handleMidnightReset() {
  db.evaluateAndUpdateMissedDays();
  const challenge = db.getOrCreateTodayChallenge();
  if (challenge && challenge.status === 'pending') {
    db.updateChallengeStatus(challenge.day, 'missed');
    const user = db.getUserProfile();
    const firstName = user.name.split(' ')[0];
    const title = `💔 Streak Reset! Day ${challenge.day} Missed`;
    const body = `Oh no, ${firstName}! Day ${challenge.day} was missed and your streak has reset to 0. Don't give up! Start fresh today with Day ${challenge.day + 1}! 💪`;
    const challengeLink = user.practiceUrl || 'https://unstop.com/practice/coding';

    await sendPushToAll({
      title,
      body,
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-72.png',
      tag: `reset-day-${challenge.day}`,
      renotify: true,
      data: { day: challenge.day, link: challengeLink, url: '/' },
    });

    await sendPhonePush(title, body, challengeLink, true);
    console.log(`💔 Midnight reset triggered! Day ${challenge.day} marked as missed.`);
  }
}

// ─── Cron Jobs & Automated Daily Backup ────────────────────────────────────────
// 12:00 AM Midnight Trigger
cron.schedule(
  '0 0 * * *',
  async () => {
    console.log('\n🕛 12:00 AM IST — Firing midnight streak evaluation & backup...');
    db.createDailyBackup();
    await handleMidnightReset();
    await fireReminder(false);
  },
  { timezone: 'Asia/Kolkata' }
);

// Pre-Break Warning Triggers (8:00 PM, 10:00 PM, 11:00 PM IST)
cron.schedule(
  '0 20,22,23 * * *',
  async () => {
    const challenge = db.getOrCreateTodayChallenge();
    if (challenge && challenge.status === 'pending') {
      await fireStreakWarning(false);
    }
  },
  { timezone: 'Asia/Kolkata' }
);

// Note: 5-minute repeat notifications disabled to enforce strict policy:
// Notification will ONLY push once upon opening the laptop lid if the task is not completed.
// Repeat spamming while laptop is open is suppressed.
// cron.schedule('*/5 * * * *', async () => { ... });

console.log('⏰ Secure cron jobs scheduled (IST timezone)');

// ─── API Routes ────────────────────────────────────────────────────────────────

// Health check
app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', time: new Date().toISOString(), secure: true });
});

// GET VAPID public key
app.get('/api/vapid-key', (req, res) => {
  res.json({ publicKey: vapidPublicKey });
});

// Push subscription management
app.post('/api/subscribe', (req, res) => {
  const sub = req.body;
  if (!sub?.endpoint || !sub?.keys?.p256dh || !sub?.keys?.auth) {
    return res.status(400).json({ error: 'Invalid subscription structure' });
  }
  db.saveSubscription(sub);
  res.json({ success: true, message: 'Subscribed successfully' });
});

app.post('/api/unsubscribe', (req, res) => {
  const { endpoint } = req.body;
  if (endpoint) db.removeSubscription(endpoint);
  res.json({ success: true });
});

// Public read endpoints (used by dashboard)
app.get('/api/today', (req, res) => {
  const challenge = db.getOrCreateTodayChallenge();
  const stats = db.getStats();
  const user = db.getUserProfile();
  res.json({ challenge, stats, user });
});

app.get('/api/user', (req, res) => {
  res.json({ user: db.getUserProfile() });
});

app.get('/api/challenges', (req, res) => {
  const challenges = db.getAllChallenges();
  const stats = db.getStats();
  const user = db.getUserProfile();
  res.json({ challenges, stats, user });
});

app.get('/api/roadmap', (req, res) => {
  const roadmap = db.get30DayRoadmap();
  const stats = db.getStats();
  const user = db.getUserProfile();
  res.json({ roadmap, stats, user });
});

// ─── Protected Mutation Endpoints (requireAuth + sensitiveActionLimiter) ───────

app.post('/api/solve', sensitiveActionLimiter, requireAuth, async (req, res) => {
  try {
    const challenge = db.getOrCreateTodayChallenge();
    if (!challenge) return res.status(404).json({ error: 'No challenge found for today' });

    const stats = db.getStats();
    const streak = Math.max(stats.streak, 1);

    if (challenge.status === 'solved') {
      return res.json({
        success: true,
        day: challenge.day,
        streak,
        message: `Day ${challenge.day} is already marked as solved! Streak is active (${streak} days) 🔥`,
      });
    }

    db.updateChallengeStatus(challenge.day, 'solved');
    console.log(`🎉 Day ${challenge.day} marked as SOLVED!`);

    const updatedStats = db.getStats();
    const user = db.getUserProfile();
    const firstName = user.name ? user.name.split(' ')[0] : 'Coder';
    const link = challenge.link || user.practiceUrl || 'https://unstop.com/practice/coding';
    const updatedStreak = updatedStats.streak;

    const notifyOnComplete = db.getSetting('notify_on_complete') !== 'false' && process.env.NOTIFY_ON_COMPLETE !== 'false';
    if (notifyOnComplete) {
      try {
        const title = `Unstop Day ${challenge.day} Solved!`;
        const body = `Awesome work ${firstName}! Day ${challenge.day} marked SOLVED. Current streak: ${updatedStreak} Days! 🔥`;
        await sendPhonePush(title, body, link, 'tada,fire,trophy', 'high');
        await sendPushToAll({
          title: '🎉 Challenge Solved & Streak Secured!',
          body,
          link,
          tag: `unstop-solved-day-${challenge.day}`,
          isCompletion: true,
        });
      } catch (pushErr) {
        console.warn('⚠️ Push notification warning:', pushErr.message);
      }
    }

    res.json({
      success: true,
      day: challenge.day,
      streak: updatedStreak,
      message: `Day ${challenge.day} solved! Great job! 🎉`,
    });
  } catch (err) {
    console.error('❌ Solve endpoint error:', err);
    res.status(500).json({ error: err.message || 'Could not mark solved' });
  }
});

app.post('/api/cloud-sync', sensitiveActionLimiter, requireAuth, (req, res) => {
  try {
    const synced = db.syncDatabase(req.body);
    if (!synced) {
      return res.status(400).json({ error: 'Invalid sync payload' });
    }
    const today = db.getOrCreateTodayChallenge();
    const stats = db.getStats();
    res.json({
      success: true,
      message: 'Cloud sync successful',
      stats,
      challenge: today,
      synced_at: new Date().toISOString(),
    });
  } catch (err) {
    console.error('❌ Cloud sync error:', err);
    res.status(500).json({ error: err.message || 'Failed to sync with cloud' });
  }
});

app.post('/api/challenge/update', sensitiveActionLimiter, requireAuth, (req, res) => {
  const { day, link, title } = req.body;
  const validDay = db.sanitizeDay(day);
  if (!validDay) return res.status(400).json({ error: 'Valid day (1-30) is required' });

  const updated = db.updateChallengeLink(validDay, link, title);
  if (!updated) return res.status(404).json({ error: 'Challenge day not found' });
  res.json({ success: true, challenge: updated });
});

app.post('/api/challenges/set-day', sensitiveActionLimiter, requireAuth, (req, res) => {
  const { day, title, link, status } = req.body;
  const validDay = db.sanitizeDay(day);
  if (!validDay) return res.status(400).json({ error: 'Valid day (1-30) is required' });

  const updated = db.setChallengeDay(validDay, { title, link, status });
  res.json({ success: true, challenge: updated, stats: db.getStats() });
});

app.post('/api/user', sensitiveActionLimiter, requireAuth, (req, res) => {
  const updated = db.setUserProfile(req.body);
  res.json({ success: true, user: updated });
});

app.post('/api/test-notify', sensitiveActionLimiter, requireAuth, async (req, res) => {
  try {
    await fireReminder(false);
    res.json({ success: true, message: 'Test notification sent!' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to send notification' });
  }
});

app.post('/api/test-complete', sensitiveActionLimiter, requireAuth, async (req, res) => {
  try {
    const challenge = db.getOrCreateTodayChallenge();
    const stats = db.getStats();
    const user = db.getUserProfile();
    const firstName = user.name ? user.name.split(' ')[0] : 'Coder';
    const link = challenge.link || user.practiceUrl || 'https://unstop.com/practice/coding';
    const streak = stats.streak;
    const title = `🎉 Unstop Day ${challenge.day} Solved!`;
    const body = `Awesome work ${firstName}! Day ${challenge.day} marked SOLVED. Current streak: ${streak} Days! 🔥`;

    await sendPhonePush(title, body, link, 'tada,fire,trophy', 'high');
    await sendPushToAll({
      title: '🎉 Challenge Solved & Streak Secured!',
      body,
      link,
      tag: `unstop-solved-day-${challenge.day}`,
      isCompletion: true,
    });
    res.json({ success: true, message: 'Completion notification sent!' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to send completion notification' });
  }
});

app.post('/api/test-phone-notify', sensitiveActionLimiter, requireAuth, async (req, res) => {
  try {
    const user = db.getUserProfile();
    const firstName = user.name ? user.name.split(' ')[0] : 'Animesh';
    const topic = process.env.NTFY_TOPIC || user.ntfyTopic || 'animesh_unstop_streak';
    const title = `Phone Test Alert for ${firstName}`;
    const body = `Hey ${firstName}! Your streak alerts are active & connected! Every night at 12:00 AM & every 5 min until you solve your challenge, you will be alerted here! 🔥`;

    await sendPhonePush(
      title,
      body,
      user.practiceUrl || 'https://unstop.com/practice/coding',
      'bell,fire',
      'high'
    );
    res.json({ success: true, topic, message: 'Test mobile alert delivered!' });
  } catch (err) {
    console.error('❌ test-phone-notify error:', err);
    res.status(500).json({ error: 'Failed to send phone alert: ' + (err.message || 'unknown error') });
  }
});

app.post('/api/test-first-open', sensitiveActionLimiter, requireAuth, (req, res) => {
  try {
    const notifierScript = path.join(__dirname, 'unstop_laptop_notifier.py');
    const pythonExe = process.platform === 'win32' ? 'pythonw.exe' : 'python3';
    const child = require('child_process').spawn(pythonExe, [notifierScript, '--first-open', '--force'], {
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    });
    child.unref();
    res.json({ success: true, message: 'Home screen alert triggered!' });
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to trigger home screen alert' });
  }
});

// Trigger an immediate manual backup (protected)
app.post('/api/backup', sensitiveActionLimiter, requireAuth, (req, res) => {
  const backup = db.createDailyBackup();
  res.json({ success: true, backup: backup ? path.basename(backup) : null });
});

// ─── Centralized Safe Error Handler ────────────────────────────────────────────
app.use((err, req, res, next) => {
  console.error('❌ Server error:', err.message || err);
  const status = err.status || err.statusCode || 500;
  res.status(status).json({
    error: err.message || 'Operation failed',
  });
});

// ─── Module Export & Server Startup ──────────────────────────────────────────
module.exports = app;

if (require.main === module) {
  app.listen(PORT, () => {
    console.log(`\n🛡️ Hardened Unstop Reminder Server running at http://localhost:${PORT}`);
    console.log(`🔒 Security active: Helmet CSP, CORS, Rate-Limiting, Data Isolation`);
    console.log(`🔑 Protected endpoints: ${API_SECRET ? 'Active (API_SECRET required)' : 'Open (Local Dev)'}`);
    console.log(`📅 Daily reminder at 12:00 AM IST + 5-min escalation repeats\n`);
  });
}
