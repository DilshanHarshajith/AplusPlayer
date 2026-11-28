// Main JavaScript file for Aplus Player Web UI

// Logout function
async function logout() {
    try {
        const response = await fetch('/api/logout', {
            method: 'POST'
        });
        
        if (response.ok) {
            window.location.href = '/login';
        } else {
            console.error('Logout failed');
        }
    } catch (error) {
        console.error('Network error during logout:', error);
        // Even if the API call fails, clear the session locally
        window.location.href = '/login';
    }
}

// Watched-lesson tracking (stored client-side; the vendor API has no
// concept of a "watched" flag, so this is purely a local UI preference)
const WATCHED_KEY = 'aplus_watched_lessons';

// Returns { [lessonId]: true, ... }
function getWatchedLessons() {
    try {
        return JSON.parse(localStorage.getItem(WATCHED_KEY) || '{}');
    } catch (error) {
        return {};
    }
}

function isLessonWatched(lessonId) {
    return !!getWatchedLessons()[lessonId];
}

function setLessonWatched(lessonId, watched) {
    const watchedLessons = getWatchedLessons();
    if (watched) {
        watchedLessons[lessonId] = true;
    } else {
        delete watchedLessons[lessonId];
    }
    localStorage.setItem(WATCHED_KEY, JSON.stringify(watchedLessons));
}

// Per-file read tracking for file lessons (a card may hold several
// attachments, each marked read independently). Stored as
// { [lessonId]: { [fileIndex]: true }, ... }. The lesson-level
// "watched" flag stays separate — the file viewer derives it from
// "all files read" (see lesson.html).
const READ_FILES_KEY = 'aplus_read_files';

// Returns { [fileIndex]: true, ... } for a lesson.
function getReadFiles(lessonId) {
    try {
        const all = JSON.parse(localStorage.getItem(READ_FILES_KEY) || '{}');
        return all[lessonId] || {};
    } catch (error) {
        return {};
    }
}

function isFileRead(lessonId, fileIndex) {
    return !!getReadFiles(lessonId)[fileIndex];
}

function setFileRead(lessonId, fileIndex, read) {
    let all;
    try {
        all = JSON.parse(localStorage.getItem(READ_FILES_KEY) || '{}');
    } catch (error) {
        all = {};
    }
    const files = all[lessonId] || {};
    if (read) {
        files[fileIndex] = true;
    } else {
        delete files[fileIndex];
    }
    all[lessonId] = files;
    localStorage.setItem(READ_FILES_KEY, JSON.stringify(all));
}

// Hidden lesson categories (the group chips on a course page). A hidden
// category keeps its chip visible but removes its lessons from the menu.
// Stored per course as { [courseId]: { [groupName]: true }, ... }.
const HIDDEN_GROUPS_KEY = 'aplus_hidden_groups';

// Returns the flat { [groupName]: true } map for one course.
function getHiddenGroups(courseId) {
    let all;
    try {
        all = JSON.parse(localStorage.getItem(HIDDEN_GROUPS_KEY) || '{}');
    } catch (error) {
        all = {};
    }
    if (!all || typeof all !== 'object' || Array.isArray(all)) all = {};
    if (!all[courseId] || typeof all[courseId] !== 'object' || Array.isArray(all[courseId])) {
        all[courseId] = {};
    }
    return all[courseId];
}

function isGroupHidden(courseId, group) {
    return !!getHiddenGroups(courseId)[group];
}

function setGroupHidden(courseId, group, hidden) {
    let all;
    try {
        all = JSON.parse(localStorage.getItem(HIDDEN_GROUPS_KEY) || '{}');
    } catch (error) {
        all = {};
    }
    if (!all || typeof all !== 'object' || Array.isArray(all)) all = {};
    if (!all[courseId] || typeof all[courseId] !== 'object' || Array.isArray(all[courseId])) {
        all[courseId] = {};
    }
    if (hidden) {
        all[courseId][group] = true;
    } else {
        delete all[courseId][group];
    }
    localStorage.setItem(HIDDEN_GROUPS_KEY, JSON.stringify(all));
}

// Hidden courses on the courses menu. Stored as { [courseId]: title, ... }
// (the title lets the hidden list show names even if a course disappears from
// the API response). Like the other UI preferences, this is browser-local.
const HIDDEN_COURSES_KEY = 'aplus_hidden_courses';

