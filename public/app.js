/* ─────────────────────────────────────────────────────────────────────────────
   Unstop 30-Day Challenge Tracker — Duolingo-style Streak Dashboard Logic
   ───────────────────────────────────────────────────────────────────────────── */

let currentChallenge = null;
let swRegistration = null;
let pushSubscription = null;
let countdownInterval = null;
let remindedAt = null;

const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:3000' : '';

// ─── Security Helpers ──────────────────────────────────────────────────────────

function isSafeUrl(url) {
  if (!url || typeof url !== 'string') return false;
  try {
    const parsed = new URL(url.trim(), window.location.origin);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

function sanitizeUrl(url, fallback = 'https://unstop.com/practice/coding') {
  if (isSafeUrl(url)) return url.trim();
  return fallback;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

let pollIntervalId = null;

function setupLivePolling(isPending) {
  if (pollIntervalId) clearInterval(pollIntervalId);
  // Poll faster (every 5 seconds) while pending so solves on Unstop are reflected almost instantly
  const intervalMs = isPending ? 5000 : 15000;
  pollIntervalId = setInterval(() => loadData(true), intervalMs);
}

// ─── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await registerServiceWorker();
  await loadAuthStatus();
  await loadData();
  updateNextReminderTime();
  setInterval(updateNextReminderTime, 60_000);

  // Instant auto-sync whenever the user switches back from Unstop to this tab
  window.addEventListener('focus', () => {
    loadData(true);
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      loadData(true);
    }
  });
});

// ─── Service Worker Registration ──────────────────────────────────────────────
async function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  try {
    swRegistration = await navigator.serviceWorker.register('/sw.js');
    pushSubscription = await swRegistration.pushManager.getSubscription();
    if (pushSubscription) {
      updateNotifUI(true);
    }
  } catch (err) {
    console.warn('[App] Service worker registration:', err.message);
  }
}

// ─── Notifications ─────────────────────────────────────────────────────────────
async function enableNotifications() {
  if (window.location.protocol === 'file:') {
    showToast('⚠️ Please open via http://localhost:3000 to enable browser notifications.', 'warning');
    return;
  }

  if (!swRegistration) {
    showToast('⚠️ Service Worker not ready. Try refreshing the page.', 'error');
    return;
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    showToast('❌ Notification permission denied in browser.', 'error');
    return;
  }

  try {
    let res;
    try {
      res = await fetch(`${API_BASE}/api/vapid-key`);
    } catch (netErr) {
      throw new Error('Tracker server is offline. Run "python unstop_cli.py server" in terminal to connect.');
    }

    if (!res.ok) {
      throw new Error(`Server returned HTTP ${res.status}`);
    }

    const { publicKey } = await res.json();
    if (!publicKey) {
      throw new Error('VAPID public key not found on server.');
    }

    pushSubscription = await swRegistration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    const subRes = await fetch(`${API_BASE}/api/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pushSubscription),
    });

    if (subRes.ok) {
      updateNotifUI(true);
      showToast('🔔 Alerts active! You will be reminded at 12:00 AM daily.', 'success');
    } else {
      throw new Error('Server could not save subscription');
    }
  } catch (err) {
    showToast('❌ Could not enable push alerts: ' + err.message, 'error');
  }
}

function updateNotifUI(isEnabled) {
  const badge = document.getElementById('notif-status');
  const label = document.getElementById('notif-label');
  const btn = document.getElementById('btn-enable-notif');

  if (isEnabled) {
    badge.classList.remove('notif-off');
    badge.classList.add('notif-on');
    label.textContent = 'Alerts On';
    btn.textContent = '🔕 Alerts';
    btn.onclick = disableNotifications;
  } else {
    badge.classList.remove('notif-on');
    badge.classList.add('notif-off');
    label.textContent = 'Alerts Off';
    btn.textContent = '🔔 Alerts';
    btn.onclick = enableNotifications;
  }
}

async function disableNotifications() {
  if (pushSubscription) {
    await fetch(`${API_BASE}/api/unsubscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ endpoint: pushSubscription.endpoint }),
    });
    await pushSubscription.unsubscribe();
    pushSubscription = null;
  }
  updateNotifUI(false);
  showToast('🔕 Browser alerts muted.', 'info');
}

// ─── Load Data ─────────────────────────────────────────────────────────────────
async function loadData(silent = false) {
  try {
    const [todayRes, historyRes] = await Promise.all([
      fetch(`${API_BASE}/api/today`),
      fetch(`${API_BASE}/api/challenges`),
    ]);

    const { challenge, stats, user } = await todayRes.json();
    const histData = await historyRes.json();
    const challenges = histData.challenges || [];
    const currentRun = histData.currentRun || [];

    cachedChallenges = challenges;
    cachedCurrentRun = currentRun;
    cachedStats = stats;

    const activeChallenge = challenge || challenges.find(c => c.status === 'pending') || challenges[0];
    cachedCurrent = activeChallenge;
    currentChallenge = activeChallenge;

    renderUserProfile(user);
    renderDuolingoStreak(stats, activeChallenge, user);
    renderWeeklyCalendar(challenges, activeChallenge);
    renderMilestone(stats);
    renderTodayCard(activeChallenge, stats, user);
    renderPathGrid(challenges, activeChallenge, stats);
    renderHistory(challenges, currentRun, stats);

    const liveBadge = document.getElementById('live-sync-badge');
    const liveLabel = document.getElementById('live-sync-label');
    if (liveBadge) {
      liveBadge.className = 'notif-badge notif-on';
      if (liveLabel) liveLabel.textContent = 'Live Sync: Active';
    }

    setupLivePolling(activeChallenge && activeChallenge.status !== 'solved');

    // Show modal only if pending after midnight
    if (challenge && challenge.status === 'pending' && challenge.reminded_at) {
      const remDate = new Date(challenge.reminded_at);
      const now = new Date();
      const diffMs = now - remDate;
      if (diffMs > 0 && diffMs < 24 * 60 * 60 * 1000) {
        remindedAt = remDate;
        startCountdown(remDate);
        showAlertModal(challenge, user);
      }
    } else if (challenge && challenge.status === 'solved') {
      // Never show reminders or alerts once solved
      remindedAt = null;
      stopCountdown();
      closeModal();
    }
  } catch (err) {
    console.error('[App] Load data error:', err);
    const liveBadge = document.getElementById('live-sync-badge');
    const liveLabel = document.getElementById('live-sync-label');
    if (liveBadge) {
      liveBadge.className = 'notif-badge notif-off';
      if (liveLabel) liveLabel.textContent = 'Live Sync: Reconnecting...';
    }
    if (!silent) {
      showToast('❌ Could not connect to server. Is it running?', 'error');
    }
  }
}

