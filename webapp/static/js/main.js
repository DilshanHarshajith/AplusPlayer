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
    getHiddenGroups,
    isGroupHidden,
    setGroupHidden,
    getReadFiles,
    isFileRead,
    setFileRead,
    getVideoPosition,
    setVideoPosition,
    clearVideoPosition
};