function getHiddenCourses() {
    let all;
    try {
        all = JSON.parse(localStorage.getItem(HIDDEN_COURSES_KEY) || '{}');
    } catch (error) {
        all = {};
    }
    if (!all || typeof all !== 'object' || Array.isArray(all)) all = {};
    return all;
}

function setCourseHidden(courseId, title, hidden) {
    const all = getHiddenCourses();
    if (hidden) {
        all[courseId] = String(title || 'Untitled Course');
    } else {
        delete all[courseId];
    }
    localStorage.setItem(HIDDEN_COURSES_KEY, JSON.stringify(all));
}

function clearHiddenCourses() {
    localStorage.setItem(HIDDEN_COURSES_KEY, '{}');
}

// Option to remove watched lessons from the course menu. Like the watched
// flags themselves, this is a browser-only preference.
const HIDE_WATCHED_KEY = 'aplus_hide_watched';

function getHideWatched() {
    try {
        return JSON.parse(localStorage.getItem(HIDE_WATCHED_KEY) || 'false') === true;
    } catch (error) {
        return false;
    }
}

function setHideWatched(hide) {
    localStorage.setItem(HIDE_WATCHED_KEY, JSON.stringify(!!hide));
}

// Last-watched-position tracking (stored client-side, same pattern as the
// watched-lesson tracking above) — lets the player resume where you left off
const POSITION_KEY = 'aplus_video_positions';

// Don't bother saving/resuming right at the very start or very end of a video
const POSITION_MIN_SECONDS = 5;
const POSITION_END_BUFFER_SECONDS = 15;

// Returns { [lessonId]: seconds, ... }
function getVideoPositions() {
    try {
        return JSON.parse(localStorage.getItem(POSITION_KEY) || '{}');
    } catch (error) {
        return {};
    }
}

function getVideoPosition(lessonId) {
    const positions = getVideoPositions();
    return positions[lessonId] || 0;
}

// `duration` (optional) avoids saving a position right at the tail end of
// the video, so a finished lesson starts over next time instead of resuming
// with only a few seconds left.
function setVideoPosition(lessonId, seconds, duration) {
    const positions = getVideoPositions();
    const nearEnd = duration && seconds >= duration - POSITION_END_BUFFER_SECONDS;
    if (seconds > POSITION_MIN_SECONDS && !nearEnd) {
        positions[lessonId] = seconds;
    } else {
        delete positions[lessonId];
    }
    localStorage.setItem(POSITION_KEY, JSON.stringify(positions));
}

function clearVideoPosition(lessonId) {
    const positions = getVideoPositions();
    delete positions[lessonId];
    localStorage.setItem(POSITION_KEY, JSON.stringify(positions));
}

// Utility function to show error messages
function showError(message) {
    const errorDiv = document.getElementById('error-message') || document.getElementById('lesson-error');
    if (errorDiv) {
        errorDiv.textContent = message;
        errorDiv.classList.remove('hidden');
    }
}

// Utility function to hide error messages
function hideError() {
    const errorDivs = document.querySelectorAll('.error-message');
    errorDivs.forEach(div => div.classList.add('hidden'));
}

// Utility function to show loading state
function showLoading() {
    const loadingDiv = document.getElementById('loading') || document.getElementById('course-loading') || document.getElementById('lesson-loading');
    if (loadingDiv) {
        loadingDiv.classList.remove('hidden');
    }
}

// Utility function to hide loading state
function hideLoading() {
    const loadingDivs = document.querySelectorAll('.loading-spinner');
    loadingDivs.forEach(div => div.classList.add('hidden'));
}

// Format date helper
function formatDate(dateString) {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}

// Debounce function for search inputs
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Check authentication status
async function checkAuth() {
    try {
        const response = await fetch('/api/user');
        if (response.status === 401) {
            window.location.href = '/login';
            return false;
        }
        return true;
    } catch (error) {
        console.error('Auth check failed:', error);
        return false;
    }
}

// Initialize tooltips or other UI components
document.addEventListener('DOMContentLoaded', function() {
    // Add any global initialization here
    
    // Smooth scroll for anchor links
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            const href = this.getAttribute('href');
            if (href !== '#') {
                e.preventDefault();
                const target = document.querySelector(href);
                if (target) {
                    target.scrollIntoView({
                        behavior: 'smooth'
                    });
                }
            }
        });
    });
});