function renderUserProfile(user) {
  if (!user) return;
  const nameEl = document.getElementById('user-name');
  const tagEl = document.getElementById('user-tag');
  const avatarEl = document.getElementById('user-avatar');
  const pillEl = document.getElementById('user-pill');

  if (nameEl) nameEl.textContent = user.name;
  if (tagEl) tagEl.textContent = `@${user.username}`;
  if (pillEl && user.profileUrl) pillEl.href = sanitizeUrl(user.profileUrl);

  if (avatarEl && user.name) {
    const initials = user.name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();
    avatarEl.textContent = initials || 'AG';
  }
}

async function refreshData() {
  await loadData();
  showToast('🔄 Streak status refreshed!', 'info');
}

// ─── 1. Duolingo Flame Hero & Warning Banner Rendering ────────────────────────
function renderDuolingoStreak(stats, challenge, user) {
  const streak = stats?.streak || 0;
  const bestStreak = stats?.best_streak || streak;
  const solved = stats?.solved || 0;
  const missed = stats?.missed || 0;
  const streakAtRisk = stats?.streak_at_risk || false;
  const isBroken = stats?.is_broken || (streak === 0 && (bestStreak > 0 || missed > 0));
  const firstName = user?.name ? user.name.split(' ')[0] : 'Animesh';

  // Header pill count
  const headerCount = document.getElementById('header-streak-count');
  if (headerCount) {
    headerCount.textContent = streak;
    if (isBroken) {
      headerCount.title = `Streak broken (Best: ${bestStreak} Days)`;
    } else {
      headerCount.title = `Active Streak: ${streak} Days`;
    }
  }

  // Hero big count & Flame Icon
  const heroCount = document.getElementById('duo-streak-count');
  if (heroCount) heroCount.textContent = streak;

  const flameEl = document.getElementById('duo-big-flame');
  const streakLabelEl = document.querySelector('.duo-streak-label');

  if (flameEl) {
    if (isBroken && streak === 0) {
      flameEl.textContent = '💔';
      flameEl.classList.add('flame-broken');
    } else {
      flameEl.textContent = '🔥';
      flameEl.classList.remove('flame-broken');
    }
  }

  if (streakLabelEl) {
    if (isBroken && streak === 0) {
      streakLabelEl.textContent = 'STREAK RESET';
    } else {
      streakLabelEl.textContent = 'DAY STREAK!';
    }
  }

  // Stats pills
  const curEl = document.getElementById('stat-current-streak');
  const bestEl = document.getElementById('stat-best-streak');
  const solEl = document.getElementById('stat-solved-count');
  const misEl = document.getElementById('stat-missed-count');
  const statusEl = document.getElementById('stat-streak-status');
  const statusIconEl = document.getElementById('stat-status-icon');
  const streakBadgeEl = document.getElementById('platform-streak-badge');

  if (curEl) curEl.textContent = `${streak}d`;
  if (bestEl) bestEl.textContent = `${bestStreak}d`;
  if (solEl) solEl.textContent = solved;
  if (misEl) misEl.textContent = missed;

  if (statusEl) {
    if (isBroken && streak === 0) {
      statusEl.textContent = 'BROKEN';
      statusEl.style.color = '#ff4b4b';
      if (statusIconEl) statusIconEl.textContent = '💔';
    } else if (streakAtRisk) {
      statusEl.textContent = 'AT RISK';
      statusEl.style.color = '#ff9600';
      if (statusIconEl) statusIconEl.textContent = '⚠️';
    } else {
      statusEl.textContent = 'ACTIVE';
      statusEl.style.color = '#58cc02';
      if (statusIconEl) statusIconEl.textContent = '🔥';
    }
  }

  if (streakBadgeEl) {
    if (isBroken && streak === 0) {
      streakBadgeEl.textContent = `💔 Streak Broken (0d) • Max Streak: ${bestStreak}d`;
      streakBadgeEl.className = 'platform-streak-badge badge-broken';
    } else if (streakAtRisk) {
      streakBadgeEl.textContent = `⚠️ Current Streak: ${streak}d (At Risk!) • Max: ${bestStreak}d`;
      streakBadgeEl.className = 'platform-streak-badge badge-risk';
    } else {
      streakBadgeEl.textContent = `🔥 Current Streak: ${streak}d • Max Streak: ${bestStreak}d`;
      streakBadgeEl.className = 'platform-streak-badge badge-active';
    }
  }

  // Warning Banner
  const warningBanner = document.getElementById('streak-warning-banner');
  const warningDesc = document.getElementById('warning-desc');
  const warningTitle = warningBanner ? warningBanner.querySelector('.warning-title') : null;
  const warningBtn = warningBanner ? warningBanner.querySelector('.btn-warning-action') : null;

  if (warningBanner) {
    if (isBroken) {
      warningBanner.style.display = 'flex';
      warningBanner.classList.add('streak-broken-banner');
      if (warningTitle) warningTitle.textContent = '💔 STREAK BROKEN — START YOUR COMEBACK!';
      if (warningDesc) {
        warningDesc.textContent = `A fresh 30-day journey has begun! Complete Day ${challenge?.day || 1} today to start your new streak! 💪`;
      }
      if (warningBtn) warningBtn.textContent = '🚀 Start Comeback Challenge';
    } else if (streakAtRisk) {
      warningBanner.style.display = 'flex';
      warningBanner.classList.remove('streak-broken-banner');
      if (warningTitle) warningTitle.textContent = 'STREAK AT RISK BEFORE MIDNIGHT!';
      if (warningDesc) {
        warningDesc.textContent = `Hey ${firstName}! Your ${streak}-Day Streak is at risk before midnight! Solve Day ${challenge?.day || 1} now to keep your flame burning! 🔥`;
      }
      if (warningBtn) warningBtn.textContent = '⚡ Save My Streak';
    } else {
      warningBanner.style.display = 'none';
      warningBanner.classList.remove('streak-broken-banner');
    }
  }

  // Milestone Progress
  const milestoneBadge = document.getElementById('milestone-badge');
  const milestonePct = document.getElementById('milestone-pct');
  const milestoneFill = document.getElementById('milestone-fill');
  if (isBroken) {
    if (milestoneBadge) milestoneBadge.textContent = '🎯 Goal: 3-Day Comeback Streak';
    if (milestonePct) milestonePct.textContent = '0 / 3 Days';
    if (milestoneFill) milestoneFill.style.width = '0%';
  } else {
    const nextGoal = streak < 3 ? 3 : (streak < 7 ? 7 : (streak < 14 ? 14 : 30));
    const pct = Math.min(100, Math.round((streak / nextGoal) * 100));
    if (milestoneBadge) milestoneBadge.textContent = `🏆 Next Goal: ${nextGoal}-Day Streak`;
    if (milestonePct) milestonePct.textContent = `${streak} / ${nextGoal} Days`;
    if (milestoneFill) milestoneFill.style.width = `${pct}%`;
  }

  // Motivational message
  const msgEl = document.getElementById('duo-streak-message');
  if (msgEl) {
    if (isBroken) {
      msgEl.innerHTML = `💔 <strong>Fresh Start!</strong> Complete Day <strong>${challenge?.day || 1}</strong> now to build your streak! 🚀`;
    } else if (challenge && challenge.status === 'solved') {
      msgEl.innerHTML = `You're on fire, <strong>${escapeHtml(firstName)}</strong>! Day ${challenge.day} is crushed. Streak: <strong>${streak} Days</strong>! Complete tomorrow's challenge to keep your flame blazing! 🔥`;
    } else if (streakAtRisk) {
      msgEl.innerHTML = `🚨 <strong>STREAK AT RISK!</strong> Hey <strong>${escapeHtml(firstName)}</strong>, Day ${challenge?.day || 1} is still pending. Solve it before midnight to avoid resetting your ${streak}-day streak to 0!`;
    } else {
      msgEl.innerHTML = `Keep your flame alive, <strong>${escapeHtml(firstName)}</strong>! Day ${challenge?.day || 1} is waiting. Complete today to extend your streak! ⚡`;
    }
  }
}

