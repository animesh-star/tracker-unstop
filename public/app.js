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

// ─── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await registerServiceWorker();
  await loadData();
  updateNextReminderTime();
  setInterval(updateNextReminderTime, 60_000);
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
async function loadData() {
  try {
    const [todayRes, historyRes] = await Promise.all([
      fetch(`${API_BASE}/api/today`),
      fetch(`${API_BASE}/api/challenges`),
    ]);

    const { challenge, stats, user } = await todayRes.json();
    const { challenges } = await historyRes.json();

    currentChallenge = challenge;
    renderUserProfile(user);
    renderDuolingoStreak(stats, challenge, user);
    renderWeeklyCalendar(challenges, challenge);
    renderMilestone(stats);
    renderTodayCard(challenge, stats, user);
    renderPathGrid(challenges, challenge);
    renderHistory(challenges);

    // Show modal if pending after midnight
    if (challenge && challenge.status === 'pending' && challenge.reminded_at) {
      const remDate = new Date(challenge.reminded_at);
      const now = new Date();
      const diffMs = now - remDate;
      if (diffMs > 0 && diffMs < 24 * 60 * 60 * 1000) {
        remindedAt = remDate;
        startCountdown(remDate);
        showAlertModal(challenge, user);
      }
    }
  } catch (err) {
    console.error('[App] Load data error:', err);
    showToast('❌ Could not connect to server. Is it running?', 'error');
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

async function sendTestPhoneAlert() {
  try {
    const res = await fetch(`${API_BASE}/api/test-phone-notify`, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`📱 Mobile alert sent to topic: ${data.topic}`, 'success');
    } else {
      showToast('❌ Failed to send mobile alert', 'error');
    }
  } catch (err) {
    showToast('❌ Mobile alert error: ' + err.message, 'error');
  }
}

// ─── 1. Duolingo Flame Hero & Warning Banner Rendering ────────────────────────
function renderDuolingoStreak(stats, challenge, user) {
  const streak = stats?.streak || 0;
  const bestStreak = stats?.best_streak || streak;
  const solved = stats?.solved || 0;
  const missed = stats?.missed || 0;
  const streakAtRisk = stats?.streak_at_risk || false;
  const firstName = user?.name ? user.name.split(' ')[0] : 'Animesh';

  // Header pill count
  const headerCount = document.getElementById('header-streak-count');
  if (headerCount) headerCount.textContent = streak;

  // Hero big count
  const heroCount = document.getElementById('duo-streak-count');
  if (heroCount) heroCount.textContent = streak;

  // Stats pills
  const curEl = document.getElementById('stat-current-streak');
  const bestEl = document.getElementById('stat-best-streak');
  const solEl = document.getElementById('stat-solved-count');
  const misEl = document.getElementById('stat-missed-count');

  if (curEl) curEl.textContent = streak;
  if (bestEl) bestEl.textContent = bestStreak;
  if (solEl) solEl.textContent = solved;
  if (misEl) misEl.textContent = missed;

  // Warning Banner
  const warningBanner = document.getElementById('streak-warning-banner');
  const warningDesc = document.getElementById('warning-desc');
  if (warningBanner) {
    if (streakAtRisk) {
      warningBanner.style.display = 'flex';
      if (warningDesc) {
        warningDesc.textContent = `Hey ${firstName}! Your ${streak}-Day Streak is at risk before midnight! Solve Day ${challenge?.day || 1} now to keep your flame burning! 🔥`;
      }
    } else {
      warningBanner.style.display = 'none';
    }
  }

  // Motivational message
  const msgEl = document.getElementById('duo-streak-message');
  if (msgEl) {
    if (challenge && challenge.status === 'solved') {
      msgEl.innerHTML = `You're on fire, <strong>${escapeHtml(firstName)}</strong>! Day ${challenge.day} is crushed. Complete tomorrow's challenge to keep your flame blazing! 🔥`;
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

  titleEl.textContent = challenge.title || `Day ${challenge.day} Challenge`;

  const effectiveLink = sanitizeUrl(challenge.link || user?.practiceUrl || 'https://unstop.com/practice/coding');
  linkEl.href = effectiveLink;
  linkEl.innerHTML = `<span>Open Problem on Unstop (${challenge.link ? 'Custom Link' : 'POTD'}) →</span>`;

  if (challenge.status === 'solved') {
    card.classList.add('solved');
    card.classList.remove('pending');
    banner.classList.add('status-solved');
    statusText.innerHTML = `✅ <strong>Completed!</strong> — Streak Protected for Today!`;
    if (questIcon) questIcon.textContent = '🌟';
    solveBtn.textContent = '✅ Solved for Today!';
    solveBtn.className = 'btn btn-solved btn-large';
    solveBtn.disabled = true;
    stopCountdown();
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
  }
}

// ─── 3. Duolingo Stepping Stones Map ───────────────────────────────────────────
function renderPathGrid(challenges = [], current) {
  const container = document.getElementById('duo-path-grid');
  const solvedCountEl = document.getElementById('path-solved-count');
  if (!container) return;
  container.innerHTML = '';

  const solvedDays = new Set(challenges.filter(c => c.status === 'solved').map(c => c.day));
  const missedDays = new Set(challenges.filter(c => c.status === 'missed').map(c => c.day));
  const currentDay = current?.day || 1;

  if (solvedCountEl) {
    solvedCountEl.textContent = solvedDays.size;
  }

  for (let d = 1; d <= 30; d++) {
    const match = challenges.find(c => c.day === d);
    const node = document.createElement('div');
    node.className = 'stone-node';

    const circle = document.createElement('div');
    circle.className = 'stone-circle';

    const questTitle = match?.title || `Day ${d} Coding Challenge`;

    if (solvedDays.has(d)) {
      circle.classList.add('stone-solved');
      circle.innerHTML = '✅';
      node.title = `Day ${d}: Solved! (${questTitle})`;
    } else if (missedDays.has(d)) {
      circle.classList.add('stone-missed');
      circle.innerHTML = '💔';
      node.title = `Day ${d}: Missed! (${questTitle})`;
    } else if (d === currentDay) {
      circle.classList.add('stone-active');
      circle.innerHTML = '⚡';
      node.title = `Day ${d}: Active Today! (${questTitle})`;
    } else {
      circle.classList.add('stone-locked');
      circle.innerHTML = `<span style="font-size:0.9rem; opacity:0.6;">🔒</span>`;
      node.title = `Day ${d}: ${questTitle}`;
    }

    const label = document.createElement('span');
    label.className = 'stone-day-label';
    label.textContent = `Day ${d}`;

    node.onclick = () => {
      if (match && match.link) {
        window.open(match.link, '_blank', 'noopener,noreferrer');
      } else {
        window.open('https://unstop.com/practice/coding', '_blank', 'noopener,noreferrer');
      }
    };

    node.appendChild(circle);
    node.appendChild(label);
    container.appendChild(node);
  }
}

// ─── 4. Render History Table (Full 30-Day Track) ──────────────────────────────
function renderHistory(challenges = []) {
  const tbody = document.getElementById('history-body');
  if (!tbody) return;

  if (!challenges || challenges.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty-row">No quest records found.</td></tr>';
    return;
  }

  // Sort chronologically Day 1 to 30
  const sorted = [...challenges].sort((a, b) => a.day - b.day);

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
      : (c.status === 'pending' ? 'Due Tonight 12 AM' : c.status === 'missed' ? 'Missed Day' : '—');

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

// ─── Actions: Mark as Solved ──────────────────────────────────────────────────
async function markSolved() {
  try {
    const res = await fetch(`${API_BASE}/api/solve`, { method: 'POST' });
    const data = await res.json();

    if (res.ok && data.success) {
      showToast(`🎉 Streak Maintained! ${data.message}`, 'success');
      launchConfetti();
      stopCountdown();
      closeModal();
      await loadData();
    } else {
      showToast('❌ ' + (data.error || data.detail || 'Could not mark solved'), 'error');
    }
  } catch (err) {
    showToast('❌ Server error: ' + err.message, 'error');
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
