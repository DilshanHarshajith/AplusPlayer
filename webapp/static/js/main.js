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
    getVideoPosition,
    setVideoPosition,
    clearVideoPosition
};