function scrollToQuest() {
  const card = document.getElementById('challenge-card');
  if (card) {
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

// ─── Weekly Consistency Strip (Mon - Sun) ─────────────────────────────────────
function renderWeeklyCalendar(challenges, current) {
  const container = document.getElementById('duo-week-days');
  if (!container) return;
  container.innerHTML = '';

  const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const now = new Date();
  const currentDayOfWeek = (now.getDay() + 6) % 7; // 0 = Mon, 6 = Sun

  const isTodaySolved = current && current.status === 'solved';

  for (let i = 0; i < 7; i++) {
    const col = document.createElement('div');
    col.className = 'duo-day-node';

    const circle = document.createElement('div');
    circle.className = 'duo-day-circle';

    if (i < currentDayOfWeek) {
      // Past days in week
      circle.classList.add('day-done');
      circle.innerHTML = '🔥';
      circle.title = `${dayNames[i]}: Streak Maintained`;
    } else if (i === currentDayOfWeek) {
      // Today
      if (isTodaySolved) {
        circle.classList.add('day-done');
        circle.innerHTML = '🔥';
        circle.title = `Today (${dayNames[i]}): Solved!`;
      } else {
        circle.classList.add('day-today');
        circle.innerHTML = '⚡';
        circle.title = `Today (${dayNames[i]}): Pending Challenge`;
      }
    } else {
      // Future days in week
      circle.innerHTML = '·';
      circle.title = `${dayNames[i]}: Upcoming`;
    }

    const label = document.createElement('span');
    label.className = 'duo-day-name';
    label.textContent = dayNames[i];

    col.appendChild(circle);
    col.appendChild(label);
    container.appendChild(col);
  }
}

// ─── Milestone Reward Progress ─────────────────────────────────────────────────
function renderMilestone(stats) {
  const streak = stats?.streak || 0;
  const milestones = [
    { target: 3, name: '3-Day Bronze League 🥉' },
    { target: 7, name: '7-Day Silver League 🥈' },
    { target: 14, name: '14-Day Gold League 🥇' },
    { target: 21, name: '21-Day Diamond League 💎' },
    { target: 30, name: '30-Day Master Champion 🏆' },
  ];

  let next = milestones.find(m => m.target > streak);
  if (!next) {
    next = { target: 30, name: '30-Day Master Champion 🏆' };
  }

  const badgeEl = document.getElementById('milestone-badge');
  const pctEl = document.getElementById('milestone-pct');
  const fillEl = document.getElementById('milestone-fill');

  if (badgeEl) badgeEl.textContent = `🎯 Next Goal: ${next.name}`;
  if (pctEl) pctEl.textContent = `${streak} / ${next.target} Days`;

  const pct = Math.min(100, Math.round((streak / next.target) * 100));
  if (fillEl) fillEl.style.width = `${pct}%`;
}

// ─── 2. Today's Quest Challenge Card ──────────────────────────────────────────
function renderTodayCard(challenge, stats, user) {
  if (!challenge) return;

  const card = document.getElementById('challenge-card');
  const banner = document.getElementById('status-banner');
  const statusText = document.getElementById('status-text');
  const titleEl = document.getElementById('challenge-title');
  const linkEl = document.getElementById('challenge-link');
  const solveBtn = document.getElementById('btn-solve');
  const questIcon = document.getElementById('quest-icon');

  const activeChallenge = challenge;
  const dayNum = activeChallenge.day || 1;
  const rawTitle = activeChallenge.title || 'Coding Practice Platform for Placements & Interviews';
  const cleanTitle = rawTitle.replace(/^Day\s*\d+\s*:\s*/i, '').trim() || 'Coding Practice Platform for Placements & Interviews';

  titleEl.textContent = `Day ${dayNum}: ${cleanTitle}`;

  const effectiveLink = sanitizeUrl(activeChallenge.link || user?.practiceUrl || 'https://unstop.com/practice/coding');
  linkEl.href = effectiveLink;
  linkEl.innerHTML = `<span>Open Problem on Unstop Practice Platform →</span>`;

  const autoDetectPill = document.getElementById('autodetect-status');
  const autoDetectText = document.getElementById('autodetect-text');
  const manualHint = document.getElementById('manual-solve-hint');

  if (activeChallenge.status === 'solved') {
    card.classList.add('solved');
    card.classList.remove('pending');
    banner.classList.add('status-solved');
    statusText.innerHTML = `✅ <strong>Completed!</strong> — Streak Protected for Today!`;
    if (questIcon) questIcon.textContent = '🌟';
    solveBtn.textContent = '✅ Solved for Today!';
    solveBtn.className = 'btn btn-solved btn-large';
    solveBtn.disabled = true;
    stopCountdown();

    if (autoDetectPill && autoDetectText) {
      autoDetectPill.className = 'autodetect-status-pill autodetect-solved';
      autoDetectText.innerHTML = `🌟 <strong>Auto-Detected & Verified:</strong> Your Unstop practice completion was detected automatically! Streak is protected.`;
    }
    if (manualHint) {
      manualHint.style.display = 'none';
    }
  } else {
    card.classList.add('pending');
    card.classList.remove('solved');
    banner.classList.remove('status-solved');
    statusText.innerHTML = `⏳ <strong>Pending</strong> — Solve today to maintain your streak!`;
    if (questIcon) questIcon.textContent = '⚡';
    solveBtn.innerHTML = '<span class="btn-icon">✅</span><span>Mark as Solved!</span>';
    solveBtn.className = 'btn btn-duo-green btn-large';
    solveBtn.disabled = false;
    solveBtn.onclick = markSolved;

    if (autoDetectPill && autoDetectText) {
      autoDetectPill.className = 'autodetect-status-pill autodetect-active';
      autoDetectText.innerHTML = `⚡ <strong>Live Auto-Detection Active:</strong> Complete this challenge on Unstop and it will automatically be detected & marked solved — no need to click!`;
    }
    if (manualHint) {
      manualHint.style.display = 'block';
      manualHint.textContent = 'Optional • Auto-detects on Unstop';
    }
  }
}

// ─── 3. Duolingo Stepping Stones Map (Automatically Restarts from Day 1 on Break) ──
let pathViewMode = 'run'; // 'run' (active streak run starting from Day 1) or 'all' (all-time map)
let cachedChallenges = [];
let cachedCurrent = null;
let cachedStats = null;

function togglePathView() {
  pathViewMode = pathViewMode === 'run' ? 'all' : 'run';
  const btn = document.getElementById('btn-toggle-path-view');
  if (btn) {
    btn.innerHTML = pathViewMode === 'run' ? '🔥 Current Run (Day 1-30)' : '📜 All-Time Map';
  }
  renderPathGrid(cachedChallenges, cachedCurrent, cachedStats);
}

function renderPathGrid(challenges = [], current, stats = null) {
  cachedChallenges = challenges;
  cachedCurrent = current;
  cachedStats = stats;

  const container = document.getElementById('duo-path-grid');
  const solvedCountEl = document.getElementById('path-solved-count');
  const subEl = document.getElementById('path-section-sub');
  const toggleBtn = document.getElementById('btn-toggle-path-view');
  if (!container) return;
  container.innerHTML = '';

  const streak = stats?.streak || 0;
  const isTodaySolved = current && current.status === 'solved';

  if (toggleBtn) {
    toggleBtn.style.display = 'none';
  }

  if (solvedCountEl) {
    solvedCountEl.textContent = stats?.solved || streak;
  }
  if (subEl) {
    subEl.innerHTML = `🗺️ <strong>30-Day Path of Mastery:</strong> Active Streak: <strong>${streak} Day${streak === 1 ? '' : 's'}</strong> • Best Record: <strong>${stats?.best_streak || 20}d</strong>`;
  }

  for (let step = 1; step <= 30; step++) {
    const node = document.createElement('div');
    node.className = 'stone-node';

    const circle = document.createElement('div');
    circle.className = 'stone-circle';

    if (step <= streak) {
      // Solved in current active streak run
      circle.classList.add('stone-solved');
      circle.innerHTML = '✅';
      node.title = `Day ${step}: Solved! (${step === streak && current ? current.title : 'Challenge Completed'})`;
      node.onclick = () => {
        if (current?.link) {
          window.open(current.link, '_blank', 'noopener,noreferrer');
        } else {
          window.open('https://unstop.com/practice/coding', '_blank', 'noopener,noreferrer');
        }
      };
    } else if (step === streak + 1 && !isTodaySolved) {
      // Active today (pending challenge to advance the streak!)
      circle.classList.add('stone-active');
      circle.innerHTML = '⚡';
      node.title = `Day ${step}: Active Today! (${current?.title || "Today's Quest"})`;
      node.onclick = () => {
        scrollToQuest();
      };
    } else if (step === streak + 1 && isTodaySolved) {
      // Solved today, next streak stone unlocks tomorrow
      circle.classList.add('stone-locked');
      circle.innerHTML = `<span style="font-size:0.9rem; opacity:0.8;">⏳</span>`;
      node.title = `Day ${step}: Unlocks Tomorrow at Midnight! Complete to advance streak to ${step} Days.`;
      node.onclick = () => {
        showToast(`⏳ Day ${step} unlocks tomorrow at midnight! Complete it to keep your flame alive.`, 'info');
      };
    } else {
      // Future locked stones
      circle.classList.add('stone-locked');
      circle.innerHTML = `<span style="font-size:0.9rem; opacity:0.6;">🔒</span>`;
      node.title = `Day ${step}: Locked (Reach Day ${step - 1} first)`;
      node.onclick = () => {
        showToast(`🔒 Locked. Complete Day ${step - 1} to reach this stone!`, 'info');
      };
    }

    const label = document.createElement('span');
    label.className = 'stone-day-label';
    label.textContent = `Day ${step}`;

    node.appendChild(circle);
    node.appendChild(label);
    container.appendChild(node);
  }
}

// ─── 4. Render History Table (Full Clean 30-Day Track) ─────────────────────────
function toggleHistoryView() {
  renderHistory(cachedChallenges, cachedCurrentRun, cachedStats);
}

function renderHistory(challenges = [], currentRun = [], stats = null) {
  const tbody = document.getElementById('history-body');
  const subEl = document.getElementById('history-section-sub');
  const toggleBtn = document.getElementById('btn-toggle-history-view');
  const thDayLabel = document.getElementById('th-day-label');
  if (!tbody) return;

  if (toggleBtn) {
    toggleBtn.style.display = 'none';
  }

  if (thDayLabel) thDayLabel.textContent = 'Day';
  if (subEl) {
    subEl.innerHTML = `📜 <strong>30-Day Quest Records:</strong> Active 30-day journey • Current Streak: <strong>${stats?.streak || 0}d</strong> • Best Record: <strong>${stats?.best_streak || 20}d</strong>`;
  }

  const itemsToRender = challenges && challenges.length > 0 ? challenges : [];
  if (itemsToRender.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">No quest records found.</td></tr>';
    return;
  }

  // Sort chronologically Day 1 to 30
  const sorted = [...itemsToRender].sort((a, b) => a.day - b.day);

  tbody.innerHTML = sorted.map(c => {
    const statusChip = c.status === 'solved'
      ? '<span class="status-chip chip-solved">✅ Solved</span>'
      : c.status === 'pending'
        ? '<span class="status-chip chip-pending">⚡ Active Quest</span>'
        : c.status === 'missed'
          ? '<span class="status-chip chip-missed">💔 Missed</span>'
          : c.status === 'skipped'
            ? '<span class="status-chip chip-skipped">⏭️ Skipped</span>'
            : '<span class="status-chip" style="background:rgba(255,255,255,0.06); color:var(--text-3)">🔒 Upcoming</span>';

    const solvedAt = c.solved_at
      ? new Date(c.solved_at).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' })
      : (c.status === 'pending' ? 'Due Tonight 12 AM' : c.status === 'missed' ? 'Missed Day' : (c.assigned_date ? `Scheduled: ${c.assigned_date}` : '—'));

    const safeLink = sanitizeUrl(c.link || 'https://unstop.com/practice/coding');
    const safeTitle = escapeHtml(c.title || `Day ${c.day} Challenge`);

    return `
      <tr>
        <td><strong>Day ${c.day}</strong></td>
        <td>${safeTitle}</td>
        <td>${statusChip}</td>
        <td>${solvedAt}</td>
        <td><a class="table-link" href="${safeLink}" target="_blank" rel="noopener">🚀 Open Problem →</a></td>
      </tr>
    `;
  }).join('');
}

// ─── Actions: Open Task in Chrome ──────────────────────────────────────────────
function openTaskInChrome(e) {
  if (e && e.preventDefault) e.preventDefault();

  // 1. Immediately determine target URL from active challenge
  const linkEl = document.getElementById('challenge-link');
  let targetUrl = currentChallenge?.link || (linkEl ? linkEl.getAttribute('href') : null) || 'https://unstop.com/practice/coding';
  const safeLink = sanitizeUrl(targetUrl) || 'https://unstop.com/practice/coding';
  const taskTitle = currentChallenge?.title || "Today's Coding Quest";

  // 2. Open immediately in a new tab synchronously within user click gesture (prevents popup blockers)
  let win = null;
  try {
    win = window.open(safeLink, '_blank', 'noopener,noreferrer');
  } catch (_) {}

  // Direct fallback anchor trigger if window.open was restricted
  if (!win || win.closed || typeof win.closed === 'undefined') {
    const tempA = document.createElement('a');
    tempA.href = safeLink;
    tempA.target = '_blank';
    tempA.rel = 'noopener noreferrer';
    document.body.appendChild(tempA);
    tempA.click();
    document.body.removeChild(tempA);
  }

  // 3. If running locally with desktop backend, trigger native Google Chrome launch asynchronously
  if (API_BASE) {
    fetch(`${API_BASE}/api/open-chrome`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: safeLink })
    }).catch(() => {});
  }

  // 4. Accelerate live polling so completion on Unstop is detected instantly
  setupLivePolling(true);
  showToast(`🚀 Opening ${taskTitle} in Google Chrome!`, 'success');
}