// Shared helpers used by the live-class UI below
// HTML-escape helper for values interpolated into innerHTML
function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function readJSON(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key) || '') || fallback; } catch (e) { return fallback; }
}
function writeJSON(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage full/blocked */ }
}

// Small non-blocking message at the bottom of the screen
function toast(message, ms) {
    let box = document.getElementById('toast-box');
    if (!box) { box = document.createElement('div'); box.id = 'toast-box'; document.body.appendChild(box); }
    const t = document.createElement('div');
    t.className = 'toast'; t.textContent = message; box.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, ms || 3500);
}


// ---------------------------------------------------------------------
// Live classes: status chip, countdown, reminders
// ---------------------------------------------------------------------
const LIVE_WINDOW_MIN = 180;   // how long a class is considered "live"
const LIVE_SOON_MIN = 10;      // "starting soon" / reminder lead time
const LIVE_TIMES_KEY = 'aplus_live_times';
const REMIND_KEY = 'aplus_live_reminders';

function parseLiveDate(title) {
    const m = /(\d{4})[-\/.](\d{1,2})[-\/.](\d{1,2})/.exec(title || '');
    return m ? { y: +m[1], mo: +m[2], d: +m[3] } : null;
}

const HIDE_OLD_LIVE_KEY = 'aplus_hide_old_live';
const TITLE_HIDE_KEY = 'aplus_hide_title_contains';
const TITLE_PARTS_KEY = 'aplus_hidden_title_parts';

// Map of lowercased title fragment -> true when it is hiding lessons.
function getTitleParts() {
    let parts;
    try { parts = JSON.parse(localStorage.getItem(TITLE_PARTS_KEY) || '{}'); }
    catch (error) { parts = {}; }
    if (!parts || typeof parts !== 'object' || Array.isArray(parts)) parts = {};
    // Migrate the old single-text setting into the first chip.
    if (!Object.keys(parts).length) {
        const legacy = String(localStorage.getItem(TITLE_HIDE_KEY) || '').trim();
        if (legacy) {
            parts[legacy.toLocaleLowerCase()] = true;
            localStorage.setItem(TITLE_PARTS_KEY, JSON.stringify(parts));
            localStorage.removeItem(TITLE_HIDE_KEY);
        }
    }
    return parts;
}

function setTitleParts(parts) {
    try { localStorage.setItem(TITLE_PARTS_KEY, JSON.stringify(parts || {})); }
    catch (error) { /* storage blocked/full: chips still work for this tab */ }
}

function getTitleHideText() {
    try { return String(localStorage.getItem(TITLE_HIDE_KEY) || ''); }
    catch (error) { return ''; }
}

function setTitleHideText(text) {
    try { localStorage.setItem(TITLE_HIDE_KEY, String(text || '')); }
    catch (error) { /* storage blocked/full: filter still works for this tab */ }
}

function getHideOldLive() {
    try {
        return JSON.parse(localStorage.getItem(HIDE_OLD_LIVE_KEY) || 'false') === true;
    } catch (error) {
        return false;
    }
}

function setHideOldLive(hide) {
    localStorage.setItem(HIDE_OLD_LIVE_KEY, JSON.stringify(!!hide));
}

// Titles are "2027 LIVE 11.28": 2027 is the exam year, 11.28 is the class
// date. parseLiveDate only understands YYYY-MM-DD, so also accept MM.DD /
// MM-DD / MM/DD here. Skip clock times like "7.30 pm".
function parseLiveClassDate(title) {
    const full = parseLiveDate(title);
    if (full) return full;
    const s = String(title || '');
    const m = /(?:^|[^\d])(\d{1,2})[.\/-](\d{1,2})(?!\d)/.exec(s);
    if (!m || /\s*[ap]\.?m/i.test(s.slice(m.index + m[0].length))) return null;
    const mo = +m[1], d = +m[2];
    return (mo >= 1 && mo <= 12 && d >= 1 && d <= 31) ? { mo: mo, d: d } : null;
}

