/* Media picker for the path-valued image fields.
 *
 * The fields this attaches to hold a *path*, not a file, so this script never
 * uploads anything and never hides the text input. Its whole job is to let an
 * editor pick from the library instead of copying a path by hand, and to show
 * what is currently in the field so a wrong choice is visible before saving.
 *
 * One modal is built lazily and reused by every widget on the page, because a
 * form can carry three of these fields and three copies of the same dialog
 * would be three times the DOM for no benefit.
 */
(function () {
    'use strict';

    var modal = null;
    var grid = null;
    var status = null;
    var search = null;
    var images = [];
    var activeInput = null;
    var loaded = false;

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    /* The preview is a convenience, not a source of truth: the input is. So it
     * is rebuilt from the input's value on every change, including a manual
     * paste, rather than only after a pick. */
    function refreshPreview(root) {
        var input = root.querySelector('input');
        var preview = root.querySelector('[data-picker-preview]');
        var clear = root.querySelector('[data-picker-clear]');
        if (!input || !preview) return;

        var path = (input.value || '').trim();

        /* Rewriting the preview is a DOM mutation, and this function is reached
           from a MutationObserver watching the whole document. A version that
           always rewrote therefore retriggered itself forever: empty the
           preview, append a fresh node, observe that, empty it again. The tab
           never went idle, so the page never finished loading -- and because
           the whole form sits inside the observed document it could not be
           saved either. Any admin page holding a media path hung on open, which
           is why the section form was unusable while the page form, whose only
           picker was empty, looked fine.

           So: touch the DOM only when what the preview should show has actually
           changed. A second pass becomes a no-op and the observer settles after
           one cycle. */
        var signature = 'v1:' + path;
        if (root.dataset.pickerSignature === signature) return;
        root.dataset.pickerSignature = signature;

        if (clear) clear.hidden = !path;
        if (!path) {
            preview.hidden = true;
            preview.textContent = '';
            return;
        }
        /* A path that is not this site's media URL cannot be thumbnailed, and
         * guessing would produce a broken image. Showing the text is honest. */
        if (!/^\/media\//.test(path)) {
            preview.hidden = false;
            preview.textContent = '';
            preview.appendChild(el('code', 'media-picker-path', path));
            return;
        }
        preview.hidden = false;
        preview.textContent = '';
        var img = document.createElement('img');
        img.className = 'media-picker-thumb';
        img.alt = '';
        img.loading = 'lazy';
        img.src = path;
        img.addEventListener('error', function () {
            preview.textContent = '';
            preview.appendChild(el('code', 'media-picker-path', path));
        });
        preview.appendChild(img);
    }

    function renderGrid(term) {
        if (!grid) return;
        grid.textContent = '';
        var needle = (term || '').trim().toLowerCase();
        var shown = 0;
        images.forEach(function (item) {
            var haystack = (item.title + ' ' + item.path + ' ' + (item.alt || ''))
                .toLowerCase();
            if (needle && haystack.indexOf(needle) === -1) return;
            shown += 1;
            var cell = el('button', 'media-picker-cell');
            cell.type = 'button';
            cell.title = item.path;
            if (item.thumb) {
                var img = document.createElement('img');
                img.className = 'media-picker-cell-img';
                img.src = item.thumb;
                img.alt = '';
                img.loading = 'lazy';
                cell.appendChild(img);
            }
            cell.appendChild(el('span', 'media-picker-cell-title', item.title));
            cell.addEventListener('click', function () {
                choose(item);
            });
            grid.appendChild(cell);
        });
        if (!shown) {
            grid.appendChild(el('p', 'media-picker-empty',
                needle ? 'No image matches that.' : 'The media library is empty.'));
        }
    }

    function choose(item) {
        if (!activeInput) return;
        activeInput.value = item.path;
        /* Dispatch both events: `input` is what a listener for a human typing
         * would hear, and `change` is what Django's own inline previews and
         * any validation hooks listen for. Setting .value alone triggers
         * neither. */
        activeInput.dispatchEvent(new Event('input', {bubbles: true}));
        activeInput.dispatchEvent(new Event('change', {bubbles: true}));
        close();
    }

    function buildModal() {
        if (modal) return modal;
        modal = el('div', 'media-picker-modal');
        modal.hidden = true;
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-label', 'Media library');

        var panel = el('div', 'media-picker-panel');
        var head = el('div', 'media-picker-head');
        head.appendChild(el('h2', 'media-picker-title', 'Media library'));
        var closeBtn = el('button', 'media-picker-x', '×');
        closeBtn.type = 'button';
        closeBtn.setAttribute('aria-label', 'Close');
        closeBtn.addEventListener('click', close);
        head.appendChild(closeBtn);

        search = el('input', 'media-picker-search');
        search.type = 'search';
        search.placeholder = 'Filter images…';
        search.setAttribute('aria-label', 'Filter images');
        search.addEventListener('input', function () {
            renderGrid(search.value);
        });

        grid = el('div', 'media-picker-grid');
        status = el('p', 'media-picker-status', 'Loading…');

        var body = el('div', 'media-picker-body');
        body.appendChild(search);
        body.appendChild(status);
        body.appendChild(grid);

        panel.appendChild(head);
        panel.appendChild(body);
        modal.appendChild(panel);

        modal.addEventListener('click', function (event) {
            /* A click on the backdrop closes; a click inside must not. */
            if (event.target === modal) close();
        });

        document.body.appendChild(modal);
        return modal;
    }

    function open(root) {
        var input = root.querySelector('input');
        if (!input) return;
        activeInput = input;
        buildModal();
        modal.hidden = false;
        document.body.classList.add('media-picker-open');
        search.focus();

        var endpoint = root.querySelector('[data-picker-open]');
        var url = endpoint ? endpoint.dataset.endpoint : null;
        if (!url) return;

        status.textContent = 'Loading…';
        fetch(url, {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
            .then(function (response) {
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.json();
            })
            .then(function (data) {
                images = data.images || [];
                loaded = true;
                status.textContent = images.length
                    ? (images.length + (data.truncated ? '+ images' : ' images'))
                    : 'No published images yet.';
                if (!images.length) {
                    grid.textContent = '';
                    grid.appendChild(el('p', 'media-picker-empty',
                        'Upload an image in the media library first, or type a path below.'));
                }
                renderGrid(search.value);
            })
            .catch(function () {
                status.textContent = 'Could not load the media library.';
            });
    }

    function close() {
        if (modal) modal.hidden = true;
        document.body.classList.remove('media-picker-open');
    }

    document.addEventListener('click', function (event) {
        var openBtn = event.target.closest('[data-picker-open]');
        if (openBtn) {
            event.preventDefault();
            open(openBtn.closest('[data-media-picker]'));
            return;
        }
        var clearBtn = event.target.closest('[data-picker-clear]');
        if (clearBtn) {
            event.preventDefault();
            var root = clearBtn.closest('[data-media-picker]');
            var input = root && root.querySelector('input');
            if (input) {
                input.value = '';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                refreshPreview(root);
            }
        }
    });

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && modal && !modal.hidden) close();
    });

    /* Keep every preview in step with its input, including inline rows added
     * by Django after page load and paths typed by hand. */
    document.addEventListener('input', function (event) {
        var root = event.target.closest && event.target.closest('[data-media-picker]');
        if (root) refreshPreview(root);
    });

    function init() {
        var roots = document.querySelectorAll('[data-media-picker]');
        Array.prototype.forEach.call(roots, function (root) {
            refreshPreview(root);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    /* Inline forms are added to the DOM by Django's inlines.js without an
     * event we can hook, so the empty-row templates are observed instead. */
    if (window.MutationObserver) {
        new MutationObserver(init).observe(document.documentElement, {
            childList: true, subtree: true
        });
    }
})();