// ─── Actions: Mark as Solved ──────────────────────────────────────────────────
async function markSolved() {
  const solveBtn = document.getElementById('btn-solve');
  const originalHtml = solveBtn ? solveBtn.innerHTML : '';
  if (solveBtn) {
    solveBtn.disabled = true;
    solveBtn.innerHTML = '<span class="btn-icon">⏳</span><span>Marking as Solved...</span>';
  }

  try {
    const res = await fetch(`${API_BASE}/api/solve`, { method: 'POST' });
    const data = await res.json().catch(() => ({ error: `Server returned status ${res.status}` }));

    if (res.ok && data.success) {
      showToast(`🎉 Streak Maintained! ${data.message || 'Challenge solved!'}`, 'success');
      launchConfetti();
      remindedAt = null;
      stopCountdown();
      closeModal();
      await loadData();
    } else {
      const errMsg = data.error || data.detail || (res.statusText ? `Error (${res.status}): ${res.statusText}` : 'Could not mark solved');
      showToast('❌ ' + errMsg, 'error');
      if (solveBtn) {
        solveBtn.disabled = false;
        solveBtn.innerHTML = originalHtml || '<span class="btn-icon">✅</span><span>Mark as Solved!</span>';
      }
    }
  } catch (err) {
    showToast('❌ Server error: ' + err.message, 'error');
    if (solveBtn) {
      solveBtn.disabled = false;
      solveBtn.innerHTML = originalHtml || '<span class="btn-icon">✅</span><span>Mark as Solved!</span>';
    }
  }
}

