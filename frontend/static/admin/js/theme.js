/* Explicit light/dark theme for the admin.
 *
 * The bug this replaces: Django 5.2 ships dark_mode.css, which repaints its
 * `:root` variables under `prefers-color-scheme: dark`. admin.css also set
 * `:root`, loaded later, and won. So on a machine with dark mode on, the admin
 * got our light palette's near-black text on Django's #121212 body -- unreadable.
 *
 * Two changes fix it, and both are load-bearing:
 *   1. tokens.css scopes every colour to `:root[data-theme="light"]` or
 *      `[data-theme="dark"]`. Both are (0,2,0), which beats Django's bare
 *      `:root` (0,1,0) even inside its media query. An explicit theme therefore
 *      always wins, whichever way the OS is set.
 *   2. The attribute is always present. Never absent, never "unset" -- if it
 *      were missing, the media query would decide again and we would be back to
 *      two palettes arguing.
 */
(function () {
    'use strict';

    var KEY = 'studio.theme';
    var root = document.documentElement;

    function stored() {
        try {
            return window.localStorage.getItem(KEY);
        } catch (e) {
            return null;  // private mode, or storage disabled
        }
    }

    function systemTheme() {
        return window.matchMedia
            && window.matchMedia('(prefers-color-scheme: dark)').matches
            ? 'dark' : 'light';
    }

    function resolve() {
        var value = stored();
        return value === 'light' || value === 'dark' ? value : systemTheme();
    }

    function apply(theme) {
        root.setAttribute('data-theme', theme);
        /* Tells the browser to render native widgets -- scrollbars, date
         * pickers, the autofill dropdown -- in the matching scheme. Without
         * this the OS keeps drawing light scrollbars on a dark page. */
        root.style.colorScheme = theme;
    }

    /* Runs in <head> before first paint, same as the sidebar-collapse snippet.
     * A toggle applied after paint would show a white flash on every load for
     * everyone who chose dark. */
    window.studioTheme = {
        key: KEY,
        current: resolve,
        apply: apply,
        systemTheme: systemTheme,
        set: function (theme) {
            apply(theme);
            try {
                window.localStorage.setItem(KEY, theme);
            } catch (e) {
                /* Not being able to remember the choice is not worth an
                 * exception; the theme still applies for this page. */
            }
            document.dispatchEvent(new CustomEvent('studio:themechange', {
                detail: {theme: theme}
            }));
        },
        toggle: function () {
            this.set(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
        }
    };
})();