// Title dates carry the exam year ("2027 LIVE 11.28" on 2026-11-28), so only
// the month/day is meaningful. Undated classes stay visible — they may be
// happening right now. Course classes all belong to the same calendar year.
function isOldLiveLesson(lesson, now) {
    now = now || new Date();
    const dt = parseLiveClassDate((lesson && lesson.title) || '');
    if (dt) {
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const day = new Date(now.getFullYear(), dt.mo - 1, dt.d);
        return day < today;
    }
    // Undated titles (e.g. "Paper live -10") still carry the vendor's
    // month/year — treat anything from an earlier month as old too.
    const month = Number((lesson || {}).month);
    const year = Number((lesson || {}).year);
    if (!Number.isFinite(month) || month < 1 || month > 12) return false;
    if (!Number.isFinite(year) || year > now.getFullYear()) return false;
    return year < now.getFullYear() || month < now.getMonth() + 1;
}
function parseLiveTime(title) {
    const s = String(title || '').replace(/\d{4}[-\/.]\d{1,2}[-\/.]\d{1,2}/g, ' ');
    let m = /\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?m\b/i.exec(s);          // 7 PM, 7.30 pm
    if (m) { let h = +m[1] % 12; if (/p/i.test(m[3])) h += 12; return { h: h, mi: +(m[2] || 0) }; }
    m = /\b([01]?\d|2[0-3]):([0-5]\d)\b/.exec(s);                             // 19:30
    return m ? { h: +m[1], mi: +m[2] } : null;
}
function getLiveTimes() { return readJSON(LIVE_TIMES_KEY, {}); }
function saveLiveTime(id, hhmm, asDefault) {
    const t = getLiveTimes();
    if (asDefault) t._default = hhmm; else t[id] = hhmm;
    writeJSON(LIVE_TIMES_KEY, t);
}
// state: live | soon | upcoming | future | today | ended | unknown
function liveInfo(lesson, now) {
    now = now || new Date();
    const title = lesson.title || '';
    const dt = parseLiveDate(title);
    if (!dt) return { state: 'unknown' };
    const times = getLiveTimes();
    let tm = parseLiveTime(title);
    if (!tm) {
        const v = times[lesson._id] || times._default;
        if (v) { const parts = v.split(':').map(Number); tm = { h: parts[0], mi: parts[1] }; }
    }
    const day = new Date(dt.y, dt.mo - 1, dt.d);
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const days = Math.round((day - today) / 864e5);
    if (!tm) {
        if (days < 0) return { state: 'ended' };
        return days === 0 ? { state: 'today', needsTime: true } : { state: 'future', days: days, needsTime: true };
    }
    const start = new Date(dt.y, dt.mo - 1, dt.d, tm.h, tm.mi);
    const diff = (start - now) / 60000;
    if (diff > LIVE_SOON_MIN) return { state: days > 0 ? 'future' : 'upcoming', start: start, days: days };
    if (diff > 0) return { state: 'soon', start: start };
    if (-diff < LIVE_WINDOW_MIN) return { state: 'live', start: start };
    return { state: 'ended', start: start };
}
function fmtClock(d) { return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
function liveLabel(i) {
    switch (i.state) {
        case 'live': return 'LIVE NOW';
        case 'soon': case 'upcoming': {
            const s = Math.max(0, Math.round((i.start - new Date()) / 1000));
            const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
            return 'Starts in ' + (h ? h + 'h ' + m + 'm' : m + 'm ' + String(x).padStart(2, '0') + 's');
        }
        case 'future': return i.days === 1 ? 'Tomorrow' + (i.start ? ' \u00b7 ' + fmtClock(i.start) : '') : 'In ' + i.days + ' days';
        case 'today': return 'TODAY';
        case 'ended': return 'Ended';
        default: return '';
    }
}
function liveChipHTML(l) {
    const info = liveInfo(l);
    if (info.state === 'unknown') return '<span class="live-chip unknown"><span class="live-dot"></span>LIVE</span>';
    return '<span class="live-chip ' + info.state + '" data-live-id="' + esc(l._id) + '" data-live-title="' + esc(l.title || '') +
           '"><span class="live-dot"></span><span class="live-text">' + esc(liveLabel(info)) + '</span></span>';
}
function startLiveTicker() {
    if (window.__liveTicker) return;
    window.__liveTicker = setInterval(() => {
        document.querySelectorAll('[data-live-id]').forEach(el => {
            const info = liveInfo({ _id: el.dataset.liveId, title: el.dataset.liveTitle });
            el.className = 'live-chip ' + info.state;
            const t = el.querySelector('.live-text'); if (t) t.textContent = liveLabel(info);
        });
    }, 1000);
}
function askLiveTime(lessonId) {
    return new Promise(resolve => {
        const cur = getLiveTimes();
        const modal = document.createElement('div');
        modal.className = 'timestamp-modal';
        modal.innerHTML = '<div class="timestamp-modal-content"><h3>Class start time</h3>' +
            '<div class="form-group"><label for="lt-input">Start time (your local time)</label>' +
            '<input type="time" id="lt-input" value="' + (cur[lessonId] || cur._default || '') + '"></div>' +
            '<label class="file-picker-select-all"><input type="checkbox" id="lt-default"> Use this time for all live classes</label>' +
            '<div class="timestamp-modal-buttons"><button class="btn btn-primary" id="lt-save">Save</button>' +
            '<button class="btn btn-secondary" id="lt-cancel">Cancel</button></div></div>';
        document.body.appendChild(modal);
        const close = v => { modal.remove(); resolve(v); };
        modal.querySelector('#lt-save').onclick = () => {
            const v = modal.querySelector('#lt-input').value;
            if (!v) { toast('Pick a time first'); return; }
            saveLiveTime(lessonId, v, modal.querySelector('#lt-default').checked); close(true);
        };
        modal.querySelector('#lt-cancel').onclick = () => close(false);
        modal.addEventListener('click', e => { if (e.target === modal) close(false); });
    });
}

// Reminders fire only while a tab of this app is open (no push server).
const _remTimers = {};
function getReminders() { return readJSON(REMIND_KEY, {}); }
function scheduleReminders() {
    Object.values(_remTimers).forEach(clearTimeout);
    const all = getReminders(), now = Date.now(); let dirty = false;
    Object.entries(all).forEach(([id, r]) => {
        if (r.start < now) { delete all[id]; dirty = true; return; }
        const delay = Math.max(0, r.start - LIVE_SOON_MIN * 60000 - now);
        if (delay > 2147483000) return;
        _remTimers[id] = setTimeout(() => {
            fireReminder(id, r);
            const a = getReminders(); delete a[id]; writeJSON(REMIND_KEY, a);
        }, delay);
    });
    if (dirty) writeJSON(REMIND_KEY, all);
}
function fireReminder(id, r) {
    if ('Notification' in window && Notification.permission === 'granted') {
        const n = new Notification('Live class starting soon', { body: r.title + '\nStarts at ' + fmtClock(new Date(r.start)) });
        n.onclick = () => { window.focus(); location.href = '/lesson/' + id + '?live=1'; };
    }
    toast('Live class starting soon: ' + r.title, 12000);
}
// Returns true when a reminder is now set, false when removed / not possible
async function toggleReminder(lesson) {
    const all = getReminders();
    if (all[lesson._id]) { delete all[lesson._id]; writeJSON(REMIND_KEY, all); scheduleReminders(); toast('Reminder removed'); return false; }
    const info = liveInfo(lesson);
    if (!info.start || info.state === 'live' || info.state === 'ended') { toast('Set the class start time first'); return false; }
    if ('Notification' in window && Notification.permission === 'default') {
        try { await Notification.requestPermission(); } catch (e) { /* ignore */ }
    }
    all[lesson._id] = { start: info.start.getTime(), title: lesson.title || 'Live class' };
    writeJSON(REMIND_KEY, all); scheduleReminders();
    toast(('Notification' in window && Notification.permission === 'granted')
        ? 'Reminder set - we will notify you 10 minutes before (keep a tab open)'
        : 'Reminder set - notifications are blocked, so you will see an in-app alert');
    return true;
}
document.addEventListener('DOMContentLoaded', scheduleReminders);

// Export functions for use in other scripts
window.AplusPlayer = {
    logout,
    showError,
    hideError,
    showLoading,
    hideLoading,
    formatDate,
    debounce,
    checkAuth,
    isLessonWatched,
    setLessonWatched,
    getHideWatched,
    setHideWatched,
    getHideOldLive,
    setHideOldLive,
    isOldLiveLesson,
    getTitleHideText,
    setTitleHideText,
    getTitleParts,
    setTitleParts,
    getHiddenGroups,
    isGroupHidden,
    setGroupHidden,
    getHiddenCourses,
    setCourseHidden,
    clearHiddenCourses,
    getReadFiles,
    isFileRead,
    setFileRead,
    getVideoPosition,
    setVideoPosition,
    clearVideoPosition,
    esc,
    toast,
    liveInfo,
    liveLabel,
    liveChipHTML,
    startLiveTicker,
    askLiveTime,
    getReminders,
    toggleReminder
};