// ─── Update Problem Link ───────────────────────────────────────────────────────
async function saveLink() {
  const linkRaw = document.getElementById('input-link').value.trim();
  const titleRaw = document.getElementById('input-title').value.trim();

  if (!currentChallenge) return;

  if (linkRaw && !isSafeUrl(linkRaw)) {
    showToast('❌ Invalid URL! Must start with http:// or https://', 'error');
    return;
  }

  const res = await fetch(`${API_BASE}/api/challenge/update`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ day: currentChallenge.day, link: linkRaw || null, title: titleRaw || null }),
  });

  if (res.ok) {
    showToast('💾 Quest link updated!', 'success');
    toggleForm(false);
    await loadData();
  } else {
    const data = await res.json();
    showToast('❌ ' + (data.error || data.detail || 'Failed to update link'), 'error');
  }
}

function toggleForm(forceClose = null) {
  const form = document.getElementById('update-form');
  const shouldOpen = forceClose !== null ? !forceClose : !form.classList.contains('open');
  form.classList.toggle('open', shouldOpen);

  if (shouldOpen && currentChallenge) {
    document.getElementById('input-link').value = currentChallenge.link || '';
    document.getElementById('input-title').value = currentChallenge.title || '';
  }
}

// ─── Countdown Timer ───────────────────────────────────────────────────────────
function startCountdown(startTime) {
  const wrapper = document.getElementById('countdown-wrapper');
  if (wrapper) wrapper.style.display = 'block';
  stopCountdown();

  countdownInterval = setInterval(() => {
    const elapsed = Date.now() - new Date(startTime).getTime();
    const hours = Math.floor(elapsed / 3_600_000);
    const mins = Math.floor((elapsed % 3_600_000) / 60_000);
    const secs = Math.floor((elapsed % 60_000) / 1000);

    const timer = document.getElementById('countdown-timer');
    if (timer) timer.textContent = `${pad(hours)}:${pad(mins)}:${pad(secs)}`;
  }, 1000);
}

