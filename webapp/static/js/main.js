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
    checkAuth
};