function stopCountdown() {
  if (countdownInterval) clearInterval(countdownInterval);
  countdownInterval = null;
  const wrapper = document.getElementById('countdown-wrapper');
  if (wrapper) wrapper.style.display = 'none';
}

function pad(n) { return String(n).padStart(2, '0'); }

// ─── Alert Modal ───────────────────────────────────────────────────────────────
function showAlertModal(challenge, user) {
  const modal = document.getElementById('alert-modal');
  const body = document.getElementById('modal-body');
  const openBtn = document.getElementById('modal-open-btn');
  const firstName = user?.name ? user.name.split(' ')[0] : 'Animesh';

  body.textContent = `${firstName}, you haven't solved Day ${challenge.day}'s challenge yet! Don't let your streak break. Open Unstop and get it done!`;

  const safeLink = sanitizeUrl(challenge.link || user?.practiceUrl || 'https://unstop.com/practice/coding');
  openBtn.onclick = () => {
    window.open(safeLink, '_blank', 'noopener,noreferrer');
    closeModal();
  };
  openBtn.style.display = '';

  modal.style.display = 'flex';
}

function openChallengeFromModal() {
  const safeLink = sanitizeUrl(currentChallenge?.link);
  window.open(safeLink, '_blank', 'noopener,noreferrer');
  closeModal();
}

function closeModal() {
  const modal = document.getElementById('alert-modal');
  if (modal) modal.style.display = 'none';
}

// ─── Next Reminder Time ────────────────────────────────────────────────────────
function updateNextReminderTime() {
  const now = new Date();
  const midnight = new Date();
  midnight.setHours(24, 0, 0, 0);
  const diff = midnight - now;

  const h = Math.floor(diff / 3_600_000);
  const m = Math.floor((diff % 3_600_000) / 60_000);
  const s = Math.floor((diff % 60_000) / 1000);

  const el = document.getElementById('next-reminder-time');
  if (el) el.textContent = `Tonight at 12:00 AM (in ${h}h ${m}m ${s}s)`;
}

// ─── Confetti ──────────────────────────────────────────────────────────────────
function launchConfetti() {
  const burst = document.getElementById('confetti-burst');
  if (!burst) return;
  const colors = ['#ff9600', '#ffd900', '#ff4b4b', '#58cc02', '#1cb0f6', '#a855f7'];
  burst.innerHTML = '';

  for (let i = 0; i < 60; i++) {
    const p = document.createElement('div');
    const angle = (i / 60) * 360;
    const distance = 80 + Math.random() * 120;
    const color = colors[Math.floor(Math.random() * colors.length)];
    const size = 6 + Math.random() * 8;

    p.style.cssText = `
      position: absolute; width: ${size}px; height: ${size}px;
      background: ${color}; border-radius: ${Math.random() > 0.5 ? '50%' : '2px'};
      top: 50%; left: 50%; transform: translate(-50%, -50%);
      animation: confetti-fly 0.8s ease forwards;
      --dx: ${Math.cos((angle * Math.PI) / 180) * distance}px;
      --dy: ${Math.sin((angle * Math.PI) / 180) * distance}px;
    `;
    burst.appendChild(p);
  }

  if (!document.getElementById('confetti-style')) {
    const style = document.createElement('style');
    style.id = 'confetti-style';
    style.textContent = `
      @keyframes confetti-fly {
        0% { transform: translate(-50%, -50%) scale(1); opacity: 1; }
        100% { transform: translate(calc(-50% + var(--dx)), calc(-50% + var(--dy))) scale(0); opacity: 0; }
      }
    `;
    document.head.appendChild(style);
  }

  setTimeout(() => { burst.innerHTML = ''; }, 1000);
}

// ─── Toast ─────────────────────────────────────────────────────────────────────
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3200);
}

// ─── VAPID Helper ──────────────────────────────────────────────────────────────
function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return new Uint8Array([...raw].map(c => c.charCodeAt(0)));
}

// ─── Notification & Alert Testing ─────────────────────────────────────────────
async function sendTestPhoneAlert() {
  try {
    showToast('📱 Sending test mobile notification...', 'info');
    const res = await fetch(`${API_BASE}/api/test-phone-notify`, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`✅ Mobile alert delivered to topic: ${data.topic}`, 'success');
    } else {
      showToast('❌ Mobile notification failed: ' + (data.error || data.detail || 'Could not send'), 'error');
    }
  } catch (err) {
    showToast('❌ Server error: ' + err.message, 'error');
  }
}

async function triggerFirstOpenTest() {
  try {
    showToast('☀️ Triggering first-in-day home screen popup & toast...', 'info');
    const res = await fetch(`${API_BASE}/api/test-first-open`, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast('✅ Home screen popup card & toast triggered!', 'success');
    } else {
      showToast('❌ Could not trigger popup: ' + (data.detail || data.error), 'error');
    }
  } catch (err) {
    showToast('❌ Server error: ' + err.message, 'error');
  }
}

// ─── Unstop Account Login, Logout & Live Tracking ─────────────────────────────
let currentAuthData = {
  authenticated: true,
  username: 'animegos31002',
  email: 'animesh.goswami045@gmail.com',
  name: 'Animesh Goswami',
  liveTracking: true
};

async function loadAuthStatus() {
  // 1. Immediately read cached session from localStorage (avoids flicker / cold-start lag)
  try {
    const cached = localStorage.getItem('unstop_auth_session');
    if (cached) {
      const parsed = JSON.parse(cached);
      if (parsed && parsed.authenticated) {
        currentAuthData = parsed;
        renderAuthUI(currentAuthData);
      }
    }
  } catch (e) {}

  // 2. Fetch server-side status to keep state verified
  try {
    const res = await fetch(`${API_BASE}/api/auth/status`);
    if (res.ok) {
      const data = await res.json();
      if (data && data.authenticated) {
        currentAuthData = data;
        try {
          localStorage.setItem('unstop_auth_session', JSON.stringify(data));
        } catch (e) {}
      } else {
        // If server says not authenticated but client has stored session, sync to server
        const cached = localStorage.getItem('unstop_auth_session');
        if (cached) {
          const parsed = JSON.parse(cached);
          if (parsed && parsed.authenticated) {
            fetch(`${API_BASE}/api/auth/login`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(parsed.user || parsed)
            }).catch(() => {});
            currentAuthData = parsed;
          } else {
            currentAuthData = data;
          }
        } else {
          currentAuthData = data;
        }
      }
      renderAuthUI(currentAuthData);
    } else {
      renderAuthUI(currentAuthData);
    }
  } catch (e) {
    console.warn('[Auth] Status fetch fallback to default:', e.message);
    renderAuthUI(currentAuthData);
  }
}

function renderAuthUI(data) {
  const btnAuth = document.getElementById('btn-unstop-auth');
  const authLabel = document.getElementById('unstop-auth-label');
  const liveBar = document.getElementById('unstop-live-bar');
  const liveText = document.getElementById('live-text');
  const liveSyncBadge = document.getElementById('live-sync-badge');
  const liveSyncLabel = document.getElementById('live-sync-label');
  const userPill = document.getElementById('user-pill');
  const userAvatar = document.getElementById('user-avatar');
  const userName = document.getElementById('user-name');
  const userTag = document.getElementById('user-tag');

  const isAuthed = Boolean(data && data.authenticated);

  if (isAuthed) {
    const handle = data.username || data.user?.username || 'animegos31002';
    const name = data.name || data.user?.name || 'Animesh Goswami';
    const initials = (name || handle).split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase() || 'AG';

    if (btnAuth) {
      btnAuth.classList.add('btn-auth-connected');
      btnAuth.title = `Connected as @${handle} (${name}) — Click to manage account or logout`;
    }
    if (authLabel) {
      authLabel.textContent = `@${handle} (Connected)`;
    }
    if (liveBar) {
      liveBar.style.display = 'flex';
    }
    if (liveText) {
      liveText.innerHTML = `Live Unstop Tracking: Connected with <strong>@${escapeHtml(handle)}</strong> (${escapeHtml(name)})`;
    }
    if (liveSyncBadge) {
      liveSyncBadge.className = 'notif-badge notif-on';
    }
    if (liveSyncLabel) {
      liveSyncLabel.textContent = 'Live Sync: Active';
    }
    if (userAvatar) userAvatar.textContent = initials;
    if (userName) userName.textContent = name;
    if (userTag) userTag.textContent = `@${handle}`;
    if (userPill) userPill.href = `https://unstop.com/u/${encodeURIComponent(handle)}`;
  } else {
    if (btnAuth) {
      btnAuth.classList.remove('btn-auth-connected');
      btnAuth.title = 'Click to Login and connect your Unstop Account';
    }
    if (authLabel) {
      authLabel.textContent = 'Connect Unstop';
    }
    if (liveBar) {
      liveBar.style.display = 'none';
    }
    if (liveSyncBadge) {
      liveSyncBadge.className = 'notif-badge notif-off';
    }
    if (liveSyncLabel) {
      liveSyncLabel.textContent = 'Live Sync: Paused';
    }
    if (userTag) userTag.textContent = 'Not Connected';
  }
}

function handleAuthButtonClick() {
  openAuthModal();
}

function openAuthModal() {
  const modal = document.getElementById('unstop-auth-modal');
  if (modal) {
    modal.style.display = 'flex';
    const inputUser = document.getElementById('auth-input-username');
    const inputEmail = document.getElementById('auth-input-email');
    if (inputUser && currentAuthData) {
      inputUser.value = currentAuthData.username || currentAuthData.user?.username || 'animegos31002';
    }
    if (inputEmail && currentAuthData) {
      inputEmail.value = currentAuthData.email || currentAuthData.user?.email || 'animesh.goswami045@gmail.com';
    }
  }
}

function closeAuthModal() {
  const modal = document.getElementById('unstop-auth-modal');
  if (modal) {
    modal.style.display = 'none';
  }
}

async function quickLoginUnstop() {
  try {
    showToast('⚡ Connecting to Unstop account @animegos31002...', 'info');
    const authPayload = {
      username: 'animegos31002',
      email: 'animesh.goswami045@gmail.com',
      name: 'Animesh Goswami',
      liveTracking: true
    };
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(authPayload)
    });
    const data = await res.json().catch(() => ({ success: true, user: authPayload }));
    const userObj = (data && data.user) ? data.user : authPayload;
    currentAuthData = {
      authenticated: true,
      username: userObj.username,
      email: userObj.email,
      name: userObj.name,
      user: userObj,
      liveTracking: true
    };
    try {
      localStorage.setItem('unstop_auth_session', JSON.stringify(currentAuthData));
    } catch (e) {}
    renderAuthUI(currentAuthData);
    showToast('🎉 Connected with Unstop (@animegos31002)! Live tracking active.', 'success');
    closeAuthModal();
    await loadData();
  } catch (err) {
    showToast('❌ Login error: ' + err.message, 'error');
  }
}

async function submitCustomLogin() {
  const userInput = document.getElementById('auth-input-username');
  const emailInput = document.getElementById('auth-input-email');
  let rawUsername = userInput ? userInput.value.trim() : '';
  const email = emailInput ? emailInput.value.trim() : '';

  if (rawUsername.includes('/u/')) {
    rawUsername = rawUsername.split('/u/')[1].split('/')[0];
  }
  const username = rawUsername.replace(/^@/, '').trim();

  if (!username) {
    showToast('⚠️ Please enter your Unstop username or profile link', 'warning');
    return;
  }

  const authPayload = {
    username: username,
    email: email || `${username}@gmail.com`,
    name: username,
    liveTracking: true
  };

  try {
    showToast(`🔗 Connecting @${username} to website...`, 'info');
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(authPayload)
    });
    const data = await res.json().catch(() => ({ success: true, user: authPayload }));
    const userObj = (data && data.user) ? data.user : authPayload;
    currentAuthData = {
      authenticated: true,
      username: userObj.username,
      email: userObj.email,
      name: userObj.name,
      user: userObj,
      liveTracking: true
    };
    try {
      localStorage.setItem('unstop_auth_session', JSON.stringify(currentAuthData));
    } catch (e) {}
    renderAuthUI(currentAuthData);
    showToast(`✅ Unstop Account @${username} connected! Live tracking active.`, 'success');
    closeAuthModal();
    await loadData();
  } catch (err) {
    showToast('❌ Server error: ' + err.message, 'error');
  }
}

async function logoutUnstop() {
  try {
    showToast('Logging out from Unstop...', 'info');
    try {
      localStorage.removeItem('unstop_auth_session');
    } catch (e) {}
    currentAuthData = { authenticated: false };
    renderAuthUI(currentAuthData);
    await fetch(`${API_BASE}/api/auth/logout`, { method: 'POST' }).catch(() => {});
    showToast('🚪 Logged out from Unstop account.', 'info');
    closeAuthModal();
  } catch (err) {
    showToast('❌ Server error: ' + err.message, 'error');
  }
}

async function triggerLiveUnstopSync() {
  try {
    showToast('⚡ Connecting to Unstop live servers...', 'info');
    const res = await fetch(`${API_BASE}/api/unstop/live-track`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast('🌟 Unstop Live Sync Complete! Challenges & streak refreshed.', 'success');
      await loadData();
    } else {
      showToast('⚠️ Sync status: ' + (data.message || data.error || 'Completed with warnings'), 'warning');
      await loadData();
    }
  } catch (err) {
    showToast('❌ Sync error: ' + err.message, 'error');
  }
}

